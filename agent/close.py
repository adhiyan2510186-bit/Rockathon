"""Stage 8 — confirmation & audit close. The last entry, and the only one that ends a run.

WHY THIS FILE EXISTS
--------------------
Every stage before this one deliberately refuses to declare the transaction
finished. Stage 6 leaves it CONFIRMING, stage 7 leaves it PAYING. That is not an
oversight — a stage that marked its own work complete would be the one stage
whose completion nobody else checked.

So closing is a separate step, and it does three things:

  1. writes the final entry: what was bought, from whom, for how much, against
     which lock and which payment reference
  2. moves the transaction to COMPLETED, which is the only place that happens
  3. notifies the requester AND finance, which is what drives the finance email

CLAUDE.md, stage 8: "Final structured entry; user and finance notified."

WHY IT IS NOT IN app.py
-----------------------
Because it is a state change and an audit entry, and the interface should not own
either. If the close lived in the Streamlit script, a run driven from a test or a
notebook would end without ever being closed, and the audit trail's last line
would depend on which screen somebody happened to be looking at.

WHAT IT DOES NOT DO
-------------------
It computes nothing. Every figure in the closing entry is read from what the
earlier stages already recorded — the confirmed quote, the payment reference, the
score the ranker produced. A close that recalculated the total would be a second
opinion on a number that was settled three stages ago, and the two could disagree.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.audit import STAGE_AUTHORISATION, STAGE_CLOSE, AuditLogger
from agent.models import Actor, EventType, TransactionContext, TransactionStatus
from agent.payment import PaymentOutcome
from agent.vendor import ConfirmationOutcome


class CloseSummary(BaseModel):
    """The one-paragraph answer to "what happened to this order?".

    The UI prints these fields and the closing audit entry stores them. Same
    figures, one source, so the confirmation screen and the audit trail cannot
    quote different numbers for the same purchase.
    """

    transaction_id: str
    product_label: str = Field(description="'Corusafe DW - PackHub (direct)'.")
    quantity: int
    unit_price_inr: float = Field(description="The CONFIRMED price, not the ranked one.")
    amount_inr: float = Field(description="What was actually paid.")
    score: float = Field(description="The ranking score of what we bought.")
    lock_reference: str
    payment_reference: str
    approved_by_human: bool = Field(
        description="True when a human had to authorise this. The single most "
        "important fact in the summary — it says where the agent's authority ended."
    )
    headline: str = Field(description="One plain sentence for the user.")


def close(
    context: TransactionContext,
    audit: AuditLogger,
    confirmation: ConfirmationOutcome,
    payment: PaymentOutcome,
) -> CloseSummary:
    """Write the final entry and mark the transaction COMPLETED.

    Takes both stage outcomes as arguments for the same reason stage 7 takes the
    confirmation: a close that had to go looking for what happened could be called
    on a run that never happened. Here the evidence is in the signature.

    Raises ValueError if nothing was paid. There is no such thing as closing an
    escalated transaction — it is parked in AWAITING_APPROVAL with its state
    intact, which is a different and deliberately unfinished state. Silence is
    never approval, and neither is tidying up.
    """
    if not payment.paid:
        raise ValueError(
            "Stage 8 closes a completed purchase. This transaction was escalated and "
            "nothing was paid, so it stays where it is rather than being closed out."
        )
    if context.status is not TransactionStatus.PAYING:
        raise ValueError(
            f"Stage 8 expects a paid transaction - this one is {context.status.value}."
        )

    bought = payment.paid_for
    assert bought is not None  # guaranteed by payment.paid
    brief = context.brief
    assert brief is not None  # nothing reaches stage 8 without one

    unit_price = confirmation.quote.quoted_price_inr if confirmation.quote else 0.0

    # Whether a human signed is read off the audit trail rather than inferred from
    # the amount. The order total only tells you whether an approval was NEEDED.
    #
    # The stage matters as much as the actor. A buyer answering the usage-context
    # question is also recorded as a USER decision (weights.py), and it is not an
    # approval - it is a preference. Only a USER decision at the AUTHORISATION
    # gate is the record of a human taking the purchase on themselves. Without
    # the stage test, any order where the buyer answered that question would
    # close claiming a sign-off that never happened.
    approved_by_human = any(
        entry.stage == STAGE_AUTHORISATION
        and entry.actor is Actor.USER
        and entry.event_type is EventType.DECISION
        for entry in context.audit
    )

    authority = (
        "after human approval" if approved_by_human else "within the agent's own limit"
    )
    headline = (
        f"Order complete. {brief.quantity:,} x {bought.product.name} from "
        f"{bought.product.source} at Rs {unit_price:.2f} per unit, Rs "
        f"{payment.amount_inr:,.0f} total, {authority}. "
        f"Payment {payment.payment_reference}, lock {payment.lock_reference}."
    )

    audit.action(
        STAGE_CLOSE,
        f"Order closed: {brief.quantity:,} x {bought.product.label} for Rs "
        f"{payment.amount_inr:,.0f}, paid {authority}. Requester and finance notified.",
        {
            "product": bought.product.label,
            "source": bought.product.source,
            "source_type": bought.product.source_type,
            "quantity": brief.quantity,
            "unit_price_inr": unit_price,
            "amount_inr": payment.amount_inr,
            "ranking_score": bought.score,
            "lock_reference": payment.lock_reference,
            "payment_reference": payment.payment_reference,
            "approved_by_human": approved_by_human,
            "payment_attempts": len(payment.attempts),
            "audit_entries": len(context.audit) + 1,  # this one included
            "action_taken": "transaction closed",
        },
        notify=["requester", "finance"],
    )

    context.status = TransactionStatus.COMPLETED

    return CloseSummary(
        transaction_id=context.transaction_id,
        product_label=bought.product.label,
        quantity=brief.quantity,
        unit_price_inr=unit_price,
        amount_inr=payment.amount_inr,
        score=bought.score,
        lock_reference=payment.lock_reference,
        payment_reference=payment.payment_reference,
        approved_by_human=approved_by_human,
        headline=headline,
    )
