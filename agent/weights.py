"""Stage 2 — the preference & weight engine. Plain Python, no model.

WHY THIS FILE EXISTS
--------------------
The user says "reliability matters a lot". Something has to turn that sentence
into the number 0.45, and turn the other three criteria into numbers that still
add up to 1. This file is that something, and it is deliberately arithmetic a
judge can do on paper.

CLAUDE.md, stage 2: "LLM extracts the priority; Python computes the weights."
language.py already did the extracting — it handed us the LABEL 'matters_a_lot'.
Nothing in this file talks to a model, and nothing in this file reads the user's
sentence. It works entirely on labels and config values.

THE WHOLE ALGORITHM, IN FOUR STEPS
----------------------------------
Worked through with our demo brief (category packaging, user says reliability
matters a lot):

  1. START from the category defaults in config.yaml.
       reliability 0.25 · price 0.30 · replacement 0.25 · delivery 0.20

  2. SUBSTITUTE the stated priority. 'matters_a_lot' looks up to 0.45.
       reliability is now 0.45, which leaves a budget of 0.55 for the rest.

  3. RESCALE the untouched criteria proportionally into that budget. Their
     defaults are 0.30 / 0.25 / 0.20, which total 0.75, so each takes its own
     share of 0.55:
       price       0.55 x 0.30/0.75 = 0.2200
       replacement 0.55 x 0.25/0.75 = 0.1833
       delivery    0.55 x 0.20/0.75 = 0.1467

  4. ROUND to the nearest 0.05 (the step lives in config.yaml).
       price 0.20 · replacement 0.20 · delivery 0.15

  Final: reliability 0.45 · price 0.20 · replacement 0.20 · delivery 0.15 = 1.00

Those four numbers are the ones in our deck, and the golden ranking test
(58.0 / 48.7 / 33.7) is computed from them. Step 4 is NOT cosmetic — the
unrounded 0.2200 / 0.1833 / 0.1467 produce different scores. If you change the
rounding step in config.yaml, the golden test goes red, which is what it is for.

WHY ROUND AT ALL
----------------
A weight is a judgement about what a person cares about. Printing
"replacement: 0.1833" on an approval screen claims a precision we do not have —
nobody's opinion is accurate to four decimal places. Multiples of 5% are a
number a human can look at and argue with, which is the point of showing them.

WHY PROPORTIONALLY AND NOT EQUALLY
----------------------------------
Because the category defaults are a considered opinion about packaging: price
usually matters more than delivery. Splitting the leftover budget equally would
throw that opinion away every time a user states a single preference. Rescaling
keeps the shape of what we know and only changes the scale.
"""

from __future__ import annotations

import math

from agent import config
from agent.audit import STAGE_WEIGHTS, AuditLogger
from agent.models import SOFT_CRITERIA, Brief, Weights

# How far a finished weight set may drift from 1.0 before we call it a bug.
# Floating point means 0.45 + 0.20 + 0.20 + 0.15 can land on 0.9999999999999999.
_SUM_TOLERANCE = 1e-6


