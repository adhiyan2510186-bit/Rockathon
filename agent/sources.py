"""The source adapter layer — where products come from, and nowhere else.

WHY THIS FILE EXISTS
--------------------
Before this file, discovery.py knew that PackHub was JSON and BoxBazaar was CSV.
That meant "add a vendor" was an edit to the middle of the pipeline. Now every
source is a small class with one job:

    SourceAdapter.fetch() -> tuple[Product, ...]

Each adapter owns its own mess — its file format, its units, its missing fields —
and hands back the same normalised `Product`. Everything downstream (the hard
gate, the ranker, the market signal, authorisation, the audit log) never learns
where a product came from and does not change when a source is added.

That is the claim the layer earns us, and it is a claim we can demonstrate rather
than assert: the UI's source toggle turns adapters on and off, and the identical
engine runs behind it either way.

ADDING A LIVE VENDOR API IS ONE NEW CLASS IN THIS FILE
------------------------------------------------------
Subclass SourceAdapter, put the HTTP call in read(), return Products. Nothing
else in the repo changes. We have deliberately NOT built one (CLAUDE.md,
"designed, not demoed") — mock vendors hide real integration pain and we would
rather name that limit than fake it. The seam is real; the adapter is not built.

THREE SCHEMAS, ON PURPOSE
-------------------------
  PackHub (direct JSON)       BoxBazaar (aggregator CSV)   Amazon (marketplace JSON)
  ---------------------       --------------------------   -------------------------
  unit_price_inr: 21.90       rate_paise: 1760             price_display: "Rs 1,14,900"
  lead_time_days: 4           ship_window: "7-9 days"      delivery_estimate: "Ships from
                                                             Delhi NCR - Delivered in 11-12 days"
  seller_rating: 4.8          vendor_score_100: 82         rating: {stars: 4.3, count: 1284}
  replacement_window_days: 7  returns_policy: "30-day…"    returns: "10 days replacement only"
  attributes: ["double-wall"] spec_blob: "DW | 200 x …"    spec_sheet: {"Wall": "Double Wall", …}
  price_history: [{date,…},…] price_trail: "date:val|…"    price_points: {"2026-08-15": 22.6, …}

Read down any row: a number, an inconvenient unit, and a string a human was
meant to read. If the feeds had the same columns the normaliser would be a
rename and would prove nothing. Every conversion above is a place a real
integration breaks, and each one lives in exactly one function below.

CACHING, AND THE ONE PLACE WE REFUSE TO CACHE
---------------------------------------------
A catalog is read from disk once and held. Before this, a single demo run
re-parsed both files on every discover() call, every category lookup, and again
at vendor confirmation — the same bytes, many times over, for an answer that had
not changed.

The exception is deliberate and it matters. Stage 6 re-validates price and stock
by looking the product up AGAIN, and the entire point of that lookup is to catch
a value that moved since stage 3. Serving it from cache would compare our copy
against our copy and pass every time, which is not a check — it is a formality.
So `fetch(fresh=True)` bypasses the cache, and vendor.py is the only caller that
passes it.

Cache where the answer cannot have changed. Refuse to cache where detecting a
change IS the job.
"""

from __future__ import annotations

import csv
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from agent.models import Observation, Product, ReviewSnippet

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class SourceAdapter(ABC):
    """One place products can come from.

    Subclasses set the four descriptive attributes and implement `read()`.
    `fetch()` is shared and handles caching, so no adapter has to think about it.
    """

    key: str                                    # stable id, used by the UI toggle
    display_name: str                           # what a user sees, e.g. "PackHub"
    source_type: Literal["direct", "aggregator"]
    feed_format: str                            # "direct JSON", "aggregator CSV"

    def __init__(self) -> None:
        self._cache: tuple[Product, ...] | None = None

    @abstractmethod
    def read(self) -> Iterable[Product]:
        """Pull the catalog from wherever it lives and normalise it to Product.

        This is the only method a new source has to write. For a live vendor it
        would be an HTTP call; here it is a file read.
        """

    def fetch(self, *, fresh: bool = False) -> tuple[Product, ...]:
        """This source's catalog, normalised.

        Cached after the first read. `fresh=True` forces a genuine re-read and is
        used by stage 6, where noticing that something changed is the whole job.
        """
        if fresh or self._cache is None:
            self._cache = tuple(self.read())
        return self._cache

    @property
    def label(self) -> str:
        """'PackHub (direct JSON)' — how this source is named in the audit log."""
        return f"{self.display_name} ({self.feed_format})"


