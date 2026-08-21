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

from typing import Any, NamedTuple

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
from agent.models import FieldStatus, TransactionContext, TransactionStatus
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
# sentence. Four rather than one because a buying tool that only knows a single
# purchase is a script, and because these four land in different places: the
# chairs and the headsets are inside the agent's spending authority and the
# laptops are far outside it, so the same engine visibly decides alone and
# visibly stops. The headsets add two cases none of the others cover — the cheap,
# badly reviewed option that passes every hard gate and loses on the ranking
# anyway, and the brief that is complete but does not say what matters, so the
# agent asks one question back before it ranks anything.
# (button key, one-line gist, the sentence the button actually sends). The key is
# part of the entry rather than the loop index because the tests select on keys -
# a fourth shortcut inserted at the top must not silently repoint them.
RECENT_REQUESTS: tuple[tuple[str, str, str], ...] = (
    (
        "start_recent",
        "**5,000 kraft mailer boxes**  \nmax Rs 22/unit · within 10 days",
        DEMO_BRIEF,
    ),
    (
        "start_recent_furniture",
        "**12 ergonomic task chairs**  \nmax Rs 7,000 each · within 14 days",
        "12 ergonomic task chairs, mesh back, adjustable height, max Rs 7,000 each, "
        "delivered within 14 days. Reliability matters a lot.",
    ),
    (
        "start_recent_laptops",
        "**8 developer laptops**  \nmax Rs 65,000 each · within 12 days",
        "8 developer laptops, 16GB RAM, 512GB SSD, max Rs 65,000 each, "
        "delivered within 12 days. Reliability matters a lot.",
    ),
    (
        "start_recent_headsets",
        "**25 noise-cancelling headsets**  \nmax Rs 4,000 each · within 12 days",
        # Deliberately says nothing about what matters. It is a complete,
        # buyable brief that still leaves the most important question open, and
        # it is the one shortcut that makes the agent ask something back.
        "25 wireless noise-cancelling headsets, over-ear, max Rs 4,000 each, "
        "delivered within 12 days.",
    ),
)

# Not wired to a button any more - the scope gate runs on whatever is typed
# into the chat box. Kept as the example our tests type, so the demo script
# and the test agree on one sentence.
OFF_TOPIC_EXAMPLE = "What's the weather in Chennai tomorrow?"

# Who the approval is recorded as. The stage-5 audit entry is one of the very
# few written with actor USER - it marks where the agent's authority ended and
# a person's began. Deliberately generic: the record says a person approved,
# without pretending we know which person the signed-in operator is.
APPROVER = "User"

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

# Who the sidebar says is signed in. The same string the approval is recorded
# under - see APPROVER above. Spelling it once means the panel and the audit
# entry can never disagree about who acted.
ACCOUNT_NAME = APPROVER


