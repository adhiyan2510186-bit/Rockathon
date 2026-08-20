"""The demo path, driven through the real interface.

WHY THIS FILE EXISTS
--------------------
Every other test checks the engine. This one checks the thing a judge actually
looks at. An engine that ranks perfectly behind a screen that throws an exception
on click is a failed demo, and the only way to know is to press the buttons.

So this drives the app end to end - off-topic refusal, brief, ranking, approval,
a declined payment, the retry, and the close - and asserts the screens survive it.

It also holds the PRODUCT BAR down. `test_no_pipeline_vocabulary_on_the_surface`
fails if a stage number or an implementation term appears on the default screen.
That rule is easy to state and easy to erode one caption at a time; a test is
what stops the erosion.

NO NETWORK
----------
The language step is forced offline by clearing the API key. These tests must
give the same answer on a laptop with no key, on a plane, and thirty seconds
after we have burned through the free tier's ten requests a minute.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit.testing.v1", reason="needs Streamlit's test harness")

from pathlib import Path

from streamlit.testing.v1 import AppTest

# Absolute, because AppTest resolves a relative path against the file that calls
# it - which is this one, in tests/, not the repo root.
APP = str(Path(__file__).resolve().parent.parent / "app.py")
TIMEOUT = 120

OFF_TOPIC = "What's the weather in Chennai tomorrow?"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No API key, so the deterministic offline parser runs. No network, ever."""
    monkeypatch.setenv("GEMINI_API_KEY", "")


def _fresh() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.run()
    return app


def _click(app: AppTest, key: str) -> AppTest:
    """Click by KEY, never by label.

    Wording is a design decision that should be free to change. Selecting on it
    made this suite fail three times for reasons that had nothing to do with
    behaviour.
    """
    return app.button(key=key).click().run()


def _surface_text(app: AppTest) -> str:
    """Everything a user can read without opening a drill-down.

    Deliberately excludes expander contents - that is where implementation detail
    is ALLOWED to live, and the whole progressive-disclosure design would be
    pointless if this test refused to let it exist anywhere.
    """
    parts = [str(element.value) for element in list(app.markdown) + list(app.caption)]
    # Drop our own injected stylesheet: CSS class names are not user-facing text.
    return " ".join(part for part in parts if not part.lstrip().startswith("<style>")).lower()


# ---------------------------------------------------------------------------
# The demo path
# ---------------------------------------------------------------------------

def test_app_starts_clean():
    app = _fresh()
    assert not app.exception
    assert len(app.tabs) == 4


def test_off_topic_is_refused_and_the_app_stays_ready():
    """Typed into the real box, because that is the only way in.

    There is deliberately no "ask something off-topic" button in the app - a
    control that exists purely to prove a feature is a demo artefact. The scope
    check runs on whatever anyone types, so that is what this types.
    """
    app = _fresh()
    app.chat_input[0].set_value(OFF_TOPIC).run()

    assert not app.exception
    assert app.session_state["ctx"].brief is None, "an off-topic message must not start an order"
    assert app.session_state["ctx"].ranked == [], "and it must not go looking for products"


def test_off_topic_mid_order_is_refused_without_disturbing_the_order():
    """The scope check runs on every message, wherever you are in the app.

    This is why no dedicated "off-topic" control is needed: there is one way in,
    and it is guarded. An unrelated question during a live order is refused and
    the order is left exactly as it was - not cancelled, not restarted, not
    quietly re-parsed with weather in the brief.
    """
    app = _click(_fresh(), "start_recent")
    before = app.session_state["ctx"]
    ranked_before = [(s.product.name, s.score) for s in before.ranked]

    app.chat_input[0].set_value(OFF_TOPIC).run()
    after = app.session_state["ctx"]

    assert not app.exception
    assert after.transaction_id == before.transaction_id, "the order must survive"
    assert [(s.product.name, s.score) for s in after.ranked] == ranked_before
    assert after.status.value == "awaiting_approval", "still waiting on the same decision"


