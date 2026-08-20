"""Stage 2's one question: what are these actually for?

WHY THIS FILE EXISTS
--------------------
"25 wireless noise-cancelling headsets, over-ear, under Rs 4,000, within 12
days" is a complete, buyable brief. Every hard constraint is stated and the pool
filters cleanly. And it still does not say the thing that decides the answer:
whether a dead headset costs a sales call or an afternoon of background music.

So for categories that declare it, the agent asks one question and applies a
weight set read from config.yaml. Everything about that has to stay
deterministic, or we have quietly bolted a mood onto a machine we spent the
whole project making reproducible:

  * the same tag gives the same weights and the same scores, every run
  * a DIFFERENT tag gives different scores, and we can say by how much
  * nothing about it touches eligibility — the same five products qualify
  * the user's own words always beat the menu
  * declining to answer is a documented default, logged as an ASSUMPTION

THE ONE RULE STILL HOLDS
------------------------
No model is involved anywhere in this file. `context_needed()` is a config
lookup, the tag is chosen by a human clicking a button, and `config.py` turns
the tag into four numbers — exactly the way it turns 'matters_a_lot' into 0.45.
The LLM never sees a context, a weight, or this question.
"""

from __future__ import annotations

import pytest

from agent import audit as audit_module
from agent import config, discovery, language, ranking, weights
from agent.models import Actor, EventType, TransactionContext

# No priority stated, on purpose. That is what makes the question fire.
OPEN_BRIEF = (
    "25 wireless noise-cancelling headsets, over-ear, max Rs 4,000 each, "
    "delivered within 12 days."
)

# The same purchase, with the buyer's own view attached. The menu must not appear.
DECIDED_BRIEF = OPEN_BRIEF[:-1] + ". Reliability matters a lot."

# A category that declares no contexts and must never ask.
PACKAGING_BRIEF = (
    "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit, "
    "delivered within 10 days."
)


def parse(text: str):
    return language._to_brief(text, language._offline_extract(text), audit=None)


def rank_under(text: str, tag: str | None):
    """Score the pool for one brief under one usage context."""
    brief = parse(text)
    computed = weights.compute(brief, context_tag=tag)
    eligible = [result.product for result in discovery.run(brief) if result.passed]
    ranked = ranking.rank(
        eligible, computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )
    return computed, ranked


def scores(ranked) -> dict[str, float]:
    return {entry.product.product_id: entry.score for entry in ranked}


# ---------------------------------------------------------------------------
# When we ask, and when we very deliberately do not
# ---------------------------------------------------------------------------

def test_a_complete_brief_with_no_stated_priority_gets_the_question():
    """Nothing is missing, and there is still something worth asking."""
    request = weights.context_needed(parse(OPEN_BRIEF))

    assert request is not None
    assert request.category == "headphones"
    assert request.question == "What will these mainly be used for?"
    assert [option.tag for option in request.options] == [
        "office_calls", "new_starters", "shared_pool",
    ]
    assert all(option.label and option.note for option in request.options)


def test_the_users_own_words_beat_the_menu():
    """"Reliability matters a lot" is an answer. We do not ask it again.

    This is the important one. A dropdown that could override a stated priority
    would be us replacing what the buyer said with our own taxonomy — the exact
    move this project exists to refuse. The menu is for silence, not disagreement.
    """
    assert weights.context_needed(parse(DECIDED_BRIEF)) is None


def test_a_category_with_no_contexts_never_asks():
    """A box is a box. Contexts are opt-in data, not a tax on every category."""
    assert weights.context_needed(parse(PACKAGING_BRIEF)) is None
    assert config.category_contexts("packaging") == {}
    assert config.category_contexts("laptops") == {}


def test_an_answered_brief_is_not_asked_twice():
    """One question, never a loop."""
    brief = parse(OPEN_BRIEF)
    assert weights.context_needed(brief) is not None

    answered = brief.model_copy(update={"context_tag": "office_calls"})
    assert weights.context_needed(answered) is None


# ---------------------------------------------------------------------------
# The tables, frozen — three contexts, three different answers
# ---------------------------------------------------------------------------

FROZEN = {
    None: {
        "AMZ-HPH-1302": 76.4, "OS-AUDIOPRO-OE": 65.9, "AMZ-HPH-1301": 61.5,
        "FLK-HPH-2301": 53.5, "FLK-HPH-2302": 32.0,
    },
    "office_calls": {
        "AMZ-HPH-1302": 84.7, "OS-AUDIOPRO-OE": 70.0, "AMZ-HPH-1301": 62.0,
        "FLK-HPH-2301": 52.6, "FLK-HPH-2302": 23.8,
    },
    "new_starters": {
        "AMZ-HPH-1302": 76.9, "AMZ-HPH-1301": 69.5, "OS-AUDIOPRO-OE": 64.5,
        "FLK-HPH-2301": 59.8, "FLK-HPH-2302": 43.2,
    },
    "shared_pool": {
        "AMZ-HPH-1302": 64.8, "OS-AUDIOPRO-OE": 58.5, "AMZ-HPH-1301": 53.3,
        "FLK-HPH-2301": 47.7, "FLK-HPH-2302": 40.5,
    },
}


