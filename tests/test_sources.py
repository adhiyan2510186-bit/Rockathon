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


def test_the_three_feeds_really_do_disagree():
    """The schemas are different on purpose — if they converge, normalisation
    stops proving anything and this test should fail loudly rather than let us
    keep telling judges the normaliser does real work.

    Read each assertion across: the same fact arrives as a number, as an
    inconvenient unit, and as a string a human was meant to read.
    """
    packhub = sources.ADAPTERS["packhub"].path.read_text(encoding="utf-8")
    boxbazaar = sources.ADAPTERS["boxbazaar"].path.read_text(encoding="utf-8")
    amazon = sources.ADAPTERS["amazon"].path.read_text(encoding="utf-8")

    # price: rupees, paise, and the string on the page
    assert "unit_price_inr" in packhub
    assert "rate_paise" in boxbazaar
    assert "price_display" in amazon

    # delivery: a number, a bare range, and a sentence naming the warehouse
    assert "lead_time_days" in packhub
    assert "ship_window" in boxbazaar
    assert "delivery_estimate" in amazon

    # reliability: out of 5, out of 100, and out of 5 inside a nested object
    assert "seller_rating" in packhub
    assert "vendor_score_100" in boxbazaar
    assert '"rating"' in amazon and '"stars"' in amazon

    # specs: a clean list, a pipe blob, and a key-value sheet with marketing keys
    assert "attributes" in packhub
    assert "spec_blob" in boxbazaar
    assert "spec_sheet" in amazon


def test_aggregator_conversions_land_on_our_units():
    """The five conversions, checked on the product the demo actually ranks."""
    ecomail = next(p for p in sources.ADAPTERS["boxbazaar"].fetch() if p.product_id == "BB-ECOMAIL-DW")

    assert ecomail.price_per_unit_inr == 17.60          # 1760 paise
    assert ecomail.delivery_days == 9                   # "7-9 days", pessimistic end
    assert ecomail.reliability_rating == pytest.approx(4.1)  # 82/100
    assert ecomail.replacement_window_days == 30        # "30-day replacement"


def test_marketplace_conversions_land_on_our_units():
    """The marketplace's five conversions, on the listing the headphones demo ranks."""
    sony = next(
        p for p in sources.ADAPTERS["amazon"].fetch() if p.product_id == "AMZ-HPH-1302"
    )

    assert sony.price_per_unit_inr == 3890.0        # "Rs 3,890", comma stripped
    assert sony.delivery_days == 4                  # "…Delivered in 3-4 days"
    assert sony.category == "headphones"            # "Headphones", lowered
    assert sony.replacement_window_days == 30       # "30 days replacement"
    assert sony.specs == ["Over-Ear", "Wireless", "Noise Cancelling", "Bluetooth"]
    assert len(sony.price_history) == 3             # a shorter window than a vendor's


def test_indian_comma_grouping_does_not_confuse_the_price_reader():
    """'Rs 1,14,900' is not thousands-separated, so anything assuming groups of
    three reads it WRONG rather than failing on it — the worst kind of bug,
    because the product simply looks cheaper than it is."""
    assert sources.rupees_from_display("Rs 1,14,900") == 114900.0
    assert sources.rupees_from_display("Rs 23.40") == 23.40
    assert sources.rupees_from_display("Rs 899") == 899.0

    # A marketplace that starts sending "See price in cart" must stop the run
    # loudly, not default to zero and hand the ranker an unbeatable price term.
    with pytest.raises(ValueError):
        sources.rupees_from_display("See price in cart")


def test_shipping_window_takes_the_late_end():
    """A window is only genuinely met if its LATEST date clears the deadline."""
    assert sources.worst_case_days("7-9 days") == 9
    assert sources.worst_case_days("10-12 days") == 12
    assert sources.worst_case_days("4 days") == 4

    # The marketplace buries the same range in prose, and names the hub in it.
    assert sources.worst_case_days("Ships from Delhi NCR - Delivered in 11-12 days") == 12
    assert sources.worst_case_days("Ships from Chennai - Delivered in 2-3 days") == 3

    with pytest.raises(ValueError):
        sources.worst_case_days("ships soon")


def test_the_same_hub_field_explains_why_two_chennai_buyers_wait_differently():
    """Delivery varies by warehouse, which is data, not a rule we wrote.

    Every estimate in both marketplace feeds is quoted to one Chennai buyer, so
    the spread between them is entirely the hub. Asserting it here stops someone
    "tidying" the feeds into one flat lead time and quietly deleting the reason
    the escalation path has anything to escalate about.
    """
    amazon = {p.product_id: p for p in sources.ADAPTERS["amazon"].fetch()}

    assert amazon["AMZ-LAP-1104"].delivery_days == 3    # Chennai hub
    assert amazon["AMZ-LAP-1105"].delivery_days == 5    # Hyderabad hub
    assert amazon["AMZ-LAP-1103"].delivery_days == 14   # Delhi NCR hub


# ---------------------------------------------------------------------------
# The toggle
# ---------------------------------------------------------------------------

