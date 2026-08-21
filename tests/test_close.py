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


# ---------------------------------------------------------------------------
# One transaction id, one order
# ---------------------------------------------------------------------------
# CLAUDE.md's promise about the audit trail is that one transaction_id replays
# the whole order in sequence. These pin the two things that promise rests on.

def test_a_fresh_id_is_never_one_the_export_directory_already_holds(tmp_path, monkeypatch):
    """Ids are checked against disk, not merely drawn at random.

    The draw is forced to hand back a number that is already taken twice over,
    so the retry loop is what has to save it. The old version returned the first
    number it thought of and let append mode do the rest.
    """
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "TXN-1111.jsonl").touch()
    (exports / "TXN-2222.jsonl").touch()

    draws = iter([1111, 2222, 3333])
    monkeypatch.setattr(audit_module.random, "randint", lambda *_: next(draws))

    assert audit_module.new_transaction_id(export_dir=exports) == "TXN-3333"


def test_a_saturated_directory_still_yields_an_unused_id(tmp_path, monkeypatch):
    """When every four-digit draw is taken, readability gives way to uniqueness.

    A wider id is ugly on screen. Two orders in one file is worse.
    """
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "TXN-4471.jsonl").touch()

    monkeypatch.setattr(audit_module, "_MINT_ATTEMPTS", 3)
    monkeypatch.setattr(audit_module.random, "randint", lambda low, high: 4471 if high == 9999 else 123456)

    assert audit_module.new_transaction_id(export_dir=exports) == "TXN-123456"


def test_two_runs_never_write_into_one_audit_file(tmp_path):
    """A reused id must fail loudly rather than append a second order.

    This is the failure we actually found in exports/: one file holding two
    complete runs, entry numbering restarting from 01 halfway down, and nothing
    anywhere saying so. Replaying that id returned two interleaved orders.
    """
    exports = tmp_path / "exports"

    first = TransactionContext(transaction_id="TXN-SAME")
    audit_module.AuditLogger(first, export_dir=exports).decision(
        STAGE_AUTHORISATION, "the first run"
    )

    second = TransactionContext(transaction_id="TXN-SAME")
    clashing = audit_module.AuditLogger(second, export_dir=exports)

    with pytest.raises(FileExistsError, match="already exists"):
        clashing.decision(STAGE_AUTHORISATION, "the second run")

    replayed = audit_module.replay("TXN-SAME", export_dir=exports)
    assert len(replayed) == 1
    assert replayed[0].reasoning == "the first run"
