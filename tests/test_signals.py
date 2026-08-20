"""Stage 4.5 — the market signal, and above all the guardrail on it.

The arithmetic tests here matter. The guardrail tests matter more.

CLAUDE.md: "urgency changes priority, never authority." An agent that can talk
itself past its own spending limit by claiming urgency is the exact failure this
project exists to rule out, and it is the first thing a sharp judge will probe.
So `test_signals_change_nothing_about_the_decision` is the most important test
in this file: it runs the full pipeline twice, once with signals computed and
once without, and asserts the ranking and the authorisation outcome are
identical.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from agent import audit as audit_module
from agent import authorisation, signals, sources
from agent.models import EventType, Observation, Product, TransactionContext
from agent.signals import Urgency
from test_ranking import build_pipeline


@pytest.fixture
def pipeline():
    return build_pipeline()


def _product(pid: str) -> Product:
    return next(p for p in sources.fetch() if p.product_id == pid)


# ---------------------------------------------------------------------------
# THE GUARDRAIL — the tests that matter most
# ---------------------------------------------------------------------------

def test_signals_change_nothing_about_the_decision(pipeline):
    """Same brief, run with and without stage 4.5. The decision must not move."""
    brief, _weights, _results, ranked = pipeline

    before_order = [(s.product.product_id, s.score) for s in ranked]

    read = signals.read(ranked, brief)

    after_order = [(s.product.product_id, s.score) for s in ranked]

    assert after_order == before_order, (
        "stage 4.5 reordered or rescored the ranking — this is the guardrail failing"
    )
    # And it genuinely had something to say, or the test proves nothing.
    assert any(s.urgency is Urgency.ACT_NOW for s in read.signals.values())


def test_urgency_does_not_move_the_authorisation_limit(pipeline):
    """The demo's punchline: the winner is 'order today' AND still escalates.

    Corusafe is flagged ACT_NOW and its order total is over the authorisation
    limit. If urgency could buy authority, this is where it would happen.
    """
    brief, _weights, _results, ranked = pipeline
    tmp = Path(tempfile.mkdtemp())

    def authorise_with(compute_signals: bool):
        ctx = TransactionContext(transaction_id="TXN-GUARD", brief=brief)
        ctx.ranked = ranked
        log = audit_module.AuditLogger(ctx, export_dir=tmp)
        if compute_signals:
            signals.read(ranked, brief, log)
        return authorisation.authorise(ctx, log)

    without = authorise_with(False)
    with_signals = authorise_with(True)

    assert with_signals.decision == without.decision
    assert with_signals.order_total_inr == without.order_total_inr

    # The signal fired, and the agent still refused to act alone.
    read = signals.read(ranked, brief)
    winner = read.for_product(ranked[0])
    assert winner.urgency is Urgency.ACT_NOW
    assert with_signals.decision == authorisation.Decision.ESCALATED


def test_signals_module_cannot_reach_the_decision_makers():
    """Structural, not a promise: signals.py imports neither ranking nor authorisation."""
    source = (Path(__file__).parent.parent / "agent" / "signals.py").read_text(encoding="utf-8")
    assert "import ranking" not in source
    assert "import authorisation" not in source
    assert "from agent.ranking" not in source
    assert "from agent.authorisation" not in source


def test_the_audit_entry_says_it_changed_nothing(pipeline):
    brief, _weights, _results, ranked = pipeline
    ctx = TransactionContext(transaction_id="TXN-SIGLOG", brief=brief)
    log = audit_module.AuditLogger(ctx, export_dir=Path(tempfile.mkdtemp()))

    signals.read(ranked, brief, log)
    entry = ctx.audit[-1]

    assert entry.event_type is EventType.MARKET_SIGNAL
    assert entry.detail["advisory_only"] is True
    assert "simulated" in entry.detail["data_provenance"]
    assert "Advisory only" in entry.reasoning
    # Advice is not a money event — finance is not copied on it.
    assert entry.notify == ["requester"]


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------

def test_corusafe_stock_read():
    """60,000 -> 20,000 across 8 days = 5,000/day. 15,000 spare over a 5,000 order = 3 days."""
    signal = signals._signal_for(_product("PH-CORUSAFE-DW"), quantity=5000)

    assert signal.daily_depletion == pytest.approx(5000.0)
    assert signal.days_until_short == pytest.approx(3.0)
    assert signal.stock_cover_days == pytest.approx(4.0)
    assert signal.urgency is Urgency.ACT_NOW
    assert signal.driver == "stock"


def test_cover_is_measured_against_the_order_not_against_zero():
    """A vendor with 20,000 units hits zero in 4 days but fails a 5,000 order in 3.

    Using cover-to-zero would report 4 days and quietly flatter every large
    catalog. The buyer's deadline is the earlier one.
    """
    signal = signals._signal_for(_product("PH-CORUSAFE-DW"), quantity=5000)
    assert signal.days_until_short < signal.stock_cover_days


def test_a_bigger_order_runs_short_sooner():
    small = signals._signal_for(_product("PH-CORUSAFE-DW"), quantity=1000)
    large = signals._signal_for(_product("PH-CORUSAFE-DW"), quantity=15000)

    assert small.days_until_short > large.days_until_short
    assert large.urgency is Urgency.ACT_NOW


def test_price_trend_is_measured_across_the_window():
    """Corusafe: 20.90 -> 21.90 is +4.78%, past the 3% materiality threshold."""
    signal = signals._signal_for(_product("PH-CORUSAFE-DW"), quantity=5000)
    assert signal.price_change_pct == pytest.approx(4.7847, abs=0.001)
    assert signal.price_direction == "rising"


def test_a_small_price_move_is_called_noise_not_a_trend():
    """KraftPro drifts -1.4%, under the 3% threshold. We say 'steady', not 'falling'."""
    signal = signals._signal_for(_product("PH-KRAFTPRO-DW"), quantity=5000)
    assert signal.price_direction == "steady"
    assert signal.urgency is Urgency.NO_RUSH


def test_the_cheapest_option_is_the_least_urgent():
    """EcoMail: 90 days of cover and a falling price. The demo's contrast case."""
    signal = signals._signal_for(_product("BB-ECOMAIL-DW"), quantity=5000)
    assert signal.urgency is Urgency.NO_RUSH
    assert signal.price_direction == "falling"