class PastOrder(NamedTuple):
    """One finished order, kept so its record can be reopened.

    It holds the transaction context and its logger rather than a summary of
    them. That is the whole trick: reopening a past order shows the SAME record
    the live screen showed, rendered by the same function, because it is the same
    objects. A second, prettier copy of an order's figures kept for a sidebar is
    exactly how a record ends up disagreeing with itself.

    `summary` rides along because the paid price lives there and not on the
    context - the screen needs what was actually charged, not what was quoted.

    Nothing here is written to disk on our account. The JSONL export was already
    written the moment each event happened, so a past order in this list is a
    pointer to a run, not a second store of it. Close the app and the list goes;
    the audit files stay exactly where they were.
    """

    transaction_id: str
    item: str
    line: str
    ctx: Any
    log: Any
    summary: Any


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
        "pending_context": None,    # ContextRequest - the one stage-2 question
        "market": None,             # MarketRead - stage 4.5, advisory only
        "auth": None,
        "stage3_escalation": None,
        "confirmation": None,
        "payment": None,
        "summary": None,
        "last_brief": "",          # so a failure can be re-tested in one click
        # Finished orders from this session, newest first, and which one is open
        # on screen. Deliberately session-scoped: closing the app clears them.
        "history": [],
        "viewing": None,           # a transaction id, when a past order is open
        "switches": config.failure_injection(),
        "source_keys": list(sources.ALL_SOURCE_KEYS),
        # Whether to spend a model call on the next brief. Starts wherever
        # config.yaml says; the sidebar switch moves it for the session.
        "use_model": config.use_model_default(),
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
    # A widget's value outlives the order it was typed for unless it is cleared
    # here, and a reason typed against last week's chairs must not be attached to
    # this week's boxes.
    st.session_state["decline_reason"] = ""
    # A past order being read is about a different transaction, so it does not
    # survive starting a new one. `history` deliberately DOES survive - it is the
    # account's record of the session, not part of the order being cleared.
    st.session_state["viewing"] = None
    for key in ("ctx", "log", "market", "auth", "stage3_escalation",
                "confirmation", "payment", "summary", "pending_context"):
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

    # Typing is about the order in hand, so reading a past one ends here.
    st.session_state["viewing"] = None

    ctx = st.session_state["ctx"]
    if ctx is None or ctx.status in _FINISHED:
        transaction = TransactionContext(transaction_id=audit_module.new_transaction_id())
        st.session_state["ctx"] = transaction
        st.session_state["log"] = audit_module.AuditLogger(transaction)
        st.session_state["pending_brief"] = ""
        for key in ("market", "auth", "stage3_escalation", "confirmation",
                    "payment", "summary", "pending_context"):
            st.session_state[key] = None

    ctx = st.session_state["ctx"]
    log = st.session_state["log"]

    pending = st.session_state["pending_brief"]
    brief_text = f"{pending} {text}".strip() if pending else text

    # -- should we even start? ---------------------------------------------
    # One flag, read once and passed to both language calls, so a single brief
    # can never be half-read by the model and half by the word matcher.
    offline = not st.session_state["use_model"]

    scope = language.check_scope(brief_text, log, force_offline=offline)
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
    parsed = language.extract_brief(brief_text, log, force_offline=offline)
    st.session_state["brief_note"] = parsed.note
    ctx.brief = parsed.brief

    # -- one question about what these are FOR, if the category asks one ----
    # Stopping here rather than after ranking is the whole point. If we scored
    # first and offered to re-score afterwards, an order inside the spending
    # limit would already have been PAID by the time the question appeared. An
    # agent cannot un-buy something because the buyer clarified their preference.
    request = weights_module.context_needed(parsed.brief)
    if request is not None:
        st.session_state["pending_context"] = request
        say("agent", request.question)
        return

    rank_and_authorise()


def apply_context(tag: str | None) -> None:
    """The user answered the usage question (or declined). Carry on from stage 2.

    `tag=None` is the "not sure" path, and it is a first-class answer rather than
    a dead end: the documented category default applies and the audit log records
    it as an ASSUMPTION. Silence is never quietly converted into a preference.

    Note what this function does NOT do. It does not re-read the sentence, does
    not call the language model, and does not re-run the scope gate. The brief
    was parsed once and is sitting in the transaction context; answering a
    question about preference cannot change what was asked for, so re-deriving it
    would be work with a known answer. Same reason approving an order resumes at
    stage 6 instead of starting over.
    """
    ctx = st.session_state["ctx"]
    if ctx is None or ctx.brief is None:
        return

    ctx.brief = ctx.brief.model_copy(update={"context_tag": tag})
    st.session_state["pending_context"] = None

    if tag:
        say("user", config.context_label(ctx.brief.category, tag))
    else:
        say("user", "Not sure — use your default.")

    rank_and_authorise()


