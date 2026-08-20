"""The other two categories, held to the same standard as the packaging demo.

tests/test_ranking.py freezes the packaging numbers because they are in our deck.
This file freezes furniture and laptops for a different reason: they are the
evidence that "the engine has never heard of mailer boxes" is true rather than
merely claimed. If adding a category had required a special case anywhere in
ranking, authorisation or discovery, these numbers would not come out of the same
code path that produces 58.0 / 48.7 / 33.7 — and they do.

The two briefs are also chosen to land on OPPOSITE sides of the authorisation
limit, because that boundary is the whole project:

    furniture -> Rs 78,000  -> under Rs 1,05,000 -> the agent proceeds alone
    laptops   -> Rs 4,64,000 -> over Rs 1,05,000 -> the agent stops and asks

Same engine, same brief shape, opposite branch. A demo that only ever shows the
escalation half never shows that the agent can be trusted to act.
"""

from __future__ import annotations

import pytest

from agent import authorisation, config, discovery, escalation, language, ranking, weights
from agent import audit as audit_module
from agent.models import TransactionContext

FURNITURE_BRIEF = (
    "12 ergonomic task chairs, mesh back, adjustable height, max Rs 7,000 each, "
    "delivered within 14 days. Reliability matters a lot."
)

LAPTOP_BRIEF = (
    "8 developer laptops, 16GB RAM, 512GB SSD, max Rs 65,000 each, "
    "delivered within 12 days. Reliability matters a lot."
)

# The third category, and the one where the cheap-and-nasty option genuinely
# QUALIFIES. Packaging, furniture and laptops all happen to reject their worst
# listing on a hard gate, which leaves an obvious question unanswered: what stops
# the agent buying the Rs 1,299 headset that passes every check and has 2.8 stars?
# Nothing stops it from qualifying. The ranking is what stops it from winning.
HEADPHONE_BRIEF = (
    "25 wireless noise-cancelling headsets, over-ear, max Rs 4,000 each, "
    "delivered within 12 days. Reliability matters a lot."
)

# Both briefs say "reliability matters a lot", so both land on the same rounded
# weights the packaging demo uses - from DIFFERENT category defaults, rescaled by
# the same pure-Python weight engine. That the two agree is the point: the phrase
# fixes reliability at 0.45 and the rest is arithmetic, not a per-category table.
STATED_WEIGHTS = {
    "reliability": 0.45,
    "price": 0.20,
    "replacement": 0.20,
    "delivery": 0.15,
}


def build_pipeline(text: str):
    """Stages 1-4 for any brief, offline, exactly as tests/test_ranking.py does."""
    extraction = language._offline_extract(text)
    brief = language._to_brief(text, extraction, audit=None)
    computed = weights.compute(brief)
    results = discovery.run(brief)
    eligible = [result.product for result in results if result.passed]
    ranked = ranking.rank(
        eligible,
        computed,
        brief.max_price_per_unit_inr,
        brief.max_delivery_days,
    )
    return brief, computed, results, ranked


def authorise(text: str):
    """Stages 1-5, returning the authorisation outcome the approval screen renders."""
    context = TransactionContext(transaction_id=audit_module.new_transaction_id())
    log = audit_module.AuditLogger(context)
    brief = language._to_brief(text, language._offline_extract(text), audit=log)
    context.brief = brief
    context.weights = weights.compute(brief, log)
    context.filter_results = discovery.run(brief, log)
    context.ranked = ranking.rank(
        [result.product for result in context.filter_results if result.passed],
        context.weights,
        brief.max_price_per_unit_inr,
        brief.max_delivery_days,
        log,
    )
    return context, authorisation.authorise(context, log)


