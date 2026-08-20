"""The interface. Four screens over the engine in agent/.

WHAT THIS FILE IS ALLOWED TO DO
-------------------------------
Show things, and pass button presses through to the engine. That is the whole
job.

Every number on every screen is read off an object some stage already produced -
the score from `ScoredProduct`, the overage from `AuthorisationOutcome`, the
timing from `MarketSignal`. This file computes nothing, decides nothing, and
never re-reads a catalog. If a figure shown here could disagree with the audit
log, we would have two answers to one question, and the log is supposed to be
the answer.

WHAT IT LOOKS LIKE, AND WHY
---------------------------
This is a product, not a walkthrough of our own architecture. The buyer is an ops
manager reordering packaging. She has never heard of "stage 4" and never sees the
words. So:

  * the recommendation comes first, as a sentence and a cost - not a table dump
  * timing, price trend and stock cover are chips, not paragraphs
  * a chart carries anything where the SHAPE is the point
  * one primary action per screen
  * every piece of reasoning is one click away in a drill-down, never printed
    by default

That last rule is the important one. The reasoning IS our product - "autonomy the
user can audit" is the whole thesis - so it is all still here. It is earned
rather than shoved: we open the score breakdown deliberately when someone asks,
which lands far better than a wall of captions nobody was asked to read.

THE FOUR SCREENS
----------------
  Request         what the buyer asked for, and what we understood
  Recommendation  what to buy, why, and how the timing looks
  Approval        the one gate where a human decides, and what followed
  Activity        the full trail, replayed from disk

Tabs rather than a wizard, so the trail can be read while the rest is on screen.

STREAMLIT, IN ONE PARAGRAPH
---------------------------
Streamlit re-runs this entire file top to bottom on every click. So nothing about
a transaction can live in a local variable - it all lives in `st.session_state`,
which survives the re-run. The engine is called only from the two handler
functions below; the screens themselves just read whatever is in state. That
split is what stops a re-run from accidentally re-running discovery or paying
twice.
"""

from __future__ import annotations

import streamlit as st

from agent import (
    audit as audit_module,
    authorisation,
    close as close_module,
    config,
    discovery,
    escalation,
    language,
    payment as payment_module,
    ranking,
    signals,
    sources,
    vendor,
    weights as weights_module,
)
from agent.models import TransactionContext, TransactionStatus
from ui import charts, components as ui, theme

DEMO_BRIEF = (
    "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit, "
    "delivered within 10 days. Reliability matters a lot - we got burned last quarter."
)

# Every button below carries an explicit `key`. Tests select on the key, not the
# label, so wording can change without breaking the suite - which it did three
# times before we did this.

# The repeat-order shortcuts, one per category we can actually source. Each is a
# one-line gist short enough to read at a glance; clicking it sends the full
# sentence. Three rather than one because a buying tool that only knows a single
# purchase is a script, and because these three land in different places: the
# chairs are inside the agent's spending authority and the laptops are far
# outside it, so the same engine visibly decides alone and visibly stops.
# (button key, one-line gist, the sentence the button actually sends). The key is
# part of the entry rather than the loop index because the tests select on keys -
# a fourth shortcut inserted at the top must not silently repoint them.
RECENT_REQUESTS: tuple[tuple[str, str, str], ...] = (
    (
        "start_recent",
        "5,000 kraft mailer boxes  ·  max Rs 22/unit  ·  within 10 days",
        DEMO_BRIEF,
    ),
    (
        "start_recent_furniture",
        "12 ergonomic task chairs  ·  max Rs 7,000 each  ·  within 14 days",
        "12 ergonomic task chairs, mesh back, adjustable height, max Rs 7,000 each, "
        "delivered within 14 days. Reliability matters a lot.",
    ),
    (
        "start_recent_laptops",
        "8 developer laptops  ·  max Rs 65,000 each  ·  within 12 days",
        "8 developer laptops, 16GB RAM, 512GB SSD, max Rs 65,000 each, "
        "delivered within 12 days. Reliability matters a lot.",
    ),
)

# Not wired to a button any more - the scope gate runs on whatever is typed
# into the chat box. Kept as the example our tests type, so the demo script
# and the test agree on one sentence.
OFF_TOPIC_EXAMPLE = "What's the weather in Chennai tomorrow?"

