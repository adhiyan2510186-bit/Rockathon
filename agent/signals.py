"""Stage 4.5 — the market signal. Timing advice, and nothing more.

WHY THIS FILE EXISTS
--------------------
Stage 4 answers "which product fits the brief best?". It does not answer "is now
a good time to order it?". A buyer needs both. A vendor with three days of stock
left and a price climbing 5% a fortnight is a different proposition from an
identical vendor sitting on six months of inventory, and the ranker — correctly —
cannot see the difference, because neither fact is in the brief.

So this file reads each product's price and stock history and says what it sees.

THE GUARDRAIL, WHICH IS THE WHOLE POINT OF THE FILE
---------------------------------------------------
A market signal may NEVER:

  * move a product past a hard gate
  * add or subtract a single point of score
  * reorder the ranking
  * justify committing spend above the authorisation limit
  * turn an escalation into an auto-proceed

Structurally, not just by promise: this module imports neither `ranking` nor
`authorisation`, it takes the ranked list as read-only input, and it returns a
SEPARATE object keyed by product id. It has no way to write a score even if a
future edit wanted it to. `tests/test_signals.py` asserts that ranking and the
authorisation decision are byte-identical with signals computed and without.

"Buy now" makes a HUMAN decide sooner. It never makes the AGENT decide alone.
An agent that can talk itself past its own spending limit by claiming urgency is
precisely the failure this project exists to rule out — and in our demo the
winner is flagged "order today" AND still escalates for approval, because the
order is over the limit. That is the guardrail working on screen.

WHY WE ANCHOR TO THE FEED, NOT TO THE WALL CLOCK
------------------------------------------------
Every calculation is measured from the LAST OBSERVATION DATE in the series, not
from `date.today()`. If we used today's date, the same catalog would produce a
different signal tomorrow, the golden test would rot overnight, and a demo run
in a different timezone would say something different again. Anchoring to the
data means: same feed in, same signal out, forever. Determinism is the property
we are selling, and it does not get an exception here.

WHERE THE DATA COMES FROM — SAID PLAINLY
----------------------------------------
The price and stock series in data/ are AUTHORED DEMO DATA. They are not
observations of a real market. Every chart the UI draws from them is labelled
"simulated market data", and `MarketSignal.simulated` carries that fact into the
audit log. Drawing a convincing chart and letting a judge assume it was real
would contradict the exact property — an auditable agent — that we are asking
them to believe in.

NO HISTORY MEANS NO SIGNAL
--------------------------
A product whose source publishes no history gets `Urgency.UNKNOWN` and no
claims. Not a neutral-looking guess, not an extrapolation from one point — an
explicit "we do not know". A source that tells us nothing should produce silence.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent import config
from agent.audit import STAGE_SIGNAL, AuditLogger
from agent.models import Brief, Observation, Product, ScoredProduct


class Urgency(str, Enum):
    """How much the timing matters. Four states, deliberately few.

    A scale with ten levels invites an argument about whether something is a 6
    or a 7. These four map onto things a buyer actually does.
    """

    ACT_NOW = "act_now"        # cover runs out inside the "order today" window
    ORDER_SOON = "order_soon"  # cover is short, or the price is climbing
    NO_RUSH = "no_rush"        # plenty of cover, price steady or falling
    UNKNOWN = "unknown"        # this source publishes no history — we say nothing


# How the four states sort, so "take whichever read is more urgent" is one line.
_RANK: dict[Urgency, int] = {
    Urgency.UNKNOWN: 0,
    Urgency.NO_RUSH: 1,
    Urgency.ORDER_SOON: 2,
    Urgency.ACT_NOW: 3,
}

Direction = str  # "rising" | "falling" | "steady"


class MarketSignal(BaseModel):
    """What stage 4.5 found for one product, with the arithmetic left visible.

    Every derived number is on the model rather than baked into `headline`, so
    the UI's drill-down shows the working and a judge can check the sentence
    against the figures that produced it.
    """

    model_config = ConfigDict(frozen=True)

    product_id: str
    product_label: str
    urgency: Urgency
    headline: str = Field(description="One plain sentence. What the user reads first.")
    chips: tuple[str, ...] = Field(
        default=(), description="Short scannable facts for the comparison row."
    )
    driver: str = Field(
        default="",
        description="Which read set the urgency — 'stock', 'price', or '' when unknown.",
    )

    # --- the working ---------------------------------------------------------
    observed_days: int = Field(default=0, description="Span of the series, first to last.")
    as_of: date | None = Field(default=None, description="Last observation date in the feed.")

    daily_depletion: float | None = Field(
        default=None, description="Units leaving stock per day across the window."
    )
    days_until_short: float | None = Field(
        default=None,
        description="Days until stock falls below THIS brief's quantity. The number that "
        "drives urgency — 'until they run out entirely' would flatter a big catalog.",
    )
    stock_cover_days: float | None = Field(
        default=None, description="Days until stock reaches zero. Shown for context only."
    )

    price_change_pct: float | None = Field(
        default=None, description="Percent change across the window, first to last."
    )
    price_direction: Direction | None = Field(default=None)

    simulated: bool = Field(
        default=True,
        description="These series are authored demo data, not a real market feed. Carried "
        "into the audit log so the record never overstates what we observed.",
    )


class MarketRead(BaseModel):
    """Stage 4.5's output for the whole pool: one signal per product, plus context."""

    model_config = ConfigDict(frozen=True)

    signals: dict[str, MarketSignal] = Field(description="product_id -> signal.")
    as_of: date | None = Field(default=None, description="Latest observation across the pool.")
    simulated: bool = True

    def for_product(self, product: Product | ScoredProduct) -> MarketSignal | None:
        """The signal for one product, or None. Accepts either shape for convenience."""
        pid = product.product.product_id if isinstance(product, ScoredProduct) else product.product_id
        return self.signals.get(pid)

    @property
    def most_urgent(self) -> MarketSignal | None:
        """The signal a user should look at first, or None if nothing is known."""
        known = [s for s in self.signals.values() if s.urgency is not Urgency.UNKNOWN]
        if not known:
            return None
        return max(known, key=lambda s: (_RANK[s.urgency], -(s.days_until_short or 1e9)))


