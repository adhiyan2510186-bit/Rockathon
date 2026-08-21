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

from pydantic import BaseModel, ConfigDict, Field

from agent import config
from agent.audit import STAGE_WEIGHTS, AuditLogger
from agent.models import SOFT_CRITERIA, Actor, Brief, FieldStatus, Weights

# How far a finished weight set may drift from 1.0 before we call it a bug.
# Floating point means 0.45 + 0.20 + 0.20 + 0.15 can land on 0.9999999999999999.
_SUM_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Step 0 — asking which job these are for
# ---------------------------------------------------------------------------
# Some categories sell one product into several different jobs, and the job
# changes what the buyer prefers among the products that already qualify. "40
# headsets, over-ear, under Rs 4,000, within 12 days" is a complete, buyable
# brief and it still does not say whether a dead one costs a sales call or an
# afternoon of background music.
#
# So for those categories we ask ONE question, and only under conditions that
# make asking honest:
#
#   1. the category declares contexts in config.yaml, AND
#   2. the user stated no priority of their own.
#
# Condition 2 is the one that matters. If someone wrote "reliability matters a
# lot", we have their answer in their own words and a dropdown that could
# override it would be us replacing what they said with our taxonomy. Their
# words always win. The menu exists for silence, not for disagreement.
#
# And the question never blocks. Every context has a documented default sitting
# behind it, so declining to answer applies that default and logs it ASSUMED —
# the same treatment any other unstated field gets. See CLAUDE.md, "AMBIGUOUS".

class ContextOption(BaseModel):
    """One choice on the menu: the tag we store, and the words a human reads."""

    model_config = ConfigDict(frozen=True)

    tag: str = Field(description="Stored on the brief and in the audit log, e.g. 'office_calls'.")
    label: str = Field(description="What the user actually clicks, e.g. 'All-day calls at a desk'.")
    note: str = Field(default="", description="One line saying why this changes the answer.")