# Who the approval is recorded as. The stage-5 audit entry is one of the very
# few written with actor USER - it marks where the agent's authority ended and
# a person's began - so it has to name someone rather than say "the user".
APPROVER = "Meena (ops manager)"

# Keys come from the stage files themselves, so a rename cannot leave a control
# flipping a switch nothing reads any more.
SWITCH_LABELS = {
    vendor.SWITCH_OUT_OF_STOCK: "Vendor is out of stock",
    vendor.SWITCH_PRICE_DRIFT: "Price moved since we looked",
    payment_module.SWITCH_DECLINE_FIRST: "First payment attempt declines",
    payment_module.SWITCH_DECLINE_EVERY: "Every payment attempt declines",
}

_FINISHED = {
    TransactionStatus.COMPLETED,
    TransactionStatus.DECLINED,
    TransactionStatus.EXPIRED,
}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def init_state() -> None:
    """Put every key we read into session_state once, so no screen guesses.

    Streamlit re-runs the file constantly; a missing key is the most common way
    one of these apps dies mid-demo.
    """
    defaults = {
        "messages": [],
        "ctx": None,
        "log": None,
        "brief_note": "",
        "scope_note": "",
        "pending_brief": "",
        "market": None,             # MarketRead - stage 4.5, advisory only
        "auth": None,
        "stage3_escalation": None,
        "confirmation": None,
        "payment": None,
        "summary": None,
        "last_brief": "",          # so a failure can be re-tested in one click
        "switches": config.failure_injection(),
        "source_keys": list(sources.ALL_SOURCE_KEYS),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset() -> None:
    """Clear the order, keeping the demo controls where they were set."""
    st.session_state["messages"] = []
    st.session_state["brief_note"] = ""
    st.session_state["scope_note"] = ""
    st.session_state["pending_brief"] = ""
    for key in ("ctx", "log", "market", "auth", "stage3_escalation",
                "confirmation", "payment", "summary"):
        st.session_state[key] = None


def say(role: str, text: str) -> None:
    st.session_state["messages"].append((role, text))


def run_again(text: str) -> None:
    """Run the same brief as a brand new order.

    Each failure scenario deserves its own transaction id and its own audit
    file - reopening a finished order to try a different outcome would make the
    trail a lie about what happened. So this resets and starts fresh rather than
    rewinding.

    What it removes is only the retyping. Flip a switch, press this, and the same
    brief runs again from scratch under the new conditions.
    """
    reset()
    handle_message(text)


# ---------------------------------------------------------------------------
# The engine calls — the only two functions here that touch agent/
# ---------------------------------------------------------------------------

def handle_message(text: str) -> None:
    """Stages 0 through 5 for one user message. Stops wherever the engine stops.

    Reading this function top to bottom IS the pipeline, which is deliberate - a
    judge asking "what happens when I type a sentence?" should be able to follow
    one screen of code.

    Note the three early returns. Each is a place the engine refuses to continue,
    and none of them is an error: an off-topic message, a brief with a hole in it,
    and a search that found nothing eligible are all designed outcomes.

    ANSWERS ARE JOINED TO THE BRIEF THEY ANSWER
    -------------------------------------------
    When the agent asks its one clarifying question, the reply is usually a
    fragment - "5,000", or "within 10 days". A fragment is not a procurement
    brief, so sending it on its own gets it refused, and the user is told we only
    handle procurement while we are in the middle of asking them about one.

    So while a question is outstanding we keep the brief so far in
    `pending_brief`, and the next message is appended rather than replacing it.
    This is conversation state, so it lives here rather than in the engine.
    """
    say("user", text)

    ctx = st.session_state["ctx"]
    if ctx is None or ctx.status in _FINISHED:
        transaction = TransactionContext(transaction_id=audit_module.new_transaction_id())
        st.session_state["ctx"] = transaction
        st.session_state["log"] = audit_module.AuditLogger(transaction)
        st.session_state["pending_brief"] = ""
        for key in ("market", "auth", "stage3_escalation", "confirmation", "payment", "summary"):
            st.session_state[key] = None

    ctx = st.session_state["ctx"]
    log = st.session_state["log"]

    pending = st.session_state["pending_brief"]
    brief_text = f"{pending} {text}".strip() if pending else text

    # -- should we even start? ---------------------------------------------
    scope = language.check_scope(brief_text, log)
    st.session_state["scope_note"] = scope.note
    if scope.verdict.verdict == "out_of_scope":
        st.session_state["pending_brief"] = ""
        say("agent", scope.verdict.message)
        return
    if scope.verdict.verdict == "incomplete":
        st.session_state["pending_brief"] = brief_text
        say("agent", scope.verdict.message)
        return

    st.session_state["pending_brief"] = ""
    st.session_state["last_brief"] = brief_text

    # -- the sentence becomes numbers --------------------------------------
    parsed = language.extract_brief(brief_text, log)
    st.session_state["brief_note"] = parsed.note
    ctx.brief = parsed.brief
    ctx.weights = weights_module.compute(parsed.brief, log)

    # -- two catalogs, one shape, one gate ---------------------------------
    ctx.status = TransactionStatus.DISCOVERING
    ctx.filter_results = discovery.run(parsed.brief, log, st.session_state["source_keys"])

    if not ctx.eligible:
        outcome = escalation.handle(ctx, escalation.Trigger.NO_ELIGIBLE_MATCH, log)
        st.session_state["stage3_escalation"] = outcome
        say("agent", outcome.headline)
        return

    # -- ranking: pure Python, same answer every run ------------------------
    ctx.ranked = ranking.rank(
        ctx.eligible,
        ctx.weights,
        parsed.brief.max_price_per_unit_inr,
        parsed.brief.max_delivery_days,
        log,
    )
    ctx.status = TransactionStatus.RANKED

    # -- timing: advisory only, and it runs AFTER the ranking is final ------
    # Placed here deliberately. Nothing below reads it, so it cannot influence
    # the authorisation decision that follows. See CLAUDE.md, stage 4.5.
    st.session_state["market"] = signals.read(ctx.ranked, parsed.brief, log)

    # -- may the agent sign for this? --------------------------------------
    auth = authorisation.authorise(ctx, log)
    st.session_state["auth"] = auth

    if auth.within_limit:
        say("agent", auth.headline)
        execute()
    else:
        say("agent", auth.headline)


def execute() -> None:
    """Confirmation, payment and close. Runs after the agent clears itself, or
    after a human approves.

    Each stage can stop the run, and when it does it has already written its own
    audit entry and parked the transaction. This function's only job is to notice
    and stop calling the next one.
    """
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]
    switches = st.session_state["switches"]

    confirmation = vendor.confirm(ctx, log, overrides=switches)
    st.session_state["confirmation"] = confirmation
    if not confirmation.confirmed:
        say("agent", confirmation.headline)
        return

    payment = payment_module.pay(ctx, log, confirmation, overrides=switches)
    st.session_state["payment"] = payment
    if not payment.paid:
        say("agent", payment.headline)
        return

    summary = close_module.close(ctx, log, confirmation, payment)
    st.session_state["summary"] = summary
    say("agent", summary.headline)


