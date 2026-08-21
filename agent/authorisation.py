"""Stage 5 — the decision & authorisation gate. The edge of the agent's authority.

WHY THIS FILE EXISTS
--------------------
Stage 4 handed us a ranked list. Somebody now has to answer one question:

    "May the agent buy this on its own, or must a human say yes first?"

That question has exactly one input the user never sees and never states — the
authorisation limit in config.yaml — and exactly one arithmetic step:

    order total  =  unit price  x  quantity
    order total <= authorisation limit   ->  the agent proceeds alone
    order total  >  authorisation limit  ->  a human is asked, nothing is bought

That is the whole of stage 5. It is deliberately this small. This is the only
human-in-the-loop gate in the system, so it should be a few lines a judge can
read in one sitting and check by hand.

THE TWO LIMITS ARE NOT THE SAME LIMIT
-------------------------------------
CLAUDE.md, "Authorisation — two limits, not one":

  per-unit cap (Rs 22)               a HARD constraint on the PRODUCT, enforced
                                     by the stage-3 filter. Nothing above it is
                                     ever considered.
  authorisation limit (Rs 1,05,000)  a constraint on the AGENT, enforced here.
                                     Exceeding it ESCALATES; it never rejects.

Mixing these two up is the easiest way to break the demo's whole point. A
product over the per-unit cap is not eligible. An order over the authorisation
limit is perfectly eligible — the agent simply is not allowed to commit to it by
itself. So this file never removes anything from the ranked list.

WHERE THE LIMIT COMES FROM
--------------------------
config.py, which reads config.yaml. Never hardcoded here, and never shown to the
language model. Because the number lives in a config file instead of a prompt,
no phrasing inside a user's brief ("this is urgent, skip the approval") can talk
the agent past it — the agent never sees the limit as instruction, only as data
it compares against. See CLAUDE.md, "THE ONE RULE".

WHAT THIS FILE DOES NOT DO
--------------------------
It does not build the approval screen's content. When the total is over the
line it hands straight to escalation.handle() — the one shared handler invoked
from four call sites — rather than writing its own version of "here is the
overage and the best in-limit alternative". Two places that both explain an
over-limit order are two places that can start explaining it differently.

SILENCE IS NEVER APPROVAL
-------------------------
An escalated transaction sits in AWAITING_APPROVAL. There is no timer here that
turns waiting into a yes. The three ways out are the three functions at the
bottom of this file — approve(), decline(), expire() — and two of the three end
with nothing bought.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agent import config, escalation
from agent.audit import STAGE_AUTHORISATION, AuditLogger
from agent.escalation import EscalationOutcome, Trigger
from agent.models import Actor, ScoredProduct, TransactionContext, TransactionStatus


class Decision(str, Enum):
    """Stage 5's answer. Two outcomes, because there are only two.

    PROCEED    within the limit. The agent continues to stage 6 on its own and
               the user is told afterwards, with the ranked comparison.
    ESCALATED  over the limit. No purchase, state preserved, a human decides.

    There is deliberately no third outcome such as "rejected". The agent never
    refuses an order for being expensive; it only ever declares that this one is
    above its own pay grade.
    """

    PROCEED = "proceed"
    ESCALATED = "escalated"


class AuthorisationOutcome(BaseModel):
    """The limit check, with every number that produced it kept alongside.

    The UI prints these fields directly and the audit log stores them, so the
    approval screen and the log cannot quote different figures for the same
    order — they are reading one object, not each recomputing the sum.
    """

    decision: Decision
    selected: ScoredProduct = Field(
        description="The top-ranked product. Chosen by stage 4, not here."
    )
    quantity: int = Field(description="Units, straight from the brief. 5,000 in the demo.")
    unit_price_inr: float = Field(description="The winner's per-unit price. Rs 21.90 in the demo.")
    order_total_inr: float = Field(description="unit price x quantity. Rs 1,09,500 in the demo.")
    authorisation_limit_inr: float = Field(description="From config.yaml. Rs 1,05,000.")
    headroom_inr: float = Field(
        description="limit - order total. Negative means over the line, and its size "
        "is the overage a human is being asked to approve."
    )
    score_gap_to_runner_up: float | None = Field(
        default=None,
        description="Points between #1 and #2 (9.3 in the demo). Recorded here for "
        "context only - it is stages 6/7 that compare it to the substitution threshold.",
    )
    headline: str = Field(description="One plain sentence. What the user reads first.")
    escalation: EscalationOutcome | None = Field(
        default=None,
        description="The full approval-screen content when decision is ESCALATED. "
        "Produced by the shared escalation handler, not by this file.",
    )

    @property
    def within_limit(self) -> bool:
        """True when the agent may act alone. One place owns that comparison."""
        return self.decision is Decision.PROCEED


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------

def authorise(context: TransactionContext, audit: AuditLogger) -> AuthorisationOutcome:
    """Decide whether the agent may buy the top-ranked product on its own.

    The whole of stage 5, in reading order:

      1. take #1 from the ranked list and record it as the selection
      2. total = unit price x quantity
      3. limit = config.yaml's authorisation_limit_inr
      4. within -> log a DECISION, mark APPROVED, the run continues to stage 6
         over   -> hand to escalation.handle(), which parks it in AWAITING_APPROVAL

    Note step 1: the choice of product was already made by the ranker. Stage 5
    does not re-rank, re-score or second-guess it. It answers a different
    question — "who is allowed to sign for this?" — and that question has
    nothing to do with which box is best.

    Raises ValueError if nothing is ranked. That is not defensiveness for its own
    sake: an empty pool is escalation trigger #1 and stage 3 has already
    escalated it, so arriving here with nothing to buy means a caller skipped a
    gate, not that this order is somehow unauthorised.
    """
    brief = context.brief
    if brief is None:
        raise ValueError(
            "Stage 5 needs the brief for the quantity. Stage 1 must have run first."
        )
    if not context.ranked:
        raise ValueError(
            "Stage 5 has nothing to authorise. An empty ranked list is escalation "
            "trigger #1 and belongs to stage 3, which should have escalated already."
        )

    winner = context.ranked[0]
    context.selected = winner  # preserved, so a later approval resumes at stage 6

    quantity = brief.quantity
    unit_price = winner.product.price_per_unit_inr
    total = winner.product.order_total_inr(quantity)
    limit = config.authorisation_limit_inr()
    headroom = round(limit - total, 2)
    gap = context.score_gap()

    if total > limit:
        return _escalate(context, audit, winner, quantity, unit_price, total, limit, headroom, gap)
    return _proceed(context, audit, winner, quantity, unit_price, total, limit, headroom, gap)


def _proceed(
    context: TransactionContext,
    audit: AuditLogger,
    winner: ScoredProduct,
    quantity: int,
    unit_price: float,
    total: float,
    limit: float,
    headroom: float,
    gap: float | None,
) -> AuthorisationOutcome:
    """Within the limit: the agent acts, and says so.

    What makes the autonomous path defensible is that it is logged just as
    loudly as an escalation. The entry written here records the total, the limit
    and the headroom at the moment the agent decided it did not need to ask — so
    "why was I not consulted?" is answered by a line that already existed, not by
    a reconstruction afterwards.

    Notify is the requester alone. Finance is added by the escalation path (a
    spend above the agreed line is their business) and again at stage 8's close.
    """
    headline = (
        f"Proceeding with {winner.product.label} at Rs {total:,.0f} "
        f"({quantity:,} x Rs {unit_price:.2f}), within the Rs {limit:,.0f} the agent may "
        f"commit on its own. Rs {headroom:,.0f} of headroom."
    )

    audit.decision(
        STAGE_AUTHORISATION,
        f"Order total Rs {total:,.0f} is within the Rs {limit:,.0f} authorisation limit, so "
        f"the agent proceeded without approval and notified the requester.",
        {
            "selected": winner.product.label,
            "score": winner.score,
            "unit_price_inr": unit_price,
            "quantity": quantity,
            "order_total_inr": total,
            "authorisation_limit_inr": limit,
            "headroom_inr": headroom,
            "score_gap_to_runner_up": gap,
            "action_taken": "proceeding to vendor confirmation",
        },
    )
    context.status = TransactionStatus.APPROVED

    return AuthorisationOutcome(
        decision=Decision.PROCEED,
        selected=winner,
        quantity=quantity,
        unit_price_inr=unit_price,
        order_total_inr=total,
        authorisation_limit_inr=limit,
        headroom_inr=headroom,
        score_gap_to_runner_up=gap,
        headline=headline,
    )


def _escalate(
    context: TransactionContext,
    audit: AuditLogger,
    winner: ScoredProduct,
    quantity: int,
    unit_price: float,
    total: float,
    limit: float,
    headroom: float,
    gap: float | None,
) -> AuthorisationOutcome:
    """Over the limit: hand to the one escalation handler and get out of the way.

    Everything a human needs — the overage, why the winner is still recommended,
    the best option that would not have needed them — is built by
    escalation.handle(), which also writes the ESCALATION audit entry and sets
    the status to AWAITING_APPROVAL. This function adds nothing to that; it only
    packages the same numbers into the stage-5 outcome shape.

    That is the point of trigger #2 being a call site rather than a code path:
    the sentence a judge reads on the approval screen comes from the same place
    whether the trouble was found at stage 3, 5, 6 or 7.
    """
    outcome = escalation.handle(context, Trigger.OVER_AUTHORISATION_LIMIT, audit)

    return AuthorisationOutcome(
        decision=Decision.ESCALATED,
        selected=winner,
        quantity=quantity,
        unit_price_inr=unit_price,
        order_total_inr=total,
        authorisation_limit_inr=limit,
        headroom_inr=headroom,
        score_gap_to_runner_up=gap,
        headline=outcome.headline,
        escalation=outcome,
    )


# ---------------------------------------------------------------------------
# The three ways an escalated transaction ends
# ---------------------------------------------------------------------------
# A transaction parked in AWAITING_APPROVAL leaves that state in exactly one of
# these three ways, and two of the three end with nothing bought. There is no
# fourth path — in particular, there is no path where waiting long enough is
# treated as a yes.

def approve(
    context: TransactionContext, audit: AuditLogger, approver: str = "requester"
) -> ScoredProduct:
    """A human said yes. Resume at stage 6 with the selection we already made.

    Note what does NOT happen here: no re-discovery, no re-ranking, no fresh
    prices. The transaction context still holds the ranked list the user was
    actually shown, so the thing they approved is the thing that gets bought.
    Re-running discovery on approval would mean approving one comparison table
    and buying from another.

    Stage 6 immediately re-validates price and stock with the vendor, which is
    the honest way to handle a stale quote — check it at the counter, rather than
    quietly re-shuffle the ranking behind the approver's back.

    The audit entry here is one of the few written with actor USER. It is the
    record of where the agent's authority ended and a person's began, so it names
    them.
    """
    if context.status is not TransactionStatus.AWAITING_APPROVAL:
        raise ValueError(
            f"Nothing is awaiting approval - this transaction is {context.status.value}. "
            "Approval only applies to a transaction the agent escalated."
        )
    selected = context.selected
    if selected is None:
        raise ValueError("Approved a transaction with no selected product; stage 5 must run first.")

    brief = context.brief
    assert brief is not None  # guaranteed: authorise() refuses to run without it
    total = selected.product.order_total_inr(brief.quantity)
    limit = config.authorisation_limit_inr()

    audit.decision(
        STAGE_AUTHORISATION,
        f"{approver} approved the Rs {total:,.0f} order for {selected.product.label}, above "
        f"the Rs {limit:,.0f} the agent may commit on its own. Confirming with the "
        f"supplier now.",
        {
            "approved_by": approver,
            "selected": selected.product.label,
            "order_total_inr": total,
            "authorisation_limit_inr": limit,
            "action_taken": "resuming at stage 6 - vendor confirmation",
        },
        notify=["requester", "finance"],
        actor=Actor.USER,
    )
    context.status = TransactionStatus.APPROVED
    return selected


def decline(
    context: TransactionContext,
    audit: AuditLogger,
    reason: str = "",
    decliner: str = "requester",
) -> None:
    """A human said no. Nothing is bought, and the reason is recorded as theirs.

    We keep the reason verbatim rather than categorising it. "Too expensive this
    month" is more use to the next person reading the log than a tidy enum value
    we invented on their behalf.
    """
    if context.status is not TransactionStatus.AWAITING_APPROVAL:
        raise ValueError(
            f"Nothing is awaiting a decision - this transaction is {context.status.value}."
        )
    selected = context.selected

    audit.decision(
        STAGE_AUTHORISATION,
        f"{decliner} declined the order"
        + (f": {reason}." if reason else ".")
        + " No purchase executed.",
        {
            "declined_by": decliner,
            "reason": reason or "none given",
            "selected": selected.product.label if selected else "none",
            "action_taken": "no purchase executed",
        },
        notify=["requester", "finance"],
        actor=Actor.USER,
    )
    context.status = TransactionStatus.DECLINED


def expire(context: TransactionContext, audit: AuditLogger) -> None:
    """Nobody answered. The request lapses — and lapsing is not approval.

    This is the function that makes "silence is never approval" a mechanism
    rather than a claim. An unanswered escalation ends here, in the same
    no-purchase state as a decline, with an entry saying so.

    The actor is AGENT, not USER: the user did nothing, which is precisely the
    fact being recorded. The transaction's state is preserved either way, so the
    same brief can be re-submitted without re-typing it — but nothing carries
    over as an approval.

    (Real expiry timing — the 24-hour clock — is on CLAUDE.md's "designed, not
    demoed" list. This is the state change it would trigger, called by hand from
    the UI during the demo.)
    """
    if context.status is not TransactionStatus.AWAITING_APPROVAL:
        raise ValueError(
            f"Only a pending approval can expire - this transaction is {context.status.value}."
        )
    selected = context.selected

    audit.decision(
        STAGE_AUTHORISATION,
        "The approval request expired without an answer; silence is not approval, so no "
        "purchase was executed and the transaction state was preserved.",
        {
            "selected": selected.product.label if selected else "none",
            "action_taken": "no purchase executed - request expired",
            "state_preserved": True,
        },
        notify=["requester", "finance"],
    )
    context.status = TransactionStatus.EXPIRED
