"""Stage 8 — the append-only audit logger that runs alongside every other stage.

WHY THIS FILE EXISTS
--------------------
Our tagline is "autonomy the user can audit". This file is the second half of
that sentence. Everything else in the project decides things; this file is the
only reason a finance manager can check those decisions afterwards without
reading a line of our code.

CLAUDE.md, "Audit trail": every entry uses one schema, written the moment it
happens. That "moment it happens" is the whole design. An assumption recorded
after the purchase went through is not an audit trail, it is a story we told
ourselves afterwards. So the logger is not a reporting step at the end of stage
8 — it is handed to every stage from 0 onwards, and each stage writes its own
line as it acts.

WHAT "APPEND-ONLY" MEANS HERE, CONCRETELY
-----------------------------------------
Three things, and we can point at the code for each:

1. `AuditEntry` is a frozen Pydantic model (see models.py), so an entry cannot
   be edited after it is created.
2. This logger only ever appends — there is no update, no delete, no reorder.
3. Each entry is flushed to its JSONL file the instant it is created, not
   buffered until the run ends. If the demo dies at stage 6, everything up to
   stage 6 is already on disk and readable.

THE FOUR QUESTIONS
------------------
A finance manager opening this log asks four things, and the schema answers them
in order:

    WHAT happened      -> event_type   (DECISION / ASSUMPTION / ESCALATION /
                                        FALLBACK / ACTION — a fixed list, so
                                        there is no vague label to hide behind)
    WHY                -> reasoning    (one sentence, plain words, never a
                                        stack trace)
    WHAT was done      -> detail       (the structured specifics, or an explicit
                                        "no purchase executed")
    WHO needs to know  -> notify       (drives the automatic finance email)

TWO EXPORTS, ONE RECORD
-----------------------
The same entries come out twice: `exports/TXN-4471.jsonl` for systems, and
`finance_view()` for a human. They are two renderings of one record, not two
records — which is why they can never disagree with each other.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.models import Actor, AuditEntry, EventType, TransactionContext

# Meena is in Chennai, so the log reads in her timezone and the timestamps match
# the "+05:30" in the CLAUDE.md schema example. We set the offset explicitly
# instead of using the laptop's local clock, so a run on a judge's machine in
# another timezone still produces the log we described in the deck.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Where the JSONL files land. .gitignore already excludes exports/ and *.jsonl:
# the audit log is a deliverable produced fresh by each run, not a file that sits
# in the repo pretending to be evidence.
EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"


# Stage labels, spelled once. The schema says stage is "5 - decision &
# authorisation" — a number AND a name — so the log reads end to end without
# anyone opening the source. Keeping them here stops nine stages from inventing
# nine slightly different spellings of the same stage.
STAGE_SCOPE = "0 - scope & completeness gate"
STAGE_EXTRACTION = "1 - requirement extraction"
STAGE_WEIGHTS = "2 - preference & weight engine"
STAGE_DISCOVERY = "3 - vendor discovery & filter"
STAGE_RANKING = "4 - ranking"
STAGE_AUTHORISATION = "5 - decision & authorisation"
STAGE_CONFIRMATION = "6 - vendor confirmation & lock"
STAGE_PAYMENT = "7 - mock payment execution"
STAGE_CLOSE = "8 - confirmation & audit close"


def new_transaction_id() -> str:
    """Mint a fresh transaction id, e.g. 'TXN-4471'.

    One id ties an entire order together: hand this string to `replay()` and the
    whole run comes back in sequence. Four random digits is plenty for a
    hackathon demo — we are not running a warehouse, and an id a judge can read
    off the screen is worth more than a UUID nobody can say out loud.
    """
    return f"TXN-{random.randint(1000, 9999)}"


class AuditLogger:
    """The append-only logger for ONE transaction.

    Created once at the start of a run and passed to every stage. Each stage
    calls one of the five event methods below at the moment it acts, and the
    entry is immediately (a) appended to the shared TransactionContext so the UI
    can show it live, and (b) written to disk so it survives a crash.

    Deliberately bound to a single transaction rather than being one global log.
    That is what makes the `entry_id` sequence simple and honest: entry 07 of
    TXN-4471 is the seventh thing that happened to THIS order, with no
    interleaving from another run to explain away.
    """

    def __init__(self, context: TransactionContext, export_dir: Path | None = None) -> None:
        self.context = context
        self.transaction_id = context.transaction_id
        self.export_dir = export_dir or EXPORT_DIR
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.export_dir / f"{self.transaction_id}.jsonl"

    # -- the one write path -------------------------------------------------

    def log(
        self,
        stage: str,
        event_type: EventType,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
        actor: Actor = Actor.AGENT,
    ) -> AuditEntry:
        """Record one event, right now. The only function in this file that writes.

        Every other method below is a thin wrapper around this one, so there is a
        single place where an entry is built, numbered, stamped and flushed. One
        write path means one thing to check when a judge asks whether we could
        have quietly skipped a log line.

        The timestamp is taken here, at the moment of the event — not when the
        file is later exported.
        """
        sequence = len(self.context.audit) + 1
        entry = AuditEntry(
            entry_id=f"{self.transaction_id}-{sequence:02d}",
            transaction_id=self.transaction_id,
            timestamp=datetime.now(IST),
            stage=stage,
            event_type=event_type,
            actor=actor,
            detail=detail or {},
            reasoning=reasoning,
            notify=notify or ["requester"],
        )
        self.context.audit.append(entry)
        self._flush(entry)
        return entry

    def _flush(self, entry: AuditEntry) -> None:
        """Append one JSON line to the transaction's file, immediately.

        Opened in append mode and closed again each time on purpose. It is a few
        microseconds slower than holding the file open, and in exchange the log
        on disk is always complete up to the last thing that happened — even if
        the next stage throws, or we hit Ctrl-C mid-demo.
        """
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")

    # -- the five event types, one method each ------------------------------
    # These exist so a call site reads like the thing that happened
    # ("audit.escalation(...)") rather than like a logging library. They add no
    # behaviour: each one names its event_type and hands over to log().

    def decision(
        self,
        stage: str,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
        actor: Actor = Actor.AGENT,
    ) -> AuditEntry:
        """Somebody chose something. Ranking outcomes, and the user's approval."""
        return self.log(stage, EventType.DECISION, reasoning, detail, notify, actor)

    def assumption(
        self,
        stage: str,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
    ) -> AuditEntry:
        """The agent filled a gap with a declared default.

        Always the agent, never the user — by definition an assumption is what we
        did BECAUSE the user did not say. Written the moment the default is
        applied, before discovery is allowed to use it.
        """
        return self.log(stage, EventType.ASSUMPTION, reasoning, detail, notify, Actor.AGENT)

    def escalation(
        self,
        stage: str,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
    ) -> AuditEntry:
        """The agent stopped and asked a human. The edge of its authority.

        Notifies finance as well as the requester unless a call site says
        otherwise: an escalation is precisely the case where somebody beyond the
        person who asked needs to know, and forgetting to add them at one call
        site should not be able to quietly shrink the audience.
        """
        return self.log(
            stage,
            EventType.ESCALATION,
            reasoning,
            detail,
            notify or ["requester", "finance"],
            Actor.AGENT,
        )

    def fallback(
        self,
        stage: str,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
    ) -> AuditEntry:
        """The agent moved to the next eligible option after something failed."""
        return self.log(stage, EventType.FALLBACK, reasoning, detail, notify, Actor.AGENT)

    def action(
        self,
        stage: str,
        reasoning: str,
        detail: dict[str, Any] | None = None,
        notify: list[str] | None = None,
        actor: Actor = Actor.AGENT,
    ) -> AuditEntry:
        """The agent did something in the world: locked an order, took a payment."""
        return self.log(stage, EventType.ACTION, reasoning, detail, notify, actor)

    # -- reading it back ----------------------------------------------------

    def entries(self) -> list[AuditEntry]:
        """Every entry so far, in the order it happened. Never sorted, never filtered."""
        return list(self.context.audit)

    def notify_list(self) -> list[str]:
        """Everyone any entry said should be told, de-duplicated, first-mention order.

        This is what drives the automatic finance email at stage 8. Reading it
        off the entries rather than tracking it separately means the audience is
        derived from the log itself — we cannot notify finance during the demo
        and forget to write down why they were told.
        """
        seen: list[str] = []
        for entry in self.context.audit:
            for who in entry.notify:
                if who not in seen:
                    seen.append(who)
        return seen

    def jsonl_path(self) -> Path:
        """Where this transaction's machine-readable export lives."""
        return self.path

    def finance_view(self) -> str:
        """The same entries rendered as a one-page log a human can read.

        CLAUDE.md: exported twice from one record — JSONL for systems, a rendered
        view for the auditor. Note that this method computes nothing and re-reads
        nothing; it only formats `self.context.audit`. That is the point. The
        page a finance manager reads and the file a system ingests cannot drift
        apart, because there is one record underneath both.
        """
        lines = [
            f"AUDIT TRAIL - {self.transaction_id}",
            f"Status: {self.context.status.value}",
            f"Entries: {len(self.context.audit)}   Notify: {', '.join(self.notify_list()) or '-'}",
            "=" * 72,
        ]
        for entry in self.context.audit:
            lines.append("")
            lines.append(f"[{entry.entry_id}]  {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S %z')}")
            lines.append(f"  Stage    : {entry.stage}")
            lines.append(f"  What     : {entry.event_type.value}  (by {entry.actor.value})")
            lines.append(f"  Why      : {entry.reasoning}")
            if entry.detail:
                lines.append("  Detail   :")
                for key, value in entry.detail.items():
                    lines.append(f"             - {key}: {value}")
            else:
                # An empty detail block is itself information — most often it
                # means nothing was bought — so we say so rather than print
                # nothing and leave the reader guessing.
                lines.append("  Detail   : (none recorded - no action taken)")
            lines.append(f"  Notify   : {', '.join(entry.notify) or '-'}")
        lines.append("")
        lines.append("=" * 72)
        lines.append(f"End of trail. Machine-readable copy: {self.path.name}")
        return "\n".join(lines)


def replay(transaction_id: str, export_dir: Path | None = None) -> list[AuditEntry]:
    """Read one transaction's whole run back from disk, in sequence.

    This function is the proof of the claim in CLAUDE.md: one transaction_id
    replays the whole order without anyone reading code. Nothing here
    reconstructs or infers anything — it parses the lines that were written as
    things happened, in the order they were written.

    Used by the audit screen, so we can close the app, reopen it, and still show
    the run a judge just watched.
    """
    directory = export_dir or EXPORT_DIR
    path = directory / f"{transaction_id}.jsonl"
    if not path.exists():
        return []
    entries: list[AuditEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(AuditEntry.model_validate(json.loads(line)))
    return entries
