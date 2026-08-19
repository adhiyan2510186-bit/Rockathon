"""The shapes of every piece of data that moves between stages 0 -> 8.

WHY THIS FILE EXISTS
--------------------
Nine stages hand work to each other. If each stage invented its own idea of what
a "product" or a "brief" looks like, the seams between them would be where the
demo breaks. This file defines each shape exactly once, so stage 3 physically
cannot hand stage 4 something stage 4 does not understand.

It is the project's vocabulary. There is no logic here — nothing scores, filters,
or decides. Those live in their own stage files. This file only answers "what
does the data look like?".

WHY PYDANTIC
------------
CLAUDE.md, "Discovery": two mock catalogs with deliberately DIFFERENT schemas,
normalised into one Product model. Pydantic is what makes that normalisation
real work rather than hope — if the aggregator CSV calls a field "rate_inr" and
forgets the reliability score, building a Product raises an error at the moment
of the mistake, naming the field. A silently missing reliability score would
change the ranking on stage, and we would not know why.

WHAT IS DELIBERATELY *NOT* HERE
-------------------------------
No weights, no limits, no thresholds. Those come from config.yaml through
agent/config.py, and only from there. See CLAUDE.md, "THE ONE RULE".
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# The three constraint classes (Stage 1)
# ---------------------------------------------------------------------------

class ConstraintClass(str, Enum):
    """Which of the three jobs a requirement field does.

    CLAUDE.md, "The three constraint classes":

    HARD      pass/fail. Controls eligibility at stage 3. A product that fails a
              hard constraint is removed from the pool and never scored.
    SOFT      ranking only. Never rejects anything. Decides how much we prefer
              one survivor over another at stage 4.
    AMBIGUOUS the parser could not extract this confidently. Asked once before
              discovery starts; if unanswered, a declared default is applied and
              logged as ASSUMED.
    """

    HARD = "hard"
    SOFT = "soft"
    AMBIGUOUS = "ambiguous"


# Which class each requirement field belongs to.
#
# This is a fixed table, not something the LLM decides per run. "Is a per-unit
# price cap a hard constraint?" has one answer for this product, always, and a
# judge can read it here. The language model's job is to find the VALUE in the
# user's sentence; this table already knows what that value is for.
#
# Note price and delivery appear as HARD here and again in SOFT_CRITERIA below.
# That is intentional and it is not double counting: stage 3 asks "does this
# qualify?" (is it under the cap at all) and stage 4 asks "how much do we prefer
# it among the ones that qualified?" (how far under the cap it landed). One
# measurement, two different questions. See CLAUDE.md, "Classification controls
# eligibility, not scoring".
CONSTRAINT_CLASS: dict[str, ConstraintClass] = {
    # HARD — eligibility gates applied by the stage-3 filter.
    "category": ConstraintClass.HARD,
    "quantity": ConstraintClass.HARD,
    "specs": ConstraintClass.HARD,
    "max_price_per_unit_inr": ConstraintClass.HARD,
    "max_delivery_days": ConstraintClass.HARD,
    # SOFT — ranking inputs applied by the stage-4 scorer.
    "reliability": ConstraintClass.SOFT,
    "replacement": ConstraintClass.SOFT,
    "price": ConstraintClass.SOFT,
    "delivery": ConstraintClass.SOFT,
}

# The non-negotiable tier of the hard constraints. These are never relaxed, not
# even when nothing passes the filter and the escalation handler is looking for
# room to move. Ordering 5,000 boxes and receiving 500 is not a near-miss.
NON_NEGOTIABLE_FIELDS: tuple[str, ...] = ("category", "quantity", "specs")

# The negotiable tier. When no product passes every hard gate, the escalation
# handler may propose relaxing these — propose, never apply silently.
NEGOTIABLE_FIELDS: tuple[str, ...] = ("max_price_per_unit_inr", "max_delivery_days")

# The four soft criteria that stage 4 scores on, in the order we display them.
# Weights for these come from config.yaml and must sum to 1.0.
SOFT_CRITERIA: tuple[str, ...] = ("reliability", "price", "replacement", "delivery")


class FieldStatus(str, Enum):
    """Where a value in the brief actually came from.

    CONFIRMED  the user said it, or answered our one clarifying question.
    ASSUMED    we applied a declared default from config.yaml because the brief
               was silent and the user did not answer.

    These must never be blurred together. An assumption presented as an
    instruction is the exact failure the audit trail exists to prevent, so the
    UI and the log always show which one a value is.
    """

    CONFIRMED = "confirmed"
    ASSUMED = "assumed"


# ---------------------------------------------------------------------------
# Stage 0 — scope gate
# ---------------------------------------------------------------------------

class ScopeVerdict(BaseModel):
    """Stage 0's answer to "should we even start on this message?".

    Three outcomes, and only these three:

    OUT_OF_SCOPE  not a purchasing request. We say what we do handle and wait
                  for the next message. No discovery, no log entry beyond the
                  refusal itself.
    INCOMPLETE    a real buying brief, but missing something we cannot guess at.
                  We ask ONE targeted question. CLAUDE.md: no discovery starts
                  on a guess.
    IN_SCOPE      complete enough to parse. Stage 1 takes over.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Literal["out_of_scope", "incomplete", "in_scope"]
    message: str = Field(
        description="What we say to the user: the scope statement, the one "
        "question, or an empty string when we simply proceed."
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Field names the brief left blank. Drives the one question.",
    )


