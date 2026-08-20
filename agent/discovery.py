"""Stage 3 — vendor discovery, normalisation, and the hard-constraint gate.

WHY THIS FILE EXISTS
--------------------
Two jobs, in order:

  1. NORMALISE. Two vendor catalogs arrive in two different shapes. Both come out
     of here as the one `Product` model the rest of the pipeline understands.
  2. GATE. Every product is checked against the brief's HARD constraints and comes
     out either eligible or rejected, with a plain-words reason.

Nothing here scores or ranks. Stage 3 answers "does this qualify?" and stops.
Stage 4 answers "how much do we prefer it among the ones that qualified?". Those
are two different questions and keeping them in two different files is what stops
them being quietly merged into one fudge.

WHY TWO SCHEMAS
---------------
CLAUDE.md asks for two mock catalogs with deliberately DIFFERENT schemas, and
data/ delivers that:

  PackHub (direct JSON)          BoxBazaar (aggregator CSV)
  ---------------------          --------------------------
  unit_price_inr: 21.90          rate_paise: 1760          -> divide by 100
  lead_time_days: 4              ship_window: "7-9 days"   -> take the LATE end
  seller_rating: 4.8             vendor_score_100: 82      -> divide by 20
  replacement_window_days: 7     returns_policy: "30-day…" -> pull the number out
  attributes: ["double-wall",…]  spec_blob: "DW | 200 x …" -> split and expand

If both files had the same columns the normaliser would be a rename and would
prove nothing. Every conversion above is a place a real integration breaks, and
a place Pydantic will shout at us if we get it wrong.

THE ONE JUDGEMENT CALL IN HERE
------------------------------
The aggregator promises "7-9 days". We record 9. A delivery window should be
judged by the date it might actually arrive, not by its most flattering end —
promising the user 7 and delivering on 9 is how a supplier loses a customer.
It is the pessimistic reading on purpose, and it is one function
(`_worst_case_days`) so it can be argued with.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from agent.audit import STAGE_DISCOVERY, AuditLogger
from agent.models import Brief, FilterResult, Product

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PACKHUB_PATH = DATA_DIR / "packhub_direct.json"
BOXBAZAAR_PATH = DATA_DIR / "boxbazaar_aggregator.csv"


# ---------------------------------------------------------------------------
# Spec vocabulary
# ---------------------------------------------------------------------------
# Trade shorthand, expanded so the two catalogs can be compared with the brief.
# The aggregator writes "DW"; the user writes "double-wall"; Gemini sometimes
# returns "double wall". All three have to mean one thing or an identical box
# fails a hard gate over punctuation.
_SPEC_SYNONYMS: dict[str, str] = {
    "dw": "doublewall",
    "doublewall": "doublewall",
    "sw": "singlewall",
    "singlewall": "singlewall",
}


def spec_key(text: str) -> str:
    """Reduce a spec to a comparable key: lowercase, no spaces, no punctuation.

    This exists because of a real mismatch we hit while testing language.py:
    Gemini returns "200x150x80 mm" and the offline parser returns
    "200x150x80mm". Same box, one space apart. Without this function that space
    fails a non-negotiable hard constraint and the demo shows zero eligible
    products for no visible reason.
    """
    cleaned = text.lower().strip()
    cleaned = cleaned.replace("×", "x")
    cleaned = re.sub(r"[\s\-_.,]", "", cleaned)
    return _SPEC_SYNONYMS.get(cleaned, cleaned)


# ---------------------------------------------------------------------------
# Source 1 — PackHub, a direct vendor's JSON
# ---------------------------------------------------------------------------

def load_packhub(path: Path | None = None) -> list[Product]:
    """Read the direct vendor feed. The easy one: units already match ours."""
    raw = json.loads((path or PACKHUB_PATH).read_text(encoding="utf-8"))
    return [
        Product(
            product_id=item["sku"],
            name=item["title"],
            source=raw["vendor"],
            source_type="direct",
            category=item["category"],
            specs=item["attributes"],
            price_per_unit_inr=item["unit_price_inr"],
            delivery_days=item["lead_time_days"],
            available_quantity=item["stock_qty"],
            reliability_rating=item["seller_rating"],
            replacement_window_days=item["replacement_window_days"],
        )
        for item in raw["catalog"]
    ]


# ---------------------------------------------------------------------------
# Source 2 — BoxBazaar, an aggregator's CSV
# ---------------------------------------------------------------------------

def load_boxbazaar(path: Path | None = None) -> list[Product]:
    """Read the aggregator feed, converting every column into our units.

    This is where the real work is. Five conversions, each one line, each one a
    place a live integration would actually break.
    """
    text = (path or BOXBAZAAR_PATH).read_text(encoding="utf-8")
    # The file carries a comment header explaining why it is shaped this way.
    # csv.DictReader has no idea what a '#' means, so we drop those lines first.
    rows = csv.DictReader(line for line in text.splitlines() if not line.startswith("#"))
    return [
        Product(
            product_id=row["item_code"],
            name=row["product_name"],
            source="BoxBazaar",
            source_type="aggregator",
            category=row["category_tag"].strip().lower(),          # "PACKAGING" -> "packaging"
            specs=[part.strip() for part in row["spec_blob"].split("|") if part.strip()],
            price_per_unit_inr=int(row["rate_paise"]) / 100,        # 1760 paise -> 17.60
            delivery_days=_worst_case_days(row["ship_window"]),     # "7-9 days" -> 9
            available_quantity=int(row["qty_available"]),
            reliability_rating=float(row["vendor_score_100"]) / 20, # 82/100 -> 4.1/5
            replacement_window_days=_first_number(row["returns_policy"]),  # "30-day…" -> 30
        )
        for row in rows
    ]


def _worst_case_days(window: str) -> int:
    """Turn a shipping window like '7-9 days' into the number we plan against: 9.

    The pessimistic end, deliberately. A window is a promise about the earliest
    AND the latest date; the user's 10-day deadline is only genuinely met if the
    latest date clears it. Taking the 7 would let a product pass a hard gate on
    its best-case story.
    """
    numbers = [int(match) for match in re.findall(r"\d+", window)]
    if not numbers:
        raise ValueError(f"could not read a shipping window from {window!r}")
    return max(numbers)


def _first_number(text: str) -> int:
    """Pull the integer out of a sentence like '30-day replacement'."""
    match = re.search(r"\d+", text)
    if not match:
        raise ValueError(f"could not read a number of days from {text!r}")
    return int(match.group())


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(category: str) -> list[Product]:
    """Every product both sources offer in this category, in one shape.

    In a real build this is where auth, rate limits, pagination and downtime
    would live. We name that in CLAUDE.md as a known limit rather than pretending
    two local files are an integration.
    """
    everything = load_packhub() + load_boxbazaar()
    return [product for product in everything if product.category == category]


def available_categories() -> list[str]:
    """What our sources actually stock, read from the catalogs themselves.

    Deliberately derived from the data rather than listed in config.yaml. The two
    are not the same question and they currently disagree: config.yaml knows
    default weights and a price cap for `labels`, but no vendor stocks a single
    labels product. Reading the answer from the catalogs means the agent tells a
    user what it can really buy, not what it has opinions about.

    Used when a brief parses perfectly and then finds nothing at all — see
    escalation._no_eligible_match. CLAUDE.md's known limits say the narrowness is
    in the data, and this is the function that lets the agent say so out loud.
    """
    everything = load_packhub() + load_boxbazaar()
    return sorted({product.category for product in everything})


# ---------------------------------------------------------------------------
# The hard-constraint gate
# ---------------------------------------------------------------------------

def apply_hard_gates(brief: Brief, products: list[Product]) -> list[FilterResult]:
    """Check every product against every HARD constraint. Pass or fail, with reasons.

    We keep the failures, not just the survivors. When nothing passes, the
    escalation handler needs the violation sizes to build the near-miss list, and
    "rejected because delivery is 12 days against a 10-day window" is the sentence
    that makes a refusal auditable.

    A product must clear ALL of these. There is no partial credit at this stage —
    partial credit is what stage 4 is for.
    """
    results: list[FilterResult] = []

    for product in products:
        violations: dict[str, str] = {}

        # -- non-negotiable tier: never relaxed, not even by the escalation handler
        if product.category != brief.category:
            violations["category"] = (
                f"is a {product.category} product, not {brief.category}"
            )

        required = {spec_key(spec) for spec in brief.specs}
        offered = {spec_key(spec) for spec in product.specs}
        unmet = sorted(required - offered)
        if unmet:
            violations["specs"] = (
                f"does not meet required spec(s): {', '.join(unmet)}"
            )

        if product.available_quantity < brief.quantity:
            short = brief.quantity - product.available_quantity
            violations["quantity"] = (
                f"has {product.available_quantity:,} in stock against {brief.quantity:,} "
                f"needed, {short:,} short"
            )

        # -- negotiable tier: may be surfaced as a near-miss, never relaxed silently
        if product.price_per_unit_inr > brief.max_price_per_unit_inr:
            over = product.price_per_unit_inr - brief.max_price_per_unit_inr
            violations["max_price_per_unit_inr"] = (
                f"Rs {product.price_per_unit_inr:.2f} exceeds the Rs "
                f"{brief.max_price_per_unit_inr:.2f} cap by Rs {over:.2f}"
            )

        if product.delivery_days > brief.max_delivery_days:
            late = product.delivery_days - brief.max_delivery_days
            violations["max_delivery_days"] = (
                f"arrives in {product.delivery_days} days against a "
                f"{brief.max_delivery_days}-day window, {late} day(s) late"
            )

        results.append(
            FilterResult(product=product, passed=not violations, violations=violations)
        )

    return results


def run(brief: Brief, audit: AuditLogger | None = None) -> list[FilterResult]:
    """Stage 3 end to end: discover, normalise, gate, log.

    This is what the app calls. It returns every result, passed and failed, and
    writes one audit entry saying how many of each and why the failures failed.
    Logging the rejections matters as much as logging the survivors — "we looked
    at seven and three qualified" is a checkable claim; "here are three" is not.
    """
    products = discover(brief.category)
    results = apply_hard_gates(brief, products)
    eligible = [result for result in results if result.passed]

    if audit:
        audit.decision(
            STAGE_DISCOVERY,
            f"Checked {len(results)} products from 2 sources against the hard constraints; "
            f"{len(eligible)} qualified.",
            {
                "sources": ["PackHub (direct JSON)", "BoxBazaar (aggregator CSV)"],
                "considered": len(results),
                "eligible": [result.product.label for result in eligible],
                "rejected": {
                    result.product.label: result.violations
                    for result in results
                    if not result.passed
                },
            },
        )

    return results