# ---------------------------------------------------------------------------
# The brief is understood before anything is scored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, category, quantity, cap, days, specs",
    [
        (FURNITURE_BRIEF, "furniture", 12, 7000.0, 14, ["mesh back", "adjustable height"]),
        (LAPTOP_BRIEF, "laptops", 8, 65000.0, 12, ["16gb ram", "512gb ssd"]),
        (HEADPHONE_BRIEF, "headphones", 25, 4000.0, 12,
         ["over-ear", "wireless", "noise-cancelling"]),
    ],
    ids=["furniture", "laptops", "headphones"],
)
def test_brief_is_parsed_into_the_right_category(text, category, quantity, cap, days, specs):
    """Category, quantity, cap, window and specs, with no model involved.

    The cap matters more than it looks. "Rs 65,000" is comma-grouped and "Rs 22"
    is not; a parser that stops at the comma reads a sixty-five rupee ceiling,
    every laptop "exceeds" it, and the screen blames the vendors for a bug in us.
    """
    brief = language._to_brief(text, language._offline_extract(text), audit=None)

    assert brief.category == category
    assert brief.quantity == quantity
    assert brief.max_price_per_unit_inr == cap
    assert brief.max_delivery_days == days
    assert sorted(brief.specs) == sorted(specs)


@pytest.mark.parametrize(
    "text",
    [FURNITURE_BRIEF, LAPTOP_BRIEF, HEADPHONE_BRIEF],
    ids=["furniture", "laptops", "headphones"],
)
def test_stated_priority_beats_the_category_default(text):
    """Three different default tables, one stated phrase, the same rounded weights."""
    _, computed, _, _ = build_pipeline(text)

    assert computed.values == STATED_WEIGHTS
    assert sum(computed.values.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The hard gate, and the four ways to fail it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, pool, survivors",
    [
        (FURNITURE_BRIEF, 10, 3),
        (LAPTOP_BRIEF, 16, 3),
        (HEADPHONE_BRIEF, 14, 5),
    ],
    ids=["furniture", "laptops", "headphones"],
)
def test_the_pool_size_is_frozen_and_every_gate_is_exercised(text, pool, survivors):
    """Every category's candidate pool fails on all four counts, and no others.

    Authored that way on purpose. A catalog where everything passes proves the
    filter runs; a catalog that fails on all four counts proves which gate is
    which - and gives us something to point at when a judge asks what happens to
    the ones that lost.

    The pool size is asserted for the same reason it is in tests/test_ranking.py:
    two of the four scoring terms are min-max across the SURVIVORS, so a listing
    that quietly starts passing would move a frozen table with nothing in the
    diff to explain it.
    """
    _, _, results, _ = build_pipeline(text)

    assert len(results) == pool
    assert sum(result.passed for result in results) == survivors

    reasons = {field for result in results if not result.passed for field in result.violations}
    assert reasons == {"specs", "quantity", "max_price_per_unit_inr", "max_delivery_days"}


# ---------------------------------------------------------------------------
# The frozen tables
# ---------------------------------------------------------------------------

def test_furniture_scores_are_frozen():
    """Computed by hand from the catalog before the code was asked for them."""
    _, _, _, ranked = build_pipeline(FURNITURE_BRIEF)

    assert {entry.product.name: entry.score for entry in ranked} == {
        "ErgoFlex Task": 62.4,
        "Postura Pro": 58.5,
        "MeshLite Task": 27.9,
    }
    assert [entry.product.name for entry in ranked] == [
        "ErgoFlex Task",
        "Postura Pro",
        "MeshLite Task",
    ]


def test_laptop_scores_are_frozen():
    _, _, _, ranked = build_pipeline(LAPTOP_BRIEF)

    assert {entry.product.name: entry.score for entry in ranked} == {
        "DevBook 14": 70.2,
        "CoreStation 15": 58.4,
        "ProBook X1": 7.9,
    }
    assert [entry.product.name for entry in ranked] == [
        "DevBook 14",
        "CoreStation 15",
        "ProBook X1",
    ]


def test_headphone_scores_are_frozen():
    """Five survivors, and the cheapest of them is last by a distance.

    This pool is the answer to "what if the bad option passes your filter?".
    Every one of these five cleared the same hard gates; the Boult at Rs 1,299 is
    Rs 1,550 cheaper than the winner and lands 52.7 points behind it, because
    2.8 stars against a 4.7 is worth more than a price margin when the user said
    reliability matters. No gate rejected it. The weights simply out-voted it.
    """
    _, _, _, ranked = build_pipeline(HEADPHONE_BRIEF)

    assert {entry.product.product_id: entry.score for entry in ranked} == {
        "AMZ-HPH-1302": 80.6,   # Sony WH-CH720N        Rs 3,890  4.7 stars
        "OS-AUDIOPRO-OE": 69.7,  # AudioPro OE           Rs 3,450  4.5 stars
        "AMZ-HPH-1301": 66.1,   # boAt Rockerz (Amazon) Rs 2,999  4.4 stars
        "FLK-HPH-2301": 57.5,   # boAt Rockerz (Flipkart) Rs 2,849 4.2 stars
        "FLK-HPH-2302": 27.9,   # Boult ProBass         Rs 1,299  2.8 stars
    }


