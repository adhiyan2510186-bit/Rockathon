"""Stage 6 — vendor confirmation & lock. The last check before money moves.

WHY THIS FILE EXISTS
--------------------
Stage 3 read the catalogs. Stage 4 ranked them. Stage 5 decided who was allowed
to sign. All three of those worked from a snapshot taken at the start of the run
— and between that snapshot and this moment, a human may have spent an hour
deciding whether to approve. Stock moves. Prices move.

So before anything is paid for, we go back to the vendor and ask two questions
about the exact product we are about to buy:

    "do you still have 5,000 of these?"        -> stock
    "is it still Rs 21.90 a unit?"             -> price

CLAUDE.md, stage 6: "Re-validate price and stock before any execution." That
sentence is this whole file. If both answers hold, we lock the order and stage 7
may pay. If either has moved against us, the option has effectively failed and we
hand to the one escalation handler — the same one stages 3, 5 and 7 use.

WHY WE RE-READ THE CATALOG INSTEAD OF TRUSTING context.selected
---------------------------------------------------------------
`context.selected` is a copy we made at stage 3. Re-checking a copy against
itself would always pass and would prove nothing. So `_live_record()` goes back
through discovery.discover() and reads the source files again, as a second,
independent lookup. In a real build that is the vendor's API call; here it is the
same two mock files read a second time. The shape of the check is the honest
part, and swapping the read for an HTTP call later is a one-function change.

WHY THIS IS A MODULE AND NOT A SERVICE
--------------------------------------
CLAUDE.md, "Mock services": a module boundary, not a network boundary. There is
no FastAPI here and no port to start. `confirm()` is the only door in, and behind
it is a pretend vendor counter with two switches a judge can flip from the
Streamlit sidebar (out of stock, price drift). Standing up a real server would
add deployment risk to a demo and demonstrate nothing we are actually claiming.

WHAT THE AGENT IS ALLOWED TO DECIDE HERE
----------------------------------------
Almost nothing, deliberately. This file may:

  - lock an order whose price and stock are exactly what was approved
  - lock an order that got CHEAPER (nobody needs approval to spend less)

It may not lock anything that costs more per unit than the figure the ranking and
the approval were based on, however small the increase. "You approved Rs
1,09,500" is a promise, and a Rs 200 drift is still not the number they said yes
to. Every other outcome leaves this file through escalation.handle().
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from agent import config, discovery, escalation
from agent.audit import STAGE_CONFIRMATION, AuditLogger
from agent.escalation import EscalationOutcome, Trigger
from agent.models import (
    Brief,
    Product,
    ScoredProduct,
    TransactionContext,
    TransactionStatus,
)

# How much the pretend vendor moves the price when the drift switch is on.
#
# This is a MOCK's behaviour, not a business rule — no real logic reads it. 8% is
# chosen so the demo's winner (Rs 21.90) drifts to Rs 23.65, landing above the
# user's Rs 22 cap. That makes the failure legible on stage: the box did not just
# get pricier, it stopped qualifying under the constraint the user actually set.
PRICE_DRIFT_MULTIPLIER = 1.08

# The switches this stage understands. Named here so config.yaml, the Streamlit
# sidebar and the code that reads them cannot drift apart.
SWITCH_OUT_OF_STOCK = "out_of_stock_at_confirmation"
SWITCH_PRICE_DRIFT = "price_drift_at_confirmation"


class ConfirmationStatus(str, Enum):
    """How stage 6 ended. Two outcomes, because there are only two.

    LOCKED     the vendor stood by price and stock; the order is held and stage 7
               may pay for it.
    ESCALATED  something moved. Nothing is locked and nothing is paid, and a
               human is now deciding.
    """

    LOCKED = "locked"
    ESCALATED = "escalated"


class VendorQuote(BaseModel):
    """What the vendor said when we asked, alongside what we expected them to say.

    Both halves are kept on purpose. A quote showing only "Rs 23.65" is a fact
    with no meaning; "Rs 23.65, against the Rs 21.90 this order was ranked and
    approved at" is the sentence an approver can act on. The audit entry and the
    UI both read this object, so they cannot quote different figures.
    """

    model_config = ConfigDict(frozen=True)

    product_id: str
    label: str = Field(description="'Corusafe DW - PackHub (direct)', the row heading.")

    quoted_price_inr: float = Field(description="The per-unit price the vendor stands by NOW.")
    expected_price_inr: float = Field(description="The price stage 4 ranked and stage 5 approved.")
    available_quantity: int = Field(description="Units the vendor says are in stock now.")
    quantity_needed: int = Field(description="Units the brief asked for.")

    switches_applied: list[str] = Field(
        default_factory=list,
        description="Which pretend-failure switches were on for this quote. Empty on a "
        "clean run. Recorded so a failure we injected for a demo can never be read "
        "back later as something a vendor really did.",
    )

    @property
    def price_delta_inr(self) -> float:
        """Quoted minus expected. Positive means it got more expensive."""
        return round(self.quoted_price_inr - self.expected_price_inr, 2)

    @property
    def price_moved(self) -> bool:
        """True if the vendor's price is not the price we were working from."""
        return abs(self.price_delta_inr) >= 0.01

    @property
    def in_stock(self) -> bool:
        """True if they can actually cover the whole order."""
        return self.available_quantity >= self.quantity_needed