@pytest.mark.parametrize("tag", list(FROZEN), ids=lambda t: t or "no-context")
def test_each_context_produces_its_frozen_table(tag):
    """Computed by hand from the catalog before the code was asked for them."""
    _, ranked = rank_under(OPEN_BRIEF, tag)
    assert scores(ranked) == FROZEN[tag]


def test_changing_the_context_changes_the_scores():
    """The headline claim, asserted as a difference rather than a table.

    If every context produced the same numbers the question would be theatre —
    a widget that makes the agent look attentive and changes nothing.
    """
    tables = [scores(rank_under(OPEN_BRIEF, tag)[1]) for tag in FROZEN]

    for later in tables[1:]:
        assert later != tables[0]


def test_the_context_reorders_the_middle_of_the_pool():
    """Not just different numbers — a different answer to "what is second?".

    Under "all-day calls" the AudioPro is runner-up on its 21-day replacement
    window. Under "kitting out new joiners" the boAt overtakes it, because it
    arrives in three days against the AudioPro's eight and a new starter has a
    start date. Same catalog, same brief, same sixty lines of arithmetic.
    """
    _, calls = rank_under(OPEN_BRIEF, "office_calls")
    _, starters = rank_under(OPEN_BRIEF, "new_starters")

    assert [entry.product.product_id for entry in calls][:3] == [
        "AMZ-HPH-1302", "OS-AUDIOPRO-OE", "AMZ-HPH-1301",
    ]
    assert [entry.product.product_id for entry in starters][:3] == [
        "AMZ-HPH-1302", "AMZ-HPH-1301", "OS-AUDIOPRO-OE",
    ]


def test_the_cheap_option_closes_the_gap_but_still_does_not_win():
    """"Shared pool" is the context that most favours the Rs 1,299 trap.

    Price goes to 0.40 and reliability drops to 0.20, and the Boult climbs 16.7
    points. It still loses by 24.3, because it is worst in the pool on TWO
    criteria and no single weight can rescue that. Worth having as a test: a
    context that could hand the purchase to the worst-reviewed product on the
    list would be a weight set we should not ship.
    """
    _, calls = rank_under(OPEN_BRIEF, "office_calls")
    _, pool = rank_under(OPEN_BRIEF, "shared_pool")

    trap_in_calls = scores(calls)["FLK-HPH-2302"]
    trap_in_pool = scores(pool)["FLK-HPH-2302"]

    assert round(trap_in_pool - trap_in_calls, 1) == 16.7
    assert pool[-1].product.product_id == "FLK-HPH-2302"


def test_a_context_is_deterministic_across_runs():
    """Same tag in, same scores out. Five times."""
    runs = [scores(rank_under(OPEN_BRIEF, "shared_pool")[1]) for _ in range(5)]
    assert all(run == runs[0] for run in runs)


# ---------------------------------------------------------------------------
# What a context is NOT allowed to do
# ---------------------------------------------------------------------------

def test_a_context_cannot_change_who_qualifies():
    """Eligibility is a hard gate; a preference has no business near it.

    The same five products survive under every context, including the one that
    weights price at 0.40. A context that could pull an ineligible product into
    the pool would have turned a preference into an authority.
    """
    pools = []
    for tag in FROZEN:
        brief = parse(OPEN_BRIEF).model_copy(update={"context_tag": tag})
        results = discovery.run(brief)
        pools.append({r.product.product_id for r in results if r.passed})

    assert all(pool == pools[0] for pool in pools)
    assert len(pools[0]) == 5


def test_every_configured_context_still_sums_to_one():
    """A set summing to 0.9 would silently shrink every score in the category.

    Validated at startup too, in agent/config.py. Asserted again here because
    this is the file someone edits when they add a context, and a startup error
    they never see is a startup error that does not help them.
    """
    for category in config.load()["categories"]:
        for tag, context in config.category_contexts(category).items():
            values = context["weights"]
            assert set(values) == set(config.category_default_weights(category)), (
                f"{category}/{tag} names different criteria from the defaults"
            )
            assert sum(values.values()) == pytest.approx(1.0), f"{category}/{tag}"