def test_stock_already_below_the_order_is_act_now():
    """CraftMail has 3,000 left against a 5,000 order — already short, not 'soon'."""
    signal = signals._signal_for(_product("BB-CRAFTMAIL-DW"), quantity=5000)
    assert signal.days_until_short <= 0
    assert signal.urgency is Urgency.ACT_NOW
    assert "already below" in signal.headline


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------

def test_no_history_means_no_signal():
    """A source that publishes nothing produces silence, not a neutral-looking guess."""
    bare = _product("PH-CORUSAFE-DW").model_copy(update={"price_history": (), "stock_history": ()})
    signal = signals._signal_for(bare, quantity=5000)

    assert signal.urgency is Urgency.UNKNOWN
    assert signal.days_until_short is None
    assert signal.price_change_pct is None
    assert signal.chips == ()


def test_one_data_point_is_not_a_trend():
    single = _product("PH-CORUSAFE-DW").model_copy(
        update={"price_history": (Observation(on=date(2026, 8, 19), value=21.90),),
                "stock_history": (Observation(on=date(2026, 8, 19), value=20000),)}
    )
    assert signals._signal_for(single, quantity=5000).urgency is Urgency.UNKNOWN


def test_restocking_never_reads_as_an_emergency():
    """Stock going UP must not produce a negative 'days of cover' that looks urgent."""
    rising = _product("PH-CORUSAFE-DW").model_copy(
        update={"stock_history": (
            Observation(on=date(2026, 8, 11), value=10000),
            Observation(on=date(2026, 8, 19), value=40000),
        )}
    )
    signal = signals._signal_for(rising, quantity=5000)

    assert signal.days_until_short is None
    assert signal.urgency is not Urgency.ACT_NOW


# ---------------------------------------------------------------------------
# Determinism and provenance
# ---------------------------------------------------------------------------

def test_anchored_to_the_feed_not_to_today(pipeline):
    """Same catalog must give the same signal tomorrow, and in another timezone."""
    brief, _weights, _results, ranked = pipeline
    read = signals.read(ranked, brief)

    assert read.as_of == date(2026, 8, 19)   # the last observation in data/, not today


def test_running_it_twice_gives_the_same_answer(pipeline):
    brief, _weights, _results, ranked = pipeline
    assert signals.read(ranked, brief) == signals.read(ranked, brief)


def test_every_signal_declares_it_is_simulated(pipeline):
    brief, _weights, _results, ranked = pipeline
    read = signals.read(ranked, brief)

    assert read.simulated is True
    assert all(s.simulated for s in read.signals.values())


def test_the_feed_never_contradicts_itself():
    """The last observation must equal the product's current price and stock.

    A history whose final point disagrees with the live figure would make every
    derived signal meaningless — and it is the kind of drift that creeps into
    authored demo data the moment someone edits one number and not the other.
    """
    for product in sources.fetch():
        if product.price_history:
            assert product.price_history[-1].value == pytest.approx(product.price_per_unit_inr), product.name
        if product.stock_history:
            assert product.stock_history[-1].value == pytest.approx(product.available_quantity), product.name


# ---------------------------------------------------------------------------
# Pool position
# ---------------------------------------------------------------------------

def test_pool_chips_go_only_to_an_outright_winner(pipeline):
    """'Joint cheapest' is not a distinguishing fact, so nobody gets the badge."""
    _brief, _weights, _results, ranked = pipeline
    products = [s.product for s in ranked]

    positions = signals._pool_positions(products)
    cheapest = min(products, key=lambda p: p.price_per_unit_inr)
    assert "cheapest that qualified" in positions[cheapest.product_id]

    tied = [products[0], products[0].model_copy(update={"product_id": "CLONE"})]
    assert signals._pool_positions(tied) == {}
