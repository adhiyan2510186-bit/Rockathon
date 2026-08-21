"""Stage 8 — what the closing entry claims about who authorised the purchase.

The audit trail is the product. An entry that says a human approved an order no
human ever saw is not a cosmetic bug: it is the record a finance manager reads,
saying the opposite of what happened. These tests pin both directions.
"""

from __future__ import annotations

import pytest

from agent import (
    audit as audit_module,
    authorisation,
    close,
    discovery,
    language,
    payment,
    ranking,
    signals,
    vendor,
    weights,
)
from agent.audit import STAGE_AUTHORISATION
from agent.models import Actor, EventType, TransactionContext

CHAIRS = "6 office chairs, mesh back, max Rs 8000 each, within 10 days"
BOXES = (
    "5,000 kraft mailer boxes, double-wall, 200x150x80 mm, max Rs 22 per unit, "
    "delivered within 10 days. Reliability matters a lot."
)


def run_to_close(text: str, context_tag: str | None = None):
    """The whole pipeline, offline, ending in a closed transaction.

    Approves only when the agent actually needs a human — which is the thing
    under test. A run that is inside the limit is never handed an approval.
    """
    context = TransactionContext(transaction_id=audit_module.new_transaction_id())
    log = audit_module.AuditLogger(context)

    parsed = language.extract_brief(text, log, force_offline=True)
    brief = parsed.brief
    if context_tag:
        brief = brief.model_copy(update={"context_tag": context_tag})
    context.brief = brief

    context.weights = weights.compute(brief, log)
    context.filter_results = discovery.run(brief, log)
    context.ranked = ranking.rank(
        [result.product for result in context.filter_results if result.passed],
        context.weights,
        brief.max_price_per_unit_inr,
        brief.max_delivery_days,
        log,
    )
    signals.read(context.ranked, brief, log)

    outcome = authorisation.authorise(context, log)
    if not outcome.within_limit:
        authorisation.approve(context, log)

    confirmation = vendor.confirm(context, log)
    paid = payment.pay(context, log, confirmation)
    return context, outcome, close.close(context, log, confirmation, paid)


def test_an_order_the_agent_cleared_alone_does_not_claim_a_human_approved_it():
    """The regression this file was written for.

    A furniture order sits inside the authorisation limit on purpose, so the
    agent proceeds without asking. The buyer still answers the usage-context
    question, and that answer is recorded as a USER decision at stage 2. It is a
    preference, not a sign-off, and the close must not read it as one.
    """
    context, outcome, summary = run_to_close(CHAIRS, context_tag="all_day_desk")

    assert outcome.within_limit, "this brief is meant to stay inside the limit"
    assert summary.approved_by_human is False
    assert "within the agent's own limit" in summary.headline
    assert "human approval" not in summary.headline


def test_the_buyers_context_answer_is_still_recorded_as_theirs():
    """The fix must not work by quietly relabelling the user's own choice.

    The stage-2 entry stays a USER decision. What changed is that stage 8 now
    asks WHERE the decision was made, not merely who made it.
    """
    context, _, _ = run_to_close(CHAIRS, context_tag="all_day_desk")

    user_decisions = [
        entry
        for entry in context.audit
        if entry.actor is Actor.USER and entry.event_type is EventType.DECISION
    ]
    assert user_decisions, "the context answer is the buyer's, and stays theirs"
    assert all(entry.stage != STAGE_AUTHORISATION for entry in user_decisions)


def test_an_order_over_the_limit_records_the_approval_it_actually_got():
    """The other direction, so the fix cannot pass by always answering False."""
    context, outcome, summary = run_to_close(BOXES)

    assert not outcome.within_limit, "this brief is meant to exceed the limit"
    assert summary.approved_by_human is True
    assert "after human approval" in summary.headline
