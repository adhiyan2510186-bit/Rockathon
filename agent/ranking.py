"""Stage 4 — the ranker. Pure Python, ~60 lines of arithmetic, no model anywhere.

WHY THIS FILE EXISTS
--------------------
This is the file the whole project is defending. Every number a judge could
question — each normalised value, each term, each score — is computed here from
the same inputs every time. Same brief in, same ranking out, on every run.

CLAUDE.md, THE ONE RULE: if you ever find yourself asking the LLM to rank or
score, stop. That is a bug, not a feature. Nothing in this file imports
language.py and nothing in it is non-deterministic.

THE FORMULA
-----------
    score = SUM( weight x normalised )  over the four soft criteria, x100

Hard gates already filtered the pool at stage 3, so this runs over survivors
only. Two things follow from that and both matter:

  * A product is never rejected here. Stage 4 cannot reject; it only orders.
  * The pool IS the comparison. "Best reliability" means best among the products
    that qualified, not best in the world.

TWO NORMALISATION METHODS, AND WHY
----------------------------------
    reliability, replacement  ->  min-max across the surviving pool
                                  best in pool = 1.0, worst = 0.0
    price, delivery           ->  sqrt(margin against the hard cap)
                                  margin = (cap - value) / cap

Min-max asks "how does this compare with the alternatives?" — the right question
for a seller's rating, where there is no natural ceiling.

Margin asks "how far under the user's own limit did this land?" — the right
question for price and delivery, where the user gave us an explicit line.

THE SQUARE ROOT IS THE INTERESTING PART
---------------------------------------
It is there so a bargain cannot drown out what the user said mattered. Without
it, EcoMail's 20% price margin would score 0.200; with it, 0.447 — but the same
root also lifts a small margin proportionally more (Corusafe's 0.45% margin
scores 0.067, not 0.005). The curve is steep near the cap and flat far from it,
which matches how buyers actually think: the first rupee under budget feels like
a win, the twentieth barely registers.

Concretely, in our demo: EcoMail is the cheapest by a distance — 20% under the
cap — and it ranks LAST, because the user said reliability mattered and EcoMail
is worst in the pool on exactly that. The formula has no favourite. The brief
does.

PRICE APPEARS TWICE, AND THAT IS NOT DOUBLE COUNTING
----------------------------------------------------
Stage 3 asked "is it under Rs 22 at all?" (eligibility). Stage 4 asks "how far
under did it land, compared with what else qualified?" (preference). Two
different questions, one measurement, each used once. See CLAUDE.md,
"Classification controls eligibility, not scoring".

THE GOLDEN NUMBERS
------------------
tests/test_ranking.py asserts 58.0 / 48.7 / 33.7 for the demo brief. If this
file and that test ever disagree, this file is wrong — the test is the deck.
"""

from __future__ import annotations

import math

from agent.audit import STAGE_RANKING, AuditLogger
from agent.models import SOFT_CRITERIA, Product, ScoredProduct, ScoreTerm, Weights

# Which method each criterion uses. A table rather than an if-chain, so the
# answer to "how was replacement normalised?" is one line you can point at.
MIN_MAX_CRITERIA = ("reliability", "replacement")
MARGIN_CRITERIA = ("price", "delivery")

METHOD_MIN_MAX = "min-max across survivors"
METHOD_MARGIN = "sqrt(margin vs cap)"