# ---------------------------------------------------------------------------
# Stage 1 & 2 — the parsed brief
# ---------------------------------------------------------------------------

class Brief(BaseModel):
    """The user's sentence, turned into structured data. Stage 1's output.

    This is the handover point between the language model and plain Python.
    Everything above this line is interpretation of English; everything below it
    is arithmetic. The LLM fills these fields in and then has no further say in
    the run.

    Note what is absent: no weights and no authorisation limit. Stage 2 computes
    weights from `stated_priorities` plus config.yaml, and stage 5 reads the
    limit from config.yaml. Neither number was ever shown to the model.
    """

    raw_text: str = Field(description="The user's original sentence, kept verbatim for the audit log.")

    # --- HARD fields: these gate eligibility at stage 3 -----------------------
    category: str = Field(description="Procurement category, e.g. 'packaging'. Also picks the default weights.")
    quantity: int = Field(gt=0, description="Units required. Non-negotiable — 5,000 means 5,000.")
    specs: list[str] = Field(
        default_factory=list,
        description="Required physical specs, e.g. ['double-wall', '200x150x80mm']. Non-negotiable.",
    )
    max_price_per_unit_inr: float = Field(gt=0, description="Per-unit ceiling (Rs 22). Negotiable tier.")
    max_delivery_days: int = Field(gt=0, description="Delivery window in days (10). Negotiable tier.")

    # --- SOFT input: what the user said mattered ------------------------------
    stated_priorities: dict[str, str] = Field(
        default_factory=dict,
        description="Criterion -> priority PHRASE LABEL the LLM recognised, e.g. "
        "{'reliability': 'matters_a_lot'}. Labels, never numbers: config.py turns "
        "'matters_a_lot' into 0.45. The model never sees 0.45.",
    )

    # --- Provenance and escalation inputs -------------------------------------
    field_status: dict[str, FieldStatus] = Field(
        default_factory=dict,
        description="Field name -> CONFIRMED or ASSUMED. Every ASSUMED entry is "
        "written to the audit log at the moment the assumption is made.",
    )
    flexibility_order: list[str] = Field(
        default_factory=list,
        description="Negotiable fields in the order the user said they would bend, "
        "if they said. Empty means the escalation handler falls back to "
        "smallest-violation ordering (a known limit we name on purpose).",
    )

    def classification(self, field_name: str) -> ConstraintClass:
        """Say whether a field is a hard gate, a soft preference, or ambiguous.

        A thin lookup into the fixed CONSTRAINT_CLASS table, here so the UI can
        show each parsed field's class next to it without importing the table.
        Anything we do not recognise is AMBIGUOUS rather than quietly ignored.
        """
        return CONSTRAINT_CLASS.get(field_name, ConstraintClass.AMBIGUOUS)


class Weights(BaseModel):
    """Stage 2's output: the four soft weights, and where each one came from.

    Kept as its own model rather than a bare dict so the "why is reliability
    0.45?" question has an answer attached to the number itself. `sources` says
    'user-stated' or 'category default' per criterion, and the approval screen
    prints it.
    """

    values: dict[str, float] = Field(description="Criterion -> weight. Sums to 1.0.")
    sources: dict[str, str] = Field(
        default_factory=dict,
        description="Criterion -> 'user-stated' or 'category default (packaging)'.",
    )


# ---------------------------------------------------------------------------
# Stage 3 — the normalised product
# ---------------------------------------------------------------------------