class ContextRequest(BaseModel):
    """Everything a screen needs to ask the one question, and nothing else.

    Returned by `context_needed()`. The UI renders it and hands a tag back to
    `compute()`; it never reads config.yaml itself and never sees a weight. The
    same split as the language step: the surface collects a choice, Python turns
    the choice into numbers.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    question: str
    options: tuple[ContextOption, ...]


def context_needed(brief: Brief) -> ContextRequest | None:
    """Should we ask what these are for? None means no — proceed to weights.

    Pure config plus what the user already said. No model, no catalog, no
    randomness: the same brief asks the same question every run, which is the
    only way a clarifying step belongs anywhere near a deterministic engine.
    """
    if brief.context_tag is not None:
        return None                      # already answered; asking twice is a form
    if brief.stated_priorities:
        return None                      # they told us in their own words
    contexts = config.category_contexts(brief.category)
    question = config.category_context_question(brief.category)
    if not contexts or not question:
        return None                      # this category has no opinion about usage

    return ContextRequest(
        category=brief.category,
        question=question,
        options=tuple(
            ContextOption(
                tag=tag,
                label=str(context["label"]),
                note=str(context.get("note", "")),
            )
            for tag, context in contexts.items()
        ),
    )


# ---------------------------------------------------------------------------
# Steps 1-4 — the weights themselves
# ---------------------------------------------------------------------------

def compute(
    brief: Brief,
    audit: AuditLogger | None = None,
    *,
    context_tag: str | None = None,
) -> Weights:
    """Turn a parsed brief into the four weights stage 4 will score with.

    The only public function here. Give it a Brief, get back a Weights with the
    numbers AND a plain-words source for each one, because "why is reliability
    0.45?" is a question the approval screen has to answer on the spot.

    `context_tag` overrides `brief.context_tag` when given. That is deliberately
    a keyword argument and deliberately not the normal path: the brief is the
    RECORD of what was chosen, and the parameter exists so a caller can ask "what
    would this brief score under a different context?" without editing the brief
    it is comparing against. tests/test_context_weights.py is the main user.
    """
    tag = context_tag if context_tag is not None else brief.context_tag
    defaults, default_label = _starting_weights(brief, tag, audit)
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
        label = default_label

        # Whether anything was stated at all, captured BEFORE the loop below
        # starts adding to `values`. This used to read `stated_total == 0`, which
        # was the same question until does_not_matter arrived: a buyer who says
        # "I don't care about price" states a priority worth 0.00, so the total
        # is zero and yet the other three genuinely were rescaled around it
        # (headphones reliability moves 0.35 -> 0.45). Reporting those as
        # untouched category defaults would have been a false line on the
        # approval screen, which is the one screen that has to be true.
        nothing_stated = not values

        for criterion in unstated:
            share = defaults[criterion] / pool if pool else 1.0 / len(unstated)
            values[criterion] = _round_to_step(budget * share)
            sources[criterion] = label if nothing_stated else f"{label}, rescaled"

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
            _reasoning(brief, ordered, tag),
            {
                "weights": ordered,
                "sources": weights.sources,
                "category": brief.category,
                # Both lines, always, even when there is no context. A reader
                # replaying this order has to be able to tell "no context applied"
                # from "the field did not exist yet", and an absent key cannot.
                "context_tag": tag,
                "context_label": config.context_label(brief.category, tag) if tag else None,
            },
        )

    return weights


def _starting_weights(
    brief: Brief,
    tag: str | None,
    audit: AuditLogger | None,
) -> tuple[dict[str, float], str]:
    """The weight set step 2 starts from, and the words that explain it on screen.

    Three ways this can go, and each one is logged differently because they are
    genuinely different events:

      a human picked a context   -> DECISION, actor USER. They chose; we obeyed.
      we asked and got no answer -> ASSUMPTION. We applied a documented default
                                    and the log says we did. Silence is not a
                                    preference.
      the category never asks    -> nothing extra. The ordinary stage-2 entry
                                    already says the defaults applied.

    The unknown-tag branch is the fourth case and it is a real one: a stale UI or
    a replayed audit line can carry a tag that config.yaml no longer defines. We
    fall back to the documented default and log the fallback loudly, because a
    weight set that quietly is not the one the log names is worse than a crash.
    """
    default = config.category_default_weights(brief.category)
    default_label = f"category default ({brief.category})"

    if tag:
        chosen = config.context_weights(brief.category, tag)
        if chosen is None:
            if audit:
                audit.assumption(
                    STAGE_WEIGHTS,
                    f"Usage context '{tag}' is not one this configuration offers for "
                    f"{brief.category}, so the documented default weights were applied "
                    f"instead of guessing at what was meant.",
                    {
                        "requested_context": tag,
                        "known_contexts": sorted(config.category_contexts(brief.category)),
                        "weights_applied": default,
                        "field_status": FieldStatus.ASSUMED.value,
                    },
                )
            return default, default_label

        label = config.context_label(brief.category, tag)
        if audit:
            audit.decision(
                STAGE_WEIGHTS,
                f"Buyer said these are for {label.lower()}, so that context's weights "
                f"were applied in place of the {brief.category} defaults.",
                {
                    "context_tag": tag,
                    "context_label": label,
                    "weights_applied": chosen,
                    "replaces": default,
                    "field_status": FieldStatus.CONFIRMED.value,
                },
                actor=Actor.USER,
            )
        return chosen, f"usage context ({label.lower()})"

    # No tag. Only an ASSUMPTION if we would have asked — a category with no
    # contexts is not assuming anything, and a user who stated a priority
    # already answered the question in their own words.
    if context_needed(brief) is not None and audit:
        audit.assumption(
            STAGE_WEIGHTS,
            f"No usage context was chosen, so the documented {brief.category} default "
            f"weights were applied. The ranking below reflects our default view of "
            f"this category, not a stated preference.",
            {
                "question_offered": config.category_context_question(brief.category),
                "options_offered": sorted(config.category_contexts(brief.category)),
                "weights_applied": default,
                "field_status": FieldStatus.ASSUMED.value,
            },
        )

    return default, default_label


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


def _reasoning(brief: Brief, values: dict[str, float], tag: str | None = None) -> str:
    """One plain sentence for the audit log, naming what drove the weights.

    The log has to survive being read by someone who was not in the room, so it
    says which criterion the user raised and what the others became as a result.
    """
    in_context = bool(tag) and config.context_weights(brief.category, tag) is not None
    basis = (
        f"the '{config.context_label(brief.category, tag).lower()}' weights"
        if in_context
        else f"the documented {brief.category} default weights"
    )

    if not brief.stated_priorities:
        # Two different silences, and the log must not blur them. "They picked a
        # context" and "they told us nothing at all" produce the same absence of
        # a stated priority and are not the same event.
        if in_context:
            return (
                f"No individual priority was stated, so {basis} were applied "
                f"unchanged from the usage context the buyer selected."
            )
        return f"No priority was stated, so {basis} were applied unchanged."

    said = ", ".join(
        f"{criterion} {phrase.replace('_', ' ')} ({values[criterion]:.2f})"
        for criterion, phrase in brief.stated_priorities.items()
        if criterion in values
    )
    return (
        f"User stated {said}; the remaining criteria were rescaled from {basis} "
        f"so the four weights still sum to 1.0."
    )