def rank(
    products: list[Product],
    weights: Weights,
    max_price_per_unit_inr: float,
    max_delivery_days: int,
    audit: AuditLogger | None = None,
) -> list[ScoredProduct]:
    """Score and order the survivors of stage 3. Best first.

    Takes the two caps as plain numbers rather than the whole Brief, because the
    only thing ranking needs from the brief is the two lines the user drew. Fewer
    inputs means fewer ways for this function to be accidentally influenced by
    something it should not see.

    Returns an empty list for an empty pool. That is not an error here — nothing
    passing the hard gates is escalation trigger #1, and it is the escalation
    handler's business, not the ranker's.
    """
    if not products:
        return []

    # The pool's spread, computed once. min-max is relative to the survivors, so
    # these bounds are part of the answer and not an implementation detail.
    pools = {
        "reliability": [product.reliability_rating for product in products],
        "replacement": [float(product.replacement_window_days) for product in products],
    }
    caps = {"price": max_price_per_unit_inr, "delivery": float(max_delivery_days)}

    scored: list[ScoredProduct] = []
    for product in products:
        raw_values = {
            "reliability": product.reliability_rating,
            "price": product.price_per_unit_inr,
            "replacement": float(product.replacement_window_days),
            "delivery": float(product.delivery_days),
        }

        terms: list[ScoreTerm] = []
        for criterion in SOFT_CRITERIA:
            value = raw_values[criterion]
            if criterion in MIN_MAX_CRITERIA:
                normalised = _min_max(value, pools[criterion])
                method = METHOD_MIN_MAX
            else:
                normalised = _margin(value, caps[criterion])
                method = METHOD_MARGIN
            terms.append(
                ScoreTerm(
                    criterion=criterion,
                    weight=weights.values[criterion],
                    raw_value=value,
                    normalised=normalised,
                    method=method,
                )
            )

        total = sum(term.contribution for term in terms)
        scored.append(
            ScoredProduct(
                product=product,
                terms=terms,
                score=round(total * 100, 1),
                rank=1,  # placeholder; set below once the whole pool is scored
            )
        )

    # Highest score first. The product_id tie-break is what makes this
    # reproducible: two products on an identical score must not swap places
    # because a catalog file was read in a different order.
    scored.sort(key=lambda item: (-item.score, item.product.product_id))
    ordered = [
        ScoredProduct(product=item.product, terms=item.terms, score=item.score, rank=position)
        for position, item in enumerate(scored, start=1)
    ]

    if audit:
        winner = ordered[0]
        audit.decision(
            STAGE_RANKING,
            _reasoning(ordered),
            {
                "ranking": [
                    {"rank": item.rank, "product": item.product.label, "score": item.score}
                    for item in ordered
                ],
                "winner_breakdown": [
                    {
                        "criterion": term.criterion,
                        "weight": term.weight,
                        "raw_value": term.raw_value,
                        "normalised": round(term.normalised, 3),
                        "contribution": round(term.contribution, 3),
                        "method": term.method,
                    }
                    for term in winner.terms
                ],
                "score_gap_to_runner_up": (
                    round(ordered[0].score - ordered[1].score, 1) if len(ordered) > 1 else None
                ),
            },
        )

    return ordered


def _min_max(value: float, pool: list[float]) -> float:
    """Place a value against the best and worst in the surviving pool.

    When every survivor scores the same, there is no spread to measure and we
    return 1.0 — nobody is penalised for a pool that happens to be uniform. It
    cannot change the ordering either way, since it adds the same amount to
    every product's score.
    """
    low, high = min(pool), max(pool)
    if high == low:
        return 1.0
    return (value - low) / (high - low)


def _margin(value: float, cap: float) -> float:
    """How far under the user's own limit this landed, square-rooted.

    The clamp at zero is defensive. Anything above the cap was already removed by
    the stage-3 hard gate, so a negative margin should be impossible here — but a
    silent NaN from sqrt(-x) would poison a score without anyone noticing, and
    that is not a failure mode we want on stage.
    """
    if cap <= 0:
        return 0.0
    return math.sqrt(max(0.0, (cap - value) / cap))


def _reasoning(ordered: list[ScoredProduct]) -> str:
    """One plain sentence naming the winner and what actually drove it.

    The log has to answer "why did this win?" without the reader recomputing
    anything, so we name the criterion that contributed most and how much of the
    score it was responsible for.
    """
    winner = ordered[0]
    top_term = max(winner.terms, key=lambda term: term.contribution)
    share = top_term.contribution / (winner.score / 100) if winner.score else 0
    sentence = (
        f"{winner.product.label} ranked first with {winner.score}, driven mainly by "
        f"{top_term.criterion} ({share:.0%} of its score)."
    )
    if len(ordered) > 1:
        gap = round(winner.score - ordered[1].score, 1)
        sentence += f" It leads {ordered[1].product.name} by {gap} points."
    return sentence