def test_a_within_limit_order_proceeds_without_asking_anyone():
    """The other half of the authorisation boundary, on screen.

    Every other test in this file drives the packaging brief, which is over the
    limit and therefore always stops for approval. That demonstrates the agent
    refusing to act - but an agent that ALWAYS stops has not demonstrated
    authority, only caution. The furniture brief costs Rs 78,000 against a
    Rs 1,05,000 limit, so the agent buys it and tells the buyer afterwards.

    Same engine, same screen, opposite branch, and no approval button rendered.
    """
    app = _click(_fresh(), "start_recent_furniture")
    assert not app.exception

    state = app.session_state
    assert [(s.product.name, s.score) for s in state["ctx"].ranked] == [
        ("ErgoFlex Task", 62.4), ("Postura Pro", 58.5), ("MeshLite Task", 27.9)
    ]

    outcome = state["auth"]
    assert outcome.within_limit, "Rs 78,000 is inside the limit; nothing should be asked"
    assert outcome.escalation is None
    assert state["ctx"].status.value != "awaiting_approval"


def test_an_order_far_over_the_limit_stops_and_offers_nothing_it_cannot_afford():
    """Eight laptops at Rs 4,64,000 against a Rs 1,05,000 limit.

    The interesting part is not that it escalates - packaging does that too. It
    is that there is no in-limit alternative to fall back on, and the screen has
    to say so rather than showing the cheapest option and letting a reader
    assume it fits. It does not fit: the cheapest is still four times the limit.
    """
    app = _click(_fresh(), "start_recent_laptops")
    assert not app.exception

    state = app.session_state
    assert [(s.product.name, s.score) for s in state["ctx"].ranked] == [
        ("DevBook 14", 70.2), ("CoreStation 15", 58.4), ("ProBook X1", 7.9)
    ]

    outcome = state["auth"]
    assert not outcome.within_limit
    assert outcome.escalation.detail["action_taken"] == "no purchase executed"
    assert outcome.escalation.detail["best_within_limit"] == (
        "none - every eligible option is over the limit"
    )


def _answer_context(app: AppTest, label: str | None) -> AppTest:
    """Answer the one usage question the headsets brief triggers.

    `label=None` takes the "not sure" path. Anything else selects that radio
    option first, because Continue is disabled until something is chosen.
    """
    if label is None:
        return _click(app, "context_skip")
    app.radio(key="context_choice").set_value(label).run()
    return _click(app, "context_apply")


def test_a_brief_that_does_not_say_what_matters_gets_one_question_back():
    """The headsets shortcut is complete and still worth asking about.

    Quantity, spec, cap and deadline are all stated - the scope gate has nothing
    to ask for. What is missing is not a fact, it is a preference, and guessing
    at it would silently pick a winner on our opinion rather than the buyer's.

    Nothing is ranked while the question is open. That ordering is the point: if
    we scored first and offered to re-score afterwards, this order (Rs 97,250,
    inside the limit) would already have been PAID by the time the question
    appeared, and an agent cannot un-buy something.
    """
    app = _click(_fresh(), "start_recent_headsets")
    assert not app.exception

    state = app.session_state
    request = state["pending_context"]

    assert request is not None
    assert request.question == "What will these mainly be used for?"
    assert state["ctx"].brief is not None, "the brief is parsed and kept"
    assert not state["ctx"].ranked, "nothing is scored on a guessed preference"
    assert state["auth"] is None, "and nothing is bought"


def test_answering_the_question_ranks_without_rereading_the_brief():
    """Answering resumes at stage 2. The sentence is never parsed twice.

    Same claim as "approval resumes at stage 6": a preference cannot change what
    was asked for, so re-deriving the brief would be work with a known answer.
    """
    app = _click(_fresh(), "start_recent_headsets")
    brief_before = app.session_state["ctx"].brief

    app = _answer_context(app, "Shared pool, handed around")
    assert not app.exception

    state = app.session_state
    assert state["pending_context"] is None
    assert state["ctx"].brief.context_tag == "shared_pool"
    assert state["ctx"].ranked

    # Everything except the tag came through untouched - not re-parsed into an
    # equal-looking copy, but the same values the first pass produced.
    assert state["ctx"].brief.model_dump(exclude={"context_tag"}) == (
        brief_before.model_dump(exclude={"context_tag"})
    )


