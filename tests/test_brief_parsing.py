"""Reading the sentence: the briefs that used to be read WRONG rather than not at all.

WHY THIS FILE EXISTS
--------------------
Every brief below was, at some point, parsed into a confident and incorrect
answer. Not a crash, not a refusal — an order that looked entirely healthy on
screen and was not what the buyer asked for. That is the failure mode this
project exists to rule out, and until this file was written nothing tested the
priority parser in isolation at all: the only coverage was "reliability matters
a lot" arriving at the right weights, which is the one phrasing we had already
built around.

The four classes of damage, one section each:

  INVERSION    "don't care about price" scored price UP, because the words
               "care about" sit inside the words "don't care about".
  SILENT DROP  "as cheap as possible" extracted no preference at all, so the
               only thing the buyer said was replaced by our house default.
  MAGNITUDE    "10k boxes" ordered ten boxes. Everything downstream agreed.
  REFUSAL      "two hundred chairs" was declined as not a purchase request.

Everything here runs the OFFLINE parser deliberately and directly. It is the
path that runs whenever the model is unreachable or out of quota, it is the path
`tests/test_ranking.py` pins the golden numbers to, and it is where all four bugs
above lived.
"""

from __future__ import annotations

import pytest

from agent import config, discovery, language, weights
from agent.models import SOFT_CRITERIA, FieldStatus


def parse(text: str):
    """The brief as the engine would see it, with no model involved."""
    return language._to_brief(text, language._offline_extract(text), None)


# ---------------------------------------------------------------------------
# Inversion — the worst of the four, because it is confidently backwards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brief,criterion", [
    ("40 headsets, over-ear, within 12 days. Don't care about price.", "price"),
    ("40 headsets within 12 days. We do not care about delivery speed.", "delivery"),
    ("40 headsets within 12 days. Price is not a concern.", "price"),
    ("40 headsets within 12 days. No preference on delivery.", "delivery"),
    ("40 headsets within 12 days. The warranty doesn't matter to us.", "replacement"),
    ("40 headsets within 12 days. We're not fussed about the return window.",
     "replacement"),
])
def test_a_dismissed_criterion_is_recorded_as_dismissed(brief, criterion):
    """A criterion the buyer waved away must not come back weighted upwards."""
    parsed = parse(brief)
    assert parsed.stated_priorities.get(criterion) == "does_not_matter", (
        f"{criterion!r} was dismissed in the brief but parsed as "
        f"{parsed.stated_priorities.get(criterion)!r}"
    )


def test_a_dismissed_criterion_scores_zero_and_the_others_take_its_share():
    """0.00 is the honest number: this criterion took no part in the ranking."""
    parsed = parse("40 headsets, over-ear, within 12 days. Don't care about price.")
    computed = weights.compute(parsed)

    assert computed.values["price"] == 0.0
    assert computed.sources["price"] == "user-stated (does not matter)"
    assert sum(computed.values.values()) == pytest.approx(1.0)

    # The other three genuinely moved, so the screen must not call them untouched
    # defaults. Reliability starts at 0.35 for this category and ends higher.
    defaults = config.category_default_weights(parsed.category)
    assert computed.values["reliability"] > defaults["reliability"]
    for criterion in ("reliability", "replacement", "delivery"):
        assert "rescaled" in computed.sources[criterion]


def test_dismissing_everything_falls_back_to_an_even_split():
    """Four zeroes cannot be normalised, so we spread the decision evenly."""
    parsed = parse(
        "40 headsets within 12 days. Don't care about price, delivery doesn't "
        "matter, no preference on reliability, warranty is not a concern."
    )
    computed = weights.compute(parsed)
    assert set(computed.values.values()) == {0.25}
    assert sum(computed.values.values()) == pytest.approx(1.0)


def test_the_dismissal_label_is_the_only_weight_allowed_to_be_zero():
    """Guards the config validator we relaxed to let 0.00 through."""
    table = config.load()["priority_phrase_weights"]
    assert table["does_not_matter"] == 0.0
    assert all(w > 0 for phrase, w in table.items() if phrase != "does_not_matter")
    assert all(w < 1 for w in table.values())


