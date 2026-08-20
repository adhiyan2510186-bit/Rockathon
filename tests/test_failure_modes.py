"""Each demo failure switch, driven through the real interface.

WHY THIS FILE EXISTS
--------------------
The switches are how a judge watches the escalation path fire on demand instead
of taking our word for it. So each one needs to actually do something different,
and we need to know that at a laptop rather than on stage.

There is also a rehearsal value here: this file IS the running order for the
failure part of the demo. Each test is one thing to show, in the order we would
show it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit.testing.v1", reason="needs Streamlit's test harness")

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent import payment as payment_module, vendor

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No API key, so the deterministic offline parser runs. No network, ever."""
    monkeypatch.setenv("GEMINI_API_KEY", "")


def _run(switches: dict[str, bool] | None = None) -> AppTest:
    """Start the app with the given failure switches, then run the demo brief.

    The switches are flipped through the sidebar CHECKBOXES, not by writing the
    session dict. That is not pedantry: the checkboxes own their state, so a
    direct write to `switches` is silently overwritten by the widget on the very
    next run. Flipping the checkbox is both what a judge actually does and the
    only thing that sticks.
    """
    app = AppTest.from_file(APP, default_timeout=120)
    app.run()

    for name, value in (switches or {}).items():
        app.checkbox(key=f"sw_{name}").set_value(value)
    if switches:
        app.run()

    [b for b in app.button if b.label == "Try this example"][0].click().run()
    return app


def _approve(app: AppTest) -> AppTest:
    return [b for b in app.button if b.label == "Approve this order"][0].click().run()


# ---------------------------------------------------------------------------
# The scripted run
# ---------------------------------------------------------------------------

def test_the_scripted_demo_declines_once_then_succeeds():
    """The default switches: payment fails once, retries, closes. No drama."""
    app = _approve(_run())
    state = app.session_state

    assert not app.exception
    assert state["payment"].paid
    assert state["payment"].declines == 1
    assert state["ctx"].status.value == "completed"


# ---------------------------------------------------------------------------
# Confirmation failures — the vendor changed its mind
# ---------------------------------------------------------------------------

def test_out_of_stock_at_confirmation_escalates_rather_than_substituting():
    """#1 leads #2 by 9.3 points, which is wider than the 5-point threshold.

    A wide gap means the runner-up is a meaningfully worse fit for what the buyer
    actually asked for, so the agent refuses to swap silently and asks.
    """
    app = _approve(_run({vendor.SWITCH_OUT_OF_STOCK: True}))
    confirmation = app.session_state["confirmation"]

    assert not app.exception
    assert not confirmation.confirmed
    assert confirmation.escalation is not None
    assert app.session_state["payment"] is None, "nothing may be paid for after a failed lock"


def test_price_drift_at_confirmation_is_caught_before_any_money_moves():
    """The re-read at confirmation exists precisely to catch this."""
    app = _approve(_run({vendor.SWITCH_PRICE_DRIFT: True}))
    confirmation = app.session_state["confirmation"]

    assert not app.exception
    assert not confirmation.confirmed
    assert app.session_state["payment"] is None


# ---------------------------------------------------------------------------
# Payment failures
# ---------------------------------------------------------------------------

def test_a_dead_card_stops_after_the_retry_rather_than_looping():
    """Both attempts decline, so the agent stops trying and escalates.

    Retrying forever is how an agent turns one failed payment into forty.
    """
    app = _approve(_run({payment_module.SWITCH_DECLINE_EVERY: True}))
    payment = app.session_state["payment"]

    assert not app.exception
    assert not payment.paid
    assert len(payment.attempts) == 2, "one retry, then stop"
    assert app.session_state["summary"] is None, "nothing may close without payment"
    assert app.session_state["ctx"].status.value != "completed"


def test_a_clean_card_takes_one_attempt():
    app = _approve(_run({payment_module.SWITCH_DECLINE_FIRST: False}))
    payment = app.session_state["payment"]

    assert not app.exception
    assert payment.paid
    assert len(payment.attempts) == 1


# ---------------------------------------------------------------------------
# The human gate
# ---------------------------------------------------------------------------

def test_declining_reaches_no_vendor_at_all():
    app = _run()
    app = [b for b in app.button if b.label == "Decline"][0].click().run()

    assert not app.exception
    assert app.session_state["ctx"].status.value == "declined"
    assert app.session_state["confirmation"] is None


def test_every_run_gets_its_own_transaction_and_its_own_audit_file():
    """Re-running a brief starts a fresh order rather than reopening a finished one.

    Reopening would make the audit trail a lie about what happened.
    """
    app = _run()
    first = app.session_state["ctx"].transaction_id

    app = _approve(app)
    assert app.session_state["ctx"].status.value == "completed"

    # "Run this brief again" - the sidebar control, without the retyping.
    [b for b in app.button if b.label == "Run this brief again"][0].click().run()
    second = app.session_state["ctx"].transaction_id

    assert not app.exception
    assert second != first, "a re-run must be a new order, not a rewind"
    assert app.session_state["ctx"].ranked, "and it must run the same brief"