# ---------------------------------------------------------------------------
# Sidebar — deliberately quiet
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    """Account-level controls, and the demo switches tucked out of the way.

    The failure switches are collapsed. They are how a judge watches the
    escalation path fire on demand, but they are not something a real buyer has,
    and leaving them open on the main surface would make the app look like a test
    harness.
    """
    with st.sidebar:
        st.markdown("### Procurement")
        ctx = st.session_state["ctx"]
        if ctx is not None:
            st.caption(f"Order {ctx.transaction_id} · {len(ctx.audit)} events")

        if st.button("New order", width="stretch", key="new_order"):
            reset()
            st.rerun()

        st.markdown("")
        st.caption(f"Approval needed above ₹{config.authorisation_limit_inr():,.0f}")

        with st.expander("Suppliers"):
            chosen = []
            for key, adapter in sources.ADAPTERS.items():
                if st.checkbox(adapter.display_name, key in st.session_state["source_keys"],
                               key=f"src_{key}"):
                    chosen.append(key)
            # Never let the pool empty. A run with no suppliers is not a useful
            # demonstration of anything, it just looks broken.
            st.session_state["source_keys"] = chosen or list(sources.ALL_SOURCE_KEYS)

        with st.expander("Demo controls"):
            st.caption("Force a failure to see how the agent responds.")
            switches = dict(st.session_state["switches"])
            for key, label in SWITCH_LABELS.items():
                switches[key] = st.checkbox(label, switches.get(key, False), key=f"sw_{key}")
            st.session_state["switches"] = switches

            # The switches are read when the order is executed, so flipping one
            # while an order is still awaiting approval takes effect on approve -
            # no new order needed. Once an order has COMPLETED it is finished for
            # good, and trying the next scenario means running the brief again.
            if st.session_state["last_brief"]:
                st.caption("")
                if st.button("Run this brief again", width="stretch", key="run_again"):
                    run_again(st.session_state["last_brief"])
                    st.rerun()
                st.caption(
                    "Starts a fresh order with a new reference and its own audit "
                    "file, using the switches above."
                )


