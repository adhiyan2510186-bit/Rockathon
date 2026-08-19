"""The golden test. If this goes red, the code is wrong — not the numbers.

WHAT THIS FILE IS FOR
---------------------
58.0 / 48.7 / 33.7 are on our slide. A judge will see the deck and then see the
app. If the app says 57.4, we have a problem we cannot explain on the spot.

So these numbers are locked. CLAUDE.md puts it plainly: "If the code disagrees
with the deck, the code is wrong." This file is the tripwire that tells us at a
laptop rather than on stage.

Run it with:   python -m pytest tests/ -q

It covers the whole chain that produces the table — the two catalogs, the
normaliser, the hard gates, the weight engine and the ranker — because any one
of them can move a score. The test deliberately uses the offline parser: no API
key, no network, no rate limit, same answer every time.
"""

from __future__ import annotations

import math

from agent import discovery, language, ranking, weights
from agent.models import Brief

# The exact brief from CLAUDE.md and the deck.
DEMO_BRIEF = (
    "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit, "
    "delivered within 10 days. Reliability matters a lot - we got burned last quarter."
)

# The table the ranker must reproduce, product name -> score.
GOLDEN_SCORES = {
    "Corusafe DW": 58.0,
    "KraftPro DW": 48.7,
    "EcoMail DW": 33.7,
}

GOLDEN_WEIGHTS = {
    "reliability": 0.45,   # user-stated: "matters a lot"
    "price": 0.20,
    "replacement": 0.20,
    "delivery": 0.15,
}


def build_pipeline():
    """Run the demo brief through stages 1-4 exactly as the app does.

    Uses the offline parser on purpose. This test must give the same answer on a
    laptop with no API key, on a plane, and thirty seconds after we have burned
    through the free tier's ten requests a minute.
    """
    extraction = language._offline_extract(DEMO_BRIEF)
    brief = language._to_brief(DEMO_BRIEF, extraction, audit=None)
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


# ---------------------------------------------------------------------------
# The golden table
# ---------------------------------------------------------------------------

def test_scores_match_the_deck():
    """58.0 / 48.7 / 33.7. The single most important assertion in the project."""
    _, _, _, ranked = build_pipeline()
    actual = {item.product.name: item.score for item in ranked}
    assert actual == GOLDEN_SCORES


def test_ranking_order():
    """Corusafe first, KraftPro second, EcoMail last."""
    _, _, _, ranked = build_pipeline()
    assert [item.product.name for item in ranked] == ["Corusafe DW", "KraftPro DW", "EcoMail DW"]
    assert [item.rank for item in ranked] == [1, 2, 3]


def test_cheapest_product_ranks_last():
    """The whole point of the demo, asserted as behaviour rather than a number.

    EcoMail is 20% under the cap and comes last, because the user said
    reliability mattered. If a future change ever makes the bargain win, this
    test fails and the pitch has quietly stopped being true.
    """
    _, _, _, ranked = build_pipeline()
    cheapest = min(ranked, key=lambda item: item.product.price_per_unit_inr)
    assert cheapest.rank == len(ranked)
    assert cheapest.product.name == "EcoMail DW"


# ---------------------------------------------------------------------------
# The winner's arithmetic, term by term
# ---------------------------------------------------------------------------

def test_winner_arithmetic():
    """0.45x1.000 + 0.20x0.067 + 0.15x0.775 + 0.20x0.000 = 0.580 -> 58.0."""
    _, _, _, ranked = build_pipeline()
    corusafe = ranked[0]
    normalised = {term.criterion: term.normalised for term in corusafe.terms}

    assert normalised["reliability"] == 1.0                        # best in the pool
    assert normalised["replacement"] == 0.0                        # worst in the pool
    assert math.isclose(normalised["price"], 0.067, abs_tol=0.001)
    assert math.isclose(normalised["delivery"], 0.775, abs_tol=0.001)

    assert math.isclose(sum(term.contribution for term in corusafe.terms), 0.580, abs_tol=0.001)


def test_reliability_dominates_the_winner():
    """Reliability contributed 0.450 of Corusafe's 0.580, and 0.000 of EcoMail's."""
    _, _, _, ranked = build_pipeline()
    by_name = {item.product.name: item for item in ranked}
    assert math.isclose(by_name["Corusafe DW"].contribution("reliability"), 0.450, abs_tol=0.001)
    assert math.isclose(by_name["EcoMail DW"].contribution("reliability"), 0.000, abs_tol=0.001)