def rank_and_authorise() -> None:
    """Stages 2 through 5, from a brief that is already parsed and settled.

    Split out of handle_message because there are now two ways in — a brief that
    needed no question, and one that just had its question answered — and they
    must run the identical pipeline. Two copies of these thirty lines is how the
    context path quietly stops matching the normal path.
    """
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]
    brief = ctx.brief

    ctx.weights = weights_module.compute(brief, log)

    # -- catalogs, one shape, one gate -------------------------------------
    ctx.status = TransactionStatus.DISCOVERING
    ctx.filter_results = discovery.run(brief, log, st.session_state["source_keys"])

    if not ctx.eligible:
        outcome = escalation.handle(ctx, escalation.Trigger.NO_ELIGIBLE_MATCH, log)
        st.session_state["stage3_escalation"] = outcome
        say("agent", outcome.headline)
        return

    # -- ranking: pure Python, same answer every run ------------------------
    # The price cap does double duty: stage 3 filters on it, and stage 4 measures
    # the price score against it (ranking._margin). Those two jobs used to be the
    # same number for the same reason. They are not any more — stage 3 now
    # declines to filter on a cap we invented — so it is worth saying which job
    # this line is doing. This is the YARDSTICK, not the gate.
    #
    # The declared category ceiling stays the yardstick even when it is ours
    # rather than the buyer's, and that is deliberate. We tried measuring against
    # the dearest product that qualified instead, and one Rs 59,900 outlier in a
    # pool of Rs 3,000 headsets squashed every sane product into the same 0.97
    # and deleted price from the ranking. A documented category ceiling gives
    # honest separation among the products a buyer would actually consider, and
    # lets the outlier score zero, which is what it deserves.
    #
    # The fallback below is for the one case that makes the yardstick meaningless:
    # nothing in the pool is under it at all, so every product would tie on zero.
    scoring_cap = brief.max_price_per_unit_inr
    if (
        brief.field_status.get("max_price_per_unit_inr") is FieldStatus.ASSUMED
        and all(p.price_per_unit_inr >= scoring_cap for p in ctx.eligible)
    ):
        scoring_cap = max(p.price_per_unit_inr for p in ctx.eligible)

    ctx.ranked = ranking.rank(
        ctx.eligible,
        ctx.weights,
        scoring_cap,
        brief.max_delivery_days,
        log,
    )
    ctx.status = TransactionStatus.RANKED

    # -- timing: advisory only, and it runs AFTER the ranking is final ------
    # Placed here deliberately. Nothing below reads it, so it cannot influence
    # the authorisation decision that follows. See CLAUDE.md, stage 4.5.
    st.session_state["market"] = signals.read(ctx.ranked, brief, log)

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
# Past orders — the session's own record
# ---------------------------------------------------------------------------

def _remember_finished_order() -> None:
    """File the current order under past orders once it has finished.

    Called once at the top of the sidebar, which is the only place that runs on
    every single re-run whatever screen is open. Doing it here rather than at the
    end of `execute()` means no path can finish an order and forget to file it -
    a decline, an expiry and a completed purchase all arrive at the same check.

    Terminal states only, and each id filed once. An order still awaiting a
    person is not a past order, and a list that grew a duplicate row every time
    Streamlit re-ran the file would be unusable within a minute.
    """
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]
    if ctx is None or log is None or ctx.status not in _FINISHED:
        return
    if any(order.transaction_id == ctx.transaction_id
           for order in st.session_state["history"]):
        return

    summary = st.session_state["summary"]
    chosen = ctx.selected or (ctx.ranked[0] if ctx.ranked else None)

    # What was bought, and what happened. A declined order says so plainly -
    # a list that only shows purchases would quietly lose the runs where the
    # answer was no, and those are the ones worth being able to point at.
    if summary is not None:
        line = f"₹{summary.amount_inr:,.0f} · Ordered"
    elif ctx.status is TransactionStatus.DECLINED:
        line = "Declined · nothing bought"
    else:
        line = "Expired · nothing bought"

    st.session_state["history"].insert(0, PastOrder(
        transaction_id=ctx.transaction_id,
        item=chosen.product.name if chosen is not None else "Nothing chosen",
        line=line,
        ctx=ctx,
        log=log,
        summary=summary,
    ))


def _open_past_order() -> PastOrder | None:
    """The past order currently on screen, if the sidebar opened one."""
    viewing = st.session_state["viewing"]
    if not viewing:
        return None
    for order in st.session_state["history"]:
        if order.transaction_id == viewing:
            return order
    return None