# ---------------------------------------------------------------------------
# Format 1 — a direct vendor's JSON
# ---------------------------------------------------------------------------
# Two vendors publish in this shape, so the READING lives here once and each
# vendor below is a name and a file path. That split is the adapter layer's
# actual claim, made checkable: a new vendor on a format we already speak costs
# four lines, and a new FORMAT costs one new class. Neither costs a change to
# filtering, ranking, signals, authorisation or audit.

class DirectJsonAdapter(SourceAdapter):
    """The friendly feed. Rupees are rupees and lead time is already in days."""

    source_type = "direct"
    feed_format = "direct JSON"
    path: Path

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        if path is not None:
            self.path = path

    def read(self) -> Iterable[Product]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw["catalog"]:
            yield Product(
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
                price_history=_json_series(item.get("price_history"), "unit_price_inr"),
                stock_history=_json_series(item.get("stock_history"), "stock_qty"),
            )


# ---------------------------------------------------------------------------
# Format 2 — an aggregator's CSV
# ---------------------------------------------------------------------------

class AggregatorCsvAdapter(SourceAdapter):
    """The awkward feed. Five conversions, each one a place a live API breaks."""

    source_type = "aggregator"
    feed_format = "aggregator CSV"
    path: Path

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        if path is not None:
            self.path = path

    def read(self) -> Iterable[Product]:
        text = self.path.read_text(encoding="utf-8")
        # The file carries a comment header explaining why it is shaped this way.
        # csv.DictReader has no idea what a '#' means, so drop those lines first.
        rows = csv.DictReader(
            line for line in text.splitlines() if not line.startswith("#")
        )
        for row in rows:
            yield Product(
                product_id=row["item_code"],
                name=row["product_name"],
                source=self.display_name,
                source_type="aggregator",
                category=row["category_tag"].strip().lower(),           # "PACKAGING" -> "packaging"
                specs=[p.strip() for p in row["spec_blob"].split("|") if p.strip()],
                price_per_unit_inr=int(row["rate_paise"]) / 100,        # 1760 paise -> 17.60
                delivery_days=worst_case_days(row["ship_window"]),      # "7-9 days" -> 9
                available_quantity=int(row["qty_available"]),
                reliability_rating=float(row["vendor_score_100"]) / 20, # 82/100 -> 4.1/5
                replacement_window_days=first_number(row["returns_policy"]),  # "30-day…" -> 30
                price_history=_trail(row.get("price_trail"), divide_by=100),  # paise -> rupees
                stock_history=_trail(row.get("stock_trail")),
            )


# ---------------------------------------------------------------------------
# Format 3 — a consumer marketplace's JSON
# ---------------------------------------------------------------------------
# The awkwardness here is different in kind from the aggregator's. BoxBazaar is
# a machine feed that chose inconvenient units; a marketplace hands you what it
# renders on a page. So the price arrives as the STRING a shopper reads, the
# delivery estimate arrives as a SENTENCE that also names the warehouse, and the
# specs arrive as a key-value SHEET whose keys are marketing labels ("Noise
# Control", "Fit") rather than anything we can match on.
#
# The one that actually bites is the title. A marketplace title is advertising -
# "Dell Inspiron 15 3520 15.6" FHD Laptop (12th Gen Core i5-1235U/8GB DDR4...)".
# Reading specs out of it is the obvious shortcut and it is how a buyer gets
# burned, because the title and the spec sheet disagree often enough that
# reviewers complain about it in our own catalog. We never parse a title. The
# gate reads spec_sheet, and the title is display text.

class MarketplaceJsonAdapter(SourceAdapter):
    """The shopper-facing feed. Prices as text, ETAs as prose, specs as a sheet."""

    source_type = "aggregator"
    feed_format = "marketplace JSON"
    path: Path

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        if path is not None:
            self.path = path

    def read(self) -> Iterable[Product]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw["listings"]:
            rating = item["rating"]
            yield Product(
                product_id=item["listing_id"],
                name=item["listing_title"],
                source=raw["marketplace"],
                source_type="aggregator",
                category=item["department"].strip().lower(),     # "Packaging" -> "packaging"
                # The VALUES of the sheet, not the keys. "Noise Control": "ANC"
                # contributes "ANC", which config.yaml's trade shorthand then maps
                # onto "noise-cancelling" exactly as it maps "DW" onto double-wall.
                specs=[str(value).strip() for value in item["spec_sheet"].values()],
                price_per_unit_inr=rupees_from_display(item["price_display"]),
                delivery_days=worst_case_days(item["delivery_estimate"]),
                available_quantity=item["in_stock"],
                reliability_rating=rating["stars"],              # already out of 5
                replacement_window_days=first_number(item["returns"]),
                review_count=rating["count"],
                sample_reviews=tuple(
                    ReviewSnippet(stars=review["stars"], text=review["text"])
                    for review in item.get("top_reviews", ())
                ),
                price_history=_date_keyed(item.get("price_points")),
                stock_history=_date_keyed(item.get("stock_points")),
            )