class Product(BaseModel):
    """One purchasable item, in the single shape the rest of the pipeline uses.

    Both vendor sources land here: PackHub's direct JSON and BoxBazaar's
    aggregator CSV have different field names, different units, and different
    ideas of what a reliability score is. The normaliser in discovery.py does
    that translation, and this model is what it must translate INTO. If it
    misses a field, Pydantic raises here rather than producing a silently wrong
    score three stages later.

    Every field below is either a hard gate input or a soft scoring input. There
    is nothing decorative in this model.
    """

    model_config = ConfigDict(frozen=True)  # A product must not change under the ranker.

    product_id: str = Field(description="Stable id, e.g. 'PH-CORUSAFE-DW'. Used by the audit log.")
    name: str = Field(description="Display name, e.g. 'Corusafe DW'.")
    source: str = Field(description="Where it came from, e.g. 'PackHub'.")
    source_type: Literal["direct", "aggregator"] = Field(
        description="Direct vendor or aggregator. Shown in the comparison table because "
        "the two source shapes are half of what stage 3 is demonstrating."
    )

    category: str = Field(description="Matched against the brief's category — a hard gate.")
    specs: list[str] = Field(
        default_factory=list,
        description="Physical specs in the same vocabulary as Brief.specs, so the "
        "stage-3 filter can compare them without guessing.",
    )

    # --- Hard gate inputs -----------------------------------------------------
    price_per_unit_inr: float = Field(gt=0, description="Compared to the brief's cap at stage 3, then scored at stage 4.")
    delivery_days: int = Field(ge=0, description="Compared to the brief's window at stage 3, then scored at stage 4.")
    available_quantity: int = Field(ge=0, description="Must cover the brief's quantity. Re-checked at stage 6.")

    # --- Soft scoring inputs --------------------------------------------------
    reliability_rating: float = Field(ge=0, le=5, description="Seller reliability out of 5, e.g. 4.8.")
    replacement_window_days: int = Field(ge=0, description="Days to raise a replacement claim, e.g. 7.")

    @property
    def label(self) -> str:
        """'Corusafe DW - PackHub (direct)', the row heading in the comparison table."""
        return f"{self.name} - {self.source} ({self.source_type})"

    def order_total_inr(self, quantity: int) -> float:
        """Cost of buying `quantity` of this product. Stage 5 checks this against the limit.

        Plain multiplication, deliberately. This is the number that decides
        whether a human is asked, so it is one line a judge can verify by hand.
        """
        return self.price_per_unit_inr * quantity


class FilterResult(BaseModel):
    """Why one product passed or failed the stage-3 hard gate.

    We keep the failures, not just the survivors. When nothing passes, the
    escalation handler needs the violation sizes to build the near-miss list,
    and "rejected because delivery is 12 days against a 10-day window" is the
    sentence that makes the refusal auditable.
    """

    product: Product
    passed: bool
    violations: dict[str, str] = Field(
        default_factory=dict,
        description="Field name -> plain-words explanation, e.g. "
        "{'max_price_per_unit_inr': 'Rs 24.50 exceeds the Rs 22.00 cap by Rs 2.50'}.",
    )


# ---------------------------------------------------------------------------
# Stage 4 — scoring
# ---------------------------------------------------------------------------

class ScoreTerm(BaseModel):
    """One line of the score arithmetic: weight x normalised = contribution.

    The formula is `score = SUM(weight x normalised)` over the soft criteria, so
    a score is only as defensible as its terms. Storing every term means the UI
    can print Corusafe's total as the four numbers that produced it, and the
    "reliability contributed 0.450 of 0.580" claim is read off the data rather
    than recomputed for the slide.
    """

    model_config = ConfigDict(frozen=True)

    criterion: str
    weight: float = Field(ge=0, le=1, description="From stage 2. User-stated or category default.")
    raw_value: float = Field(description="The product's actual figure: 4.8, 21.90, 4 days.")
    normalised: float = Field(ge=0, le=1, description="That figure mapped onto 0-1 for comparison.")
    method: str = Field(
        description="How it was normalised: 'min-max across survivors' or 'sqrt(margin vs cap)'. "
        "Margins use sqrt so a bargain cannot drown out what the user said mattered."
    )

    @property
    def contribution(self) -> float:
        """weight x normalised — this term's share of the final score."""
        return self.weight * self.normalised


class ScoredProduct(BaseModel):
    """A product that survived the hard gate, with its score and its arithmetic."""

    product: Product
    terms: list[ScoreTerm] = Field(description="One per soft criterion, in display order.")
    score: float = Field(ge=0, le=100, description="Sum of contributions x 100. 58.0 for Corusafe.")
    rank: int = Field(ge=1, description="1 is the winner.")

    def contribution(self, criterion: str) -> float:
        """This product's score share from one criterion. 0.450 for Corusafe's reliability."""
        for term in self.terms:
            if term.criterion == criterion:
                return term.contribution
        return 0.0