def compute(brief: Brief, audit: AuditLogger | None = None) -> Weights:
    """Turn a parsed brief into the four weights stage 4 will score with.

    The only public function here. Give it a Brief, get back a Weights with the
    numbers AND a plain-words source for each one, because "why is reliability
    0.45?" is a question the approval screen has to answer on the spot.
    """
    defaults = config.category_default_weights(brief.category)
    values: dict[str, float] = {}
    sources: dict[str, str] = {}

    # -- step 2: substitute whatever the user actually stated -----------------
    for criterion, phrase in brief.stated_priorities.items():
        if criterion not in SOFT_CRITERIA:
            continue  # not something we score on; ignore rather than invent a slot
        weight = config.priority_phrase_weight(phrase)
        if weight is None:
            continue  # unknown label -> fall through to the category default
        values[criterion] = weight
        sources[criterion] = f"user-stated ({phrase.replace('_', ' ')})"

    stated_total = sum(values.values())
    unstated = [criterion for criterion in SOFT_CRITERIA if criterion not in values]

    if not unstated:
        # The user expressed a view on all four. Nothing to rescale into.
        values = _normalise_to_one(values)
    elif stated_total >= 1.0:
        # Someone said several things "matter a lot" and the stated weights alone
        # already fill the whole decision. We do not silently invent room: the
        # stated set is scaled back to exactly 1.0 and the criteria they never
        # mentioned get 0. A zero weight is honest — it means this criterion did
        # not affect the ranking — and the source line below says so out loud.
        values = _normalise_to_one(values)
        for criterion in unstated:
            values[criterion] = 0.0
            sources[criterion] = "0.0 - every stated priority together filled the decision"
    else:
        # -- step 3: rescale the rest proportionally into what is left --------
        budget = 1.0 - stated_total
        pool = sum(defaults[criterion] for criterion in unstated)
        label = f"category default ({brief.category})"
        for criterion in unstated:
            share = defaults[criterion] / pool if pool else 1.0 / len(unstated)
            values[criterion] = _round_to_step(budget * share)
            sources[criterion] = label if stated_total == 0 else f"{label}, rescaled"

        # -- step 4 repair: rounding can leave the total a step off 1.0 -------
        _absorb_rounding_drift(values, unstated)

    # Put them back in display order so every screen and log reads the same way,
    # and trim to four decimals — a weight of 0.33333333 helps nobody.
    #
    # That trim is itself a rounding, so it can reintroduce drift: three criteria
    # at 0.3333 sum to 0.9999. We absorb it here, AFTER trimming, so the numbers
    # we store and display are the ones that were checked. Checking before the
    # trim would validate a set nobody ever sees.
    ordered = {criterion: round(values[criterion], 4) for criterion in SOFT_CRITERIA}
    _absorb_rounding_drift(ordered, [c for c in SOFT_CRITERIA if ordered[c] > 0])

    total = sum(ordered.values())
    if abs(total - 1.0) > _SUM_TOLERANCE:
        # Loud rather than quiet. Weights that do not sum to 1 silently shrink or
        # inflate every score, and we would only notice on stage.
        raise ValueError(f"weights sum to {total}, not 1.0: {ordered}")

    weights = Weights(values=ordered, sources={c: sources[c] for c in SOFT_CRITERIA})

    if audit:
        audit.decision(
            STAGE_WEIGHTS,
            _reasoning(brief, ordered),
            {"weights": ordered, "sources": weights.sources, "category": brief.category},
        )

    return weights


def _round_to_step(value: float) -> float:
    """Round to the nearest multiple of the config step (0.05), halves rounding up.

    We add 0.5 and floor rather than using Python's round(), because round() uses
    banker's rounding: round(4.5) is 4 and round(5.5) is 6. Fine for statistics,
    surprising in a demo where someone checks our arithmetic by hand.
    """
    step = config.weight_rounding_step()
    return round(math.floor(value / step + 0.5) * step, 10)


def _absorb_rounding_drift(values: dict[str, float], adjustable: list[str]) -> None:
    """Nudge one weight so the set sums to exactly 1.0 after rounding.

    Rounding three numbers to 0.05 can land the total on 0.95 or 1.05. We put the
    difference on the LARGEST rescaled criterion, because a 0.05 correction
    distorts a big weight proportionally less than a small one — and never on a
    criterion the user stated, whose weight came straight from config and is not
    ours to adjust.

    In the demo this changes nothing: 0.20 + 0.20 + 0.15 already lands on 0.55
    exactly. It exists so an unusual brief cannot produce weights that quietly
    fail to sum to 1.
    """
    drift = round(1.0 - sum(values.values()), 10)
    if abs(drift) <= _SUM_TOLERANCE or not adjustable:
        return
    target = max(adjustable, key=lambda criterion: values[criterion])
    values[target] = round(max(0.0, values[target] + drift), 10)


def _normalise_to_one(values: dict[str, float]) -> dict[str, float]:
    """Scale a set of weights so it sums to 1.0, keeping the ratios between them."""
    total = sum(values.values())
    if total <= 0:
        share = 1.0 / len(values)
        return {criterion: share for criterion in values}
    return {criterion: round(weight / total, 10) for criterion, weight in values.items()}


def _reasoning(brief: Brief, values: dict[str, float]) -> str:
    """One plain sentence for the audit log, naming what drove the weights.

    The log has to survive being read by someone who was not in the room, so it
    says which criterion the user raised and what the others became as a result.
    """
    if not brief.stated_priorities:
        return (
            f"No priority was stated, so the documented {brief.category} default weights "
            f"were applied unchanged."
        )
    said = ", ".join(
        f"{criterion} {phrase.replace('_', ' ')} ({values[criterion]:.2f})"
        for criterion, phrase in brief.stated_priorities.items()
        if criterion in values
    )
    return (
        f"User stated {said}; the remaining criteria were rescaled from the "
        f"{brief.category} defaults so the four weights still sum to 1.0."
    )
