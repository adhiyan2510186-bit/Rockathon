"""Stage 7 — mock payment execution. The only stage that spends money.

WHY THIS FILE EXISTS
--------------------
Stage 6 got the vendor to stand by a price and hold the stock, and handed back a
lock reference. This stage does the one irreversible thing in the whole pipeline:
it pays.

CLAUDE.md, stage 7: "Simulated outcome; retry once, then fall back." Three
sentences is genuinely the whole specification, and the file is deliberately
shaped like them:

    attempt 1 declined  ->  retry, because a declined card is usually transient
    attempt 2 declined  ->  stop. This option cannot be bought.
    cannot be bought    ->  escalation trigger 3, the same one stage 6 uses

WHAT THIS FILE PAYS FOR
-----------------------
The locked quote, and nothing else. The amount comes from the
ConfirmationOutcome stage 6 produced — never recomputed from the ranked product,
never re-read from the catalog. Stage 6 already did the "is this still the price?"
work; asking again here would be a second, competing answer to a question that has
already been settled, and the gap between the two is where a customer gets charged
a figure nobody approved.

That is also why `pay()` takes the ConfirmationOutcome as an argument rather than
digging it out of the context: you cannot call this function without having a
confirmation in your hand. The ordering of stages 6 and 7 is enforced by the
signature rather than by everyone remembering.

WHY RETRY ONCE, AND ONLY ONCE
-----------------------------
Once, because the most common decline in real life is transient — a timeout, a
momentary hold, an issuer having a bad second — and giving up on the first one
would send a human a question the system could have answered itself.

Only once, because a second decline is information, not noise. It means something
is actually wrong with this purchase, and the honest response is to stop and use
the escalation path rather than hammer a gateway. Retry loops are also how real
systems produce duplicate charges, which is a bad thing to demonstrate.

A retry is a RETRY, not a fresh decision: same lock reference, same amount, same
product. Nothing is re-priced, re-ranked or re-chosen between two attempts.

WHY THE FALLBACK GETS A NEW LOCK
--------------------------------
When both attempts fail and the escalation handler swaps to the next eligible
product, this file sends that product back through stage 6 before paying for it.
You cannot pay for something you never confirmed — the fallback needs its own
price check, its own stock check and its own lock reference, exactly like the
product it is replacing. Re-using the failed product's lock would be paying for
one thing with the paperwork for another.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent import config, escalation, vendor
from agent.audit import STAGE_PAYMENT, AuditLogger
from agent.escalation import EscalationOutcome, Trigger
from agent.models import ScoredProduct, TransactionContext, TransactionStatus
from agent.vendor import ConfirmationOutcome

# One retry, then stop. The number CLAUDE.md specifies, named rather than typed
# into a loop condition, so "retry once" is checkable in one place.
MAX_ATTEMPTS_PER_LOCK = 2

# The switches this stage understands, mirroring vendor.py's pair.
#
# decline_first_payment_attempt   the scripted demo: one decline, then the retry
#                                 succeeds on screen. ON by default in config.yaml.
# decline_every_payment_attempt   a genuinely dead card, so both attempts fail and
#                                 the trigger-3 fallback path becomes visible.
SWITCH_DECLINE_FIRST = "decline_first_payment_attempt"
SWITCH_DECLINE_EVERY = "decline_every_payment_attempt"

# What the pretend gateway says when it turns us down. A plain sentence, because
# it ends up in the audit log where a finance manager reads it — "ERR_51" would
# make the log something you need a decoder ring for.
DECLINE_REASON = "issuer declined the transaction (simulated gateway response)"

# Re-confirming a fallback runs stage 6 again, and stage 6 would otherwise re-read
# and re-apply its own injected failures to a product that has done nothing wrong.
# An injected failure describes one vendor having one bad day; it is not a curse on
# every vendor in the run. Same rule as vendor.py's "first quote only".
_NO_INJECTED_FAILURES = {
    vendor.SWITCH_OUT_OF_STOCK: False,
    vendor.SWITCH_PRICE_DRIFT: False,
}


class PaymentStatus(str, Enum):
    """How stage 7 ended. Two outcomes, because there are only two.

    PAID       money moved, for the locked quote, and stage 8 may close the audit.
    ESCALATED  nothing was paid. A human is deciding, and the transaction is
               parked with its state intact.
    """

    PAID = "paid"
    ESCALATED = "escalated"


class AttemptOutcome(str, Enum):
    """What the gateway said to one attempt."""

    APPROVED = "approved"
    DECLINED = "declined"


class PaymentAttempt(BaseModel):
    """One trip to the gateway, kept whether it worked or not.

    The declines are the point of storing these. "Paid on the second attempt" is a
    claim; a list showing attempt 1 declined at 10:22:41 and attempt 2 approved at
    10:22:43, both against the same lock reference and the same amount, is the
    evidence for it — and it is also the evidence that the retry did not quietly
    pay twice.
    """

    model_config = ConfigDict(frozen=True)

    attempt: int = Field(description="1 or 2 for this lock. Never higher.")
    payment_reference: str = Field(description="'PAY-TXN-4471-01'. One per attempt.")
    lock_reference: str = Field(description="The stage-6 lock this attempt paid against.")
    product_label: str = Field(description="What was being bought, in plain words.")
    amount_inr: float = Field(description="The locked total. Identical across a retry.")
    outcome: AttemptOutcome
    reason: str = Field(default="", description="Why it was declined. Empty when approved.")
    switches_applied: list[str] = Field(
        default_factory=list,
        description="Which pretend-failure switches were on. Empty on a clean run, so an "
        "injected demo decline can never be read back later as a real one.",
    )


class PaymentOutcome(BaseModel):
    """Stage 7's result, in the shape the UI and stage 8 both read."""

    status: PaymentStatus
    paid_for: ScoredProduct | None = Field(
        default=None, description="The product actually bought, when status is PAID."
    )
    payment_reference: str = Field(
        default="", description="The reference of the attempt that succeeded."
    )
    lock_reference: str = Field(default="", description="The stage-6 lock that was paid against.")
    amount_inr: float = Field(default=0.0, description="What was actually spent.")
    attempts: list[PaymentAttempt] = Field(
        default_factory=list,
        description="Every attempt in this run, in order, including the declines and "
        "including attempts against a fallback's lock.",
    )
    headline: str = Field(description="One plain sentence. What the user reads first.")
    escalation: EscalationOutcome | None = Field(
        default=None,
        description="The approval-screen content when status is ESCALATED. Built by the "
        "shared handler, not by this file.",
    )

    @property
    def paid(self) -> bool:
        """True when money moved and stage 8 may close. One place owns that question."""
        return self.status is PaymentStatus.PAID

    @property
    def declines(self) -> int:
        """How many attempts were turned down. The demo's answer is 1."""
        return sum(1 for a in self.attempts if a.outcome is AttemptOutcome.DECLINED)