# ---------------------------------------------------------------------------
# Silent drop — a preference stated without naming a strength word
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brief,criterion", [
    ("5,000 mailer boxes within 10 days, as cheap as possible", "price"),
    ("200 chairs in 14 days, cheapest option please", "price"),
    ("500 boxes in 5 days, reliability is everything", "reliability"),
    ("500 boxes in 5 days. Reliability is non-negotiable.", "reliability"),
    ("8 laptops, 16GB RAM, in 9 days. Budget is tight.", "price"),
])
def test_a_superlative_is_a_stated_priority(brief, criterion):
    """"Cheapest" is a preference in anyone's English, and used to vanish."""
    parsed = parse(brief)
    assert parsed.stated_priorities.get(criterion) == "matters_a_lot"


def test_non_negotiable_survives_its_own_hyphen():
    """The clause splitter used to break on "-", cutting the phrase in half."""
    parsed = parse("500 boxes in 5 days. Reliability is non-negotiable.")
    assert parsed.stated_priorities["reliability"] == "matters_a_lot"


def test_a_stated_limit_is_still_not_a_preference():
    """The fix must not turn requirements into priorities.

    "max Rs 22 per unit" and "within 10 days" are things the purchase has to
    meet. If they started registering as preferences, every brief would arrive
    with price and delivery pinned and the category defaults would never apply.
    """
    parsed = parse(
        "5,000 kraft mailer boxes, double-wall, max Rs 22 per unit, "
        "delivered within 10 days."
    )
    assert parsed.stated_priorities == {}


# ---------------------------------------------------------------------------
# Magnitude — an order size read an order of magnitude wrong
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brief,quantity", [
    ("reorder 10k mailer boxes, need them in 7 days", 10_000),
    ("order 2.5k boxes within 8 days", 2_500),
    ("buy 1 lakh labels in 6 days", 100_000),
    ("order 3 thousand boxes in 9 days", 3_000),
])
def test_a_scale_suffix_multiplies_the_order(brief, quantity):
    assert parse(brief).quantity == quantity


def test_a_product_name_starting_with_a_scale_letter_is_not_a_multiplier():
    """"5,000 kraft" must stay 5,000 and not become five million.

    This is the case that makes the \\b in the quantity pattern load bearing, and
    it is the demo brief, so it is also what the golden test would catch.
    """
    assert parse(
        "5,000 kraft mailer boxes, double-wall, max Rs 22 per unit, "
        "delivered within 10 days."
    ).quantity == 5_000


# ---------------------------------------------------------------------------
# Refusal — a real brief declined because it had no digits in it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brief,quantity", [
    ("two hundred chairs, delivered within 10 days", 200),
    ("buy one thousand five hundred labels in 6 days", 1_500),
    ("a dozen chairs delivered in 9 days", 12),
    ("order twenty laptops, 16GB RAM, within 15 days", 20),
])
def test_an_order_size_written_in_words_is_read_not_refused(brief, quantity):
    parsed = language._offline_extract(brief)
    assert parsed.quantity == quantity
    assert language._offline_scope(brief).verdict == "in_scope"


def test_the_delivery_window_is_not_mistaken_for_the_order_size():
    """The word-number path only runs once the digits are accounted for."""
    parsed = parse("two hundred chairs, delivered within 10 days")
    assert parsed.quantity == 200
    assert parsed.max_delivery_days == 10


# ---------------------------------------------------------------------------
# The assumed price cap — an invented number acting as a hard gate
# ---------------------------------------------------------------------------

def test_a_stated_cap_is_confirmed_and_an_unstated_one_is_assumed():
    stated = parse("40 headsets, over-ear, max Rs 4,000 each, within 12 days")
    silent = parse("40 headsets, over-ear, within 12 days")

    assert stated.field_status["max_price_per_unit_inr"] is FieldStatus.CONFIRMED
    assert silent.field_status["max_price_per_unit_inr"] is FieldStatus.ASSUMED
    # We still fill the value in, so nothing downstream holds a None.
    assert silent.max_price_per_unit_inr == config.per_unit_cap_default_inr(
        silent.category
    )


def test_an_assumed_cap_never_rejects_a_product():
    """A hard gate is a promise the buyer made, not one we wrote for them."""
    silent = parse("40 headsets, over-ear, within 12 days")
    results = discovery.run(silent)

    assert results, "no products were even considered, so this proves nothing"
    rejected_on_price = [
        result.product.label
        for result in results
        if "max_price_per_unit_inr" in result.violations
    ]
    assert rejected_on_price == []