def test_the_worst_reviewed_headset_qualifies_and_still_loses():
    """The trap in full: it passes every hard gate, and the ranking buries it.

    A filter cannot express "this is cheap because it is bad" — reliability is a
    SOFT criterion and soft criteria never reject. So the Boult is in the pool,
    on screen, with its 2.8 stars and its reviews visible, and it comes last. That
    is the honest shape of the answer: we do not hide the cheap option, we explain
    why it is not the recommendation.
    """
    _, _, results, ranked = build_pipeline(HEADPHONE_BRIEF)

    trap = next(r for r in results if r.product.product_id == "FLK-HPH-2302")
    assert trap.passed and not trap.violations

    cheapest = min(ranked, key=lambda entry: entry.product.price_per_unit_inr)
    assert cheapest.product.product_id == "FLK-HPH-2302"
    assert cheapest.rank == len(ranked)

    # And it loses on exactly the criterion the user named, not on price.
    assert cheapest.contribution("reliability") == pytest.approx(0.0)
    assert cheapest.contribution("price") > ranked[0].contribution("price")


def test_the_same_headset_listed_twice_is_ranked_twice_and_ordered_sanely():
    """One product, two marketplaces, two rows — and the better listing wins.

    AMZ-HPH-1301 and FLK-HPH-2301 are the same boAt Rockerz 550. We do not
    de-duplicate them, and we say so: picking a "canonical" listing would mean
    silently discarding a real offer. What the engine does instead is rank both
    on their own terms. Flipkart is Rs 150 cheaper and loses anyway, on three
    days' extra delivery and a shorter replacement window — a trade the user can
    see and overrule.
    """
    _, _, _, ranked = build_pipeline(HEADPHONE_BRIEF)
    by_id = {entry.product.product_id: entry for entry in ranked}

    amazon, flipkart = by_id["AMZ-HPH-1301"], by_id["FLK-HPH-2301"]

    assert flipkart.product.price_per_unit_inr < amazon.product.price_per_unit_inr
    assert amazon.rank < flipkart.rank
    assert amazon.contribution("delivery") > flipkart.contribution("delivery")
    assert amazon.contribution("replacement") > flipkart.contribution("replacement")


@pytest.mark.parametrize(
    "text, winner",
    [
        (FURNITURE_BRIEF, "ErgoFlex Task"),
        (LAPTOP_BRIEF, "DevBook 14"),
        (HEADPHONE_BRIEF, "Sony WH-CH720N Wireless Over-Ear Active Noise Cancelling "
                          "Headphones with Mic, 35 Hr Battery, Bluetooth 5.2"),
    ],
    ids=["furniture", "laptops", "headphones"],
)
def test_the_cheapest_survivor_does_not_win(text, winner):
    """The same lesson the packaging table teaches, in two more categories.

    In both pools the cheapest eligible option ranks LAST, because the user said
    reliability matters and the formula has no opinion of its own. If a category
    ever produced "cheapest wins", the weights would have stopped doing anything.
    """
    _, _, _, ranked = build_pipeline(text)

    cheapest = min(ranked, key=lambda entry: entry.product.price_per_unit_inr)
    assert cheapest.rank == len(ranked)
    assert ranked[0].product.name == winner


# ---------------------------------------------------------------------------
# The authorisation boundary, from both sides
# ---------------------------------------------------------------------------

def test_furniture_order_is_within_the_limit_and_the_agent_proceeds():
    """12 x Rs 6,500 = Rs 78,000 against a Rs 1,05,000 limit. No human needed."""
    _, outcome = authorise(FURNITURE_BRIEF)

    assert outcome.order_total_inr == 78000
    assert outcome.order_total_inr < config.authorisation_limit_inr()
    assert outcome.within_limit
    assert outcome.escalation is None