class ConfirmationOutcome(BaseModel):
    """Stage 6's result, in the shape the UI and stage 7 both read."""

    status: ConfirmationStatus
    quote: VendorQuote | None = Field(
        default=None, description="The quote we locked against, when status is LOCKED."
    )
    locked: ScoredProduct | None = Field(
        default=None, description="The product actually held. Stage 7 pays for exactly this."
    )
    lock_reference: str = Field(
        default="",
        description="'LOCK-TXN-4471-PH-CORUSAFE-DW'. Quoted at payment and in the log.",
    )
    order_total_inr: float = Field(
        default=0.0, description="Recomputed at the CONFIRMED price, not the ranked one."
    )
    attempts: list[VendorQuote] = Field(
        default_factory=list,
        description="Every quote we asked for in this run, in order. A fallback that was "
        "itself confirmed leaves two entries here — the failure and the replacement.",
    )
    headline: str = Field(description="One plain sentence. What the user reads first.")
    escalation: EscalationOutcome | None = Field(
        default=None,
        description="The approval-screen content when status is ESCALATED. Built by the "
        "shared handler, not by this file.",
    )

    @property
    def confirmed(self) -> bool:
        """True when stage 7 may proceed. One place owns that question."""
        return self.status is ConfirmationStatus.LOCKED


# ---------------------------------------------------------------------------
# The stage itself
# ---------------------------------------------------------------------------

def confirm(
    context: TransactionContext,
    audit: AuditLogger,
    overrides: dict[str, bool] | None = None,
) -> ConfirmationOutcome:
    """Re-validate price and stock with the vendor, then lock the order or escalate.

    In reading order:

      1. ask the vendor for a fresh quote on the selected product
      2. put that quote through the SAME hard gates stage 3 used
      3. also refuse any price above what was ranked and approved
      4. clean -> lock it, log an ACTION, stage 7 may pay
         moved -> escalation.handle(), trigger 3

    Step 4's escalation can come back resolved: if the next option re-validated
    cleanly and trails by no more than the 5-point substitution threshold, the
    handler swaps to it. When that happens we loop and confirm THAT product at the
    counter too, rather than assuming a swap made on paper is a swap the vendor
    will honour. The loop terminates because every product we have tried is added
    to `tried` and is never offered back as its own fallback.

    Raises ValueError unless the transaction is APPROVED. Stage 6 sits downstream
    of the authorisation gate by design — reaching a vendor counter without having
    passed stage 5 would mean the one human-in-the-loop gate had been skipped.
    """
    brief = context.brief
    if brief is None:
        raise ValueError("Stage 6 needs the brief for the quantity; stage 1 must have run.")
    if context.status is not TransactionStatus.APPROVED:
        raise ValueError(
            f"Stage 6 only runs on an APPROVED transaction - this one is "
            f"{context.status.value}. Either stage 5 has not run, or it escalated and "
            f"nobody has answered yet."
        )
    if context.selected is None:
        raise ValueError("Nothing is selected to confirm; stage 5 records the selection.")

    switches = dict(config.failure_injection())
    switches.update(overrides or {})

    ceiling = _authorised_ceiling(context, brief)
    context.status = TransactionStatus.CONFIRMING
    attempts: list[VendorQuote] = []
    tried: set[str] = set()

    while True:
        selected = context.selected
        assert selected is not None  # checked above, and re-set by the handler on a swap

        # The switches describe ONE vendor having ONE bad day, so they apply to the
        # first quote only. A flag that broke every vendor in the country would make
        # the fallback path untestable, and that is the path we most want to show.
        quote = _quote(selected.product, brief, switches if not attempts else {})
        attempts.append(quote)

        problem = _problem(quote, brief, selected.product) or _over_ceiling(quote, ceiling)
        if problem == "":
            return _lock(context, audit, selected, quote, attempts)

        tried.add(selected.product.product_id)
        outcome = escalation.handle(
            context,
            Trigger.UNAVAILABLE_AT_CONFIRMATION,
            audit,
            failed_product=selected.product,
            failure_note=problem,
            exclude_ids=tried,
        )

        if not outcome.resolved:
            # A human is now required. escalation.handle() has already written the
            # entry and parked the transaction in AWAITING_APPROVAL — this file adds
            # nothing to that, it only packages the same result in stage 6's shape.
            return ConfirmationOutcome(
                status=ConfirmationStatus.ESCALATED,
                attempts=attempts,
                headline=outcome.headline,
                escalation=outcome,
            )

        # Resolved: the handler swapped to a re-validated fallback and set
        # context.selected. Loop round and put that one through the counter too.
        context.status = TransactionStatus.CONFIRMING