def test_score_gap_triggers_escalation_not_substitution():
    """#1 leads #2 by 9.3 points, comfortably over the 5-point threshold.

    That gap is why the agent escalates instead of silently swapping in #2 when
    the winner becomes unavailable. If it ever fell below 5, stage 5's behaviour
    in the demo would change without anyone touching stage 5.
    """
    from agent import config

    _, _, _, ranked = build_pipeline()
    gap = round(ranked[0].score - ranked[1].score, 1)
    assert gap == 9.3
    assert gap > config.substitution_threshold_points()


# ---------------------------------------------------------------------------
# The inputs the golden numbers rest on
# ---------------------------------------------------------------------------

def test_weights_match_the_deck():
    """0.45 / 0.20 / 0.20 / 0.15, summing to exactly 1.0."""
    _, computed, _, _ = build_pipeline()
    assert computed.values == GOLDEN_WEIGHTS
    assert math.isclose(sum(computed.values.values()), 1.0, abs_tol=1e-9)


def test_three_of_seven_products_survive_the_hard_gates():
    """Seven considered, three eligible, four rejected for four different reasons."""
    _, _, results, _ = build_pipeline()
    assert len(results) == 7
    passed = [result for result in results if result.passed]
    assert len(passed) == 3

    rejected_reasons = {
        field for result in results if not result.passed for field in result.violations
    }
    assert rejected_reasons == {
        "specs",
        "quantity",
        "max_price_per_unit_inr",
        "max_delivery_days",
    }


def test_survivor_figures_match_the_deck():
    """The table's raw columns: price, days, reliability, replacement window."""
    _, _, _, ranked = build_pipeline()
    actual = {
        item.product.name: (
            item.product.price_per_unit_inr,
            item.product.delivery_days,
            item.product.reliability_rating,
            item.product.replacement_window_days,
        )
        for item in ranked
    }
    assert actual == {
        "Corusafe DW": (21.90, 4, 4.8, 7),
        "KraftPro DW": (20.90, 6, 4.6, 10),
        "EcoMail DW": (17.60, 9, 4.1, 30),
    }


def test_order_total_exceeds_the_authorisation_limit():
    """5,000 x Rs 21.90 = Rs 1,09,500, over the Rs 1,05,000 limit.

    This is what makes the approval screen fire in the demo. Asserted here so a
    catalog price edit cannot quietly delete the most important moment of the run.
    """
    from agent import config

    brief, _, _, ranked = build_pipeline()
    total = ranked[0].product.order_total_inr(brief.quantity)
    assert total == 109500
    assert total > config.authorisation_limit_inr()


# ---------------------------------------------------------------------------
# Properties that must hold for any pool, not just this one
# ---------------------------------------------------------------------------

def test_ranking_is_deterministic():
    """Same brief in, same ranking out. Run it five times and compare."""
    runs = [[(item.product.name, item.score) for item in build_pipeline()[3]] for _ in range(5)]
    assert all(run == runs[0] for run in runs)


def test_catalog_order_does_not_change_the_result():
    """Reading the catalogs in a different order must not move a score.

    Guards the tie-break in rank(). A ranking that depends on file order is not
    reproducible, whatever the scores happen to say today.
    """
    brief, computed, results, ranked = build_pipeline()
    eligible = [result.product for result in results if result.passed]
    reversed_ranking = ranking.rank(
        list(reversed(eligible)), computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )
    assert [item.product.name for item in reversed_ranking] == [
        item.product.name for item in ranked
    ]


def test_empty_pool_returns_empty_not_an_error():
    """Nothing eligible is escalation trigger #1, handled elsewhere — not a crash here."""
    _, computed, _, _ = build_pipeline()
    assert ranking.rank([], computed, 22.0, 10) == []


def test_scores_stay_within_zero_and_one_hundred():
    """A score is a percentage of a perfect fit; it cannot exceed 100 or go below 0."""
    _, _, _, ranked = build_pipeline()
    for item in ranked:
        assert 0.0 <= item.score <= 100.0
        for term in item.terms:
            assert 0.0 <= term.normalised <= 1.0
