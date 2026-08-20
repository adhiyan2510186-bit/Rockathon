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

from agent import authorisation, config, discovery, language, ranking, weights
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
    ],
    ids=["furniture", "laptops"],
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


@pytest.mark.parametrize("text", [FURNITURE_BRIEF, LAPTOP_BRIEF], ids=["furniture", "laptops"])
def test_stated_priority_beats_the_category_default(text):
    """Two different default tables, one stated phrase, the same rounded weights."""
    _, computed, _, _ = build_pipeline(text)

    assert computed.values == STATED_WEIGHTS
    assert sum(computed.values.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The hard gate, and the four ways to fail it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [FURNITURE_BRIEF, LAPTOP_BRIEF], ids=["furniture", "laptops"])
def test_three_of_seven_survive_and_every_gate_is_exercised(text):
    """Seven candidates, three survivors, and the four rejections are one each.

    Authored that way on purpose. A catalog where everything passes proves the
    filter runs; a catalog that fails on all four counts proves which gate is
    which - and gives us something to point at when a judge asks what happens to
    the ones that lost.
    """
    _, _, results, _ = build_pipeline(text)

    assert len(results) == 7
    assert sum(result.passed for result in results) == 3

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


@pytest.mark.parametrize(
    "text, winner",
    [(FURNITURE_BRIEF, "ErgoFlex Task"), (LAPTOP_BRIEF, "DevBook 14")],
    ids=["furniture", "laptops"],
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
    for category in ("packaging", "furniture", "laptops"):
        found = discovery.discover(category)
        assert found, f"no products found for {category}"
        assert {product.category for product in found} == {category}


def test_a_category_nobody_stocks_is_reported_honestly():
    """We answer from the catalogs, not from the config's opinions.

    config.yaml holds weights and a price cap for `labels` and no vendor stocks
    one. The agent must say what it can actually buy.
    """
    stocked = discovery.available_categories()

    assert stocked == ["furniture", "laptops", "packaging"]
    assert "labels" not in stocked
    assert "cement" not in stocked