def test_selecting_sources_changes_the_pool_and_nothing_else():
    """Turning a source off removes its products and touches nothing downstream."""
    everything = discovery.discover("packaging")
    direct_only = discovery.discover("packaging", ["packhub"])
    aggregator_only = discovery.discover("packaging", ["boxbazaar"])
    marketplace_only = discovery.discover("packaging", ["amazon", "flipkart"])

    assert {p.source for p in direct_only} == {"PackHub"}
    assert {p.source for p in aggregator_only} == {"BoxBazaar"}
    assert {p.source for p in marketplace_only} == {"Amazon", "Flipkart"}

    # The whole pool is exactly its parts — no source is silently dropped or
    # counted twice when several are selected at once.
    assert len(everything) == len(direct_only) + len(aggregator_only) + len(marketplace_only)
    assert {p.product_id for p in everything} == (
        {p.product_id for p in direct_only}
        | {p.product_id for p in aggregator_only}
        | {p.product_id for p in marketplace_only}
    )


def test_unknown_source_raises_rather_than_silently_shrinking_the_pool():
    """A typo that quietly halves the candidate pool would change a ranking with
    no visible cause — the one class of bug the audit log cannot catch."""
    with pytest.raises(KeyError):
        discovery.discover("packaging", ["packhubb"])


def test_audit_labels_follow_the_sources_actually_used():
    assert sources.labels() == [
        "PackHub (direct JSON)",
        "BoxBazaar (aggregator CSV)",
        "OfficeStock (direct JSON)",
        "TradeBridge (aggregator CSV)",
        "Amazon (marketplace JSON)",
        "Flipkart (marketplace JSON)",
    ]
    assert sources.labels(["boxbazaar"]) == ["BoxBazaar (aggregator CSV)"]


def test_a_second_vendor_on_a_known_format_costs_no_new_parsing():
    """Six vendors, three read() methods. That ratio is the claim.

    The adapter layer is supposed to mean "a new source is one small file". Here
    it is smaller than that: a vendor publishing in a shape we already speak is a
    key, a display name and a path, and the normalising code is shared with
    whichever vendor arrived on that format first. A new FORMAT costs one class.
    The test asserts the sharing rather than the file length, because the file
    length is not the point.
    """
    assert sources.PackHubAdapter.read is sources.DirectJsonAdapter.read
    assert sources.OfficeStockAdapter.read is sources.DirectJsonAdapter.read
    assert sources.BoxBazaarAdapter.read is sources.AggregatorCsvAdapter.read
    assert sources.TradeBridgeAdapter.read is sources.AggregatorCsvAdapter.read
    assert sources.AmazonAdapter.read is sources.MarketplaceJsonAdapter.read
    assert sources.FlipkartAdapter.read is sources.MarketplaceJsonAdapter.read

    # Six sources, three distinct read() implementations between them.
    assert len(sources.ADAPTERS) == 6
    assert len({adapter.read.__qualname__ for adapter in sources.ADAPTERS.values()}) == 3

    # ...and they really are different vendors selling different things.
    assert sources.OfficeStockAdapter().path != sources.PackHubAdapter().path
    assert {p.category for p in sources.OfficeStockAdapter().fetch()} == {
        "furniture", "laptops", "headphones",
    }


# ---------------------------------------------------------------------------
# What the marketplace feeds add that the vendor feeds could not
# ---------------------------------------------------------------------------

def test_one_physical_product_arrives_under_two_different_titles():
    """The listing text is marketing; the normalised specs are the product.

    Amazon calls it 'Dell Inspiron 15 3520 15.6" FHD Laptop (12th Gen Core
    i5-1235U/8GB DDR4/512GB SSD...)'. Flipkart calls the same machine 'Dell 15
    Laptop 11th Gen Core i5 (8 GB/512 GB SSD...)' and prices it Rs 1,491 lower.
    Nothing downstream compares those strings. After normalisation both carry
    the same RAM and storage, so both fail a 16GB brief for the same stated
    reason — which is the only sense in which the engine "knows" they are one
    product.
    """
    amazon = {p.product_id: p for p in sources.ADAPTERS["amazon"].fetch()}
    flipkart = {p.product_id: p for p in sources.ADAPTERS["flipkart"].fetch()}

    amz, flk = amazon["AMZ-LAP-1101"], flipkart["FLK-LAP-2101"]

    assert amz.name != flk.name                                  # nothing alike
    assert amz.price_per_unit_inr - flk.price_per_unit_inr == 1491.0
    assert discovery.spec_key("8GB RAM") in {discovery.spec_key(s) for s in amz.specs}
    assert discovery.spec_key("8GB RAM") in {discovery.spec_key(s) for s in flk.specs}


def test_trade_shorthand_survives_a_third_category():
    """The aggregator writes 'DW' for double-wall; the marketplace writes 'ANC'.

    Both are one config entry, not a code path. FLK-HPH-2302's spec sheet says
    ANC and the buyer's brief says noise-cancelling, and they have to be the same
    requirement or an eligible product fails a hard gate over vocabulary.
    """
    boult = next(
        p for p in sources.ADAPTERS["flipkart"].fetch() if p.product_id == "FLK-HPH-2302"
    )

    assert "ANC" in boult.specs
    assert discovery.spec_key("ANC") == discovery.spec_key("noise-cancelling")


def test_vendor_feeds_publish_no_reviews_and_say_so_with_an_empty_list():
    """Our B2B vendors carry no buyer feedback, and we do not invent any.

    An empty list is the honest way to say "this source publishes none". A
    fabricated rating count to fill a column would be the same class of lie as a
    fabricated price chart.
    """
    packhub = sources.ADAPTERS["packhub"].fetch()
    amazon = sources.ADAPTERS["amazon"].fetch()

    assert all(p.review_count == 0 and p.sample_reviews == () for p in packhub)
    assert all(p.review_count > 0 and p.sample_reviews for p in amazon)


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
