"""The interface: four screens over the engine in agent/.

WHAT THIS FILE IS ALLOWED TO DO
-------------------------------
Show things, and pass button presses through to the engine. That is the whole
job.

Every number on every screen is read off an object some stage already produced —
the score from `ScoredProduct`, the overage from `AuthorisationOutcome`, the
amount paid from `PaymentOutcome`. This file computes nothing, decides nothing,
and never re-reads a catalog. If a figure shown here could disagree with the
audit log, we would have two answers to one question, and the log is supposed to
be the answer.

That is also why the stage-8 close lives in `agent/close.py` and not here: a run
driven from a test or a notebook has to end the same way a run driven from these
buttons does.

THE FOUR SCREENS (CLAUDE.md, "Interface")
-----------------------------------------
  1  Brief        chat intake — stages 0, 1, 2
  2  Comparison   what was found and how it scored — stages 3, 4
  3  Decision     the authorisation gate and what happened after — stages 5-8
  4  Audit        the trail, replayed from disk — stage 8

Tabs rather than a wizard, so a judge can jump straight to the audit trail and
read it while the rest is still on screen.

STREAMLIT, IN ONE PARAGRAPH
---------------------------
Streamlit re-runs this entire file top to bottom on every click. So nothing about
a transaction can live in a local variable — it all lives in `st.session_state`,
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
    vendor,
    weights as weights_module,
)
from agent.models import TransactionContext, TransactionStatus

DEMO_BRIEF = (
    "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit, "
    "delivered within 10 days. Reliability matters a lot - we got burned last quarter."
)

OFF_TOPIC_EXAMPLE = "What's the weather in Chennai tomorrow?"

# The switches, with the plain-words labels the sidebar shows. Keys come from the
# stage files themselves so a rename cannot leave the sidebar flipping a switch
# nothing reads any more.
SWITCH_LABELS = {
    vendor.SWITCH_OUT_OF_STOCK: "Stage 6 · vendor is out of stock",
    vendor.SWITCH_PRICE_DRIFT: "Stage 6 · price drifted up since discovery",
    payment_module.SWITCH_DECLINE_FIRST: "Stage 7 · decline the first payment attempt",
    payment_module.SWITCH_DECLINE_EVERY: "Stage 7 · decline every payment attempt",
}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def init_state() -> None:
    """Put every key we read into session_state once, so no screen guesses.

    Streamlit re-runs the file constantly; a missing key is the most common way
    one of these apps dies mid-demo. Setting them all here means every screen can
    read state without defending itself.
    """
    defaults = {
        "messages": [],          # the chat transcript: list of (role, text)
        "ctx": None,             # TransactionContext — the shared state CLAUDE.md requires
        "log": None,             # AuditLogger bound to that context
        "brief_note": "",        # 'gemini' or 'offline', for honest labelling
        "scope_note": "",
        "auth": None,            # AuthorisationOutcome  — stage 5
        "stage3_escalation": None,  # EscalationOutcome   — stage 3, nothing eligible
        "confirmation": None,    # ConfirmationOutcome    — stage 6
        "payment": None,         # PaymentOutcome         — stage 7
        "summary": None,         # CloseSummary           — stage 8
        "switches": config.failure_injection(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset() -> None:
    """Clear everything except the failure switches, which the demo leaves set."""
    st.session_state["messages"] = []
    st.session_state["brief_note"] = ""
    st.session_state["scope_note"] = ""
    for key in ("ctx", "log", "auth", "stage3_escalation", "confirmation", "payment", "summary"):
        st.session_state[key] = None


def say(role: str, text: str) -> None:
    """Add one line to the chat transcript."""
    st.session_state["messages"].append((role, text))


# The three states a transaction cannot come back from. The next brief after
# one of these is a new order with its own transaction id and its own log.
_FINISHED = {
    TransactionStatus.COMPLETED,
    TransactionStatus.DECLINED,
    TransactionStatus.EXPIRED,
}


# ---------------------------------------------------------------------------
# The engine calls — the only two functions here that touch agent/
# ---------------------------------------------------------------------------

def handle_message(text: str) -> None:
    """Stages 0 → 5 for one user message. Stops wherever the engine stops.

    Reading this function top to bottom IS the pipeline, which is deliberate — a
    judge asking "what happens when I type a sentence?" should be able to follow
    one screen of code.

    Note the three early returns. Each is a place the engine refuses to continue,
    and none of them is an error: an off-topic message, a brief with a hole in it,
    and a search that found nothing eligible are all designed outcomes.
    """
    say("user", text)

    # A finished transaction is not reopened. The next brief is a new order, with
    # a new transaction id and its own audit file.
    ctx = st.session_state["ctx"]
    if ctx is None or ctx.status in _FINISHED:
        transaction = TransactionContext(transaction_id=audit_module.new_transaction_id())
        st.session_state["ctx"] = transaction
        st.session_state["log"] = audit_module.AuditLogger(transaction)
        for key in ("auth", "stage3_escalation", "confirmation", "payment", "summary"):
            st.session_state[key] = None

    ctx = st.session_state["ctx"]
    log = st.session_state["log"]

    # -- stage 0: should we even start? ------------------------------------
    scope = language.check_scope(text, log)
    st.session_state["scope_note"] = scope.note
    if scope.verdict.verdict == "out_of_scope":
        say("agent", scope.verdict.message)
        return
    if scope.verdict.verdict == "incomplete":
        say("agent", scope.verdict.message)
        return

    # -- stage 1 & 2: the sentence becomes numbers --------------------------
    parsed = language.extract_brief(text, log)
    st.session_state["brief_note"] = parsed.note
    ctx.brief = parsed.brief
    ctx.weights = weights_module.compute(parsed.brief, log)

    # -- stage 3: two catalogs, one shape, one gate -------------------------
    ctx.status = TransactionStatus.DISCOVERING
    ctx.filter_results = discovery.run(parsed.brief, log)

    if not ctx.eligible:
        outcome = escalation.handle(ctx, escalation.Trigger.NO_ELIGIBLE_MATCH, log)
        st.session_state["stage3_escalation"] = outcome
        say("agent", outcome.headline + "  \n\nSee the **Decision** tab.")
        return

    # -- stage 4: pure Python, same answer every run ------------------------
    ctx.ranked = ranking.rank(
        ctx.eligible,
        ctx.weights,
        parsed.brief.max_price_per_unit_inr,
        parsed.brief.max_delivery_days,
        log,
    )
    ctx.status = TransactionStatus.RANKED

    # -- stage 5: may the agent sign for this? ------------------------------
    auth = authorisation.authorise(ctx, log)
    st.session_state["auth"] = auth

    if auth.within_limit:
        say("agent", auth.headline + "  \n\nProceeding without approval — see the **Decision** tab.")
        execute()
    else:
        say("agent", auth.headline + "  \n\n**This needs your approval** — see the **Decision** tab.")


def execute() -> None:
    """Stages 6 → 8. Run after the agent clears itself, or after a human approves.

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
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    """Transaction state, the honest parser label, and the failure switches."""
    with st.sidebar:
        st.subheader("Transaction")
        ctx = st.session_state["ctx"]
        if ctx is None:
            st.caption("No transaction yet. Send a brief to start one.")
        else:
            st.code(ctx.transaction_id, language=None)
            st.caption(f"Status: **{ctx.status.value}** · {len(ctx.audit)} audit entries")

        st.divider()
        st.subheader("Language step")
        # We label the parser honestly rather than hiding a degraded run. If the
        # key is missing or the free tier is exhausted, the offline parser takes
        # over and the screen says so — see CLAUDE.md, "we degrade honestly".
        if language.is_online():
            st.success("Gemini reachable · " + config.llm_model())
        else:
            st.warning("Offline parser · no API key found")
        note = st.session_state["brief_note"] or st.session_state["scope_note"]
        if note:
            st.caption(note)

        st.divider()
        st.subheader("Failure injection")
        st.caption(
            "Mock switches for the vendor and payment stages, so the escalation "
            "path can be triggered on demand instead of taken on trust."
        )
        switches = dict(st.session_state["switches"])
        for key, label in SWITCH_LABELS.items():
            switches[key] = st.checkbox(label, value=switches.get(key, False), key=f"sw_{key}")
        st.session_state["switches"] = switches

        st.divider()
        if st.button("Reset — new transaction", width="stretch"):
            reset()
            st.rerun()

        st.caption(
            "Limits are read from config.yaml, never from a prompt: "
            f"authorisation Rs {config.authorisation_limit_inr():,.0f}, "
            f"substitution threshold {config.substitution_threshold_points():.0f} pts."
        )


