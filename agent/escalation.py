"""The ONE escalation handler — one mechanism, entered from four places.

WHY THIS FILE EXISTS
--------------------
Four different things can go wrong in a run, at four different stages:

    stage 3   nothing passed the hard gates
    stage 5   a valid match, but the order total is over the authorisation limit
    stage 6   the winner turned out to be unavailable, or its price moved
    stage 7   the payment was declined twice

The tempting thing is to patch each of those where it happens. CLAUDE.md,
"Escalation & fallback", says explicitly not to: ONE mechanism, invoked from
four call sites, not four separate code paths. That is not tidiness for its own
sake. The boundary we are demonstrating is "here is where the agent's authority
ends", and a boundary implemented four times is four chances to draw it in four
slightly different places. Implemented once, a judge can read the edge of the
agent's authority in a single file.

THE FIVE STEPS, IN ORDER
------------------------
Every trigger runs the same five steps. Each has its own section below.

    1. DETECT             which invariant broke, at which stage
    2. RE-VALIDATE        is the next option independently eligible? Never trust
                          sort order — a product that was eligible ten seconds
                          ago is re-checked against the hard gates before we
                          lean on it
    3. RELAX              negotiable constraints only, in the user's declared
                          flexibility order — PROPOSED, never applied
    4. RE-RANK & SURFACE  2-3 options, each with its violation delta spelled out
    5. ESCALATE & LOG     one audit entry, state preserved, no silent action

WHAT THIS FILE IS NOT ALLOWED TO DO
-----------------------------------
- It never relaxes a NON-NEGOTIABLE constraint. Category, quantity and specs are
  never bent, not even to rescue a demo. Ordering 5,000 boxes and receiving 500
  is not a near-miss.
- It never applies a relaxation by itself. It writes down what relaxing WOULD
  admit and hands that to a human. Proposed, never applied silently.
- It never raises the authorisation limit. The limit is the whole point; an
  escalation handler that could move it would be a bug wearing a feature's hat.
- It never treats silence as approval. When it escalates, the transaction is
  parked in AWAITING_APPROVAL with all its state intact, so approving later
  resumes at stage 6 rather than re-running discovery.

THE ONE CASE WHERE IT ACTS ALONE
--------------------------------
Trigger 3 only. If the winner falls over and the next option is independently
eligible AND trails by no more than the substitution threshold from config.yaml
(5 points), the agent swaps and logs a FALLBACK. A wider gap means #2 is a
meaningfully worse fit for what the user actually asked for, so a human decides.
In our demo the gap is 9.3 points, so the agent escalates rather than swapping.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agent import config, discovery
from agent.audit import (
    STAGE_AUTHORISATION,
    STAGE_CONFIRMATION,
    STAGE_DISCOVERY,
    STAGE_PAYMENT,
    AuditLogger,
)
from agent.models import (
    NEGOTIABLE_FIELDS,
    Brief,
    FilterResult,
    Product,
    ScoredProduct,
    TransactionContext,
    TransactionStatus,
)

# How many alternatives we put in front of a human. CLAUDE.md says 2-3. Two is a
# comparison; three is a shortlist; ten is homework we are handing back to the
# person we were supposed to be helping.
MAX_OPTIONS_SURFACED = 3

# Plain-words names for the negotiable fields, for the sentences a human reads.
FIELD_LABELS: dict[str, str] = {
    "max_price_per_unit_inr": "per-unit price cap",
    "max_delivery_days": "delivery window",
}


class Trigger(str, Enum):
    """The four call sites, mapped onto CLAUDE.md's three escalation triggers.

    NO_ELIGIBLE_MATCH            trigger 1, raised at stage 3
    OVER_AUTHORISATION_LIMIT     trigger 2, raised at stage 5
    UNAVAILABLE_AT_CONFIRMATION  trigger 3, raised at stage 6
    PAYMENT_DECLINED             trigger 3, raised at stage 7

    Four names for three triggers because the last two are the same situation
    ("the option we picked cannot be bought") discovered at two different stages,
    and the audit log should say which stage found it. They share one handler
    below, so they cannot drift apart in behaviour.

    Deliberately absent: "the top pick is imperfect on a soft preference".
    Ranking already absorbs that. If it escalated, every order would escalate,
    and the boundary we are demonstrating would be erased.
    """

    NO_ELIGIBLE_MATCH = "no_eligible_match"
    OVER_AUTHORISATION_LIMIT = "over_authorisation_limit"
    UNAVAILABLE_AT_CONFIRMATION = "unavailable_at_confirmation"
    PAYMENT_DECLINED = "payment_declined"


# Which stage label each trigger writes into the audit log. A table rather than
# an if-chain, so "which stage does a payment decline log against?" is one line.
TRIGGER_STAGE: dict[Trigger, str] = {
    Trigger.NO_ELIGIBLE_MATCH: STAGE_DISCOVERY,
    Trigger.OVER_AUTHORISATION_LIMIT: STAGE_AUTHORISATION,
    Trigger.UNAVAILABLE_AT_CONFIRMATION: STAGE_CONFIRMATION,
    Trigger.PAYMENT_DECLINED: STAGE_PAYMENT,
}


class SurfacedOption(BaseModel):
    """One alternative shown to the human, with the cost of choosing it spelled out.

    An option without its delta is just a suggestion. "EcoMail DW, Rs 17.60" tells
    an approver nothing they can act on; "EcoMail DW, 24.3 points behind, arrives
    9 days out instead of 4" is a decision they can actually make. So every option
    surfaced from here carries the specific thing that is wrong with it.
    """

    label: str = Field(description="'MegaBox DW - PackHub (direct)', the row heading.")
    product: Product
    order_total_inr: float = Field(description="What buying this one would actually cost.")
    score: float | None = Field(
        default=None,
        description="Its ranking score, when it has one. Near-misses from stage 3 "
        "have none: they never passed the hard gates, so they were never scored.",
    )
    score_gap: float | None = Field(
        default=None, description="Points behind the option it is being offered against."
    )
    violations: dict[str, str] = Field(
        default_factory=dict,
        description="Field name -> plain words, straight from the stage-3 filter.",
    )
    note: str = Field(default="", description="One sentence: why this is on the list.")


class EscalationOutcome(BaseModel):
    """What the handler decided, in a shape the UI and the audit log both read.

    `resolved` is the field that matters. True means the agent handled it alone
    (the only case being an in-threshold fallback at trigger 3) and the run
    continues. False means a human is now required and nothing further happens
    until they answer.
    """

    resolved: bool = Field(
        description="True: handled autonomously via fallback. False: a human is required."
    )
    trigger: Trigger
    stage: str = Field(description="Where it was detected, e.g. '5 - decision & authorisation'.")
    headline: str = Field(description="One plain sentence. The first thing a human reads.")
    options: list[SurfacedOption] = Field(
        default_factory=list, description="2-3 alternatives with their deltas."
    )
    selected: ScoredProduct | None = Field(
        default=None, description="The fallback the agent moved to, when resolved is True."
    )
    proposed_relaxations: list[str] = Field(
        default_factory=list,
        description="What relaxing a NEGOTIABLE constraint would admit. Proposed, never applied.",
    )
    detail: dict = Field(default_factory=dict, description="The structured specifics for the log.")


# ---------------------------------------------------------------------------
# The single entry point — all four call sites come through here
# ---------------------------------------------------------------------------

def handle(
    context: TransactionContext,
    trigger: Trigger,
    audit: AuditLogger,
    failed_product: Product | None = None,
    failure_note: str = "",
    exclude_ids: set[str] | None = None,
) -> EscalationOutcome:
    """Run the five steps for whichever invariant broke. The only public function here.

    Stages 3, 5, 6 and 7 all call this one function. They differ in what they
    pass, never in what happens next:

        stage 3   handle(ctx, Trigger.NO_ELIGIBLE_MATCH, audit)
        stage 5   handle(ctx, Trigger.OVER_AUTHORISATION_LIMIT, audit)
        stage 6   handle(ctx, Trigger.UNAVAILABLE_AT_CONFIRMATION, audit,
                         failed_product=p, failure_note="is out of stock")
        stage 7   handle(ctx, Trigger.PAYMENT_DECLINED, audit,
                         failed_product=p, failure_note="was declined twice")

    `exclude_ids` is how a second failure remembers the first: a product we have
    already tried and lost is never offered back as its own fallback.
    """
    if context.brief is None:
        raise ValueError(
            "Escalation needs the brief to re-validate against. "
            "Stage 1 must have run before anything can escalate."
        )

    exclude = set(exclude_ids or set())
    if failed_product is not None:
        exclude.add(failed_product.product_id)

    if trigger is Trigger.NO_ELIGIBLE_MATCH:
        return _no_eligible_match(context, audit)
    if trigger is Trigger.OVER_AUTHORISATION_LIMIT:
        return _over_authorisation_limit(context, audit)
    return _option_failed(context, trigger, audit, failed_product, failure_note, exclude)


# ---------------------------------------------------------------------------
# Trigger 1 — stage 3, nothing passed the hard gates
# ---------------------------------------------------------------------------

def _no_eligible_match(context: TransactionContext, audit: AuditLogger) -> EscalationOutcome:
    """Nothing qualified. Surface the closest misses and what it would take to admit them.

    The five steps, in this case:

    DETECT       every product failed at least one hard gate.
    RE-VALIDATE  nothing to re-validate — there is no eligible option to check.
    RELAX        look only at products whose ONLY failures are negotiable (price,
                 delivery). Anything that failed on category, quantity or specs is
                 not a near-miss and is never offered.
    SURFACE      the 2-3 smallest violations, each with its delta in rupees or days.
    ESCALATE     no purchase, state preserved, one audit entry.
    """
    brief = context.brief
    assert brief is not None  # handle() already checked; this keeps type checkers quiet

    # "Nothing qualified" and "nothing was even looked at" are two different facts,
    # and only one of them is about the products. Telling a furniture buyer that the
    # gaps were on category, quantity or specification implies we weighed products
    # and turned them down; we searched a catalog that has never contained a chair.
    # A refusal that overstates what the agent did is exactly the kind of thing the
    # audit trail exists to prevent, so this case gets its own branch.
    if not context.filter_results:
        return _no_vendor_coverage(context, audit)

    near_misses = _near_misses(brief, context.rejected)
    options = [
        SurfacedOption(
            label=result.product.label,
            product=result.product,
            order_total_inr=result.product.order_total_inr(brief.quantity),
            violations=result.violations,
            note=_near_miss_note(brief, result.product),
        )
        for result in near_misses[:MAX_OPTIONS_SURFACED]
    ]

    relaxations = _proposed_relaxations(brief, [result.product for result in near_misses])

    if options:
        headline = (
            f"No product met every requirement. {len(options)} came close - each would "
            f"need one negotiable limit moved, which only you can authorise."
        )
    else:
        # Everything failed on the non-negotiable tier, so there is nothing to
        # propose — but we know exactly WHICH requirement did it, on every single
        # row, and saying "the gaps are on category, quantity or specification"
        # withholds that. A buyer who types "5 gaming laptops" and is told the
        # gap is "on specification" has to guess which word broke it. Naming it
        # turns a dead end into an answer they can act on: drop the word, or buy
        # somewhere else.
        blockers = _blocking_requirements(brief, context.rejected)
        if blockers:
            headline = (
                f"We checked {len(context.filter_results)} "
                f"{config.unit_noun(brief.category)} and none of them clears every "
                f"requirement - {_and_list(blockers)}. That is not a limit we move, "
                f"so nothing was ordered."
            )
        else:
            headline = (
                "No product met every requirement, and none of the misses were on a "
                "negotiable limit - the gaps are on category, quantity or specification, "
                "which we do not relax."
            )

    detail = {
        "products_considered": len(context.filter_results),
        "products_eligible": 0,
        "near_misses": {option.label: option.violations for option in options},
        "proposed_relaxations": relaxations,
        "blocking_requirements": _blocking_requirements(brief, context.rejected),
        "non_negotiable_never_relaxed": ["category", "quantity", "specs"],
        "action_taken": "no purchase executed",
    }

    audit.escalation(
        STAGE_DISCOVERY,
        f"No product passed all hard constraints; surfaced {len(options)} near-miss(es) "
        f"for a human to accept or reject. No purchase executed.",
        detail,
    )
    context.status = TransactionStatus.AWAITING_APPROVAL

    return EscalationOutcome(
        resolved=False,
        trigger=Trigger.NO_ELIGIBLE_MATCH,
        stage=STAGE_DISCOVERY,
        headline=headline,
        options=options,
        proposed_relaxations=relaxations,
        detail=detail,
    )


def _no_vendor_coverage(context: TransactionContext, audit: AuditLogger) -> EscalationOutcome:
    """No source stocks this category at all. Say that, and say what we do stock.

    This is the honest version of our narrowest limit. The brief was understood
    perfectly — quantity, cap, deadline and priorities all parsed — and then the
    search found nothing to check, because our two catalogs cover packaging and
    nothing else. CLAUDE.md, "Known limits": mock catalogs, one product line. We
    name it rather than dressing it up as a filtering result.

    Nothing is surfaced and nothing is proposed, deliberately. There are no
    near-misses to show when there were no products, and no relaxation would help:
    moving the price cap does not make a vendor start selling chairs. Category is
    non-negotiable, so this is a wall rather than a limit to be argued with.
    """
    brief = context.brief
    assert brief is not None

    stocked = discovery.available_categories()
    stocked_text = ", ".join(stocked) if stocked else "nothing"

    headline = (
        f"I understood the brief - {brief.quantity:,} units of {brief.category}, up to Rs "
        f"{brief.max_price_per_unit_inr:,.2f} a unit, within {brief.max_delivery_days} days - "
        f"but no vendor source stocks {brief.category}. My catalogs cover {stocked_text}. "
        f"Nothing was searched and nothing was bought."
    )

    detail = {
        "requested_category": brief.category,
        "categories_stocked_by_our_sources": stocked,
        "products_considered": 0,
        "products_eligible": 0,
        "why_nothing_surfaced": (
            "no products exist in this category to compare, so there are no near-misses"
        ),
        "why_no_relaxation_proposed": (
            "category is a non-negotiable constraint; moving the price cap or the "
            "delivery window would not make a source stock it"
        ),
        "action_taken": "no purchase executed",
    }

    audit.escalation(
        STAGE_DISCOVERY,
        f"The brief parsed cleanly but no vendor source stocks '{brief.category}' - our "
        f"catalogs cover {stocked_text}. Nothing was searched and no purchase was executed.",
        detail,
    )
    context.status = TransactionStatus.AWAITING_APPROVAL

    return EscalationOutcome(
        resolved=False,
        trigger=Trigger.NO_ELIGIBLE_MATCH,
        stage=STAGE_DISCOVERY,
        headline=headline,
        detail=detail,
    )


def _and_list(items: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c'. One sentence, not a bulleted list."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _blocking_requirements(brief: Brief, rejected: list[FilterResult]) -> list[str]:
    """The non-negotiable requirements that EVERY product missed, in plain words.

    Only requirements that blocked the WHOLE pool are named. A spec that two
    products lack is not why the search failed — the others failed on something
    else, and naming it would send the buyer off to fix the wrong word.

    The spec case reads the unmet keys back off the violation message rather than
    re-running the comparison, so this can only ever say what the filter actually
    said. Re-deriving it here would let the explanation and the decision drift
    apart, and then the screen and the audit log would disagree about why nothing
    was bought.

    Reported in the buyer's own words where we can: they wrote "double-wall", the
    gate compares "doublewall", and being told your own sentence back is the
    difference between an answer and a diagnostic.
    """
    failures = [result for result in rejected if not result.passed]
    if not failures:
        return []

    phrases: list[str] = []

    if all("category" in result.violations for result in failures):
        phrases.append(f"none of them is {brief.category}")

    if all("specs" in result.violations for result in failures):
        unmet = [
            set(
                result.violations["specs"]
                .removeprefix(discovery.SPEC_VIOLATION_PREFIX)
                .split(", ")
            )
            for result in failures
        ]
        common = set.intersection(*unmet)
        if common:
            spoken = {discovery.spec_key(spec): spec for spec in brief.specs}
            words = sorted(spoken.get(key, key) for key in common)
            phrases.append(
                "nothing we can source lists "
                + _and_list([f"'{word}'" for word in words])
            )

    if all("quantity" in result.violations for result in failures):
        phrases.append(f"no supplier has {brief.quantity:,} of them in stock")

    return phrases


def _near_misses(brief: Brief, rejected: list[FilterResult]) -> list[FilterResult]:
    """The rejected products that missed ONLY on a negotiable limit, closest first.

    Two rules, and both are load-bearing:

    1. A product that failed any non-negotiable check is dropped entirely, even if
       it also has a small price miss. There is no partial credit on the tier we
       said we would never relax.
    2. The rest are ordered by how far off they were. If the user declared a
       flexibility order ("I would rather pay more than wait longer"), that decides
       the ordering. If they did not — and most briefs will not — we fall back to
       smallest-violation first. That fallback is a known limit we name on purpose
       in CLAUDE.md rather than pretending we inferred a preference.
    """
    candidates = [
        result
        for result in rejected
        if result.violations and all(field in NEGOTIABLE_FIELDS for field in result.violations)
    ]
    return sorted(candidates, key=lambda result: _violation_sort_key(brief, result.product))


def _violation_sort_key(brief: Brief, product: Product) -> tuple[int, float]:
    """Order near-misses by the user's declared flexibility, else by smallest violation.

    Violations are compared as FRACTIONS of the user's own limit, never as raw
    numbers. Rs 2.50 over a Rs 22 cap and 2 days over a 10-day window are not
    comparable as 2.5 against 2, but they are perfectly comparable as 11% against
    20% — and 11% is genuinely the smaller ask.
    """
    deltas = _negotiable_deltas(brief, product)

    if brief.flexibility_order:
        # The user told us which limit they would bend first. A product that misses
        # on that limit sorts ahead of one that misses on a limit they never
        # mentioned.
        ranks = [
            brief.flexibility_order.index(field)
            if field in brief.flexibility_order
            else len(brief.flexibility_order)
            for field in deltas
        ]
        primary = min(ranks) if ranks else len(brief.flexibility_order)
    else:
        primary = 0  # nothing declared: everything ties here and size decides

    return (primary, sum(fraction for _, fraction in deltas.values()))


def _negotiable_deltas(brief: Brief, product: Product) -> dict[str, tuple[float, float]]:
    """How far past each negotiable limit this product is: (absolute, fraction).

    Returns only the limits it actually misses. The fraction is the miss divided
    by the user's own limit, which is what makes rupees and days comparable.
    """
    deltas: dict[str, tuple[float, float]] = {}

    over_price = product.price_per_unit_inr - brief.max_price_per_unit_inr
    if over_price > 0:
        deltas["max_price_per_unit_inr"] = (over_price, over_price / brief.max_price_per_unit_inr)

    late_days = product.delivery_days - brief.max_delivery_days
    if late_days > 0:
        deltas["max_delivery_days"] = (float(late_days), late_days / brief.max_delivery_days)

    return deltas


def _near_miss_note(brief: Brief, product: Product) -> str:
    """One sentence saying exactly what accepting this product would cost the user."""
    deltas = _negotiable_deltas(brief, product)
    parts = []
    if "max_price_per_unit_inr" in deltas:
        over, fraction = deltas["max_price_per_unit_inr"]
        parts.append(
            f"Rs {over:.2f}/unit over your cap ({fraction:.0%}), "
            f"Rs {over * brief.quantity:,.0f} more across {brief.quantity:,} units"
        )
    if "max_delivery_days" in deltas:
        over, fraction = deltas["max_delivery_days"]
        parts.append(f"{int(over)} day(s) later than your window ({fraction:.0%} over)")
    return "; ".join(parts) if parts else "within every negotiable limit"


def _proposed_relaxations(brief: Brief, products: list[Product]) -> list[str]:
    """What moving each negotiable limit would admit. Written down, never applied.

    This is the difference between an agent that asks and an agent that decides.
    We compute the smallest change that would let something through and put that
    sentence in front of a human. Nothing here writes back to the brief, and no
    later stage reads this list as configuration — it is text for a person, and
    only a person can act on it.
    """
    proposals: list[str] = []

    for field in _relaxation_order(brief):
        misses = [
            delta
            for delta in (_negotiable_deltas(brief, product).get(field) for product in products)
            if delta is not None
        ]
        if not misses:
            continue
        smallest = min(misses, key=lambda delta: delta[0])[0]

        if field == "max_price_per_unit_inr":
            new_cap = brief.max_price_per_unit_inr + smallest
            proposals.append(
                f"Raise the {FIELD_LABELS[field]} from Rs {brief.max_price_per_unit_inr:.2f} "
                f"to Rs {new_cap:.2f} (+Rs {smallest * brief.quantity:,.0f} on this order) "
                f"to admit the closest option."
            )
        else:
            new_window = brief.max_delivery_days + int(smallest)
            proposals.append(
                f"Extend the {FIELD_LABELS[field]} from {brief.max_delivery_days} days "
                f"to {new_window} days to admit the closest option."
            )

    return proposals


def _relaxation_order(brief: Brief) -> list[str]:
    """Which negotiable limit we propose bending first.

    The user's declared order wins. With nothing declared we use the file order of
    NEGOTIABLE_FIELDS (price, then delivery) and say so out loud — CLAUDE.md,
    "Known limits": relaxation order needs a human signal, and with none declared
    we fall back to smallest-violation ordering rather than inventing a preference
    the user never expressed.
    """
    declared = [field for field in brief.flexibility_order if field in NEGOTIABLE_FIELDS]
    remainder = [field for field in NEGOTIABLE_FIELDS if field not in declared]
    return declared + remainder


# ---------------------------------------------------------------------------
# Trigger 2 — stage 5, a valid match over the authorisation limit
# ---------------------------------------------------------------------------

def _over_authorisation_limit(context: TransactionContext, audit: AuditLogger) -> EscalationOutcome:
    """The agent found the right product and cannot buy it alone. Always a human.

    This is the escalation the whole project exists to demonstrate, so note what
    it does NOT do. It does not reject the winner — the recommendation stands and
    is shown with its reasoning. It does not quietly drop to a cheaper option that
    fits under the line. It does not relax anything, because the limit is not a
    constraint on the product, it is a constraint on the AGENT.

    It shows three things: the overage, why the winner is still the right pick,
    and the best option that would not have needed approval — so a human can
    approve in one click or take the in-limit alternative knowingly.
    """
    brief = context.brief
    assert brief is not None

    winner = context.ranked[0]
    limit = config.authorisation_limit_inr()
    total = winner.product.order_total_inr(brief.quantity)
    overage = total - limit

    options = [
        SurfacedOption(
            label=winner.product.label,
            product=winner.product,
            order_total_inr=total,
            score=winner.score,
            score_gap=0.0,
            note=(
                f"Recommended. Over the Rs {limit:,.0f} limit by Rs {overage:,.0f}. "
                f"{_why_recommended(winner)}"
            ),
        )
    ]

    alternative = _best_within_limit(context, limit)
    if alternative is not None:
        gap = round(winner.score - alternative.score, 1)
        options.append(
            SurfacedOption(
                label=alternative.product.label,
                product=alternative.product,
                order_total_inr=alternative.product.order_total_inr(brief.quantity),
                score=alternative.score,
                score_gap=gap,
                note=(
                    f"Needs no approval - within the limit. Scores {gap} points lower "
                    f"than the recommendation."
                ),
            )
        )

    headline = (
        f"This order totals Rs {total:,.0f}, which is Rs {overage:,.0f} over the "
        f"Rs {limit:,.0f} the agent may commit on its own. Nothing has been bought."
    )

    detail = {
        "recommended": winner.product.label,
        "unit_price_inr": winner.product.price_per_unit_inr,
        "quantity": brief.quantity,
        "order_total_inr": total,
        "authorisation_limit_inr": limit,
        "overage_inr": round(overage, 2),
        "why_still_recommended": _why_recommended(winner),
        "best_within_limit": (
            f"{alternative.product.label} at Rs "
            f"{alternative.product.order_total_inr(brief.quantity):,.0f} "
            f"(score {alternative.score})"
            if alternative is not None
            else "none - every eligible option is over the limit"
        ),
        "action_taken": "no purchase executed",
    }

    audit.escalation(
        STAGE_AUTHORISATION,
        f"Order total Rs {total:,.0f} exceeds the Rs {limit:,.0f} authorisation limit by "
        f"Rs {overage:,.0f}; held for human approval. No purchase executed.",
        detail,
    )
    context.status = TransactionStatus.AWAITING_APPROVAL

    return EscalationOutcome(
        resolved=False,
        trigger=Trigger.OVER_AUTHORISATION_LIMIT,
        stage=STAGE_AUTHORISATION,
        headline=headline,
        options=options,
        detail=detail,
    )


def _best_within_limit(context: TransactionContext, limit: float) -> ScoredProduct | None:
    """The highest-scoring eligible product whose order total fits under the limit.

    Walks the ranked list in order and returns the first one that fits, so the
    alternative we offer is the best available, not merely the cheapest. We are not
    trying to spend as little as possible; we are trying to give an approver the
    strongest option that would not have needed them at all.
    """
    brief = context.brief
    assert brief is not None
    for scored in context.ranked:
        if scored.product.order_total_inr(brief.quantity) <= limit:
            return scored
    return None


def _why_recommended(scored: ScoredProduct) -> str:
    """One sentence naming the criterion that actually drove this product's score.

    Read off the stored score terms rather than recomputed, so the sentence on the
    approval screen and the arithmetic in the comparison table cannot disagree.
    """
    top = max(scored.terms, key=lambda term: term.contribution)
    share = top.contribution / (scored.score / 100) if scored.score else 0
    return (
        f"It leads on {top.criterion}, which you weighted at {top.weight:.2f} and which "
        f"supplied {share:.0%} of its {scored.score}-point score."
    )


# ---------------------------------------------------------------------------
# Trigger 3 — stages 6 and 7, the chosen option cannot be bought
# ---------------------------------------------------------------------------

def _option_failed(
    context: TransactionContext,
    trigger: Trigger,
    audit: AuditLogger,
    failed_product: Product | None,
    failure_note: str,
    exclude: set[str],
) -> EscalationOutcome:
    """The winner fell over at confirmation or payment. Fall back, or escalate.

    This is the only place in the project where the agent may act alone after
    something has gone wrong, and it is fenced by two conditions that both have to
    hold:

    RE-VALIDATE  the next option is re-checked against every hard gate, from
                 scratch, right now. Never trust sort order — the ranked list was
                 correct when it was built, and "was correct earlier" is not the
                 same claim as "is eligible now".
    THRESHOLD    the fallback must trail the failed product by no more than the
                 substitution threshold in config.yaml (5 points). Inside that
                 band the two products are close enough that swapping is a
                 judgement the user would recognise as their own. Outside it, #2
                 is a meaningfully worse fit and a human decides.

    Fail either condition — or run out of options entirely — and we escalate with
    nothing bought.
    """
    brief = context.brief
    assert brief is not None

    stage = TRIGGER_STAGE[trigger]
    threshold = config.substitution_threshold_points()
    failed_label = failed_product.label if failed_product is not None else "the selected product"
    failure = failure_note or "could not be purchased"
    failed_score = _score_of(context, failed_product)

    candidate = _next_eligible(context, exclude)

    # -- nothing left to fall back to --------------------------------------
    if candidate is None:
        detail = {
            "failed_product": failed_label,
            "failure": failure,
            "eligible_fallbacks_found": 0,
            "action_taken": "no purchase executed",
        }
        audit.escalation(
            stage,
            f"{failed_label} {failure} and no remaining option passed an independent "
            f"re-validation. No purchase executed.",
            detail,
        )
        context.status = TransactionStatus.AWAITING_APPROVAL
        return EscalationOutcome(
            resolved=False,
            trigger=trigger,
            stage=stage,
            headline=(
                f"{failed_label} {failure}, and no other option is currently eligible. "
                f"Nothing has been bought."
            ),
            detail=detail,
        )

    gap = round(failed_score - candidate.score, 1) if failed_score is not None else None
    option = SurfacedOption(
        label=candidate.product.label,
        product=candidate.product,
        order_total_inr=candidate.product.order_total_inr(brief.quantity),
        score=candidate.score,
        score_gap=gap,
        note=f"Re-validated against every hard constraint just now. {_why_recommended(candidate)}",
    )

    # -- the gap is too wide: a human decides -------------------------------
    if gap is not None and gap > threshold:
        detail = {
            "failed_product": failed_label,
            "failure": failure,
            "fallback_considered": candidate.product.label,
            "score_gap_points": gap,
            "substitution_threshold_points": threshold,
            "action_taken": "no purchase executed",
        }
        audit.escalation(
            stage,
            f"{failed_label} {failure}; the next eligible option trails by {gap} points, "
            f"over the {threshold:.0f}-point substitution threshold, so the swap was not made "
            f"automatically. No purchase executed.",
            detail,
        )
        context.status = TransactionStatus.AWAITING_APPROVAL
        return EscalationOutcome(
            resolved=False,
            trigger=trigger,
            stage=stage,
            headline=(
                f"{failed_label} {failure}. The next option is {gap} points behind - more "
                f"than the {threshold:.0f}-point limit for an automatic swap - so this needs "
                f"your decision. Nothing has been bought."
            ),
            options=[option],
            detail=detail,
        )

    # -- close enough: the agent swaps, and says so -------------------------
    detail = {
        "failed_product": failed_label,
        "failure": failure,
        "fallback_selected": candidate.product.label,
        "score_gap_points": gap,
        "substitution_threshold_points": threshold,
        "re_validated": "passed all hard constraints on re-check",
        "action_taken": f"switched to {candidate.product.label}",
    }
    audit.fallback(
        stage,
        f"{failed_label} {failure}; switched to {candidate.product.label}, which re-validated "
        f"cleanly and trails by only {gap} points, inside the {threshold:.0f}-point "
        f"substitution threshold.",
        detail,
    )
    context.selected = candidate

    return EscalationOutcome(
        resolved=True,
        trigger=trigger,
        stage=stage,
        headline=(
            f"{failed_label} {failure}. Switched to {candidate.product.label}, {gap} points "
            f"behind and within the {threshold:.0f}-point limit for an automatic swap."
        ),
        options=[option],
        selected=candidate,
        detail=detail,
    )


def _next_eligible(context: TransactionContext, exclude: set[str]) -> ScoredProduct | None:
    """The best-ranked product that is NOT excluded and still passes every hard gate.

    The re-validation is the point of this function. We take the next candidate off
    the ranked list and put it back through the stage-3 filter as if we had never
    seen it — same brief, same gates, same code. If stock has fallen below the
    quantity we need since discovery ran, it fails here and we move on.

    Reusing discovery.apply_hard_gates rather than writing a quick check of our own
    is deliberate: two different definitions of "eligible" in one codebase is
    exactly how an ineligible product ends up bought.
    """
    brief = context.brief
    assert brief is not None

    for scored in context.ranked:
        if scored.product.product_id in exclude:
            continue
        if discovery.apply_hard_gates(brief, [scored.product])[0].passed:
            return scored
    return None


def _score_of(context: TransactionContext, product: Product | None) -> float | None:
    """The ranked score of a product we already scored, or None if we never did.

    None is a real answer, not a missing one: a product that was never ranked has
    no gap to compare against the substitution threshold, so the caller treats it
    as "no reason to block the swap" rather than inventing a number.
    """
    if product is None:
        return None
    for scored in context.ranked:
        if scored.product.product_id == product.product_id:
            return scored.score
    return None