# ---------------------------------------------------------------------------
# Screen 1 — Request
# ---------------------------------------------------------------------------

def render_request() -> None:
    """The conversation, and a compact read-back of what we understood."""
    if not st.session_state["messages"]:
        _empty_request_state()
        return

    for role, text in st.session_state["messages"]:
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(text)

    ctx = st.session_state["ctx"]
    if ctx is None or ctx.brief is None:
        return

    ui.rule()
    _brief_readback(ctx)


def _empty_request_state() -> None:
    """What a first-time user sees. One action, and the example spelled out.

    There is deliberately no second button for asking something off-topic. The
    scope check runs on whatever is typed into the box at the bottom, so a
    button for it would be a control that exists only to prove we have a
    feature - which is a demo artefact, not a product. Typing a real question
    into the real box is also the more convincing demonstration.

    The example sentence is printed rather than hidden behind the button. A
    button whose effect you cannot predict is a mystery box, and the sentence
    itself is half of what we are showing off: this much mess in, a decision out.
    """
    st.markdown("")
    ui.hero(
        "Procurement",
        "What do you need to buy?",
        "Type your request in the box below — how many, what specification, "
        "your budget and your deadline.",
    )

    # Repeat-order shortcuts, which is what an ops manager's tool would actually
    # offer: Meena reorders the same few things every quarter. They double as the
    # fast way to start without typing a long sentence live, but they are not
    # framed as demo buttons, because to a real user they would not be.
    #
    # Honest scope: this is a stub with hardcoded entries. There is no request
    # history in this build, and nothing here reads one.
    st.caption("Recent requests")
    for key, label, brief_text in RECENT_REQUESTS:
        if st.button(label, width="content", key=key):
            handle_message(brief_text)
            st.rerun()


def _brief_readback(ctx) -> None:
    """What the agent understood, as chips a buyer can check at a glance.

    Requirements and preferences are shown differently because they DO different
    things: a requirement can disqualify a product, a preference can only change
    its position. Presenting them as one undifferentiated list would hide the
    single most important distinction in the whole system.
    """
    brief = ctx.brief
    palette = theme.active()

    ui.section("What we understood")
    ui.chips([
        (f"{brief.quantity:,} {config.unit_noun(brief.category)}", palette.accent),
        (brief.category, None),
        *[(spec, None) for spec in brief.specs],
        (f"max ₹{brief.max_price_per_unit_inr:.2f}/unit", None),
        (f"within {brief.max_delivery_days} days", None),
    ])
    st.caption("Any product missing one of these is not considered.")

    if ctx.weights:
        st.markdown("")
        ui.section("What you said matters")
        ui.chips([
            (f"{criterion} {weight:.0%}", palette.criterion_colour.get(criterion))
            for criterion, weight in ctx.weights.values.items()
        ])
        st.caption("These decide the order of the results, never who qualifies.")

    with ui.detail("How this was read"):
        note = st.session_state["brief_note"]
        if note:
            st.caption(note)
        st.markdown("**Requirements** — pass or fail. A product missing any of these is excluded.")
        st.json({
            "category": brief.category,
            "quantity": brief.quantity,
            "specs": brief.specs,
            "max_price_per_unit_inr": brief.max_price_per_unit_inr,
            "max_delivery_days": brief.max_delivery_days,
        })
        if ctx.weights:
            st.markdown("**Preferences** — ranking only. Never rejects anything.")
            st.json({
                criterion: {"weight": weight, "from": ctx.weights.sources.get(criterion, "")}
                for criterion, weight in ctx.weights.values.items()
            })