def _lock(
    context: TransactionContext,
    audit: AuditLogger,
    selected: ScoredProduct,
    quote: VendorQuote,
    attempts: list[VendorQuote],
) -> ConfirmationOutcome:
    """The vendor stood by the order. Hold it, log it, hand stage 7 a reference.

    "Lock" here means what it means at a counter: the vendor is holding this
    quantity at this price while payment happens. There is nothing to reserve in
    two local files, so the lock is a reference string — but that reference is
    quoted at payment and written into the audit entry, so the thing paid for and
    the thing confirmed are provably the same thing.

    The reference is derived from the transaction and product ids rather than
    generated randomly. Same run, same reference, every time: a demo that prints a
    different lock id on each rehearsal is a demo nobody can check.
    """
    total = round(quote.quoted_price_inr * quote.quantity_needed, 2)
    reference = f"LOCK-{context.transaction_id}-{quote.product_id}"

    if quote.price_moved:
        # Only ever downward — an increase never reaches this function.
        saving = abs(quote.price_delta_inr) * quote.quantity_needed
        price_note = (
            f"Price moved in our favour: Rs {quote.quoted_price_inr:.2f} against the "
            f"Rs {quote.expected_price_inr:.2f} approved, saving Rs {saving:,.0f}."
        )
    else:
        price_note = "Price and stock unchanged since discovery."

    audit.action(
        STAGE_CONFIRMATION,
        f"Re-validated {quote.label} with the vendor before payment: Rs "
        f"{quote.quoted_price_inr:.2f} per unit and {quote.available_quantity:,} in stock "
        f"against {quote.quantity_needed:,} needed. Order locked as {reference}.",
        {
            "product": quote.label,
            "lock_reference": reference,
            "expected_price_inr": quote.expected_price_inr,
            "confirmed_price_inr": quote.quoted_price_inr,
            "price_delta_inr": quote.price_delta_inr,
            "available_quantity": quote.available_quantity,
            "quantity_needed": quote.quantity_needed,
            "order_total_inr": total,
            "re_validated": "passed all hard constraints on an independent re-read",
            "quotes_requested": len(attempts),
            "action_taken": "order locked, proceeding to payment",
        },
    )

    return ConfirmationOutcome(
        status=ConfirmationStatus.LOCKED,
        quote=quote,
        locked=selected,
        lock_reference=reference,
        order_total_inr=total,
        attempts=attempts,
        headline=(
            f"{quote.label} confirmed at Rs {quote.quoted_price_inr:.2f} per unit, "
            f"{quote.quantity_needed:,} units, Rs {total:,.0f} total. {price_note}"
        ),
    )


# ---------------------------------------------------------------------------
# The money ceiling — the one thing a fallback must not quietly walk past
# ---------------------------------------------------------------------------
# Stage 5 asked "may the agent sign for THIS order?" and got an answer for one
# specific product at one specific price. A trigger-3 fallback then swaps the
# product underneath that answer, and the escalation handler gates that swap on
# the SCORE gap — how different the box is — not on what it costs.
#
# For our catalog the fallback is always cheaper, so this never fires in the
# demo. It is here because "the agent may not commit more than it was authorised
# to" is the claim the whole project rests on, and a rule that only holds because
# our seven mock products happen to be priced conveniently is not a rule.

def _authorised_ceiling(context: TransactionContext, brief: Brief) -> float:
    """The most this transaction may spend, whoever it was that signed for it.

    Two ways a transaction legitimately arrives here APPROVED, and each sets its
    own ceiling:

      within the limit  the agent cleared it itself, so the agent's own
                        authorisation limit is the ceiling
      a human approved  they said yes to a specific total above that limit, so
                        THAT total is the ceiling

    `max()` of the two covers both without needing to know which happened. Note
    what it is read from: config.yaml and the selection stage 5 recorded — this
    file never invents a limit of its own.
    """
    assert context.selected is not None  # confirm() checks before calling
    approved_total = context.selected.product.order_total_inr(brief.quantity)
    return max(config.authorisation_limit_inr(), approved_total)