# ---------------------------------------------------------------------------
# The stage itself
# ---------------------------------------------------------------------------

def pay(
    context: TransactionContext,
    audit: AuditLogger,
    confirmation: ConfirmationOutcome,
    overrides: dict[str, bool] | None = None,
) -> PaymentOutcome:
    """Pay for the locked quote. Retry once on a decline, then fall back or escalate.

    In reading order:

      1. attempt payment against the stage-6 lock, for the locked amount
      2. declined -> attempt exactly one more time, unchanged
      3. declined again -> this option cannot be bought: escalation trigger 3
      4. handler swaps to a fallback -> re-lock it at stage 6, then pay for THAT
         handler escalates -> nothing paid, a human decides

    Step 4 loops, and it terminates for the same reason stage 6's loop does: every
    product we have failed on is added to `tried` and is never offered back as its
    own fallback.

    Raises ValueError on an unconfirmed transaction. Paying for something no vendor
    ever agreed to hold is precisely the failure stage 6 exists to prevent, so this
    stage refuses to be the place it happens.
    """
    if not confirmation.confirmed:
        raise ValueError(
            "Stage 7 will not pay against an unconfirmed order. Stage 6 escalated or "
            "was never run, so there is no locked price to pay."
        )
    if context.status is not TransactionStatus.CONFIRMING:
        raise ValueError(
            f"Stage 7 only runs on a confirmed transaction - this one is "
            f"{context.status.value}. Stage 6 sets CONFIRMING when it locks an order."
        )

    switches = dict(config.failure_injection())
    switches.update(overrides or {})

    context.status = TransactionStatus.PAYING
    attempts: list[PaymentAttempt] = []
    tried: set[str] = set()
    first_lock = True

    while True:
        locked = confirmation.locked
        assert locked is not None  # guaranteed by confirmation.confirmed

        # Both switches describe one card having one bad day, so they apply only
        # while we are paying for the FIRST locked product. A flag that declined
        # every card in the run would make the fallback path unreachable, and that
        # is the path the trigger-3 rule exists to demonstrate.
        active = switches if first_lock else {}

        for attempt_no in range(1, MAX_ATTEMPTS_PER_LOCK + 1):
            attempt = _attempt(
                context, confirmation, attempt_no, len(attempts) + 1, active
            )
            attempts.append(attempt)
            _log_attempt(audit, attempt)

            if attempt.outcome is AttemptOutcome.APPROVED:
                return _paid(context, audit, locked, confirmation, attempt, attempts)

        # Both attempts declined. This option cannot be bought.
        tried.add(locked.product.product_id)
        outcome = escalation.handle(
            context,
            Trigger.PAYMENT_DECLINED,
            audit,
            failed_product=locked.product,
            failure_note=f"was declined twice ({DECLINE_REASON})",
            exclude_ids=tried,
        )

        if not outcome.resolved:
            # escalation.handle() has already written the entry and parked the
            # transaction in AWAITING_APPROVAL. Nothing was paid.
            return PaymentOutcome(
                status=PaymentStatus.ESCALATED,
                attempts=attempts,
                headline=outcome.headline,
                escalation=outcome,
            )

        # The handler swapped to a re-validated fallback. It needs its own lock
        # before anyone pays for it, so it goes back through stage 6 — which also
        # re-checks its price, its stock and the authorised money ceiling.
        context.status = TransactionStatus.APPROVED
        confirmation = vendor.confirm(context, audit, overrides=_NO_INJECTED_FAILURES)
        first_lock = False

        if not confirmation.confirmed:
            # Stage 6 refused the fallback and has already escalated and logged it.
            return PaymentOutcome(
                status=PaymentStatus.ESCALATED,
                attempts=attempts,
                headline=confirmation.headline,
                escalation=confirmation.escalation,
            )

        context.status = TransactionStatus.PAYING