# ---------------------------------------------------------------------------
# Screen 1 — the brief
# ---------------------------------------------------------------------------

def render_brief_tab() -> None:
    """Chat intake, then what stages 1 and 2 made of the sentence."""
    st.subheader("1 · Brief")
    st.caption(
        "The language model reads the sentence and fills in fields. That is the "
        "only thing it does in this app — it never scores, ranks or approves."
    )

    left, right = st.columns(2)
    if left.button("Load the demo brief", width="stretch"):
        handle_message(DEMO_BRIEF)
        st.rerun()
    if right.button("Try an off-topic message", width="stretch"):
        handle_message(OFF_TOPIC_EXAMPLE)
        st.rerun()

    for role, text in st.session_state["messages"]:
        with st.chat_message(role if role == "user" else "assistant"):
            st.markdown(text)

    ctx = st.session_state["ctx"]
    if ctx is None or ctx.brief is None:
        return

    brief = ctx.brief
    st.divider()
    st.markdown("**What the parser extracted, and what each field is for**")

    rows = []
    for field, value in (
        ("category", brief.category),
        ("quantity", f"{brief.quantity:,}"),
        ("specs", ", ".join(brief.specs) or "-"),
        ("max_price_per_unit_inr", f"Rs {brief.max_price_per_unit_inr:.2f}"),
        ("max_delivery_days", f"{brief.max_delivery_days} days"),
    ):
        status = brief.field_status.get(field)
        rows.append(
            {
                "Field": field,
                "Value": value,
                # HARD / SOFT / AMBIGUOUS comes from a fixed table in models.py,
                # not from the model. The LLM finds values; the table says what
                # values are for.
                "Class": brief.classification(field).value.upper(),
                # CONFIRMED means the user said it. ASSUMED means we applied a
                # declared default because they did not — and it is logged as an
                # assumption the moment it is applied.
                "Provenance": status.value.upper() if status else "-",
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")

    if any(row["Provenance"] == "ASSUMED" for row in rows):
        st.info(
            "An **ASSUMED** field means the brief was silent and a declared default "
            "from config.yaml was applied. It is written to the audit log as an "
            "assumption, never as an instruction."
        )

    if ctx.weights:
        st.markdown("**Stage 2 · the weights, and where each came from**")
        st.caption(
            "The model extracts the phrase (\"matters a lot\"); plain Python looks up "
            "the number. The model is never shown the number."
        )
        st.dataframe(
            [
                {
                    "Criterion": criterion,
                    "Weight": f"{value:.2f}",
                    "Source": ctx.weights.sources.get(criterion, "-"),
                }
                for criterion, value in ctx.weights.values.items()
            ],
            hide_index=True,
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Screen 2 — the comparison
# ---------------------------------------------------------------------------

def render_comparison_tab() -> None:
    """What both catalogs offered, what survived the gate, and how it scored."""
    st.subheader("2 · Comparison")
    ctx = st.session_state["ctx"]
    if ctx is None or not ctx.filter_results:
        st.caption("Nothing discovered yet. Send a brief on the first tab.")
        return

    st.caption(
        "Two sources with deliberately different schemas — PackHub sends direct "
        "JSON in rupees, BoxBazaar sends aggregator CSV in paise with a shipping "
        "range and a score out of 100 — normalised into one Product model."
    )

    passed = [result for result in ctx.filter_results if result.passed]
    st.markdown(
        f"**Stage 3 · hard gate:** {len(ctx.filter_results)} products considered, "
        f"**{len(passed)} qualified**."
    )

    if ctx.ranked:
        st.markdown("**Stage 4 · ranked** — pure Python, same brief in, same order out.")
        st.dataframe(
            [
                {
                    "#": scored.rank,
                    "Product · source": scored.product.label,
                    "Rs/unit": f"{scored.product.price_per_unit_inr:.2f}",
                    "Days": scored.product.delivery_days,
                    "Reliab.": scored.product.reliability_rating,
                    "Replace": f"{scored.product.replacement_window_days} d",
                    "Score": scored.score,
                }
                for scored in ctx.ranked
            ],
            hide_index=True,
            width="stretch",
        )

        gap = ctx.score_gap()
        if gap is not None:
            st.caption(
                f"#1 leads #2 by **{gap} points**. The substitution threshold is "
                f"{config.substitution_threshold_points():.0f} — a wider gap means the agent "
                f"escalates rather than silently swapping if #1 falls through."
            )

        st.markdown("**Every score term, so nothing is taken on trust**")
        for scored in ctx.ranked:
            with st.expander(f"{scored.product.label} — {scored.score}"):
                st.dataframe(
                    [
                        {
                            "Criterion": term.criterion,
                            "Weight": f"{term.weight:.2f}",
                            "Raw": term.raw_value,
                            "Normalised": f"{term.normalised:.3f}",
                            "Contribution": f"{term.contribution:.3f}",
                            "How": term.method,
                        }
                        for term in scored.terms
                    ],
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    f"Sum of contributions x 100 = **{scored.score}**. "
                    f"Reliability alone contributed {scored.contribution('reliability'):.3f}."
                )

    rejected = ctx.rejected
    if rejected:
        st.markdown(f"**Rejected ({len(rejected)}) — kept, with reasons**")
        st.caption(
            "We keep the failures, not just the survivors. \"We looked at seven and "
            "three qualified\" is a checkable claim; \"here are three\" is not."
        )
        st.dataframe(
            [
                {
                    "Product · source": result.product.label,
                    "Rs/unit": f"{result.product.price_per_unit_inr:.2f}",
                    "Days": result.product.delivery_days,
                    "In stock": f"{result.product.available_quantity:,}",
                    "Why it failed": " · ".join(result.violations.values()),
                }
                for result in rejected
            ],
            hide_index=True,
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Screen 3 — the decision
# ---------------------------------------------------------------------------

def render_decision_tab() -> None:
    """The authorisation gate, and everything that happened after it."""
    st.subheader("3 · Decision")
    ctx = st.session_state["ctx"]
    if ctx is None:
        st.caption("No transaction yet.")
        return

    stage3 = st.session_state["stage3_escalation"]
    if stage3 is not None:
        _render_escalation(stage3, ctx)
        return

    auth = st.session_state["auth"]
    if auth is None:
        st.caption("Stage 5 has not run yet.")
        return

    _render_limit_check(auth)

    if ctx.status is TransactionStatus.AWAITING_APPROVAL and auth.escalation is not None:
        _render_escalation(auth.escalation, ctx)
        _render_approval_buttons(ctx)
    else:
        _render_execution(ctx)


def _render_limit_check(auth) -> None:
    """The one multiplication and the one comparison that make up stage 5."""
    st.markdown("**Stage 5 · the limit check**")
    columns = st.columns(4)
    columns[0].metric("Order total", f"Rs {auth.order_total_inr:,.0f}")
    columns[1].metric("Authorisation limit", f"Rs {auth.authorisation_limit_inr:,.0f}")
    columns[2].metric(
        "Headroom",
        f"Rs {auth.headroom_inr:,.0f}",
        delta="within limit" if auth.within_limit else "over limit",
        delta_color="normal" if auth.within_limit else "inverse",
    )
    columns[3].metric("Selected", f"{auth.selected.score}", help=auth.selected.product.label)
    st.caption(
        f"{auth.quantity:,} x Rs {auth.unit_price_inr:.2f} = Rs {auth.order_total_inr:,.0f}. "
        "The Rs 22 per-unit cap is a limit on the PRODUCT and was applied at stage 3; "
        "this is a limit on the AGENT. Being over it escalates — it never rejects."
    )
    if auth.within_limit:
        st.info(auth.headline)
    else:
        st.warning(auth.headline)


def _render_escalation(outcome, ctx) -> None:
    """The shared handler's output, whichever of the four call sites produced it."""
    st.warning(outcome.headline)
    st.caption(f"Detected at stage {outcome.stage} · trigger `{outcome.trigger.value}`")

    if outcome.options:
        st.markdown("**Alternatives, each with the cost of choosing it**")
        st.dataframe(
            [
                {
                    "Product · source": option.label,
                    "Order total": f"Rs {option.order_total_inr:,.0f}",
                    "Score": option.score if option.score is not None else "-",
                    "Behind by": f"{option.score_gap} pts" if option.score_gap is not None else "-",
                    "Note": option.note or " · ".join(option.violations.values()),
                }
                for option in outcome.options
            ],
            hide_index=True,
            width="stretch",
        )

    if outcome.proposed_relaxations:
        st.markdown("**What relaxing a negotiable limit would admit**")
        for proposal in outcome.proposed_relaxations:
            st.markdown(f"- {proposal}")
        st.caption(
            "Proposed, never applied. Category, quantity and specs are never "
            "relaxed at all — see CLAUDE.md, the non-negotiable tier."
        )


def _render_approval_buttons(ctx) -> None:
    """The only human-in-the-loop gate in the system. Three ways out, two buy nothing."""
    st.divider()
    st.markdown("**Your decision**")
    log = st.session_state["log"]

    if ctx.selected is None:
        st.caption("Nothing is selected to approve — this escalation is about the search, not a purchase.")
        return

    approve, decline, expire = st.columns(3)

    if approve.button("Approve", type="primary", width="stretch"):
        authorisation.approve(ctx, log, approver="Meena (ops manager)")
        say("user", "Approved.")
        execute()
        st.rerun()

    if decline.button("Decline", width="stretch"):
        authorisation.decline(ctx, log, reason="over budget this month", decliner="Meena (ops manager)")
        say("user", "Declined.")
        st.rerun()

    if expire.button("Let it expire", width="stretch"):
        authorisation.expire(ctx, log)
        say("agent", "The approval request expired. Silence is not approval, so nothing was bought.")
        st.rerun()

    st.caption(
        "Silence is never approval: an unanswered request expires into the same "
        "no-purchase state as a decline, with the transaction state preserved."
    )


def _render_execution(ctx) -> None:
    """Stages 6, 7 and 8 for a run that got past the gate."""
    confirmation = st.session_state["confirmation"]
    payment = st.session_state["payment"]
    summary = st.session_state["summary"]

    if confirmation is None:
        if ctx.status is TransactionStatus.DECLINED:
            st.error("Declined by the requester. No purchase executed.")
        elif ctx.status is TransactionStatus.EXPIRED:
            st.error("The request expired without an answer. No purchase executed.")
        return

    st.divider()
    st.markdown("**Stage 6 · vendor confirmation**")
    if confirmation.confirmed:
        st.success(confirmation.headline)
        st.caption(f"Lock reference `{confirmation.lock_reference}`")
    else:
        st.warning(confirmation.headline)
        if confirmation.escalation:
            _render_escalation(confirmation.escalation, ctx)
        return

    if payment is None:
        return

    st.markdown("**Stage 7 · payment**")
    st.dataframe(
        [
            {
                "Attempt": attempt.attempt,
                "Product": attempt.product_label,
                "Amount": f"Rs {attempt.amount_inr:,.0f}",
                "Reference": attempt.payment_reference,
                "Outcome": attempt.outcome.value.upper(),
                "Reason": attempt.reason or "-",
            }
            for attempt in payment.attempts
        ],
        hide_index=True,
        width="stretch",
    )
    if payment.paid:
        st.success(payment.headline)
        st.caption(
            "A retry is a retry, not a new decision: same lock, same amount, same "
            "product. Two declines and the option is treated as unbuyable."
        )
    else:
        st.warning(payment.headline)
        if payment.escalation:
            _render_escalation(payment.escalation, ctx)
        return

    if summary is not None:
        st.divider()
        st.markdown("**Stage 8 · closed**")
        st.success(summary.headline)
        columns = st.columns(3)
        columns[0].metric("Paid", f"Rs {summary.amount_inr:,.0f}")
        columns[1].metric("Score", summary.score)
        columns[2].metric("Human approval", "required" if summary.approved_by_human else "not needed")


# ---------------------------------------------------------------------------
# Screen 4 — the audit trail
# ---------------------------------------------------------------------------

def render_audit_tab() -> None:
    """One record, exported twice: JSONL for systems, a rendered page for a human."""
    st.subheader("4 · Audit trail")
    ctx = st.session_state["ctx"]
    log = st.session_state["log"]
    if ctx is None or log is None or not ctx.audit:
        st.caption("Nothing logged yet. Send a brief on the first tab.")
        return

    st.caption(
        "Written at the moment of each event, not assembled afterwards. One "
        "transaction id replays the whole order in sequence."
    )

    st.dataframe(
        [
            {
                "Entry": entry.entry_id,
                "Time": entry.timestamp.strftime("%H:%M:%S"),
                "Stage": entry.stage,
                "What": entry.event_type.value,
                "Who": entry.actor.value,
                "Why": entry.reasoning,
                "Notify": ", ".join(entry.notify),
            }
            for entry in ctx.audit
        ],
        hide_index=True,
        width="stretch",
    )

    st.markdown(f"**Finance is notified:** {', '.join(log.notify_list())}")

    with st.expander("The one-page auditor view"):
        st.code(log.finance_view(), language=None)

    left, right = st.columns(2)
    left.download_button(
        "Download JSONL (for systems)",
        data=log.path.read_text(encoding="utf-8") if log.path.exists() else "",
        file_name=log.path.name,
        mime="application/x-ndjson",
        width="stretch",
    )
    right.download_button(
        "Download the auditor view (for humans)",
        data=log.finance_view(),
        file_name=f"{ctx.transaction_id}-audit.txt",
        mime="text/plain",
        width="stretch",
    )

    with st.expander("Replay it from disk instead of from memory"):
        st.caption(
            "This re-reads the .jsonl file rather than the objects in memory, which "
            "is the proof that the trail survives the app being closed."
        )
        replayed = audit_module.replay(ctx.transaction_id)
        st.write(f"{len(replayed)} entries read back from `{log.path.name}`.")
        for entry in replayed:
            st.markdown(f"- `{entry.entry_id}` **{entry.event_type.value}** — {entry.reasoning}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Autonomous Commerce Agent", page_icon="📦", layout="wide")
init_state()

st.title("Autonomous Commerce Engineering Agent")
st.caption(
    "One sentence in, an audited purchase decision out — and the agent knows "
    "exactly where its own authority ends. **Autonomy the user can audit.**"
)

render_sidebar()

brief_tab, comparison_tab, decision_tab, audit_tab = st.tabs(
    ["1 · Brief", "2 · Comparison", "3 · Decision", "4 · Audit trail"]
)

with brief_tab:
    render_brief_tab()
with comparison_tab:
    render_comparison_tab()
with decision_tab:
    render_decision_tab()
with audit_tab:
    render_audit_tab()

# The chat box sits outside the tabs so it is reachable from any screen — a judge
# who is looking at the audit trail can type the next brief without navigating back.
if prompt := st.chat_input("Describe what you need to buy…"):
    handle_message(prompt)
    st.rerun()
