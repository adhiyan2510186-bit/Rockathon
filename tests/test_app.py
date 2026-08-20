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


def _click(app: AppTest, label: str) -> AppTest:
    matches = [button for button in app.button if button.label == label]
    assert matches, f"no button labelled {label!r}. Present: {[b.label for b in app.button]}"
    return matches[0].click().run()


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
    app = _click(_fresh(), "Try this example")
    before = app.session_state["ctx"]
    ranked_before = [(s.product.name, s.score) for s in before.ranked]

    app.chat_input[0].set_value(OFF_TOPIC).run()
    after = app.session_state["ctx"]

    assert not app.exception
    assert after.transaction_id == before.transaction_id, "the order must survive"
    assert [(s.product.name, s.score) for s in after.ranked] == ranked_before
    assert after.status.value == "awaiting_approval", "still waiting on the same decision"


def test_the_whole_demo_path_runs_without_an_exception():
    """Brief in, ranked, over the limit, approved, payment declines once, closes."""
    app = _click(_fresh(), "Try this example")
    assert not app.exception

    state = app.session_state
    assert [(s.product.name, s.score) for s in state["ctx"].ranked] == [
        ("Corusafe DW", 58.0), ("KraftPro DW", 48.7), ("EcoMail DW", 33.7)
    ]
    assert state["auth"].within_limit is False
    assert state["ctx"].status.value == "awaiting_approval"

    app = _click(app, "Approve this order")
    assert not app.exception

    state = app.session_state
    assert state["confirmation"].confirmed
    assert state["payment"].paid
    assert state["payment"].declines == 1, "the scripted demo declines once, then retries"
    assert len(state["payment"].attempts) == 2
    assert state["summary"] is not None
    assert state["ctx"].status.value == "completed"


def test_declining_buys_nothing():
    app = _click(_fresh(), "Try this example")
    app = _click(app, "Decline")

    assert not app.exception
    assert app.session_state["ctx"].status.value == "declined"
    assert app.session_state["confirmation"] is None, "a decline must not reach the vendor"


def test_the_timing_signal_reaches_the_screen_without_moving_the_decision():
    app = _click(_fresh(), "Try this example")

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
    app = _click(_fresh(), "Try this example")
    surface = _surface_text(app)

    found = [word for word in PIPELINE_VOCABULARY if word in surface]
    assert not found, (
        f"pipeline vocabulary reached the default surface: {found}. "
        f"Move it into a drill-down, a docstring, or presentation.txt."
    )


def test_the_simulated_data_label_is_always_present():
    """We never draw a convincing chart and let a reader assume it is real."""
    app = _click(_fresh(), "Try this example")
    assert "simulated market data" in _surface_text(app)