def _over_ceiling(quote: VendorQuote, ceiling: float) -> str:
    """Plain words if buying this quote would spend more than was authorised, else "".

    One comparison, phrased so it slots into the same escalation sentence every
    other stage-6 failure uses.
    """
    total = round(quote.quoted_price_inr * quote.quantity_needed, 2)
    if total <= ceiling:
        return ""
    return (
        f"would cost Rs {total:,.0f}, above the Rs {ceiling:,.0f} authorised for this "
        f"order, so the agent may not sign for it"
    )


# ---------------------------------------------------------------------------
# The pretend vendor counter
# ---------------------------------------------------------------------------

def _quote(product: Product, brief: Brief, switches: dict[str, bool]) -> VendorQuote:
    """Ask the vendor what this product costs and whether they have it, right now.

    The lookup goes back through discovery.discover(), which re-reads both catalog
    files. That second read is the whole point: checking the copy we cached at
    stage 3 against itself would pass every time and prove nothing.

    The pretend-failure switches are then applied on top and recorded on the quote,
    so an injected demo failure is never mistaken later for something a vendor
    really did.
    """
    live = _live_record(product)

    price = live.price_per_unit_inr
    stock = live.available_quantity
    applied: list[str] = []

    if switches.get(SWITCH_OUT_OF_STOCK):
        stock = 0
        applied.append(SWITCH_OUT_OF_STOCK)
    if switches.get(SWITCH_PRICE_DRIFT):
        price = round(price * PRICE_DRIFT_MULTIPLIER, 2)
        applied.append(SWITCH_PRICE_DRIFT)

    return VendorQuote(
        product_id=live.product_id,
        label=live.label,
        quoted_price_inr=price,
        expected_price_inr=product.price_per_unit_inr,
        available_quantity=stock,
        quantity_needed=brief.quantity,
        switches_applied=applied,
    )


def _live_record(product: Product) -> Product:
    """Look the product up again from source, rather than reusing the stage-3 copy.

    A product that has vanished from the catalog between discovery and now comes
    back as a zero-stock version of itself. That is not a fudge: "the vendor no
    longer lists it" and "the vendor has none" are the same fact for a buyer, and
    routing both through the stock gate means one failure path instead of two.
    """
    for candidate in discovery.discover(product.category):
        if candidate.product_id == product.product_id:
            return candidate
    return product.model_copy(update={"available_quantity": 0})


def _problem(quote: VendorQuote, brief: Brief, ranked: Product) -> str:
    """Say what is wrong with this quote in plain words, or "" if nothing is.

    Two checks, in this order — and the order matters for the sentence a human
    reads, because "out of stock" is a more useful headline than "the price moved"
    when both are true.

    1. RE-VALIDATE against the SAME hard gates stage 3 used. We rebuild the product
       at the vendor's quoted figures and run discovery.apply_hard_gates() over it.
       Reusing that function rather than writing a quick check here is deliberate:
       two definitions of "eligible" in one codebase is exactly how an ineligible
       product ends up bought.

    2. REFUSE ANY INCREASE, even one that still clears the cap. Stage 4 ranked this
       product at a price and stage 5 approved a total built from it. A drift to
       Rs 21.95 is still not the number anybody said yes to, and the escalation
       handler exists precisely so that asking a human is cheap.

    A price that moved DOWN returns "" — nothing needs approving in order to spend
    less than was approved.
    """
    at_the_counter = ranked.model_copy(
        update={
            "price_per_unit_inr": quote.quoted_price_inr,
            "available_quantity": quote.available_quantity,
        }
    )
    result = discovery.apply_hard_gates(brief, [at_the_counter])[0]

    if not result.passed:
        if "quantity" in result.violations:
            # Phrased the way the vendor would say it, because that is what happened.
            return f"is out of stock at confirmation - {result.violations['quantity']}"
        return "no longer passes a hard constraint - " + "; ".join(
            result.violations[field] for field in sorted(result.violations)
        )

    if quote.price_delta_inr > 0:
        return (
            f"was re-quoted at Rs {quote.quoted_price_inr:.2f} per unit, Rs "
            f"{quote.price_delta_inr:.2f} above the Rs {quote.expected_price_inr:.2f} this "
            f"order was ranked and approved at"
        )

    return ""