def test_a_stated_priority_still_wins_even_if_a_context_is_forced():
    """Belt and braces: the menu cannot be used to smuggle a weight past a user.

    `context_needed` already refuses to ask when a priority was stated, so this
    should be unreachable through the UI. It is asserted anyway, because "the
    caller will not do that" is not a guarantee.
    """
    computed, _ = rank_under(DECIDED_BRIEF, "shared_pool")

    # shared_pool would put price at 0.40. The stated phrase pins reliability at
    # 0.45 and only the REMAINING budget is rescaled from the context's shape.
    assert computed.values["reliability"] == 0.45
    assert computed.values["price"] < 0.40
    assert sum(computed.values.values()) == pytest.approx(1.0)


def test_an_unknown_tag_falls_back_to_the_default_rather_than_crashing():
    """A stale UI or a replayed audit line can carry a tag config no longer has.

    Falling back is right; falling back SILENTLY is not, so the log gets an
    entry naming the tag we did not recognise. A weight set that quietly is not
    the one the record names is worse than a crash.
    """
    default_weights, default_ranked = rank_under(OPEN_BRIEF, None)
    unknown_weights, unknown_ranked = rank_under(OPEN_BRIEF, "sitting_on_a_beach")

    assert unknown_weights.values == default_weights.values
    assert scores(unknown_ranked) == scores(default_ranked)


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------

def logged(text: str, tag: str | None):
    """Run stage 2 with a logger attached and hand back the entries."""
    context = TransactionContext(transaction_id=audit_module.new_transaction_id())
    log = audit_module.AuditLogger(context)
    brief = parse(text).model_copy(update={"context_tag": tag})
    computed = weights.compute(brief, log)
    return computed, context.audit


def test_the_log_records_the_chosen_context_as_the_users_decision():
    """WHO chose it matters as much as WHAT was chosen.

    A context entry is actor USER, because a human clicked it. If the agent could
    write its own preference into the record as a decision, the log would stop
    being evidence of where the agent's authority ended.
    """
    computed, entries = logged(OPEN_BRIEF, "shared_pool")

    chosen = [
        entry for entry in entries
        if entry.event_type is EventType.DECISION and entry.detail.get("context_tag")
        and entry.actor is Actor.USER
    ]
    assert len(chosen) == 1

    detail = chosen[0].detail
    assert detail["context_tag"] == "shared_pool"
    assert detail["context_label"] == "Shared pool, handed around"
    assert detail["weights_applied"] == config.context_weights("headphones", "shared_pool")
    assert detail["field_status"] == "confirmed"
    assert "shared pool" in chosen[0].reasoning.lower()


def test_the_weights_entry_carries_the_context_and_the_numbers_it_produced():
    """One transaction id replays the whole order without reading code.

    Someone auditing this six months from now has to be able to see the tag AND
    the four weights it turned into, in the same record, without holding a copy
    of config.yaml from the day it ran.
    """
    computed, entries = logged(OPEN_BRIEF, "office_calls")

    weights_entry = next(
        entry for entry in entries
        if entry.stage.startswith("2") and entry.detail.get("weights")
    )
    assert weights_entry.detail["context_tag"] == "office_calls"
    assert weights_entry.detail["context_label"] == "All-day calls at a desk"
    assert weights_entry.detail["weights"] == computed.values


def test_declining_to_answer_is_logged_as_an_assumption_not_a_choice():
    """Silence is never quietly converted into a preference.

    The user was offered a question and did not answer it. We applied a
    documented default, and the record says ASSUMED — the same treatment any
    other unstated field gets. An assumption presented as an instruction is the
    exact failure the audit trail exists to prevent.
    """
    _, entries = logged(OPEN_BRIEF, None)

    assumed = [entry for entry in entries if entry.event_type is EventType.ASSUMPTION]
    assert len(assumed) == 1
    assert assumed[0].actor is Actor.AGENT
    assert assumed[0].detail["field_status"] == "assumed"
    assert assumed[0].detail["options_offered"] == [
        "new_starters", "office_calls", "shared_pool",
    ]


def test_a_category_that_never_asks_logs_no_assumption_about_context():
    """Packaging is not assuming a usage context. It has none to assume.

    Without this distinction the log would carry an ASSUMPTION on every single
    order, which trains a reader to skip them — and the ones that matter are the
    ones nobody reads.
    """
    _, entries = logged(PACKAGING_BRIEF, None)

    assert not [entry for entry in entries if entry.event_type is EventType.ASSUMPTION]


def test_an_unknown_tag_is_logged_loudly():
    _, entries = logged(OPEN_BRIEF, "sitting_on_a_beach")

    assumed = next(entry for entry in entries if entry.event_type is EventType.ASSUMPTION)
    assert assumed.detail["requested_context"] == "sitting_on_a_beach"
    assert "not one this configuration offers" in assumed.reasoning