# ---------------------------------------------------------------------------
# Price and stock history — three feeds, three shapes, one series type
# ---------------------------------------------------------------------------
# Stage 4.5 computes over `tuple[Observation, ...]` and never learns which feed a
# series came from. Getting there is two different jobs:
#
#   PackHub    [{"date": "2026-08-11", "unit_price_inr": 20.90}, ...]   nested JSON
#   BoxBazaar  "2026-08-11:2090|2026-08-13:2110|..."                    flat, in paise
#   Amazon     {"2026-08-15": 22.60, "2026-08-17": 23.00, ...}          keyed by date
#
# Same information, and a real integration would meet all three. Each helper
# below returns an empty tuple when its column is absent, because a source that
# publishes no history should produce no signal — see signals.py. Saying nothing
# is the honest answer; inventing a trend is not.
#
# The marketplace series are also SHORTER — three readings against PackHub's
# five, because a shopping site publishes a narrower window than a contract
# vendor. Stage 4.5 handles both without being told, because it computes over a
# series rather than over a fixed number of points.

def _json_series(raw: list[dict] | None, value_key: str) -> tuple[Observation, ...]:
    """PackHub's shape: a list of objects, each with a date and one named value."""
    if not raw:
        return ()
    return tuple(
        Observation(on=date.fromisoformat(point["date"]), value=float(point[value_key]))
        for point in raw
    )


def _trail(raw: str | None, divide_by: float = 1.0) -> tuple[Observation, ...]:
    """BoxBazaar's shape: 'date:value|date:value', optionally in paise.

    `divide_by` is the same paise-to-rupees conversion the rate column needs. It
    is a parameter rather than two near-identical functions so the price and
    stock trails cannot drift apart.
    """
    if not raw or not raw.strip():
        return ()

    observations = []
    for pair in raw.split("|"):
        pair = pair.strip()
        if not pair:
            continue
        day, _, value = pair.partition(":")
        if not value:
            raise ValueError(f"could not read a date:value pair from {pair!r}")
        observations.append(
            Observation(on=date.fromisoformat(day.strip()), value=float(value) / divide_by)
        )
    return tuple(observations)


def _date_keyed(raw: dict[str, float] | None) -> tuple[Observation, ...]:
    """The marketplace's shape: {'2026-08-15': 22.60, ...}, already in rupees.

    Sorted by date rather than trusted in file order. The other two feeds are
    ordered lists and a list has an order; a JSON object does not promise one,
    and stage 4.5 reads `series[-1]` as "the latest reading". A series that
    arrived newest-first would invert every trend on screen with nothing to show
    that it had.
    """
    if not raw:
        return ()
    return tuple(
        Observation(on=date.fromisoformat(day), value=float(value))
        for day, value in sorted(raw.items())
    )


# ---------------------------------------------------------------------------
# Unit conversions the aggregator forces on us
# ---------------------------------------------------------------------------

def worst_case_days(window: str) -> int:
    """Turn a shipping window into the number we plan against: '7-9 days' -> 9.

    The pessimistic end, deliberately. A window is a promise about the earliest
    AND the latest date; the user's 10-day deadline is only genuinely met if the
    latest date clears it. Taking the 7 would let a product pass a hard gate on
    its best-case story. This is the one judgement call in normalisation, and it
    is one function so it can be argued with.

    It also reads the marketplace's prose form, 'Ships from Delhi NCR -
    Delivered in 11-12 days' -> 12, which is why this function takes the largest
    number rather than the last one. The warehouse name is carried in that
    sentence so a reader can see WHY a Chennai buyer waits two weeks for a Delhi
    listing; only the number survives, because only the number gates anything.
    A hub whose name contained a digit would poison this, which is the price of
    reading a number out of a sentence and is worth knowing before we add one.
    """
    numbers = [int(match) for match in re.findall(r"\d+", window)]
    if not numbers:
        raise ValueError(f"could not read a shipping window from {window!r}")
    return max(numbers)


def rupees_from_display(text: str) -> float:
    """'Rs 1,14,900' -> 114900.0. The price as a shopper sees it, made arithmetic.

    Indian comma grouping is not the three-digit kind, so anything that assumed
    thousands separators would read this wrong rather than fail on it. We strip
    every character that is not a digit or a decimal point and let the number
    speak for itself.

    Raising on an unreadable price is the point. A marketplace that started
    sending 'See price in cart' must stop the run here, loudly, rather than
    default to zero and hand the ranker a product that beats everything on the
    price term.
    """
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned or cleaned == ".":
        raise ValueError(f"could not read a price from {text!r}")
    return float(cleaned)