def _past_orders_panel() -> None:
    """The list itself. Each row opens that order's own record.

    These are real orders from this session, not a stub - the item, the amount
    and the reference are read off the transaction the engine actually ran. That
    is the difference between this list and the reorder shortcuts on the opening
    screen, which are starting points we wrote by hand.
    """
    history = st.session_state["history"]
    if not history:
        return

    st.caption("Your orders")
    for order in history:
        if st.button(
            f"**{order.item}**  \n{order.transaction_id} · {order.line}",
            width="stretch",
            key=f"past_{order.transaction_id}",
        ):
            st.session_state["viewing"] = order.transaction_id
            st.rerun()


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
    _remember_finished_order()

    with st.sidebar:
        # No heading here. The product's name is already in the page header two
        # inches away, and a sidebar that repeats it is a second title competing
        # with the first. The account block is not a heading either - it says
        # whose panel this is, which is a different job.
        theme.sidebar_account(
            ACCOUNT_NAME,
            f"Approves above ₹{config.authorisation_limit_inr():,.0f}",
        )

        ctx = st.session_state["ctx"]
        # "This order" means the one on screen, so the count is dropped while a
        # past order is open - otherwise the panel counts one order's events
        # beside another order's record, and one of the two numbers is a lie.
        # The pill below stays either way: it is a call to action about the order
        # in progress and says so in its own words.
        if not st.session_state["viewing"]:
            st.caption(
                f"{len(ctx.audit)} events on this order" if ctx is not None
                else "No order in progress"
            )

        # The one ambient signal in the app. An order that is waiting on a person
        # is waiting whichever tab you happen to be reading, and the sidebar is
        # the only thing on screen from all four - so this is where a pulse
        # earns its keep rather than being decoration on a screen you are
        # already looking at.
        #
        # It appears for exactly one status and disappears the moment that
        # status changes. A finished order does not pulse, because a record that
        # throbs is claiming something is still happening to it.
        if ctx is not None and ctx.status is TransactionStatus.AWAITING_APPROVAL:
            ui.status_pill("waiting", live=True)

        if st.button("New order", width="stretch", key="new_order"):
            reset()
            st.rerun()

        # Finished orders, newest first. Sits above the controls because it is
        # the only thing in this panel a buyer opens on purpose; everything
        # below it is setup.
        st.markdown("")
        _past_orders_panel()

        with st.expander("Suppliers"):
            chosen = []
            for key, adapter in sources.ADAPTERS.items():
                if st.checkbox(adapter.display_name, key in st.session_state["source_keys"],
                               key=f"src_{key}"):
                    chosen.append(key)
            # Never let the pool empty. A run with no suppliers is not a useful
            # demonstration of anything, it just looks broken.
            st.session_state["source_keys"] = chosen or list(sources.ALL_SOURCE_KEYS)

        with st.expander("Reading your request"):
            _language_switch()

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

        # Signs the panel off. Still not a heading - it sits under every control
        # rather than above them, so it cannot compete with the page header.
        theme.sidebar_brand()


def _language_switch() -> None:
    """Choose who reads the brief: the model, or the word matcher on this machine.

    Why a user-facing control and not just a config line. The free tier is a
    DAILY allowance and one brief spends two calls, so every practice run with
    the model on is a run we cannot make on stage. Before this switch existed the
    only way to save the allowance was to hide the API key, which also made the
    app look like it had no AI at all.

    It is worded for a buyer, not for us, because a buyer has the same two
    reasons to want it: cost, and not sending their purchasing plans to a third
    party. What it can NEVER do is change the outcome — both readings produce the
    same structured brief and everything after stage 2 is plain Python either
    way. The caption says exactly that, because a control that looks like it
    might quietly change the answer is worse than no control.

    With no API key there is nothing to switch, so the box is disabled rather
    than offering a choice that does not exist.
    """
    have_key = language.is_online()

    st.session_state["use_model"] = st.checkbox(
        "Use AI to read my request",
        value=st.session_state["use_model"] and have_key,
        disabled=not have_key,
        key="use_model_box",
    )

    if not have_key:
        st.caption("No API key set, so requests are read on this machine.")
    elif st.session_state["use_model"]:
        st.caption("Costs one AI request per brief.")
    else:
        st.caption("Read on this machine by word matching. Nothing is sent anywhere.")

    st.caption("Either way, the ranking and the spending limit are worked out the same.")


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

    request = st.session_state["pending_context"]
    if request is not None:
        # The question first, because it is the only action on this screen — but
        # the read-back stays underneath it. Someone answering "what are these
        # for?" should be able to see what we understood them to be buying
        # without scrolling back through the conversation to check.
        ui.rule()
        _context_picker(request)

    ui.rule()
    _brief_readback(ctx)
    _what_the_agent_did(ctx)


def _what_the_agent_did(ctx) -> None:
    """The compact rail, under the read-back: what just happened to this request.

    WHY IT IS HERE AND NOT ONLY ON THE RECORD. This is the screen someone is
    looking at when they press send, so it is the screen that owes them an
    answer about what the agent then did on their behalf. Sending a sentence
    into a box and getting a recommendation back two tabs away leaves the middle
    invisible, and the middle is the part we are asking anyone to trust.

    Compact - what happened and when, no sentences. The full rail with every
    reason on it is on the record, which is where somebody goes when they want
    to interrogate a step rather than watch one.

    Read off the same audit entries the record uses, so this cannot drift from
    it and cannot be running ahead of what was actually written down.
    """
    if not ctx.audit:
        return
    ui.rule()
    ui.section("What we did with it")
    ui.trace(ctx.audit, reasons=False)


