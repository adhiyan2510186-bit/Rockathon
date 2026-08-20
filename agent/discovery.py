"""Stage 3 — the hard-constraint gate.

WHY THIS FILE EXISTS
--------------------
One job: decide which products qualify.

Every product that arrives here is checked against the brief's HARD constraints
and comes out either eligible or rejected, with a plain-words reason. Nothing
here scores or ranks. Stage 3 answers "does this qualify?" and stops. Stage 4
answers "how much do we prefer it among the ones that qualified?". Those are two
different questions and keeping them in two files is what stops them being
quietly merged into one fudge.

WHERE THE PRODUCTS COME FROM
----------------------------
Not from here. `agent/sources.py` owns that — every catalog is a SourceAdapter
handing back the same normalised `Product`, whatever shape it started in. This
file has no idea that PackHub is JSON and BoxBazaar is CSV, and that is the
point: adding a vendor never touches the gate.

WHY THE FAILURES ARE KEPT
-------------------------
We return rejections alongside survivors. When nothing passes, the escalation
handler needs the violation sizes to build the near-miss list, and "rejected
because delivery is 12 days against a 10-day window" is the sentence that makes
a refusal auditable. "Here are three products" is a claim; "we looked at seven
and three qualified, here is why the other four did not" is a checkable one.

NON-NEGOTIABLE VS NEGOTIABLE
----------------------------
Both tiers reject here — a product must clear ALL hard constraints, there is no
partial credit at this stage. The difference shows up later: the escalation
handler may propose relaxing a NEGOTIABLE violation (price, delivery, minor
spec) and may never touch a NON-NEGOTIABLE one (category, specs, quantity).
`NEGOTIABLE_FIELDS` in models.py is what the handler reads.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from agent import config, sources
from agent.audit import STAGE_DISCOVERY, AuditLogger
from agent.models import Brief, FilterResult, Product

# ---------------------------------------------------------------------------
# Spec vocabulary
# Trade shorthand lives in config.yaml, one table per category, merged into one
# lookup here. The aggregator writes "DW"; the user writes "double-wall"; the
# parser sometimes returns "double wall". All three have to mean one thing or an
# identical box fails a hard gate over punctuation. Every category brings its
# own shorthand the same way, which is why this file names none of them.
_SPEC_NOISE = re.compile(r"[\s\-_.,]")


@lru_cache(maxsize=512)
def _spec_key(text: str, generation: int) -> str:
    """The cached half of spec_key. See spec_key for what and why.

    `generation` is not used in the body — it is here to be part of the cache
    key. config.generation() changes when config.yaml is re-read, which
    invalidates every entry computed from the old synonym table rather than
    leaving stale keys behind.
    """
    cleaned = text.lower().strip().replace("×", "x")
    cleaned = _SPEC_NOISE.sub("", cleaned)
    return config.spec_synonyms().get(cleaned, cleaned)


def spec_key(text: str) -> str:
    """Reduce a spec to a comparable key: lowercase, no spaces, no punctuation.

    This exists because of a real mismatch we hit while testing language.py: the
    model returns "200x150x80 mm" and the offline parser returns "200x150x80mm".
    Same box, one space apart. Without this function that space fails a
    non-negotiable hard constraint and the demo shows zero eligible products for
    no visible reason.

    Cached because the gate asks for the same handful of strings over and over —
    every brief spec against every product spec. The catalog vocabulary is tiny
    and fixed, so after the first pass this is a dictionary lookup instead of a
    regex substitution.
    """
    return _spec_key(text, config.generation())


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(
    category: str,
    source_keys: Sequence[str] | None = None,
    *,
    fresh: bool = False,
) -> list[Product]:
    """Every product the selected sources offer in this category, in one shape.

    `source_keys=None` means every source we have; the UI passes a subset when a
    judge switches one off. `fresh=True` forces a real re-read and is used only
    by stage 6, where the job is to notice that something moved.
    """
    return [
        product
        for product in sources.fetch(source_keys, fresh=fresh)
        if product.category == category
    ]


def available_categories(source_keys: Sequence[str] | None = None) -> list[str]:
    """What our sources actually stock, read from the catalogs themselves.

    Deliberately derived from the data rather than listed in config.yaml. The two
    are not the same question and they currently disagree: config.yaml knows
    default weights and a price cap for `labels`, but no vendor stocks a single
    labels product. Reading the answer from the catalogs means the agent tells a
    user what it can really buy, not what it has opinions about.

    Used when a brief parses perfectly and then finds nothing at all — see
    escalation._no_eligible_match.
    """
    return sorted({product.category for product in sources.fetch(source_keys)})


# ---------------------------------------------------------------------------
# The hard-constraint gate
# ---------------------------------------------------------------------------

def apply_hard_gates(brief: Brief, products: list[Product]) -> list[FilterResult]:
    """Check every product against every HARD constraint. Pass or fail, with reasons.

    The brief's own values are normalised ONCE, above the loop, rather than
    re-derived for every product. The required-spec set does not change between
    products, so rebuilding it per product was the same work repeated for no
    answer — n x m spec normalisations to learn m facts.
    """
    # --- brief-side values: constant across the whole pool, computed once
    required_specs = frozenset(spec_key(spec) for spec in brief.specs)
    category = brief.category
    quantity = brief.quantity
    price_cap = brief.max_price_per_unit_inr
    delivery_cap = brief.max_delivery_days

    results: list[FilterResult] = []

    for product in products:
        violations: dict[str, str] = {}

        # -- non-negotiable tier: never relaxed, not even by the escalation handler
        if product.category != category:
            violations["category"] = (
                f"is a {product.category} product, not {category}"
            )

        unmet = sorted(required_specs - {spec_key(spec) for spec in product.specs})
        if unmet:
            violations["specs"] = f"does not meet required spec(s): {', '.join(unmet)}"

        if product.available_quantity < quantity:
            short = quantity - product.available_quantity
            violations["quantity"] = (
                f"has {product.available_quantity:,} in stock against {quantity:,} "
                f"needed, {short:,} short"
            )

        # -- negotiable tier: may be surfaced as a near-miss, never relaxed silently
        if product.price_per_unit_inr > price_cap:
            over = product.price_per_unit_inr - price_cap
            violations["max_price_per_unit_inr"] = (
                f"Rs {product.price_per_unit_inr:.2f} exceeds the Rs "
                f"{price_cap:.2f} cap by Rs {over:.2f}"
            )

        if product.delivery_days > delivery_cap:
            late = product.delivery_days - delivery_cap
            violations["max_delivery_days"] = (
                f"arrives in {product.delivery_days} days against a "
                f"{delivery_cap}-day window, {late} day(s) late"
            )

        results.append(
            FilterResult(product=product, passed=not violations, violations=violations)
        )

    return results


def run(
    brief: Brief,
    audit: AuditLogger | None = None,
    source_keys: Sequence[str] | None = None,
) -> list[FilterResult]:
    """Stage 3 end to end: discover, gate, log.

    This is what the app calls. It returns every result, passed and failed, and
    writes one audit entry saying how many of each and why the failures failed.
    The source list in that entry is read from the adapters actually used, so
    turning a source off in the UI is recorded without anyone editing a string.
    """
    used = sources.labels(source_keys)
    products = discover(brief.category, source_keys)
    results = apply_hard_gates(brief, products)
    eligible = [result for result in results if result.passed]

    if audit:
        audit.decision(
            STAGE_DISCOVERY,
            f"Checked {len(results)} products from {len(used)} source(s) against the "
            f"hard constraints; {len(eligible)} qualified.",
            {
                "sources": used,
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