def first_number(text: str) -> int:
    """Pull the integer out of a sentence like '30-day replacement'."""
    match = re.search(r"\d+", text)
    if not match:
        raise ValueError(f"could not read a number of days from {text!r}")
    return int(match.group())


# ---------------------------------------------------------------------------
# The sources — a name, a format, and a file
# ---------------------------------------------------------------------------
# Each of these is a whole vendor integration. There is no per-vendor parsing
# below this line because the format classes above already did it, and no
# per-category anything because nothing here knows what it is selling: the
# category travels in the feed, and stage 3 filters on it.
#
# PackHub and BoxBazaar sell packaging. OfficeStock and TradeBridge sell office
# furniture, laptops and headsets. Amazon and Flipkart sell all four categories,
# with the messy titles and contradictory reviews a marketplace actually carries.
# Downstream, all six are indistinguishable.

class PackHubAdapter(DirectJsonAdapter):
    key = "packhub"
    display_name = "PackHub"
    path = DATA_DIR / "packhub_direct.json"


class BoxBazaarAdapter(AggregatorCsvAdapter):
    key = "boxbazaar"
    display_name = "BoxBazaar"
    path = DATA_DIR / "boxbazaar_aggregator.csv"


class OfficeStockAdapter(DirectJsonAdapter):
    key = "officestock"
    display_name = "OfficeStock"
    path = DATA_DIR / "officestock_direct.json"


class TradeBridgeAdapter(AggregatorCsvAdapter):
    key = "tradebridge"
    display_name = "TradeBridge"
    path = DATA_DIR / "tradebridge_aggregator.csv"


class AmazonAdapter(MarketplaceJsonAdapter):
    key = "amazon"
    display_name = "Amazon"
    path = DATA_DIR / "amazon_marketplace.json"


class FlipkartAdapter(MarketplaceJsonAdapter):
    key = "flipkart"
    display_name = "Flipkart"
    path = DATA_DIR / "flipkart_marketplace.json"


# ---------------------------------------------------------------------------
# The registry — the only list of sources in the codebase
# ---------------------------------------------------------------------------
# Six vendors, three formats, two vendors per format. The arithmetic is the
# claim: adding a vendor on a format we already speak costs four lines, adding a
# FORMAT costs one class, and neither costs a change to filtering, ranking,
# signals, authorisation or audit.

ADAPTERS: dict[str, SourceAdapter] = {
    adapter.key: adapter
    for adapter in (
        PackHubAdapter(),
        BoxBazaarAdapter(),
        OfficeStockAdapter(),
        TradeBridgeAdapter(),
        AmazonAdapter(),
        FlipkartAdapter(),
    )
}

ALL_SOURCE_KEYS: tuple[str, ...] = tuple(ADAPTERS)


def resolve(source_keys: Sequence[str] | None = None) -> tuple[SourceAdapter, ...]:
    """Turn a list of keys into adapters. `None` means every source we have.

    Raises on an unknown key rather than silently returning fewer sources. A
    typo that quietly halves the candidate pool would change a ranking with no
    visible cause, which is exactly the class of bug the audit log cannot catch.
    """
    if source_keys is None:
        return tuple(ADAPTERS.values())

    unknown = [key for key in source_keys if key not in ADAPTERS]
    if unknown:
        raise KeyError(
            f"unknown source(s): {', '.join(unknown)}. "
            f"Known sources: {', '.join(ALL_SOURCE_KEYS)}"
        )
    return tuple(ADAPTERS[key] for key in source_keys)


def fetch(
    source_keys: Sequence[str] | None = None,
    *,
    fresh: bool = False,
) -> tuple[Product, ...]:
    """Every product from the selected sources, in one shape.

    This is the single door between "somewhere products live" and the rest of the
    pipeline. Stage 3 calls it once per brief; stage 6 calls it with fresh=True.
    """
    return tuple(
        product
        for adapter in resolve(source_keys)
        for product in adapter.fetch(fresh=fresh)
    )


def labels(source_keys: Sequence[str] | None = None) -> list[str]:
    """['PackHub (direct JSON)', 'BoxBazaar (aggregator CSV)'] — for the audit log.

    Derived from the adapters actually used, never hardcoded. If a judge turns a
    source off in the UI, the audit entry says so without anyone updating a
    string.
    """
    return [adapter.label for adapter in resolve(source_keys)]


def reset_cache() -> None:
    """Drop every cached catalog. Used by tests and by an explicit UI reload."""
    for adapter in ADAPTERS.values():
        adapter._cache = None