# ---------------------------------------------------------------------------
# The two independent reads
# ---------------------------------------------------------------------------

def _span_days(series: Sequence[Observation]) -> int:
    """How many days the series covers. 0 if it cannot support a trend."""
    if len(series) < 2:
        return 0
    return (series[-1].on - series[0].on).days


def _stock_read(product: Product, quantity: int) -> dict:
    """How long until this vendor no longer has enough for THIS order.

    Deliberately not "until they hit zero". A vendor with 50,000 units and a
    buyer who needs 5,000 cares about the moment stock drops below 5,000, not
    the moment it empties. Using the wrong one flatters big catalogs and makes
    the signal useless for exactly the buyer it is meant to help.
    """
    series = product.stock_history
    span = _span_days(series)
    if not span:
        return {}

    depleted = series[0].value - series[-1].value
    daily = depleted / span

    # Flat or restocking. Nothing to warn about, and dividing by <= 0 here would
    # produce a negative "days of cover" that reads like an emergency.
    if daily <= 0:
        return {"daily_depletion": 0.0, "restocking": True}

    current = series[-1].value
    return {
        "daily_depletion": daily,
        "stock_cover_days": current / daily,
        "days_until_short": (current - quantity) / daily,
    }


def _price_read(product: Product) -> dict:
    """Which way the price has moved across the window, if far enough to matter."""
    series = product.price_history
    if _span_days(series) == 0 or series[0].value <= 0:
        return {}

    first, last = series[0].value, series[-1].value
    pct = (last - first) / first * 100
    threshold = config.material_price_move_pct()

    if pct >= threshold:
        direction = "rising"
    elif pct <= -threshold:
        direction = "falling"
    else:
        direction = "steady"

    return {"price_change_pct": pct, "price_direction": direction}