# ---------------------------------------------------------------------------
# Screen 2 — Recommendation
# ---------------------------------------------------------------------------

def render_recommendation() -> None:
    """The decision first, then the evidence, then the arithmetic on request."""
    ctx = st.session_state["ctx"]
    stage3 = st.session_state["stage3_escalation"]

    if stage3 is not None:
        _no_match(stage3)
        return

    if ctx is None or not ctx.ranked:
        st.caption("Nothing to compare yet. Describe what you need to buy.")
        return

    winner = ctx.ranked[0]
    market = st.session_state["market"]
    signal = market.for_product(winner) if market else None
    total = winner.product.order_total_inr(ctx.brief.quantity)

    # -- the recommendation -------------------------------------------------
    ui.hero(
        "Recommended",
        f"{winner.product.name} — ₹{total:,.0f}",
        f"{winner.product.source} · ₹{winner.product.price_per_unit_inr:.2f} per unit · "
        f"arrives in {winner.product.delivery_days} days",
    )

    runner_up_gap = winner.score - ctx.ranked[1].score if len(ctx.ranked) > 1 else None
    ui.figures([
        ("Order total", f"₹{total:,.0f}",
         f"{ctx.brief.quantity:,} {config.unit_noun(ctx.brief.category)}"),
        ("Delivery", f"{winner.product.delivery_days} days",
         f"{ctx.brief.max_delivery_days - winner.product.delivery_days} days inside your deadline"),
        ("Supplier rating", f"{winner.product.reliability_rating:.1f}",
         f"best of the {len(ctx.ranked)} that qualified"),
        ("Match", f"{winner.score:.1f}",
         f"{runner_up_gap:.1f} ahead of next" if runner_up_gap is not None else ""),
    ])

    if signal is not None:
        ui.urgency_chips(signal)

    # -- why it won ---------------------------------------------------------
    ui.rule()
    ui.section("Why this one")
    st.caption(
        f"Each bar is the score, split by what you told us matters. "
        f"{len(ctx.filter_results)} products were checked; {len(ctx.ranked)} qualified."
    )
    st.plotly_chart(charts.score_composition(ctx.ranked), width="stretch",
                    config={"displayModeBar": False})
    ui.chips(list(theme.active().criterion_colour.items()))

    st.markdown("")
    ui.comparison_table(ctx.ranked, market)

    for scored in ctx.ranked:
        with ui.detail(f"Score breakdown — {scored.product.name} ({scored.score})"):
            ui.score_breakdown(scored)

    rejected = [result for result in ctx.filter_results if not result.passed]
    if rejected:
        with ui.detail(f"Not considered ({len(rejected)})"):
            for result in rejected:
                reasons = " · ".join(result.violations.values())
                st.markdown(f"**{result.product.name}** — {reasons}")

    # -- market context -----------------------------------------------------
    _market_section(ctx, market)


def _market_section(ctx, market) -> None:
    """Price and stock over time. Two charts, never one with two axes.

    Overlaying rupees and units on a shared plot would let us imply any
    correlation we liked by choosing the scales. Two charts cannot lie that way.
    """
    products = [scored.product for scored in ctx.ranked]
    price_fig = charts.price_history(products)
    stock_fig = charts.stock_burndown(products, ctx.brief.quantity)

    if price_fig is None and stock_fig is None:
        return

    ui.rule()
    ui.section("Market context")

    left, right = st.columns(2)
    if price_fig is not None:
        with left:
            st.caption("Price per unit")
            st.plotly_chart(price_fig, width="stretch", config={"displayModeBar": False})
    if stock_fig is not None:
        with right:
            st.caption("Stock available")
            st.plotly_chart(stock_fig, width="stretch", config={"displayModeBar": False})

    ui.provenance("Simulated market data — authored for this demo, not a live vendor feed.")

    if market is None:
        return

    with ui.detail("How the timing was worked out"):
        st.caption(
            "Two independent readings per product — how fast stock is leaving, and "
            "which way the price is moving. Whichever is more urgent is the one "
            "shown, and it never changes the ranking or the approval limit."
        )
        for scored in ctx.ranked:
            signal = market.for_product(scored)
            if signal is None:
                continue
            st.markdown(f"**{scored.product.name}** — {signal.headline}")
            if signal.daily_depletion:
                st.caption(
                    f"Leaving stock at {signal.daily_depletion:,.0f} units/day · "
                    f"drops below your {ctx.brief.quantity:,} in "
                    f"{signal.days_until_short:.1f} days · "
                    f"price {signal.price_change_pct:+.1f}% over {signal.observed_days} days"
                )