def _context_picker(request) -> None:
    """The one usage question, as a choice rather than a form field.

    Radio buttons rather than a dropdown, because there are three answers and a
    buyer should be able to read all of them at once — the note under each option
    is doing as much work as the label. A dropdown hides two thirds of a decision
    behind a click.

    "Not sure" is deliberately present and deliberately not the default. Present,
    because an agent that will not proceed until you classify your own purchase
    is worse than one with a documented default. Not the default, because the
    whole reason we stopped here is that the answer changes the result.
    """
    labels = [option.label for option in request.options]
    notes = {option.label: option.note for option in request.options}
    tags = {option.label: option.tag for option in request.options}

    ui.section(request.question)
    st.caption("This changes the order of the results, never who qualifies.")

    chosen = st.radio(
        request.question,
        labels,
        index=None,
        key="context_choice",
        label_visibility="collapsed",
        captions=[notes[label] for label in labels],
    )

    left, right = st.columns([1, 3])
    with left:
        if st.button("Continue", type="primary", key="context_apply", disabled=chosen is None):
            apply_context(tags[chosen])
            st.rerun()
    with right:
        if st.button("Not sure — use your default", key="context_skip"):
            apply_context(None)
            st.rerun()


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
        theme.PRODUCT_NAME,
        "What do you need to buy?",
        "Type your request in the box below — how many, what specification, "
        "your budget and your deadline.",
    )

    # Repeat-order shortcuts, which is what an ops manager's tool would actually
    # offer: Meena reorders the same few things every quarter. They double as the
    # fast way to start without typing a long sentence live, but they are not
    # framed as demo buttons, because to a real user they would not be.
    #
    # Honest scope: these four are hardcoded starting points, not a record of
    # anything. The real record is "Your orders" in the sidebar - orders this
    # session actually placed - and nothing here reads it.
    st.caption("Recent requests")

    # A 2x2 grid of cards rather than four buttons down the left edge. Four
    # stacked buttons of different widths is a list of links; a grid of equal
    # cards is a set of things you can pick from, which is what these are.
    #
    # They are still `st.button` with the same keys they always had. The tests
    # select on those keys, and a div with a click handler is not a button to a
    # keyboard or a screen reader - so the control is restyled, never replaced.
    # The marker span is what the stylesheet hooks onto; it renders nothing.
    with st.container():
        st.markdown('<span class="recent-anchor"></span>', unsafe_allow_html=True)
        for row_start in range(0, len(RECENT_REQUESTS), 2):
            for column, (key, label, brief_text) in zip(
                st.columns(2), RECENT_REQUESTS[row_start:row_start + 2]
            ):
                with column:
                    if st.button(label, width="stretch", key=key):
                        handle_message(brief_text)
                        st.rerun()


def _weight_note(ctx) -> str:
    """One line saying where the four weights came from, for the arithmetic to cite.

    The score is only as defensible as its weights, so wherever the maths is shown
    it says whose numbers they are — the buyer's own words, their answer to the
    usage question, or the documented default for this kind of purchase. A weight
    with no stated origin is the number a finance manager would refuse to sign.
    """
    brief = ctx.brief
    if brief is not None and brief.context_tag:
        label = config.context_label(brief.category, brief.context_tag).lower()
        return (
            f"Weighted for **{label}**, which you chose. Same request, same "
            f"weights, same result — every run."
        )
    return (
        "Weights come from what you said mattered; anything you did not mention "
        "uses the documented default for this kind of purchase."
    )