def test_a_stated_cap_still_rejects():
    """The other half of the same claim: a real ceiling still does its job."""
    stated = parse("40 headsets, over-ear, max Rs 1,500 each, within 12 days")
    results = discovery.run(stated)

    assert any(
        "max_price_per_unit_inr" in result.violations for result in results
    ), "a ceiling the buyer stated must still exclude products above it"


def test_the_audit_log_says_when_the_price_gate_did_not_run():
    """A log that reports a check it skipped is worse than no log."""
    from agent import audit as audit_module
    from agent.models import TransactionContext

    ctx = TransactionContext(transaction_id="TXN-TEST")
    log = audit_module.AuditLogger(ctx)
    discovery.run(parse("40 headsets, over-ear, within 12 days"), log)

    entry = next(e for e in log.entries() if e.stage == discovery.STAGE_DISCOVERY)
    assert entry.detail["price_gate_applied"] is False
    assert "nothing was rejected on price" in entry.reasoning


# ---------------------------------------------------------------------------
# Cross-cutting: the parser must not have learned to over-reach
# ---------------------------------------------------------------------------

def test_a_dismissal_counts_as_the_buyer_speaking_for_themselves():
    """So the usage-context menu must not appear and overrule it.

    weights.context_needed() stands down whenever the buyer stated a priority.
    "Don't care about price" IS a stated priority now, which means this brief
    stops being asked what the headsets are for. That is correct — their words
    beat our taxonomy — but it is a behaviour change, so it is pinned here.
    """
    parsed = parse("40 headsets, over-ear, within 12 days. Don't care about price.")
    assert weights.context_needed(parsed) is None

    silent = parse("40 headsets, over-ear, within 12 days")
    assert weights.context_needed(silent) is not None


def test_every_extracted_phrase_is_one_config_knows_a_weight_for():
    """The parser may not invent a label stage 2 would then have to guess at."""
    known = set(config.priority_phrase_labels())
    for brief in (
        "40 headsets within 12 days. Don't care about price.",
        "200 chairs in 14 days, cheapest option please",
        "500 boxes in 5 days, reliability is everything",
    ):
        for phrase in parse(brief).stated_priorities.values():
            assert phrase in known


def test_priorities_only_ever_name_a_criterion_we_score_on():
    for brief in (
        "40 headsets within 12 days. Don't care about price.",
        "200 chairs in 14 days, cheapest option please",
    ):
        assert set(parse(brief).stated_priorities) <= set(SOFT_CRITERIA)


# ---------------------------------------------------------------------------
# Choosing the parser — the switch that saves the daily allowance
# ---------------------------------------------------------------------------
# The free tier is a DAILY quota and one brief costs two calls, so a rehearsal
# afternoon can spend the demo's budget. force_offline exists to stop that, and
# the only thing worth testing about it is the negative: that no call is made.
# A switch that still spends the request and throws the answer away would look
# identical on screen.