# ---------------------------------------------------------------------------
# Turning two reads into one verdict
# ---------------------------------------------------------------------------

def _urgency_from_stock(days_until_short: float | None) -> tuple[Urgency, str]:
    if days_until_short is None:
        return Urgency.UNKNOWN, ""
    if days_until_short <= 0:
        return Urgency.ACT_NOW, "stock is already below your order quantity"
    if days_until_short <= config.act_now_cover_days():
        return Urgency.ACT_NOW, f"about {days_until_short:.0f} days of cover left"
    if days_until_short <= config.order_soon_cover_days():
        return Urgency.ORDER_SOON, f"about {days_until_short:.0f} days of cover left"
    return Urgency.NO_RUSH, f"about {days_until_short:.0f} days of cover"


def _urgency_from_price(direction: Direction | None, pct: float | None) -> tuple[Urgency, str]:
    if direction is None:
        return Urgency.UNKNOWN, ""
    if direction == "rising":
        return Urgency.ORDER_SOON, f"price up {pct:.1f}% across the window"
    if direction == "falling":
        return Urgency.NO_RUSH, f"price down {abs(pct):.1f}% across the window"
    return Urgency.NO_RUSH, "price steady"


def _signal_for(product: Product, quantity: int) -> MarketSignal:
    """Both reads, combined. The more urgent of the two wins, and we say which."""
    stock = _stock_read(product, quantity)
    price = _price_read(product)

    stock_urgency, stock_words = _urgency_from_stock(stock.get("days_until_short"))
    price_urgency, price_words = _urgency_from_price(
        price.get("price_direction"), price.get("price_change_pct")
    )

    # A vendor that is restocking has a stock read, just not an alarming one.
    if stock.get("restocking"):
        stock_urgency, stock_words = Urgency.NO_RUSH, "stock steady or rising"

    if stock_urgency is Urgency.UNKNOWN and price_urgency is Urgency.UNKNOWN:
        return MarketSignal(
            product_id=product.product_id,
            product_label=product.label,
            urgency=Urgency.UNKNOWN,
            headline="No price or stock history published for this product.",
        )

    # Two independent reads; report whichever is more urgent, and always name it.
    # Reporting an average would let a falling price mask a stock cliff.
    if _RANK[stock_urgency] >= _RANK[price_urgency]:
        urgency, driver, lead = stock_urgency, "stock", stock_words
    else:
        urgency, driver, lead = price_urgency, "price", price_words

    span = max(_span_days(product.stock_history), _span_days(product.price_history))
    as_of = max(
        (s[-1].on for s in (product.stock_history, product.price_history) if s),
        default=None,
    )

    chips = tuple(words for words in (stock_words, price_words) if words)

    return MarketSignal(
        product_id=product.product_id,
        product_label=product.label,
        urgency=urgency,
        headline=_headline(urgency, driver, lead),
        chips=chips,
        driver=driver,
        observed_days=span,
        as_of=as_of,
        daily_depletion=stock.get("daily_depletion"),
        days_until_short=stock.get("days_until_short"),
        stock_cover_days=stock.get("stock_cover_days"),
        price_change_pct=price.get("price_change_pct"),
        price_direction=price.get("price_direction"),
    )


def _headline(urgency: Urgency, driver: str, lead: str) -> str:
    """One sentence a buyer can act on, naming the reason rather than just the verdict."""
    if urgency is Urgency.ACT_NOW:
        return f"Order today — {lead}."
    if urgency is Urgency.ORDER_SOON:
        return f"Order this week — {lead}."
    if urgency is Urgency.NO_RUSH:
        return f"No rush — {lead}."
    return "No price or stock history published for this product."


# ---------------------------------------------------------------------------
# Pool position — how this option sits against the others that qualified
# ---------------------------------------------------------------------------

_POOL_BESTS: tuple[tuple[str, str, bool], ...] = (
    # (attribute, chip wording, lower_is_better)
    ("price_per_unit_inr", "cheapest that qualified", True),
    ("delivery_days", "fastest that qualified", True),
    ("reliability_rating", "most reliable that qualified", False),
    ("replacement_window_days", "longest replacement window", False),
)