# ---------------------------------------------------------------------------
# Stage 8 — the audit entry
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """The five things that can happen to a transaction. Nothing else is loggable.

    A fixed list is the point. A finance manager reading the log should never
    meet an event type they have to ask about, and we should never be able to
    bury an escalation under a vague label.
    """

    DECISION = "DECISION"        # the agent chose something
    ASSUMPTION = "ASSUMPTION"    # the agent filled a gap with a declared default
    ESCALATION = "ESCALATION"    # the agent stopped and asked a human
    FALLBACK = "FALLBACK"        # the agent moved to the next eligible option
    ACTION = "ACTION"            # the agent did something in the world (paid, ordered)


class Actor(str, Enum):
    """Who caused the entry. Two options, because there are two participants."""

    AGENT = "AGENT"
    USER = "USER"


class AuditEntry(BaseModel):
    """One line of the audit trail, in the exact schema from CLAUDE.md.

    The log answers a finance manager's four questions:
      WHAT happened        -> event_type, in plain words
      WHY                  -> reasoning, one sentence, never a stack trace
      WHAT was done        -> detail, or "no purchase executed"
      WHO needs to know    -> notify, which drives the finance email

    Written at the moment of the event, never assembled afterwards. An
    assumption logged after the purchase is just a story.
    """

    model_config = ConfigDict(frozen=True)  # Append-only: an entry is never edited.

    entry_id: str = Field(description="'TXN-4471-07' — transaction id plus sequence number.")
    transaction_id: str = Field(description="'TXN-4471'. Replays one whole order in sequence.")
    timestamp: datetime = Field(description="When it happened, with timezone. Not when it was written up.")
    stage: str = Field(description="'5 - decision & authorisation'. Number and name, so the log reads without code.")
    event_type: EventType
    actor: Actor
    detail: dict[str, Any] = Field(default_factory=dict, description="The structured specifics: amounts, ids, scores.")
    reasoning: str = Field(description="One sentence, plain words. The 'why' a human reads first.")
    notify: list[str] = Field(default_factory=list, description="e.g. ['requester', 'finance'].")


# ---------------------------------------------------------------------------
# The shared transaction context
# ---------------------------------------------------------------------------

class TransactionStatus(str, Enum):
    """Where a transaction currently stands.

    AWAITING_APPROVAL is the one that matters: silence is never approval, so a
    transaction can sit here and expire without anything being bought. The state
    is preserved, which is why approving later resumes at stage 6 instead of
    re-running discovery.
    """

    PARSING = "parsing"
    DISCOVERING = "discovering"
    RANKED = "ranked"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    CONFIRMING = "confirming"
    PAYING = "paying"
    COMPLETED = "completed"
    DECLINED = "declined"
    EXPIRED = "expired"


class TransactionContext(BaseModel):
    """Everything known about one order, carried alongside every stage.

    CLAUDE.md: a shared Transaction Context and an append-only Audit Logger run
    alongside every stage — no state is reconstructed after the fact. This model
    is that context. Each stage reads what it needs and adds its own output; no
    stage recomputes an earlier stage's work from scratch.

    It is also what makes "approve later" honest. The ranked list sitting in
    `ranked` is the list the user was shown, not a fresh one that might have
    come out differently.
    """

    transaction_id: str = Field(description="'TXN-4471'. Ties every audit entry together.")
    status: TransactionStatus = TransactionStatus.PARSING

    brief: Brief | None = None
    weights: Weights | None = None
    filter_results: list[FilterResult] = Field(default_factory=list, description="Stage 3, survivors and failures.")
    ranked: list[ScoredProduct] = Field(default_factory=list, description="Stage 4, best first.")
    selected: ScoredProduct | None = Field(default=None, description="The product we are actually buying.")

    audit: list[AuditEntry] = Field(default_factory=list, description="Append-only. Never edited, never reordered.")

    @property
    def eligible(self) -> list[Product]:
        """The products that passed every hard gate. Stage 4 scores exactly these."""
        return [result.product for result in self.filter_results if result.passed]

    @property
    def rejected(self) -> list[FilterResult]:
        """The products that failed, with their violations — the near-miss raw material."""
        return [result for result in self.filter_results if not result.passed]

    def score_gap(self) -> float | None:
        """Points between #1 and #2, or None if there is no runner-up.

        Stage 5 compares this to the substitution threshold in config.yaml. In
        the demo it is 9.3 (58.0 - 48.7), comfortably over the 5-point line, so
        the agent escalates rather than silently swapping in #2.
        """
        if len(self.ranked) < 2:
            return None
        return round(self.ranked[0].score - self.ranked[1].score, 1)