def _brief_readback(ctx) -> None:
    """What the agent understood, as chips a buyer can check at a glance.

    Requirements and preferences are shown differently because they DO different
    things: a requirement can disqualify a product, a preference can only change
    its position. Presenting them as one undifferentiated list would hide the
    single most important distinction in the whole system.
    """
    brief = ctx.brief
    palette = theme.active()

    # A ceiling the buyer stated is a requirement. A ceiling we filled in from
    # config is not, and since stage 3 no longer filters on it, showing it as one
    # would be the screen claiming a check that never ran.
    cap_stated = (
        brief.field_status.get("max_price_per_unit_inr") is not FieldStatus.ASSUMED
    )
    price_chip = (
        f"max ₹{brief.max_price_per_unit_inr:.2f}/unit" if cap_stated
        else "no price ceiling given"
    )
    # Same shape as the price chip above: a limit the buyer did not set is
    # still worth showing, because "no deadline" is a fact about this order that
    # changes which products qualified.
    window_stated = brief.max_delivery_days is not None
    window_chip = (
        f"within {brief.max_delivery_days} days" if window_stated
        else "no delivery deadline"
    )

    ui.section("What we understood")
    ui.chips([
        (f"{brief.quantity:,} {config.unit_noun(brief.category)}", palette.accent),
        (brief.category, None),
        *[(spec, None) for spec in brief.specs],
        (price_chip, None),
        (window_chip, None),
    ])
    # WHICH LIMITS THE BUYER DID NOT SET, SAID PLAINLY.
    # Both negotiable limits can be absent, and when one is, the sentence below
    # is the only place a buyer learns that nothing was ruled out on it. Built
    # from a list rather than written out four times, because "no price limit",
    # "no deadline", "neither" and "both set" is four sentences to keep in step.
    unruled = []
    if not cap_stated:
        unruled.append("cost")
    if not window_stated:
        unruled.append("delivery time")

    caption = "Any product missing one of these is not considered."
    if unruled:
        caption += (
            f" Nothing was ruled out on {' or '.join(unruled)}, because you set "
            f"no limit there — but it still counts towards the ranking below."
        )
    st.caption(caption)

    if ctx.weights:
        st.markdown("")
        ui.section("What you said matters")
        ui.chips([
            (f"{criterion} {weight:.0%}", palette.criterion_colour.get(criterion))
            for criterion, weight in ctx.weights.values.items()
        ])
        # Where these four numbers came from, in one line. Without it the weights
        # look like a house opinion; with it they are traceable to something the
        # buyer either said or clicked.
        if brief.context_tag:
            st.caption(
                f"Weighted for **{config.context_label(brief.category, brief.context_tag).lower()}**. "
                f"These decide the order of the results, never who qualifies."
            )
        else:
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
            "max_price_per_unit_inr": (
                brief.max_price_per_unit_inr if cap_stated
                else f"none stated — not used as a filter (our {brief.category} "
                     f"default of ₹{brief.max_price_per_unit_inr:.2f} was recorded "
                     f"as an assumption, not applied as a limit)"
            ),
            "max_delivery_days": (
                brief.max_delivery_days if window_stated
                else "none stated - you said there was no deadline, so nothing "
                     "was ruled out on delivery time"
            ),
        })
        if ctx.weights:
            st.markdown("**Preferences** — ranking only. Never rejects anything.")
            st.json({
                "usage_context": brief.context_tag or "none chosen — category default applied",
                **{
                    criterion: {"weight": weight, "from": ctx.weights.sources.get(criterion, "")}
                    for criterion, weight in ctx.weights.values.items()
                },
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
        if st.session_state["pending_context"] is not None:
            st.caption(
                "Waiting on one answer — tell us what these are for and the "
                "comparison appears here."
            )
        else:
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

    # Who it comes from and what it is, directly under the recommendation. Both
    # are things a buyer checks before they read a single figure: an unfamiliar
    # supplier or a missing spec ends the conversation, and no amount of score
    # makes up for either.
    ui.source_badge(winner.product)
    ui.spec_badges(winner.product, ctx.brief.specs)

    runner_up_gap = winner.score - ctx.ranked[1].score if len(ctx.ranked) > 1 else None
    ui.figures([
        ("Order total", f"₹{total:,.0f}",
         f"{ctx.brief.quantity:,} {config.unit_noun(ctx.brief.category)}"),
        ("Delivery", f"{winner.product.delivery_days} days",
         f"{ctx.brief.max_delivery_days - winner.product.delivery_days} days inside your deadline"
         if ctx.brief.max_delivery_days is not None else "no deadline set"),
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
            ui.score_breakdown(scored, ctx.weights, _weight_note(ctx))
            ui.buyer_reviews(scored.product)

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

    # The reason is optional and it is the only field on this screen. A decline
    # recorded as "reason: none given" answers the WHAT and leaves the WHY blank,
    # which is the one question a finance manager reading this back will actually
    # have. It used to be read from a session key nothing ever wrote, so every
    # decline was logged without one.
    st.text_input(
        "Reason for declining (optional)",
        key="decline_reason",
        placeholder="e.g. budget moved to next quarter",
        label_visibility="collapsed",
    )

    approve, decline = st.columns([1, 1])
    with approve:
        if st.button("Approve this order", type="primary", width="stretch", key="approve"):
            authorisation.approve(ctx, st.session_state["log"], approver=APPROVER)
            execute()
            st.rerun()
    with decline:
        if st.button("Decline", width="stretch", key="decline"):
            authorisation.decline(ctx, st.session_state["log"],
                                  reason=st.session_state.get("decline_reason", "").strip(),
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

    # Built as a list first and drawn once, rather than printed line by line as
    # the code walks the outcome. Printing as we go is how the payment step
    # ended up styled differently from the confirmation step above it - they were
    # written at different times by different hands.
    steps: list[tuple[str, str, str, list[str]]] = []

    if confirmation.confirmed:
        steps.append((
            "approved", "Supplier confirmed", confirmation.headline,
            [f"Held under {confirmation.lock_reference}"],
        ))
    else:
        steps.append(("stopped", "Supplier could not confirm", confirmation.headline, []))

    if payment is not None:
        if payment.paid:
            steps.append(("placed", "Payment taken", f"₹{payment.amount_inr:,.0f}", []))
        else:
            steps.append(("stopped", "Payment failed", payment.headline, []))

        # Retries are worth showing: a first decline followed by a success is the
        # system recovering, and hiding it would make the log look nicer than the
        # run actually was. They sit UNDER the payment step rather than beside it,
        # because the decline and the retry are one event, not two.
        if payment.declines:
            # The reason is only present on a decline, so it is appended rather
            # than interpolated. Formatting it in unconditionally left the
            # successful retry reading "Attempt 2: went through - " with a dash
            # pointing at nothing.
            steps[-1][3].extend(
                f"Attempt {attempt.attempt}: {attempt.outcome.label}"
                + (f" — {attempt.reason}" if attempt.reason else "")
                for attempt in payment.attempts
            )

    ui.outcome_steps(steps)

    if not confirmation.confirmed and confirmation.escalation is not None:
        _escalation_options(confirmation.escalation)
    if payment is not None and payment.escalation is not None:
        _escalation_options(payment.escalation)

    if summary is not None:
        st.markdown("")
        authority = (
            f"approved by {APPROVER}" if summary.approved_by_human
            else "within the agent's own limit"
        )
        ui.hero("Order placed",
                f"{summary.product_label} — ₹{summary.amount_inr:,.0f}",
                f"{summary.quantity:,} {config.unit_noun(ctx.brief.category)}"
                f" at ₹{summary.unit_price_inr:.2f} each · {authority}",
                variant="done")

        # The three references, together, the moment the order completes. This is
        # what someone quotes when they ring up about it in a month - and the
        # order reference is the one that pulls back every single thing that
        # happened, which is why it is first and why it is on this screen rather
        # than only on the trail.
        ui.keyvalues([
            ("Order reference", ctx.transaction_id),
            ("Payment", summary.payment_reference),
            ("Supplier lock", summary.lock_reference),
        ], mono=True)
        st.caption("Quote the order reference for anything to do with this purchase.")


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

def _order_record(ctx, summary) -> None:
    """The order this trail is about, at the top of the trail.

    A list of events answers "what did it do?". It does not answer the question
    anyone actually opens this screen with — "what did we buy, from whom, for how
    much, and who said yes?" — without reading fifteen lines and adding up. So the
    answer sits above the events, with the reference beside it, and the arithmetic
    that produced the choice one click below.

    Every figure here is read off what an earlier step already recorded. Nothing
    on this screen is recalculated, because a second opinion on a settled number
    is how a record ends up disagreeing with itself.
    """
    ui.section(f"Order {ctx.transaction_id}")

    chosen = ctx.selected or (ctx.ranked[0] if ctx.ranked else None)
    if chosen is None or ctx.brief is None:
        st.caption("No product was chosen, so nothing was ordered.")
        return

    product = chosen.product
    quantity = ctx.brief.quantity

    # Paid figures when there are any, the quoted ones otherwise. The two can
    # differ - a supplier may re-quote at confirmation - and the record has to
    # show what was actually charged, not what we expected to be charged.
    unit_price = summary.unit_price_inr if summary else product.price_per_unit_inr
    total = summary.amount_inr if summary else product.order_total_inr(quantity)

    # The outcome is a tag rather than a word tacked onto the supplier badge.
    # `kind` drives the colour and `label` the wording, so a screen can say
    # "Declined" instead of the generic "Stopped" without being able to
    # accidentally paint a declined order green.
    #
    # `live` is only ever true while a person is genuinely being waited on. It
    # is the pulse, and a record that throbs after the fact would be claiming
    # activity that is not happening.
    if summary is not None:
        kind, label = "placed", "Bought"
        who = APPROVER if summary.approved_by_human else "Agent, within its limit"
    elif ctx.status is TransactionStatus.AWAITING_APPROVAL:
        kind, label = "waiting", "Waiting on you"
        who = "Nobody yet — nothing ordered"
    elif ctx.status is TransactionStatus.DECLINED:
        kind, label = "stopped", "Declined"
        who = f"{APPROVER} said no"
    else:
        kind, label = "qualified", "Chosen"
        who = "Not ordered"

    ui.source_badge(product)
    ui.status_pill(kind, label, live=kind == "waiting")
    ui.keyvalues([
        ("Item", product.name),
        ("Quantity", f"{quantity:,} {config.unit_noun(ctx.brief.category)}"),
        ("Unit price", f"₹{unit_price:,.2f}"),
        ("Order total", f"₹{total:,.0f}"),
        ("Match", f"{chosen.score} / 100"),
        ("Authorised by", who),
    ])
    st.markdown("")
    ui.spec_badges(product, ctx.brief.specs)

    with ui.detail(f"How the {chosen.score} was worked out"):
        ui.score_breakdown(chosen, ctx.weights, _weight_note(ctx))


def render_activity() -> None:
    """Everything that happened to this order, in the order it happened."""
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]

    if ctx is None or not ctx.audit:
        st.caption("Nothing has happened yet.")
        return

    _record_screen(ctx, log, st.session_state["summary"])


def render_past_order(order: PastOrder) -> None:
    """A finished order reopened from the sidebar, and the way back.

    It calls the same function the live screen calls, with that order's own
    objects. There is no second rendering of an order anywhere in this file, so
    a record cannot look one way while it is happening and another way a week
    later - which is the only version of this feature worth having in a product
    whose whole claim is that the record can be trusted.

    It replaces the four screens rather than switching to one of them. The tabs
    are about the order in progress; a finished order is a different subject, and
    borrowing one of its tabs would leave three showing somebody else's numbers.
    """
    if st.button("← Back", key="close_past"):
        st.session_state["viewing"] = None
        st.rerun()

    _record_screen(order.ctx, order.log, order.summary)


def _record_screen(ctx, log, summary) -> None:
    """The order, then everything that happened to it, then the saved copy."""
    _order_record(ctx, summary)

    ui.rule()
    ui.section("What happened")
    st.caption(f"{len(ctx.audit)} events · notifying {', '.join(log.notify_list())}")
    ui.trace(log.entries())

    ui.rule()

    with ui.detail("The finance record"):
        st.code(log.finance_view(), language="text")

    with ui.detail("Check this against the saved copy"):
        st.caption(
            "This is the saved record, read back from the file itself — the same "
            "one your finance team receives, not a copy of what is on screen."
        )
        replayed = audit_module.replay(ctx.transaction_id)
        st.caption(f"{len(replayed)} entries, {ctx.transaction_id}")
        ui.saved_entries(replayed)

    st.download_button(
        "Download audit trail",
        data=log.jsonl_path().read_text(encoding="utf-8"),
        file_name=log.jsonl_path().name,
        mime="application/x-ndjson",
        width="stretch",
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

# The tab icon is the real mark rather than an emoji. On stage there is never
# just one tab open, and a box emoji is what every other packaging tool in the
# world also picked.
st.set_page_config(
    page_title=theme.PRODUCT_NAME,
    page_icon=str(theme.LOGO_MARK_PATH),
    layout="wide",
)
theme.inject()
init_state()

# The header carries the current order reference on its right. It is the one
# number a buyer needs from every screen - it is what they quote on the phone -
# so it belongs in the chrome rather than being hunted for on the trail.
# The sidebar files any finished order into the history list, so it runs before
# anything reads that list.
render_sidebar()

# Opening a past order puts its reference in the header too. The reference is
# what a buyer quotes on the phone, and while a past order is on screen it is
# that order they would be quoting.
_past = _open_past_order()
_ctx = st.session_state["ctx"]
theme.app_header(
    _past.transaction_id if _past is not None
    else (_ctx.transaction_id if _ctx is not None else "")
)

if _past is not None:
    render_past_order(_past)
else:
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