def test_two_different_answers_give_two_different_rankings():
    """The widget has to change the answer, or it is theatre.

    Under "all-day calls" the AudioPro is runner-up on its 21-day replacement
    window. Under "kitting out new joiners" the boAt overtakes it, because it
    arrives in three days and a new starter has a start date.
    """
    calls = _answer_context(_click(_fresh(), "start_recent_headsets"),
                            "All-day calls at a desk")
    starters = _answer_context(_click(_fresh(), "start_recent_headsets"),
                               "Kitting out new joiners")

    assert not calls.exception and not starters.exception

    order = lambda app: [s.product.product_id for s in app.session_state["ctx"].ranked]
    assert order(calls)[:3] == ["AMZ-HPH-1302", "OS-AUDIOPRO-OE", "AMZ-HPH-1301"]
    assert order(starters)[:3] == ["AMZ-HPH-1302", "AMZ-HPH-1301", "OS-AUDIOPRO-OE"]


def test_declining_the_question_still_produces_a_decision():
    """"Not sure" is an answer, not a dead end.

    An agent that will not move until you classify your own purchase is worse
    than one with a documented default it admits to using.
    """
    app = _answer_context(_click(_fresh(), "start_recent_headsets"), None)
    assert not app.exception

    state = app.session_state
    assert state["ctx"].brief.context_tag is None
    assert state["ctx"].ranked
    assert state["auth"] is not None


def test_the_cheap_badly_reviewed_option_is_shown_and_is_not_recommended():
    """The headsets brief, and the case the other three shortcuts never reach.

    Packaging, furniture and laptops all reject their nastiest listing on a hard
    gate, which leaves a fair question open: what happens when the bad option
    genuinely qualifies? Here it does. The Boult at Rs 1,299 passes every gate,
    is Rs 1,550 cheaper than the winner, carries 2.8 stars and reviews saying the
    right cup died in a fortnight - and it comes last, on screen, with its price
    advantage intact and visible.

    Asserted under "shared pool", the context that most favours it: price
    weighted 0.40, reliability dropped to 0.20, and it still loses.

    We do not hide it and we do not let it win. That is the whole argument.
    """
    app = _answer_context(_click(_fresh(), "start_recent_headsets"),
                          "Shared pool, handed around")
    assert not app.exception

    state = app.session_state
    ranked = state["ctx"].ranked

    assert ranked[0].product.product_id == "AMZ-HPH-1302"
    assert ranked[-1].product.product_id == "FLK-HPH-2302"
    assert ranked[-1].product.price_per_unit_inr < ranked[0].product.price_per_unit_inr

    # Rs 97,250 against a Rs 1,05,000 limit: the agent acts, and it does not
    # reach for the cheaper option to stay comfortably inside its authority.
    outcome = state["auth"]
    assert outcome.within_limit
    assert outcome.escalation is None


def test_review_text_stays_behind_a_drill_down():
    """Meena sees a decision. She opens the reviews if she wants them.

    The reviews are real content and they belong on the screen somewhere - but
    pasting three strangers' complaints next to a recommendation is a wall of
    text, not a product. They live under "Score breakdown", one click away,
    directly beneath the four numbers that DID decide it.

    Asserted against the SOURCE rather than the rendered text, and that is worth
    knowing: `_surface_text` above says it excludes expander contents, and
    Streamlit's test harness does not actually honour that - it flattens every
    element into one list regardless of nesting. So "the words are not on the
    surface" is not a thing this harness can tell us. What it can tell us is
    where the call sits, which is the design decision we are protecting.
    """
    app = _click(_fresh(), "start_recent_headsets")
    assert not app.exception

    source = Path(APP).read_text(encoding="utf-8")
    detail_block = source.split('with ui.detail(f"Score breakdown')[1].split("\n\n")[0]

    assert "ui.buyer_reviews(scored.product)" in detail_block, (
        "buyer reviews must be rendered inside the score-breakdown drill-down, "
        "not on the default surface"
    )
    assert source.count("ui.buyer_reviews(") == 1, (
        "reviews are shown in exactly one place; a second call site is how they "
        "end up on the recommendation screen by accident"
    )


