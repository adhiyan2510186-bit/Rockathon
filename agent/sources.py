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

TWO SCHEMAS, ON PURPOSE
-----------------------
  PackHub (direct JSON)          BoxBazaar (aggregator CSV)
  ---------------------          --------------------------
  unit_price_inr: 21.90          rate_paise: 1760          -> divide by 100
  lead_time_days: 4              ship_window: "7-9 days"   -> take the LATE end
  seller_rating: 4.8             vendor_score_100: 82      -> divide by 20
  replacement_window_days: 7     returns_policy: "30-day…" -> pull the number out
  attributes: ["double-wall",…]  spec_blob: "DW | 200 x …" -> split and expand

If both feeds had the same columns the normaliser would be a rename and would
prove nothing. Every conversion above is a place a real integration breaks.

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

from agent.models import Observation, Product

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
# Source 1 — PackHub, a direct vendor's JSON
# ---------------------------------------------------------------------------

class PackHubAdapter(SourceAdapter):
    """The friendly feed. Rupees are rupees and lead time is already in days."""

    key = "packhub"
    display_name = "PackHub"
    source_type = "direct"
    feed_format = "direct JSON"

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = path or DATA_DIR / "packhub_direct.json"

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
# Source 2 — BoxBazaar, an aggregator's CSV
# ---------------------------------------------------------------------------

class BoxBazaarAdapter(SourceAdapter):
    """The awkward feed. Five conversions, each one a place a live API breaks."""

    key = "boxbazaar"
    display_name = "BoxBazaar"
    source_type = "aggregator"
    feed_format = "aggregator CSV"

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = path or DATA_DIR / "boxbazaar_aggregator.csv"

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
                source="BoxBazaar",
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
# Price and stock history — two feeds, two shapes, one series type
# ---------------------------------------------------------------------------
# Stage 4.5 computes over `tuple[Observation, ...]` and never learns which feed a
# series came from. Getting there is two different jobs:
#
#   PackHub    [{"date": "2026-08-11", "unit_price_inr": 20.90}, ...]   nested JSON
#   BoxBazaar  "2026-08-11:2090|2026-08-13:2110|..."                    flat, in paise
#
# Same information, and a real integration would meet both. Each helper below
# returns an empty tuple when its column is absent, because a source that
# publishes no history should produce no signal — see signals.py. Saying nothing
# is the honest answer; inventing a trend is not.

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


# ---------------------------------------------------------------------------
# Unit conversions the aggregator forces on us
# ---------------------------------------------------------------------------

def worst_case_days(window: str) -> int:
    """Turn a shipping window like '7-9 days' into the number we plan against: 9.

    The pessimistic end, deliberately. A window is a promise about the earliest
    AND the latest date; the user's 10-day deadline is only genuinely met if the
    latest date clears it. Taking the 7 would let a product pass a hard gate on
    its best-case story. This is the one judgement call in normalisation, and it
    is one function so it can be argued with.
    """
    numbers = [int(match) for match in re.findall(r"\d+", window)]
    if not numbers:
        raise ValueError(f"could not read a shipping window from {window!r}")
    return max(numbers)


def first_number(text: str) -> int:
    """Pull the integer out of a sentence like '30-day replacement'."""
    match = re.search(r"\d+", text)
    if not match:
        raise ValueError(f"could not read a number of days from {text!r}")
    return int(match.group())


# ---------------------------------------------------------------------------
# The registry — the only list of sources in the codebase
# ---------------------------------------------------------------------------

ADAPTERS: dict[str, SourceAdapter] = {
    adapter.key: adapter
    for adapter in (PackHubAdapter(), BoxBazaarAdapter())
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