@pytest.fixture
def with_key(monkeypatch):
    """Pretend a key is configured, so the online path is the one being avoided."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    assert language.is_online() is True


@pytest.fixture
def call_is_a_failure(monkeypatch):
    """Any model call from here on is the bug this section is about."""
    def explode(*_args, **_kwargs):
        raise AssertionError("force_offline still called the model")

    monkeypatch.setattr(language, "_call_gemini", explode)


BRIEF = "5,000 kraft mailer boxes, double-wall, max Rs 22 per unit, within 10 days"


def test_force_offline_makes_no_model_call(with_key, call_is_a_failure):
    """Both stage 0 and stage 1, because a brief costs one call each."""
    scope = language.check_scope(BRIEF, force_offline=True)
    parsed = language.extract_brief(BRIEF, force_offline=True)

    assert scope.source == "offline"
    assert parsed.source == "offline"


def test_the_offline_note_says_we_chose_it_not_that_something_broke(with_key, call_is_a_failure):
    """A deliberate choice must not read as a fault.

    'Rate limit reached' next to a switch the user themselves turned off would
    have them hunting a problem that does not exist.
    """
    note = language.extract_brief(BRIEF, force_offline=True).note

    assert "switched off" in note
    assert "rate limit" not in note
    assert "unavailable" not in note


def test_forcing_offline_changes_who_read_it_and_nothing_else(with_key, call_is_a_failure):
    """The switch is a parser choice, not a behaviour change.

    This is the claim the sidebar caption makes to the user, so it is pinned:
    the Brief that comes out of the forced path is the same object the plain
    offline path produces, field for field.
    """
    through_the_switch = language.extract_brief(BRIEF, force_offline=True).brief

    assert through_the_switch == parse(BRIEF)


def test_no_key_and_chosen_offline_are_told_apart(monkeypatch):
    """Same parser, different reason, and the user is entitled to know which."""
    monkeypatch.setenv("GEMINI_API_KEY", "")

    assert "no API key" in language.extract_brief(BRIEF).note


# ---------------------------------------------------------------------------
# "I have no deadline" is an answer, not a hole
# ---------------------------------------------------------------------------
# The gate used to require a delivery window and accept exactly one shape for
# it: a digit followed by the word "days". A buyer whose honest answer was "no
# rush" got the same question back forever, because app.py appends the reply to
# the brief and re-runs the same check. These tests pin the way out.


@pytest.mark.parametrize("phrase", [
    "no rush",
    "no hurry",
    "no deadline",
    "whenever",
    "take your time",
    "not urgent",
])
def test_a_buyer_with_no_deadline_is_not_asked_for_one(phrase):
    """The complaint, pinned: an open-ended answer has to get past the gate."""
    brief = f"25 headsets, over-ear, max Rs 4,000 each, {phrase}"

    extraction = language._offline_extract(brief)
    assert extraction.delivery_is_open is True
    assert extraction.max_delivery_days is None

    assert language._offline_scope(brief).verdict == "in_scope", (
        f"'{phrase}' says there is no deadline. Asking again for one is the dead "
        f"end this change exists to remove."
    )
    assert parse(brief).max_delivery_days is None


@pytest.mark.parametrize("brief,days", [
    ("25 headsets, over-ear, max Rs 4,000 each, in 2 weeks", 14),
    ("25 headsets, over-ear, max Rs 4,000 each, within 6 weeks", 42),
    ("25 headsets, over-ear, max Rs 4,000 each, within a month", 30),
    ("25 headsets, over-ear, max Rs 4,000 each, in 3 months", 90),
    ("25 headsets, over-ear, max Rs 4,000 each, in a fortnight", 14),
    ("25 headsets, over-ear, max Rs 4,000 each, delivered within 12 days", 12),
])
def test_a_window_can_be_said_in_weeks_or_months(brief, days):
    """"Two weeks" is a delivery window a buyer would actually type."""
    assert parse(brief).max_delivery_days == days


def test_a_stated_number_beats_an_open_ended_phrase():
    """A date and a shrug in one sentence: the date is the constraint."""
    parsed = parse("40 headsets within 12 days, no rush otherwise")

    assert parsed.max_delivery_days == 12


def test_a_window_in_weeks_is_not_mistaken_for_the_order_size():
    """The regression the strip guards against.

    Whatever survives the delivery match is read as the quantity further down,
    so an unstripped "2 weeks" turns a 200-unit order into an order for 2.
    """
    parsed = parse("200 bags of cement, max Rs 400 each, in 2 weeks")

    assert parsed.quantity == 200
    assert parsed.max_delivery_days == 14


def test_dismissing_delivery_speed_is_not_the_same_as_having_no_deadline():
    """Two different facts about two different stages, and both can be stated.

    "We do not care about delivery speed" is a PREFERENCE - it zeroes the
    stage-4 weight. "No rush" is an open DEADLINE - it removes the stage-3 gate.
    A brief can say one without the other, and neither list is allowed to read
    the other's phrases.
    """
    priority_only = parse("40 headsets within 12 days. We do not care about delivery speed.")
    assert priority_only.max_delivery_days == 12
    assert priority_only.stated_priorities.get("delivery") == "does_not_matter"

    deadline_only = parse("40 headsets, over-ear, max Rs 4,000 each, no rush")
    assert deadline_only.max_delivery_days is None
    assert "delivery" not in deadline_only.stated_priorities