def test_laptop_order_exceeds_the_limit_and_the_agent_stops():
    """8 x Rs 58,000 = Rs 4,64,000. Over four times the limit, so nothing is bought."""
    _, outcome = authorise(LAPTOP_BRIEF)

    assert outcome.order_total_inr == 464000
    assert outcome.order_total_inr > config.authorisation_limit_inr()
    assert not outcome.within_limit
    assert outcome.escalation is not None
    assert outcome.escalation.detail["action_taken"] == "no purchase executed"


def test_headphone_order_is_within_the_limit_and_the_agent_proceeds():
    """25 x Rs 3,890 = Rs 97,250, comfortably inside Rs 1,05,000.

    The agent buys the BEST-scoring option here, not the cheapest one that fits.
    That distinction is the whole product: a Rs 32,475 order of the 2.8-star
    Boult would also have been within authority, and an agent optimising for
    "spend less" would have placed it without asking anyone.
    """
    context, outcome = authorise(HEADPHONE_BRIEF)

    assert outcome.order_total_inr == 97250
    assert outcome.order_total_inr < config.authorisation_limit_inr()
    assert outcome.within_limit
    assert outcome.escalation is None

    cheapest = min(context.ranked, key=lambda entry: entry.product.price_per_unit_inr)
    cheapest_total = cheapest.product.order_total_inr(context.brief.quantity)
    assert cheapest_total < outcome.order_total_inr
    assert context.ranked[0].product.product_id != cheapest.product.product_id


def test_laptops_have_no_in_limit_alternative_and_we_say_so():
    """Every eligible laptop is over the limit, and the screen has to admit it.

    The tempting failure here is to show the cheapest option anyway and let a
    reader assume it fits. It does not: Rs 4,39,200 is still four times the
    limit. An approval screen that implies otherwise is worse than one that
    offers nothing.
    """
    context, outcome = authorise(LAPTOP_BRIEF)
    limit = config.authorisation_limit_inr()

    assert all(
        entry.product.order_total_inr(context.brief.quantity) > limit
        for entry in context.ranked
    )
    assert outcome.escalation.detail["best_within_limit"] == (
        "none - every eligible option is over the limit"
    )


# ---------------------------------------------------------------------------
# The substitution threshold, from both sides
# ---------------------------------------------------------------------------

def test_furniture_gap_is_inside_the_substitution_threshold():
    """3.9 points between #1 and #2, so #2 is a fair silent stand-in if #1 fails.

    The packaging demo shows the opposite case (9.3 points, agent escalates
    rather than swap). Having both on hand means the 5-point threshold can be
    demonstrated deciding, not just described.
    """
    _, _, _, ranked = build_pipeline(FURNITURE_BRIEF)
    gap = round(ranked[0].score - ranked[1].score, 1)

    assert gap == 3.9
    assert gap <= config.substitution_threshold_points()


def test_laptop_gap_is_outside_the_substitution_threshold():
    _, _, _, ranked = build_pipeline(LAPTOP_BRIEF)
    gap = round(ranked[0].score - ranked[1].score, 1)

    assert gap == 11.8
    assert gap > config.substitution_threshold_points()


# ---------------------------------------------------------------------------
# Categories do not leak into one another
# ---------------------------------------------------------------------------

def test_each_category_only_ever_sees_its_own_products():
    """The stage-3 category gate runs before anything else, in every category."""
    for category in ("packaging", "furniture", "laptops", "headphones"):
        found = discovery.discover(category)
        assert found, f"no products found for {category}"
        assert {product.category for product in found} == {category}


def test_a_category_nobody_stocks_is_reported_honestly():
    """We answer from the catalogs, not from the config's opinions.

    config.yaml holds weights and a price cap for `labels` and no vendor stocks
    one. The agent must say what it can actually buy.
    """
    stocked = discovery.available_categories()

    assert stocked == ["furniture", "headphones", "laptops", "packaging"]
    assert "labels" not in stocked
    assert "cement" not in stocked