def _no_match(outcome) -> None:
    """Nothing qualified. Show the near-misses and what it would take to admit them."""
    ui.hero("No match", outcome.headline, variant="escalated")

    if outcome.options:
        ui.section("Closest available")
        for option in outcome.options:
            st.markdown(f"**{option.label}** — {option.note}")
            ui.chips([(text, theme.active().serious) for text in option.violations.values()])

    if outcome.proposed_relaxations:
        ui.section("What would need to change")
        for proposal in outcome.proposed_relaxations:
            st.markdown(f"- {proposal}")
        st.caption("Nothing here has been applied. Change your requirements to proceed.")


# ---------------------------------------------------------------------------
# Screen 3 — Approval
# ---------------------------------------------------------------------------

def render_approval() -> None:
    """The one gate where a human decides, and everything that followed it."""
    ctx = st.session_state["ctx"]
    auth = st.session_state["auth"]

    if ctx is None or auth is None:
        st.caption("Nothing awaiting a decision.")
        return

    # The hero writes its own sentence rather than reusing the engine's headline.
    # The engine writes "Rs" for the log and for systems that cannot be trusted
    # with a rupee sign; the screen uses the symbol. Showing both in one card was
    # the kind of small inconsistency that makes a product look unfinished.
    if auth.within_limit:
        ui.hero(
            "Approved automatically",
            f"₹{auth.order_total_inr:,.0f} — no approval needed",
            f"Within the ₹{auth.authorisation_limit_inr:,.0f} the agent may commit on its own. "
            f"You are being told, not asked.",
        )
    else:
        over = auth.order_total_inr - auth.authorisation_limit_inr
        ui.hero(
            "Your approval needed",
            f"₹{auth.order_total_inr:,.0f} — ₹{over:,.0f} over the limit",
            "Nothing has been ordered. The agent stopped here because this is more "
            "than it may commit without you.",
            variant="escalated",
        )

        ui.figures([
            ("Order total", f"₹{auth.order_total_inr:,.0f}", ""),
            ("Approval limit", f"₹{auth.authorisation_limit_inr:,.0f}", "set by your finance team"),
            ("Over by", f"₹{over:,.0f}", f"{over / auth.authorisation_limit_inr:.1%}"),
        ])

    _approval_actions(ctx, auth)
    _outcome_trail(ctx)


def _approval_actions(ctx, auth) -> None:
    """One primary action, and its opposite. Nothing else competes for the eye."""
    if ctx.status is not TransactionStatus.AWAITING_APPROVAL:
        return

    st.markdown("")
    approve, decline = st.columns([1, 1])
    with approve:
        if st.button("Approve this order", type="primary", width="stretch", key="approve"):
            authorisation.approve(ctx, st.session_state["log"], approver=APPROVER)
            execute()
            st.rerun()
    with decline:
        if st.button("Decline", width="stretch", key="decline"):
            authorisation.decline(ctx, st.session_state["log"],
                                  reason=st.session_state.get("decline_reason", ""),
                                  decliner=APPROVER)
            st.rerun()

    st.caption(
        "Nothing is ordered until you approve. If this is left unanswered it "
        "expires rather than going ahead."
    )