def _pool_positions(products: Sequence[Product]) -> dict[str, list[str]]:
    """Which product wins each criterion outright, in ONE pass over the pool.

    Four bests are tracked simultaneously while walking the list once, rather
    than sorting or re-scanning per criterion. O(n x k) with k=4 fixed, instead
    of four sorts.

    Only a STRICTLY unique best earns the chip. "Joint cheapest" is not a
    distinguishing fact, and a badge on two rows tells a reader nothing.
    """
    if not products:
        return {}

    best: dict[str, tuple[float, str, bool]] = {}   # attr -> (value, product_id, unique)

    for product in products:
        for attr, _, lower_is_better in _POOL_BESTS:
            value = getattr(product, attr)
            if attr not in best:
                best[attr] = (value, product.product_id, True)
                continue
            current, _holder, _unique = best[attr]
            if value == current:
                best[attr] = (current, _holder, False)          # now a tie: no chip
            elif (value < current) if lower_is_better else (value > current):
                best[attr] = (value, product.product_id, True)  # new outright winner

    positions: dict[str, list[str]] = {}
    for attr, wording, _ in _POOL_BESTS:
        _value, holder, unique = best[attr]
        if unique:
            positions.setdefault(holder, []).append(wording)
    return positions


# ---------------------------------------------------------------------------
# Stage 4.5, end to end
# ---------------------------------------------------------------------------

def read(
    ranked: Sequence[ScoredProduct],
    brief: Brief,
    audit: AuditLogger | None = None,
) -> MarketRead:
    """Compute a signal for every ranked product and log what we found.

    Takes the ranked list as READ-ONLY input and returns a separate object. This
    function cannot change a score, an order, or an authorisation outcome — it
    has no reference to anything that holds one. See the guardrail at the top.
    """
    products = [scored.product for scored in ranked]
    positions = _pool_positions(products)

    signals: dict[str, MarketSignal] = {}
    for product in products:
        signal = _signal_for(product, brief.quantity)
        extra = positions.get(product.product_id, [])
        if extra:
            signal = signal.model_copy(update={"chips": signal.chips + tuple(extra)})
        signals[product.product_id] = signal

    as_of = max((s.as_of for s in signals.values() if s.as_of), default=None)
    result = MarketRead(signals=signals, as_of=as_of)

    if audit:
        _log(result, audit)

    return result


def _log(result: MarketRead, audit: AuditLogger) -> None:
    """One MARKET_SIGNAL entry, saying plainly that nothing was acted on.

    The record has to be explicit that this changed nothing. An audit line that
    merely notes "stock is low" invites a later reader to wonder whether it
    tipped the decision. Saying "advisory only; ranking and authorisation
    unchanged" closes that question in the log itself.
    """
    flagged = [
        signal for signal in result.signals.values()
        if signal.urgency in (Urgency.ACT_NOW, Urgency.ORDER_SOON)
    ]

    if not flagged:
        reasoning = (
            "Checked price and stock history for every ranked product; none is under "
            "timing pressure. Advisory only — ranking and authorisation unchanged."
        )
    else:
        names = ", ".join(f"{s.product_label} ({s.urgency.value})" for s in flagged)
        reasoning = (
            f"Timing pressure noted on {names}. Advisory only — this did not change "
            f"eligibility, score, ranking, or the authorisation decision."
        )

    audit.market_signal(
        STAGE_SIGNAL,
        reasoning,
        {
            "as_of": result.as_of.isoformat() if result.as_of else None,
            "data_provenance": "simulated market data (authored demo series, not a live feed)",
            "advisory_only": True,
            "signals": {
                signal.product_label: {
                    "urgency": signal.urgency.value,
                    "driver": signal.driver,
                    "days_until_short": signal.days_until_short,
                    "price_change_pct": signal.price_change_pct,
                }
                for signal in result.signals.values()
            },
        },
    )