def test_a_requirement_nothing_can_meet_is_named_rather_than_hinted_at():
    """"The gaps are on specification" is not an answer. Which specification?

    A buyer who asks for something no supplier lists gets a refusal, and that is
    correct - a spec is a requirement, not a wish. What was NOT correct was the
    screen: every rejected row carried the exact word that blocked it and we
    showed none of them, so the buyer was told the shape of the problem and left
    to guess its content.

    Only a requirement that blocked the WHOLE pool is named. A spec two products
    lack is not why the search failed, and naming it would send someone off to
    fix the wrong word.
    """
    text = ("5,000 kraft mailer boxes, double-wall, max Rs 22 per unit, "
            "delivered within 10 days.")
    brief = language._to_brief(text, language._offline_extract(text), audit=None)
    brief = brief.model_copy(update={"specs": ["double-wall", "flame-retardant"]})

    context = TransactionContext(transaction_id="TXN-BLOCKED", brief=brief)
    context.filter_results = discovery.run(brief)
    assert not context.eligible, "nothing stocks flame-retardant; this must not qualify"

    outcome = escalation.handle(
        context, escalation.Trigger.NO_ELIGIBLE_MATCH, audit_module.AuditLogger(context)
    )

    assert "'flame-retardant'" in outcome.headline
    assert "'double-wall'" not in outcome.headline, (
        "double-wall is stocked, so it did not block anything and must not be blamed"
    )
    assert outcome.detail["blocking_requirements"] == [
        "nothing we can source lists 'flame-retardant'"
    ]
    assert outcome.detail["action_taken"] == "no purchase executed"
    assert not outcome.options, "nothing is offered when the miss is non-negotiable"


# ---------------------------------------------------------------------------
# The brief does not have to be shaped like ours
# ---------------------------------------------------------------------------
# Every brief in this file and in tests/test_ranking.py opens with the number:
# "12 ergonomic task chairs", "5,000 kraft mailer boxes". Real people do not.
# "Buy me latex gloves under 50 each" puts the product first and the number
# second, and the parser used to read the words AFTER the number as the product
# - which handed stage 3 a category called "each arriving in not more than".
#
# The user then sees the agent decline a thing they never asked for. Nothing
# crashes, nothing is logged as wrong, and the one honest answer we owed them
# ("we don't stock gloves") never gets said. That is why this is a test and not
# a tidy-up.

@pytest.mark.parametrize(
    "text, category, quantity, cap, days",
    [
        # Product first, number second - the shape that used to break.
        ("Buy me latex gloves under 50 each arriving in not more than 12 days",
         "latex gloves", None, 50.0, 12),
        # Same sentence with the user's original typo. We echo their word back
        # rather than guessing at it; "uner" is still a better answer than a
        # category made of the delivery clause.
        ("Buy me latex gloves uner 50 each arriving in not more than 12 days",
         "latex gloves uner", None, 50.0, 12),
        # Number first, noun after - the shape our own briefs use, unchanged.
        ("200 bags of cement, max Rs 400 each, delivered within 7 days",
         "bags of cement", 200, 400.0, 7),
    ],
    ids=["noun-first", "noun-first-with-typo", "number-first"],
)
def test_an_unstocked_product_is_named_from_the_users_own_words(text, category, quantity, cap, days):
    """Whatever we cannot map to a category, we at least repeat back correctly."""
    extraction = language._offline_extract(text)

    assert extraction.category == category
    assert extraction.quantity == quantity
    assert extraction.max_price_per_unit_inr == cap
    assert extraction.max_delivery_days == days


def test_a_price_written_without_a_currency_symbol_is_not_read_as_a_quantity():
    """"50 each" is a ceiling. Read as an order of fifty, it looks like nothing
    went wrong - the pool just quietly comes back with the wrong sized order.

    So the number must land in the cap and NOT in the quantity, and the missing
    quantity must reach the scope gate as the one question worth asking.
    """
    text = "Buy me latex gloves under 50 each arriving in not more than 12 days"
    extraction = language._offline_extract(text)

    assert extraction.max_price_per_unit_inr == 50.0
    assert extraction.quantity is None

    check = language._offline_scope(text)
    assert check.verdict == "incomplete"
    assert check.missing_fields == ["quantity"]