def _paid(
    context: TransactionContext,
    audit: AuditLogger,
    locked: ScoredProduct,
    confirmation: ConfirmationOutcome,
    approved: PaymentAttempt,
    attempts: list[PaymentAttempt],
) -> PaymentOutcome:
    """Money moved. Record what was bought, for how much, and against which lock.

    Finance is on the notify list here and not on the declines: a decline is an
    operational hiccup, a completed payment is a number that has to reconcile
    against somebody's books.

    The status is left at PAYING rather than COMPLETED on purpose. Stage 8 closes
    the transaction, and a stage that marked itself finished would be the one stage
    whose completion nobody else verified.
    """
    # Two different counts, and confusing them produces a sentence that is simply
    # untrue. A fallback paid on its first attempt has declines behind it in this
    # transaction, but none of them were ITS declines — they belonged to the option
    # it replaced. So the retry story is told from the winning lock's own attempt
    # number, and the earlier failures are reported as what they were.
    declines_total = sum(1 for a in attempts if a.outcome is AttemptOutcome.DECLINED)
    declines_here = sum(
        1
        for a in attempts
        if a.outcome is AttemptOutcome.DECLINED and a.lock_reference == approved.lock_reference
    )
    declines_earlier = declines_total - declines_here

    if approved.attempt > 1:
        retry_note = " The first attempt was declined; the retry succeeded."
    elif declines_earlier:
        retry_note = (
            f" Approved first time, after {declines_earlier} declined attempt(s) on the "
            f"option this one replaced."
        )
    else:
        retry_note = " Approved on the first attempt."

    audit.action(
        STAGE_PAYMENT,
        f"Paid Rs {approved.amount_inr:,.0f} for {approved.product_label} against lock "
        f"{approved.lock_reference} on attempt {approved.attempt}"
        + (f", after {declines_here} decline(s) against this lock." if declines_here else ".")
        + (
            f" {declines_earlier} earlier decline(s) belonged to the option it replaced."
            if declines_earlier
            else ""
        ),
        {
            "product": approved.product_label,
            "amount_inr": approved.amount_inr,
            "payment_reference": approved.payment_reference,
            "lock_reference": approved.lock_reference,
            "attempt": approved.attempt,
            "attempts_in_transaction": len(attempts),
            "declines_against_this_lock": declines_here,
            "declines_on_replaced_options": declines_earlier,
            "unit_price_inr": confirmation.quote.quoted_price_inr if confirmation.quote else None,
            "action_taken": "payment executed",
        },
        notify=["requester", "finance"],
    )

    return PaymentOutcome(
        status=PaymentStatus.PAID,
        paid_for=locked,
        payment_reference=approved.payment_reference,
        lock_reference=approved.lock_reference,
        amount_inr=approved.amount_inr,
        attempts=attempts,
        headline=(
            f"Paid Rs {approved.amount_inr:,.0f} for {approved.product_label}."
            + retry_note
            + f" Payment reference {approved.payment_reference}."
        ),
    )