def _outcome_trail(ctx) -> None:
    """What happened after approval: the lock, the payment, the receipt."""
    confirmation = st.session_state["confirmation"]
    payment = st.session_state["payment"]
    summary = st.session_state["summary"]

    if confirmation is None:
        return

    ui.rule()
    ui.section("Order progress")

    if confirmation.confirmed:
        st.markdown(f"**Supplier confirmed** — {confirmation.headline}")
        st.caption(f"Held under {confirmation.lock_reference}")
    else:
        st.markdown(f"**Supplier could not confirm** — {confirmation.headline}")
        if confirmation.escalation is not None:
            _escalation_options(confirmation.escalation)

    if payment is not None:
        st.markdown("")
        if payment.paid:
            st.markdown(f"**Payment taken** — ₹{payment.amount_inr:,.0f}")
        else:
            st.markdown(f"**Payment failed** — {payment.headline}")

        # Retries are worth showing: a first decline followed by a success is the
        # system recovering, and hiding it would make the log look nicer than the
        # run actually was.
        if payment.declines:
            for attempt in payment.attempts:
                st.caption(f"Attempt {attempt.attempt}: {attempt.outcome.value} — {attempt.reason}")
        if payment.escalation is not None:
            _escalation_options(payment.escalation)

    if summary is not None:
        st.markdown("")
        ui.hero("Order placed",
                f"{summary.product_label} — ₹{summary.amount_inr:,.0f}",
                f"{summary.quantity:,} {config.unit_noun(ctx.brief.category)}"
                f" · payment {summary.payment_reference}",
                variant="done")


def _escalation_options(outcome) -> None:
    """Alternatives after a failure, each with the cost of choosing it."""
    if not outcome.options:
        return
    st.caption("Alternatives:")
    for option in outcome.options:
        gap = f" · {option.score_gap:.1f} points behind" if option.score_gap else ""
        st.markdown(f"- **{option.label}** — ₹{option.order_total_inr:,.0f}{gap}. {option.note}")


# ---------------------------------------------------------------------------
# Screen 4 — Activity
# ---------------------------------------------------------------------------

# What each event type is called on screen. The internal names are for the
# JSONL export and for finance systems; a person reading the page gets English.
_EVENT_WORDS = {
    "DECISION": "Decided",
    "ASSUMPTION": "Assumed",
    "ESCALATION": "Asked you",
    "FALLBACK": "Switched",
    "ACTION": "Did",
    "MARKET_SIGNAL": "Noticed",
}


def render_activity() -> None:
    """Everything that happened to this order, in the order it happened."""
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]

    if ctx is None or not ctx.audit:
        st.caption("Nothing has happened yet.")
        return

    ui.section(f"Order {ctx.transaction_id}")
    st.caption(f"{len(ctx.audit)} events · notifying {', '.join(log.notify_list())}")

    for entry in log.entries():
        word = _EVENT_WORDS.get(entry.event_type.value, entry.event_type.value)
        st.markdown(f"**{word}** · {entry.timestamp.strftime('%H:%M:%S')} — {entry.reasoning}")

    ui.rule()

    with ui.detail("The finance record"):
        st.code(log.finance_view(), language="text")

    with ui.detail("Read it back from disk"):
        st.caption(
            "Re-read from the exported file rather than from memory — the log on "
            "disk is what a finance system would receive."
        )
        replayed = audit_module.replay(ctx.transaction_id)
        st.caption(f"{len(replayed)} entries read back from {log.jsonl_path().name}")
        for entry in replayed:
            st.markdown(f"- `{entry.entry_id}` **{entry.event_type.value}** — {entry.reasoning}")

    st.download_button(
        "Download audit trail",
        data=log.jsonl_path().read_text(encoding="utf-8"),
        file_name=log.jsonl_path().name,
        mime="application/x-ndjson",
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Procurement", page_icon="📦", layout="wide")
theme.inject()
init_state()

st.markdown("# Procurement")
theme.brandbar()

render_sidebar()

request_tab, recommendation_tab, approval_tab, activity_tab = st.tabs(
    ["Request", "Recommendation", "Approval", "Activity"]
)

with request_tab:
    render_request()
with recommendation_tab:
    render_recommendation()
with approval_tab:
    render_approval()
with activity_tab:
    render_activity()

# The chat box sits outside the tabs so it is reachable from any screen.
if prompt := st.chat_input("Describe what you need to buy…"):
    handle_message(prompt)
    st.rerun()