def test_the_unit_noun_follows_the_category():
    """"12 chairs", not "12 units". The screen says what is being bought."""
    surface = _surface_text(_click(_fresh(), "start_recent_furniture"))
    assert "12 chairs" in surface

    surface = _surface_text(_click(_fresh(), "start_recent_laptops"))
    assert "8 laptops" in surface

    surface = _surface_text(_click(_fresh(), "start_recent_headsets"))
    assert "25 headsets" in surface


def test_a_category_we_do_not_stock_is_declined_by_name():
    """A real buying request for something no vendor sells.

    This is not the off-topic path - "200 bags of cement" IS a purchase, and the
    scope gate correctly lets it through. It fails later, at discovery, and the
    honest answer names what we can actually source. An agent that answers this
    with an empty table has told the user nothing.
    """
    app = _fresh()
    app.chat_input[0].set_value(
        "200 bags of cement, max Rs 400 each, delivered within 7 days"
    ).run()

    assert not app.exception
    assert app.session_state["ctx"].ranked == [], "nothing should be ranked"

    surface = _surface_text(app)
    assert "packaging" in surface and "furniture" in surface and "laptops" in surface


def test_the_whole_demo_path_runs_without_an_exception():
    """Brief in, ranked, over the limit, approved, payment declines once, closes."""
    app = _click(_fresh(), "start_recent")
    assert not app.exception

    state = app.session_state
    assert [(s.product.name, s.score) for s in state["ctx"].ranked] == [
        ("Corusafe DW", 58.0), ("KraftPro DW", 48.7), ("EcoMail DW", 33.7)
    ]
    assert state["auth"].within_limit is False
    assert state["ctx"].status.value == "awaiting_approval"

    app = _click(app, "approve")
    assert not app.exception

    state = app.session_state
    assert state["confirmation"].confirmed
    assert state["payment"].paid
    assert state["payment"].declines == 1, "the scripted demo declines once, then retries"
    assert len(state["payment"].attempts) == 2
    assert state["summary"] is not None
    assert state["ctx"].status.value == "completed"


def test_declining_buys_nothing():
    app = _click(_fresh(), "start_recent")
    app = _click(app, "decline")

    assert not app.exception
    assert app.session_state["ctx"].status.value == "declined"
    assert app.session_state["confirmation"] is None, "a decline must not reach the vendor"


def test_the_timing_signal_reaches_the_screen_without_moving_the_decision():
    app = _click(_fresh(), "start_recent")

    market = app.session_state["market"]
    winner = app.session_state["ctx"].ranked[0]

    assert market.for_product(winner).urgency.value == "act_now"
    # Flagged as urgent, and STILL waiting for a human. The guardrail, on screen.
    assert app.session_state["ctx"].status.value == "awaiting_approval"


# ---------------------------------------------------------------------------
# The product bar
# ---------------------------------------------------------------------------

# Words that would give away that this is a pipeline walkthrough rather than a
# product. CLAUDE.md, "The product bar": Meena has never heard of stage 4.
PIPELINE_VOCABULARY = [
    "stage 0", "stage 1", "stage 2", "stage 3", "stage 4",
    "stage 5", "stage 6", "stage 7", "stage 8",
    "pure python", "normalise", "normalis", "claude.md",
    "hard gate", "soft criteri", "aggregator csv", "direct json",
    "pydantic", "audit logger", "transaction context",
]


def test_no_pipeline_vocabulary_on_the_surface():
    """The default screen is for a buyer. Implementation talk goes in drill-downs."""
    app = _click(_fresh(), "start_recent")
    surface = _surface_text(app)

    found = [word for word in PIPELINE_VOCABULARY if word in surface]
    assert not found, (
        f"pipeline vocabulary reached the default surface: {found}. "
        f"Move it into a drill-down, a docstring, or presentation.txt."
    )


def test_the_simulated_data_label_is_always_present():
    """We never draw a convincing chart and let a reader assume it is real."""
    app = _click(_fresh(), "start_recent")
    assert "simulated market data" in _surface_text(app)