def _log_attempt(audit: AuditLogger, attempt: PaymentAttempt) -> None:
    """Write the decline down at the moment it happens, not in the summary afterwards.

    Only the declines are logged here — the successful attempt is logged by
    `_paid()`, together with everything else the close needs. Logging a decline as
    it happens is what makes "we retried once" checkable rather than asserted: the
    entry exists even if the retry then throws.
    """
    if attempt.outcome is AttemptOutcome.APPROVED:
        return

    audit.action(
        STAGE_PAYMENT,
        f"Payment attempt {attempt.attempt} of {MAX_ATTEMPTS_PER_LOCK} for "
        f"{attempt.product_label} was declined: {attempt.reason}."
        + (
            " Retrying once with the same lock and the same amount."
            if attempt.attempt < MAX_ATTEMPTS_PER_LOCK
            else " No further attempts will be made against this lock."
        ),
        {
            "product": attempt.product_label,
            "amount_inr": attempt.amount_inr,
            "payment_reference": attempt.payment_reference,
            "lock_reference": attempt.lock_reference,
            "attempt": attempt.attempt,
            "outcome": attempt.outcome.value,
            "reason": attempt.reason,
            "injected": attempt.switches_applied,
            "action_taken": "no money moved",
        },
    )


# ---------------------------------------------------------------------------
# The pretend gateway
# ---------------------------------------------------------------------------

def _attempt(
    context: TransactionContext,
    confirmation: ConfirmationOutcome,
    attempt_no: int,
    sequence: int,
    switches: dict[str, bool],
) -> PaymentAttempt:
    """Present the locked amount to the gateway once and report what came back.

    This is the mock, and it is deliberately the dullest function in the project:
    it looks at two switches and returns approved or declined. There is no
    randomness. A demo that pays on some rehearsals and not others is a demo you
    cannot rehearse, and "same inputs, same outcome" is a claim we make about the
    whole system — it would be an odd place to stop making it.

    The amount and the lock reference are read from the confirmation, never
    recomputed. Everything about this attempt traces back to what the vendor
    actually agreed to.
    """
    locked = confirmation.locked
    assert locked is not None

    applied: list[str] = []
    declined = False

    if switches.get(SWITCH_DECLINE_EVERY):
        declined = True
        applied.append(SWITCH_DECLINE_EVERY)
    elif switches.get(SWITCH_DECLINE_FIRST) and attempt_no == 1:
        declined = True
        applied.append(SWITCH_DECLINE_FIRST)

    return PaymentAttempt(
        attempt=attempt_no,
        payment_reference=f"PAY-{context.transaction_id}-{sequence:02d}",
        lock_reference=confirmation.lock_reference,
        product_label=locked.product.label,
        amount_inr=confirmation.order_total_inr,
        outcome=AttemptOutcome.DECLINED if declined else AttemptOutcome.APPROVED,
        reason=DECLINE_REASON if declined else "",
        switches_applied=applied,
    )
