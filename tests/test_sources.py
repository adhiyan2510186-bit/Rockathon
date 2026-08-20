"""The source adapter layer — the contract the UI toggle depends on.

These tests exist because "our engine does not care where products come from" is
a claim we make out loud. A claim we demonstrate on stage should be a claim a
test holds down, or the first schema change quietly turns it into a lie.
"""

from __future__ import annotations

import pytest

from agent import discovery, sources
from agent.models import Product


# ---------------------------------------------------------------------------
# Every adapter honours the same contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter", sources.ADAPTERS.values(), ids=lambda a: a.key)
def test_adapter_returns_normalised_products(adapter):
    """Whatever shape it started in, it comes back as Product and nothing else."""
    fetched = adapter.fetch()

    assert fetched, f"{adapter.key} returned an empty catalog"
    assert all(isinstance(item, Product) for item in fetched)
    assert all(item.source_type == adapter.source_type for item in fetched)


def test_the_two_feeds_really_do_disagree():
    """The schemas are different on purpose — if they converge, normalisation
    stops proving anything and this test should fail loudly rather than let us
    keep telling judges the normaliser does real work."""
    packhub = sources.ADAPTERS["packhub"].path.read_text(encoding="utf-8")
    boxbazaar = sources.ADAPTERS["boxbazaar"].path.read_text(encoding="utf-8")

    assert "unit_price_inr" in packhub and "rate_paise" in boxbazaar   # rupees vs paise
    assert "lead_time_days" in packhub and "ship_window" in boxbazaar  # number vs range
    assert "seller_rating" in packhub and "vendor_score_100" in boxbazaar  # /5 vs /100


def test_aggregator_conversions_land_on_our_units():
    """The five conversions, checked on the product the demo actually ranks."""
    ecomail = next(p for p in sources.ADAPTERS["boxbazaar"].fetch() if p.product_id == "BB-ECOMAIL-DW")

    assert ecomail.price_per_unit_inr == 17.60          # 1760 paise
    assert ecomail.delivery_days == 9                   # "7-9 days", pessimistic end
    assert ecomail.reliability_rating == pytest.approx(4.1)  # 82/100
    assert ecomail.replacement_window_days == 30        # "30-day replacement"


def test_shipping_window_takes_the_late_end():
    """A window is only genuinely met if its LATEST date clears the deadline."""
    assert sources.worst_case_days("7-9 days") == 9
    assert sources.worst_case_days("10-12 days") == 12
    assert sources.worst_case_days("4 days") == 4

    with pytest.raises(ValueError):
        sources.worst_case_days("ships soon")


# ---------------------------------------------------------------------------
# The toggle
# ---------------------------------------------------------------------------

def test_selecting_sources_changes_the_pool_and_nothing_else():
    """Turning a source off removes its products and touches nothing downstream."""
    both = discovery.discover("packaging")
    direct_only = discovery.discover("packaging", ["packhub"])
    aggregator_only = discovery.discover("packaging", ["boxbazaar"])

    assert len(both) == len(direct_only) + len(aggregator_only)
    assert {p.source for p in direct_only} == {"PackHub"}
    assert {p.source for p in aggregator_only} == {"BoxBazaar"}


def test_unknown_source_raises_rather_than_silently_shrinking_the_pool():
    """A typo that quietly halves the candidate pool would change a ranking with
    no visible cause — the one class of bug the audit log cannot catch."""
    with pytest.raises(KeyError):
        discovery.discover("packaging", ["packhubb"])


def test_audit_labels_follow_the_sources_actually_used():
    assert sources.labels() == ["PackHub (direct JSON)", "BoxBazaar (aggregator CSV)"]
    assert sources.labels(["boxbazaar"]) == ["BoxBazaar (aggregator CSV)"]


# ---------------------------------------------------------------------------
# Caching, and the one place we refuse to cache
# ---------------------------------------------------------------------------

def test_catalogs_are_read_once_and_held(monkeypatch):
    adapter = sources.PackHubAdapter()
    calls = {"n": 0}
    real_read = adapter.read

    def counted():
        calls["n"] += 1
        return real_read()

    monkeypatch.setattr(adapter, "read", counted)

    adapter.fetch()
    adapter.fetch()
    adapter.fetch()
    assert calls["n"] == 1, "catalog should be parsed once, not once per call"


def test_stage_six_bypasses_the_cache():
    """Stage 6 re-validates price and stock. Serving that from cache would compare
    our copy against our copy and pass every time — a formality, not a check."""
    adapter = sources.PackHubAdapter()
    first = adapter.fetch()
    cached = adapter.fetch()
    refetched = adapter.fetch(fresh=True)

    assert cached is first, "a normal fetch should reuse the held catalog"
    assert refetched is not first, "fresh=True must produce a genuine re-read"
    assert refetched == first, "same file, so same products — only the read is new"
