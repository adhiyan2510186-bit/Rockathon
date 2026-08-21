"""Stages 0-2 — the ONLY file in this project that talks to a language model.

WHY THIS FILE EXISTS
--------------------
CLAUDE.md, "THE ONE RULE": the LLM interprets language, it never decides the
purchase. This file is where that rule is either kept or broken, so it is worth
being able to say exactly what happens in here:

    IN   one messy human sentence
    OUT  a filled-in Brief (structured data)

That is all. Nothing here scores, ranks, filters, compares to a limit, or
approves anything. Those live in ranking.py, discovery.py and authorisation.py,
in plain Python, and they never call this file back.

WHAT THE MODEL IS AND IS NOT SHOWN
----------------------------------
Shown:      the user's sentence, a JSON shape to fill in, and the NAMES of the
            priority phrases ('matters_a_lot', 'matters', 'nice_to_have').
Not shown:  the Rs 1,05,000 authorisation limit, the per-unit cap defaults, any
            weight, the 5-point substitution threshold. Not once, not as
            context, not as an example.

That is why no phrasing in a brief can talk the agent past a limit — you cannot
argue with a number you were never given. `_build_prompt()` below takes only the
user's text, so this claim is checkable by reading one function.

The model returns the LABEL 'matters_a_lot'. It never returns 0.45 and is never
told 0.45 exists. config.py does that lookup afterwards, in Python. That single
hop is the difference between a model interpreting language and a model deciding
a purchase.

TWO PATHS, ONE DESTINATION
--------------------------
There is a real Gemini path and an offline word-matching path (free tier is
roughly 10 requests a minute, and a demo cannot die because we hit it). Both
paths produce the same intermediate `_Extraction` and then go through the same
`_to_brief()` conversion. So the offline mode cannot quietly behave differently
from the online mode — only the reading of the sentence changes, never what
happens to the result.

We always say which path ran. `.source` is 'gemini' or 'offline' and the UI
prints it. We degrade honestly; we never pretend a model call happened.

The offline path is reachable three ways, and they are genuinely different
things:

    no API key         nothing to call
    a call failed      out of quota, or the model is down (allow_offline_fallback)
    we CHOSE to        force_offline=True, from the sidebar switch

The third one exists because the free tier is a DAILY allowance, and one brief
costs two calls — the scope check and the extraction. Every rehearsal run we
make with the model on is a run we cannot make on stage. `force_offline` skips
the call entirely rather than making it and discarding the answer, so the
allowance is genuinely untouched, and the note we surface says we chose this
rather than implying something broke.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agent import config
from agent.audit import STAGE_EXTRACTION, STAGE_SCOPE, AuditLogger
from agent.models import Brief, FieldStatus, ScopeVerdict

# The .env file at the project root, holding the Gemini key. We read it with the
# six lines in _api_key() rather than adding python-dotenv, because a sixth
# dependency to parse "KEY=value" is not a dependency we could defend.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# What we say when someone asks us something that is not a purchasing brief.
# CLAUDE.md, stage 0: state the scope, stay ready for the next message. We do not
# lecture and we do not attempt a half-answer outside our lane.
#
# It is also the one line where the agent introduces itself by name, because it
# is the one moment a person has clearly not understood what they are talking to.
# Nowhere else does it announce itself: a tool that says its own name in every
# reply is a chatbot, not a colleague.
SCOPE_STATEMENT = (
    "I'm Evets, and I handle procurement briefs only: what to buy, how many, "
    "your price ceiling and your delivery window. I can't help with that one, "
    "but send me a buying brief and I'll take it from there."
)

# Fields discovery genuinely cannot start without. Everything else either has a
# declared default in config.yaml (and is logged as ASSUMED) or is a soft
# preference that simply gets a category default weight.
#
# Note what is NOT in this list: the per-unit price cap. config.yaml declares a
# default cap per category, so a silent brief gets that default and an ASSUMED
# tag. We only interrupt the user for the things we have no declared answer for.
REQUIRED_FOR_DISCOVERY: tuple[str, ...] = ("category", "quantity", "max_delivery_days")


# ---------------------------------------------------------------------------
# What this module hands back
# ---------------------------------------------------------------------------

class ScopeOutcome(BaseModel):
    """Stage 0's answer, plus an honest note about how we arrived at it."""

    verdict: ScopeVerdict
    source: Literal["gemini", "offline"]
    note: str = Field(description="Plain sentence for the UI, e.g. 'offline mode - no AI used'.")


class BriefOutcome(BaseModel):
    """Stage 1's parsed brief, plus the same honesty about which path ran."""

    brief: Brief
    source: Literal["gemini", "offline"]
    note: str


# ---------------------------------------------------------------------------
# The shapes we ask the model to fill in
# ---------------------------------------------------------------------------
# These are deliberately NOT the Brief model. Brief carries provenance
# (field_status: CONFIRMED vs ASSUMED) and that is ours to decide, not the
# model's — a model that could mark its own guess CONFIRMED would defeat the
# audit trail. So the model fills in these narrower shapes, and Python decides
# what each answer means.

class _StatedPriority(BaseModel):
    """One thing the user said mattered, and how strongly they said it."""

    criterion: Literal["reliability", "price", "delivery", "replacement"]
    phrase: str = Field(description="One of the labels from config: matters_a_lot / matters / nice_to_have.")


class _Extraction(BaseModel):
    """The raw reading of the sentence. Values only — no judgement about them."""

    category: str = Field(default="", description="What is being bought, in the user's words.")
    quantity: int | None = None
    specs: list[str] = Field(default_factory=list)
    max_price_per_unit_inr: float | None = None
    max_delivery_days: int | None = None
    # THREE STATES, NOT TWO, AND THE THIRD IS WHY THIS FIELD EXISTS.
    # `max_delivery_days = None` used to carry two different meanings that the
    # gate has to tell apart: "the buyer never mentioned a deadline" (ask them)
    # and "the buyer says there is no deadline" (get on with it). A single
    # nullable int cannot say both, so the second one gets its own flag.
    # Set it and leave the number null; never both.
    delivery_is_open: bool = Field(
        default=False,
        description="True only when the buyer SAID there is no deadline - 'no "
        "rush', 'whenever'. Not the same as failing to mention one.",
    )
    stated_priorities: list[_StatedPriority] = Field(default_factory=list)
    flexibility_order: list[str] = Field(
        default_factory=list,
        description="Only if the user actually said what they would bend on first.",
    )


class _ScopeCheck(BaseModel):
    """The model's read on whether this message is even a buying brief."""

    verdict: Literal["out_of_scope", "incomplete", "in_scope"]
    missing_fields: list[str] = Field(default_factory=list)
    question: str = Field(default="", description="ONE targeted question, only when incomplete.")


# ---------------------------------------------------------------------------
# Stage 0 — scope & completeness gate
# ---------------------------------------------------------------------------

def check_scope(
    text: str,
    audit: AuditLogger | None = None,
    *,
    force_offline: bool = False,
) -> ScopeOutcome:
    """Decide whether to start work on this message at all.

    Three outcomes and no others: refuse politely (not procurement), ask ONE
    question (a real brief with a hole in it), or proceed. CLAUDE.md is blunt
    about the middle one: no discovery starts on a guess.

    The one question is logged as a DECISION, not an ESCALATION. Escalation is
    reserved for the three stage-5 triggers — if a routine clarifying question
    counted as an escalation, the boundary we are demonstrating would blur on the
    very first screen.

    `force_offline` skips the model and reads the sentence by word matching. A
    caller's choice, passed in rather than read from a global, so a run's parser
    is decided in one visible place instead of by whatever last set a flag.
    """
    check, source, note = _read_scope(text, force_offline)

    if check.verdict == "out_of_scope":
        verdict = ScopeVerdict(verdict="out_of_scope", message=SCOPE_STATEMENT, missing_fields=[])
        if audit:
            audit.decision(
                STAGE_SCOPE,
                "Message is not a procurement brief, so no discovery was started.",
                {"user_message": text[:200], "action": "declined, scope stated"},
            )
    elif check.verdict == "incomplete" and _blockers(check):
        missing = _blockers(check)
        question = check.question or _default_question(missing)
        verdict = ScopeVerdict(verdict="incomplete", message=question, missing_fields=missing)
        if audit:
            audit.decision(
                STAGE_SCOPE,
                "Brief was missing something we have no declared default for, so we asked once "
                "rather than guessing.",
                {"missing_fields": missing, "question_asked": question},
            )
    else:
        verdict = ScopeVerdict(verdict="in_scope", message="", missing_fields=[])

    return ScopeOutcome(verdict=verdict, source=source, note=note)


def _blockers(check: _ScopeCheck) -> list[str]:
    """The missing fields that are actually worth stopping the user for.

    The model is asked about three fields but can name others, and anything with
    a declared default in config.yaml is not a blocker — we fill it in and log it
    as an assumption instead of interrupting.

    This filter used to be applied to the field list while the model's QUESTION
    was passed through untouched, which let the two disagree: a run could report
    nothing missing and still stop to ask about a price ceiling, leaving the user
    answering a question the agent had already decided it did not need. Now an
    empty list after filtering means there is nothing to ask, and check_scope
    proceeds.
    """
    return [field for field in check.missing_fields if field in REQUIRED_FOR_DISCOVERY]


def _default_question(missing: list[str]) -> str:
    """Turn missing field names into ONE sentence a human would actually ask.

    One question, never a form. Asking three separate things is how a chat
    assistant turns into paperwork.
    """
    asks = {
        "category": "what you're buying",
        "quantity": "how many units you need",
        # Naming the get-out is the point. "your delivery deadline in days"
        # described the only shape the parser accepted without ever saying so,
        # and a buyer with no deadline had no way to say that.
        "max_delivery_days": "your delivery deadline, or say 'no rush' if you have none",
        "max_price_per_unit_inr": "your per-unit price ceiling",
    }
    wanted = [asks[field] for field in missing if field in asks] or ["a bit more detail"]
    if len(wanted) == 1:
        return f"Before I start looking - {wanted[0]}?"
    return f"Before I start looking - {', '.join(wanted[:-1])} and {wanted[-1]}?"


# ---------------------------------------------------------------------------
# Stage 1 — requirement extraction (and the labels stage 2 needs)
# ---------------------------------------------------------------------------

def extract_brief(
    text: str,
    audit: AuditLogger | None = None,
    *,
    force_offline: bool = False,
) -> BriefOutcome:
    """Turn the sentence into a Brief. The handover from English to arithmetic.

    Everything before this line is interpretation of language. Everything after
    it is plain Python. Once this function returns, the model has no further say
    in the run — it is not consulted again at any stage.

    `force_offline` reads the sentence by word matching instead of calling the
    model. Note what it does NOT change: the Brief that comes out, the audit
    entry's shape, or anything downstream. Only who did the reading, and the log
    records which parser that was.
    """
    extraction, source, note = _read_extraction(text, force_offline)
    brief = _to_brief(text, extraction, audit)

    if audit:
        audit.decision(
            STAGE_EXTRACTION,
            "Read your request and picked out what you asked for: what, how "
            f"many, by when, and at what price.{_reader_note(source)}",
            {
                "category": brief.category,
                "quantity": brief.quantity,
                "max_price_per_unit_inr": brief.max_price_per_unit_inr,
                "max_delivery_days": brief.max_delivery_days,
                "specs": brief.specs,
                "stated_priorities": brief.stated_priorities,
                "parser": source,
            },
        )

    return BriefOutcome(brief=brief, source=source, note=note)


def _to_brief(raw_text: str, extraction: _Extraction, audit: AuditLogger | None) -> Brief:
    """Convert a raw reading into a Brief, and decide what every value MEANS.

    Both the Gemini path and the offline path end up here, which is the point:
    provenance, defaults and category normalisation happen once, in Python, no
    matter who read the sentence.

    A field the user stated is CONFIRMED. A field we filled from config.yaml is
    ASSUMED and is written to the audit log at the moment we fill it — before
    discovery is allowed to use it. An assumption logged after the purchase is
    just a story.
    """
    category = _normalise_category(extraction.category, raw_text)
    status: dict[str, FieldStatus] = {
        "category": FieldStatus.CONFIRMED,
        "quantity": FieldStatus.CONFIRMED,
        "max_delivery_days": FieldStatus.CONFIRMED,
    }

    cap = extraction.max_price_per_unit_inr
    if cap is None:
        cap = config.per_unit_cap_default_inr(category)
        status["max_price_per_unit_inr"] = FieldStatus.ASSUMED
        if audit:
            audit.assumption(
                STAGE_EXTRACTION,
                f"No per-unit price cap was stated, so the declared {category} default of "
                f"Rs {cap:.2f} was applied.",
                {
                    "field": "max_price_per_unit_inr",
                    "value_inr": cap,
                    "status": "ASSUMED",
                    "source": "config.yaml per_unit_cap_defaults_inr",
                },
            )
    else:
        status["max_price_per_unit_inr"] = FieldStatus.CONFIRMED

    # Only labels the config actually knows survive. If the model returns a
    # phrase we have no weight for, dropping it means the criterion falls back to
    # its category default — a documented number — instead of stage 2 inventing
    # one at runtime.
    #
    # A duplicate criterion keeps the FIRST reading, matching the offline parser's
    # rule. This used to be a plain dict comprehension, which silently kept the
    # LAST — so if the model returned reliability twice, the two parsers could
    # disagree about the same sentence, and neither of them said so. Contradictory
    # input deserves one documented rule, applied the same way on both paths.
    known = set(config.priority_phrase_labels())
    priorities: dict[str, str] = {}
    for item in extraction.stated_priorities:
        if item.phrase in known and item.criterion not in priorities:
            priorities[item.criterion] = item.phrase

    return Brief(
        raw_text=raw_text,
        category=category,
        quantity=extraction.quantity or 0,
        specs=extraction.specs,
        max_price_per_unit_inr=cap,
        # Passed straight through, including None. The `or 0` that used to be
        # here could only ever produce a number the Brief then rejected
        # (`gt=0`), so an unstated deadline was a ValidationError waiting on the
        # scope gate to prevent it. None is now a value the field can hold and
        # every stage downstream reads as "no window was set".
        max_delivery_days=extraction.max_delivery_days,
        stated_priorities=priorities,
        field_status=status,
        flexibility_order=extraction.flexibility_order,
    )


def _normalise_category(stated: str, raw_text: str) -> str:
    """Map whatever words came back onto a category config.yaml knows about.

    Done in Python, not by the model, because this string chooses which default
    weights and which default price cap apply. That is a decision with numbers
    attached, so it does not belong on the model's side of the line.

    When nothing matches, we keep the USER'S OWN WORDS rather than replacing them
    with the string "default". Every config lookup already falls back to the
    `default` block on its own (agent/config.py, `_category`), so nothing needed
    the placeholder — and writing it into the Brief threw away the one fact the
    refusal has to state. An agent that answers a cement brief with "no vendor
    stocks default" has lost the question, which is worse than being unable to
    answer it.
    """
    haystack = f"{stated} {raw_text}".lower()
    for category, keywords in config.category_keywords().items():
        if any(word in haystack for word in keywords):
            return category
    return stated.strip().lower() or "unspecified"


# ---------------------------------------------------------------------------
# The Gemini path
# ---------------------------------------------------------------------------

def _api_key() -> str | None:
    """Read GEMINI_API_KEY from the environment, or from .env if it is not there.

    An empty value set DELIBERATELY means "no key", and stops here rather than
    falling through to the file. That distinction is load bearing: our test
    suite clears this variable to force the offline parser, and for a long time
    it did not work — the empty string was falsy, we read .env instead, and the
    tests quietly went to the network. They passed anyway, because the quota was
    exhausted and every call fell back offline. The moment the quota came back
    the "offline" tests started producing live parses and one of them failed.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if key is not None:
        return key.strip() or None
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                value = line.split("=", 1)[1].strip()
                if value and value != "paste-your-key-here":
                    return value
    return None


def is_online() -> bool:
    """Whether a real model call is even possible. The sidebar shows this."""
    return _api_key() is not None


def _call_gemini(prompt: str, schema: type[BaseModel]) -> BaseModel:
    """One schema-constrained call. The only place in the project that reaches a model.

    Temperature 0 because this is an extraction task, not a creative one: the
    same sentence should read the same way twice. The response_schema means we
    get a validated object back rather than prose we have to hope parses.
    """
    from google import genai  # imported here so the offline path needs no SDK

    client = genai.Client(api_key=_api_key())
    response = client.models.generate_content(
        model=config.llm_model(),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": 0,
        },
    )
    parsed = response.parsed
    if parsed is None:
        raise ValueError("Gemini returned no parsable JSON for the requested schema")
    return parsed


def _build_prompt(text: str, task: str) -> str:
    """Assemble the prompt. Read the arguments: the user's text and a task string.

    No limit, no weight, no threshold is reachable from here — this function has
    no access to one. That is the enforcement, and it is one function long.
    """
    return f"{task}\n\nUser's message:\n\"\"\"\n{text.strip()}\n\"\"\"\n"


_SCOPE_TASK = (
    "You are the intake step of a procurement assistant. Decide ONE of:\n"
    "- out_of_scope: the message is not about buying goods or supplies at all.\n"
    "- incomplete: it is a buying request, but it does not say what is being "
    "bought, or how many, or anything at all about when it is needed.\n"
    "- in_scope: it says what, how many, and EITHER a delivery deadline OR that there is no deadline.\n"
    "A buyer is allowed to have no date. 'No rush', 'whenever', 'take your time' and 'no deadline' are COMPLETE answers about timing, not missing ones, and someone who has already said they have no deadline must never be asked for one again.\n\n"
    "List the missing field names using exactly these words: category, quantity, "
    "max_delivery_days. If incomplete, write ONE short question that asks for all "
    "the missing pieces together. Do not answer the user's request and do not "
    "recommend anything."
)


def _extraction_task() -> str:
    """Build the extraction instructions, including the phrase labels — labels only.

    config.priority_phrase_labels() returns the KEYS from config.yaml
    ('matters_a_lot'), never the values (0.45). The model chooses a label; Python
    looks up the number afterwards.
    """
    labels = ", ".join(config.priority_phrase_labels())
    return (
        "Extract the buying requirements from the message into the given JSON shape. "
        "Copy what the user said; do not add requirements they did not state, and do "
        "not judge whether anything is reasonable.\n\n"
        f"For stated_priorities, use only these phrase labels: {labels}. "
        "Include a criterion ONLY if the user actually expressed a preference about "
        "it. 'Reliability matters a lot' is a stated priority. Stating a LIMIT is "
        "not a preference: 'max Rs 22 per unit' and 'delivered within 10 days' are "
        "requirements the purchase has to meet, and neither is a priority.\n"
        # Added after "5 gaming laptops - as cheap as possible" came back with no
        # priority at all. That is a stated preference in anyone's English, and
        # missing it does more damage than missing a spec: a missed spec ends in
        # an honest refusal, while a missed priority ends in a confident
        # recommendation ranked against the one thing the buyer asked for.
        "A preference does not have to name the criterion to be one. 'As cheap as "
        "possible' and 'the cheapest you can find' are price at the strongest "
        "label; 'whatever is most reliable' is reliability; 'we need these fast' "
        "is delivery. A superlative about the purchase is a stated priority.\n"
        # Added because the schema had no way to express this at all. The weakest
        # label used to be nice_to_have, so a buyer who dismissed a criterion
        # outright could only be recorded as mildly wanting it - and the offline
        # parser did worse, reading "don't care about price" as price MATTERING
        # because the words "care about" sit inside it. A criterion the buyer
        # waved away must be able to score zero, or we are quietly overruling the
        # one instruction they gave us.
        "A preference can also be NEGATIVE, and that is still a stated priority. "
        "'Don't care about price', 'price is not a concern', 'no preference on "
        "delivery' and 'we're not fussy about the warranty' all mean the buyer "
        "wants that criterion to carry no weight - pick the label whose own name "
        "says the criterion does not matter. Choose the label by reading its "
        "name; the names mean what they say. "
        "Saying nothing about a criterion is different and is NOT a "
        "priority: leave it out entirely.\n\n"
        "TIMING HAS THREE ANSWERS, NOT TWO. If the user gives a deadline, "
        "put it in max_delivery_days in DAYS, converting yourself - two "
        "weeks is 14, a month is 30.\n"
        "If the user says there is no deadline ('no rush', 'whenever', "
        "'take your time'), set delivery_is_open true and leave "
        "max_delivery_days null.\n"
        "If the user says nothing about timing at all, leave both alone. "
        "That is the case we ask a question about, and it is NOT the same "
        "as being told there is no deadline.\n\n"
        "A deadline and a PREFERENCE about speed are different facts. 'We "
        "do not care about delivery speed' is a priority; 'no rush' is an "
        "open deadline. A message can state either without the other.\n\n"
        "Leave a numeric field null if the user did not state it. Never guess a "
        "number. Put physical or technical requirements - materials, "
        "dimensions, sizes, capacities - in specs.\n\n"
        # Added after watching a live parse drop two of three specs. The model
        # read "25 wireless noise-cancelling headsets, over-ear" as a PRODUCT
        # NAME with one spec after the comma, so a headset with no noise
        # cancelling passed a requirement the buyer had actually stated. Specs
        # are a pass/fail gate, so a spec the parser misses is not a cosmetic
        # loss - it is a product qualifying on something it does not have.
        "A word describing the item itself is a spec, not part of its name. In "
        "'25 wireless noise-cancelling headsets, over-ear' the specs are "
        "wireless, noise-cancelling AND over-ear. In '5,000 kraft mailer boxes, "
        "double-wall' they are kraft and double-wall. Use the user's own words "
        "for each one."
    )


# ---------------------------------------------------------------------------
# Choosing a path
# ---------------------------------------------------------------------------

def _use_model(force_offline: bool) -> bool:
    """Whether this particular read should call the model.

    Two ways to end up offline: the caller asked for it, or there is no key to
    call with. Both skip the call rather than making one and discarding the
    answer — the point of the switch is the request that never leaves.

    config.use_model_default() is deliberately NOT consulted here. It is the
    position the UI switch starts in, and if this function enforced it as well
    then a config of false would silently ignore a user turning the switch back
    on. One decision, one owner: config seeds the switch, the switch is passed in
    as force_offline, and this line reads only what it was handed.
    """
    return not force_offline and is_online()


# What we tell the user when we did not call the model. Kept apart from
# _offline_note() below, which explains a call that FAILED. Collapsing the two
# would mean a deliberate choice reads as a fault, and "the AI is off because you
# turned it off" and "the AI is off because it stopped answering" are not
# something a user should have to tell apart from one shared sentence.
_CHOSE_OFFLINE_NOTE = "offline mode - AI reading is switched off, so no request was sent"
_NO_KEY_NOTE = "offline mode - no API key, no AI used"


def _read_scope(
    text: str, force_offline: bool = False,
) -> tuple[_ScopeCheck, Literal["gemini", "offline"], str]:
    """Stage 0's reading of the sentence, via Gemini if we can, offline if we must."""
    if _use_model(force_offline):
        try:
            check = _call_gemini(_build_prompt(text, _SCOPE_TASK), _ScopeCheck)
            return check, "gemini", f"read by {config.llm_model()}"
        except Exception as exc:  # noqa: BLE001 — any SDK failure means the same thing to us
            if not config.allow_offline_fallback():
                raise
            return _offline_scope(text), "offline", _offline_note(exc)
    return _offline_scope(text), "offline", _skipped_note(force_offline)


def _read_extraction(
    text: str, force_offline: bool = False,
) -> tuple[_Extraction, Literal["gemini", "offline"], str]:
    """Stage 1's reading of the sentence, same two paths."""
    if _use_model(force_offline):
        try:
            extraction = _call_gemini(_build_prompt(text, _extraction_task()), _Extraction)
            return extraction, "gemini", f"read by {config.llm_model()}"
        except Exception as exc:  # noqa: BLE001
            if not config.allow_offline_fallback():
                raise
            return _offline_extract(text), "offline", _offline_note(exc)
    return _offline_extract(text), "offline", _skipped_note(force_offline)


def _reader_note(source: str) -> str:
    """How the sentence was read, in words a buyer would use.

    The audit trail has to record WHICH reader ran - a run parsed by word
    matching and a run parsed by the model are not the same run, and a judge is
    entitled to know which one they watched. But "the offline parser" is our
    word for our own machinery. A buyer reads this entry too, in the same list,
    so it says what happened rather than which code path did it.
    """
    return "" if source == "gemini" else " Read by word matching, with no AI."


def _skipped_note(force_offline: bool) -> str:
    """Why no call was made: because we chose not to, or because we could not."""
    return _CHOSE_OFFLINE_NOTE if force_offline else _NO_KEY_NOTE


def _offline_note(exc: Exception) -> str:
    """Say plainly why we dropped offline. Rate limits are the likely reason.

    The free tier is roughly ten requests a minute and we would rather show a
    judge an honest 'offline mode' label than have the demo stop dead.
    """
    reason = "rate limit reached" if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc) else "model unavailable"
    return f"offline mode - {reason}, parsed by word matching (no AI used)"


# ---------------------------------------------------------------------------
# The offline parser
# ---------------------------------------------------------------------------
# Deliberate word matching. It is not clever and it is not meant to be — it is
# the thing that keeps a demo alive when the free tier says no. It handles the
# briefs we demo and it fails visibly rather than quietly on anything unusual,
# which is the right trade for a fallback.

_BUYING_WORDS = (
    "buy", "order", "reorder", "purchase", "procure", "source", "supplier",
    "vendor", "quote", "restock", "need", "want", "get me", "looking for",
)

_PRIORITY_WORDS: dict[str, tuple[str, ...]] = {
    "reliability": ("reliab", "trust", "burned", "let us down", "dependab", "consistent"),
    "price": ("price", "cost", "cheap", "budget", "afford", "value for money"),
    "delivery": ("delivery", "deliver", "fast", "quick", "speed", "urgent", "lead time"),
    "replacement": ("replace", "return", "warrant", "refund", "damage"),
}

# Strongest phrasing first — "matters a lot" also contains "matters", so order
# decides the answer and the strongest reading must win.
#
# The superlatives in the first row were added to match what the Gemini prompt
# already tells the model. Without them "5,000 boxes, as cheap as possible" came
# back with NO priority at all: "cheap" found the price criterion, and then not
# one strength phrase matched, so the only thing the buyer actually said was
# dropped and the category default silently took its place. A missed spec ends in
# an honest refusal; a missed priority ends in a confident recommendation ranked
# against something they never asked for.
_PHRASE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("matters_a_lot", ("matters a lot", "really matters", "very important", "critical",
                       "top priority", "non-negotiable", "matters most", "hugely",
                       "cheapest", "as cheap as possible", "as much as possible",
                       "as low as possible", "as fast as possible", "asap",
                       "is everything", "most important", "above all",
                       "budget is tight", "tight budget", "absolutely")),
    ("matters", ("matters", "important", "care about", "priority", "prefer")),
    ("nice_to_have", ("nice to have", "would be nice", "bonus", "if possible", "ideally")),
)

# Checked BEFORE any strength phrase, because a dismissal reads like an
# endorsement to a substring matcher. "Don't care about price" contains "care
# about", so the old code scored price at 0.30 — up from the 0.25 default on some
# categories. The parser did the exact opposite of what the buyer said, and
# nothing on screen looked wrong.
#
# These are cues about the CLAUSE, not about a criterion, so they live apart from
# _PRIORITY_WORDS: the clause still has to name a criterion before we record
# anything, or "whatever you think" would zero a column nobody mentioned.
_NEGATION_CUES: tuple[str, ...] = (
    "don't care", "dont care", "do not care", "not care", "couldn't care",
    "no preference", "not fussed", "not bothered", "not worried",
    "not a concern", "isn't a concern", "is not a concern", "no concern",
    "doesn't matter", "does not matter", "dont matter", "not important",
    "not fussy", "no strong", "whatever", "don't mind", "dont mind",
    "do not mind", "least of", "not a priority", "no issue with",
)

# --- how long is "two weeks"? -------------------------------------------------
# Calendar arithmetic, so it lives here rather than in config.yaml. config.yaml
# holds LIMITS - things a buyer or a finance manager might argue about and edit.
# Seven days in a week is not one of those. A month is thirty because a buying
# window is a rough span, not a calendar date, and thirty is the convention
# every purchasing system already uses.
#
# Order matters: the longest unit first, so "18 months" cannot be read as
# "18 mon..." by a shorter pattern.
_DELIVERY_UNITS: tuple[tuple[str, int], ...] = (
    (r"(\d+)\s*months?", 30),
    (r"(\d+)\s*weeks?", 7),
)

# The article forms, which carry no digit for the patterns above to catch.
# Checked against the lowered sentence, longest first so "a fortnight" is not
# shadowed by a prefix of something else.
_DELIVERY_PHRASES: dict[str, int] = {
    "a fortnight": 14,
    "one fortnight": 14,
    "a month": 30,
    "one month": 30,
    "next month": 30,
    "a week": 7,
    "one week": 7,
    "next week": 7,
}

# WHAT A BUYER SAYS WHEN THERE IS NO DEADLINE.
#
# This list is the whole reason the interface stopped being a dead end. The gate
# asks for a delivery window and would only accept a digit followed by the word
# "days"; someone whose honest answer was "no rush" got the same question back
# forever, because their reply was appended to the brief and re-checked against
# the same regex.
#
# These are about the DEADLINE, and they are deliberately separate from
# _NEGATION_CUES, which is about a PREFERENCE. "We do not care about delivery
# speed" says the buyer will not weight speed highly (a stage-2 weight of 0.00).
# "No rush" says there is no date to hit (no stage-3 gate at all). Two different
# facts about two different stages, and a brief can state either without the
# other. The overlap in ordinary English is why both lists exist and why neither
# is allowed to read the other's phrases.
_NO_DEADLINE_CUES: tuple[str, ...] = (
    "no rush", "no hurry", "not in a rush", "not in a hurry",
    "no deadline", "no delivery deadline", "no due date",
    "whenever", "take your time", "not urgent", "no time pressure",
    "flexible on delivery", "flexible on the delivery",
    "no particular deadline", "no specific deadline",
)


# The offline parser's idea of where a product name stops. Everything in
# _CLAUSE_BOUNDARY opens a clause ABOUT the purchase (what it may cost, when it
# must land) rather than naming the thing itself, so the noun phrase ends there.
# _LEAD_IN is the polite runway in front of it ("buy me...", "i need some...").
# Neither list decides anything - the category string they build only ever
# reaches stage 3 to be matched against what we stock, or named back to the user
# in a refusal. A wrong word here costs us an ugly sentence, never a wrong buy.
_CLAUSE_BOUNDARY: frozenset[str] = frozenset({
    "under", "below", "over", "above", "max", "maximum", "minimum", "least",
    "most", "at", "upto", "each", "apiece", "per", "within", "budget",
    "cost", "costing", "price", "priced", "rs", "inr", "rupees",
    "deliver", "delivered", "delivery", "arriving", "arrive", "arrives",
    "ship", "shipped", "shipping", "by", "before", "in", "no", "not",
    "that", "which", "needed", "please",
})

_LEAD_IN: frozenset[str] = frozenset({
    "buy", "order", "reorder", "get", "source", "procure", "purchase", "find",
    "need", "want", "looking", "for", "me", "us", "i", "we", "my", "our",
    "please", "can", "could", "you", "some", "a", "an", "the",
})


# Trade shorthand for order sizes. "10k boxes" is how a real reorder gets typed.
_SCALE_SUFFIXES: dict[str, int] = {
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "lakh": 100_000, "lakhs": 100_000,
}

# Enough English to read an order size written out in words. Deliberately small:
# this only runs when the sentence contains no digit at all, so it is a rescue
# path for "two hundred chairs", not a general number parser.
_WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "dozen": 12,
}

_WORD_SCALES: dict[str, int] = {
    "hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000,
    "million": 1_000_000,
}


def _word_number(segment: str) -> int | None:
    """Read an order size written in words ("two hundred" -> 200), or None.

    Stops at the first word that is not part of the number, so "two hundred
    chairs" reads 200 and does not wander into the rest of the sentence. "and" is
    allowed through the middle ("one thousand and fifty") because it is how the
    number is spoken, not a break in it.
    """
    total = 0
    current = 0
    started = False

    for word in re.findall(r"[a-z]+", segment):
        if word in _WORD_NUMBERS:
            current += _WORD_NUMBERS[word]
            started = True
        elif word in _WORD_SCALES:
            scale = _WORD_SCALES[word]
            # "hundred" multiplies what we are holding; "thousand" and above bank
            # it, so "two hundred thousand" is 200,000 rather than 100,000 + 2.
            if scale >= 1_000:
                total += max(current, 1) * scale
                current = 0
            else:
                current = max(current, 1) * scale
            started = True
        elif word == "and" and started:
            continue
        elif started:
            break

    return (total + current) or None


def _noun_phrase(segment: str) -> str:
    """Read a product name out of one stretch of the sentence, or return "".

    Strip the runway off the front, then keep words until one of them turns out
    to be talking about the deal instead of the goods. Five words is the cap:
    past that we are copying the sentence back, not naming a product.
    """
    words = re.findall(r"[a-z][a-z\-]*", segment)
    while words and words[0] in _LEAD_IN:
        words.pop(0)

    kept: list[str] = []
    for word in words:
        if word in _CLAUSE_BOUNDARY:
            break
        kept.append(word)
    return " ".join(kept[:5])


def _clause_strength(clause: str) -> str | None:
    """How strongly this clause speaks about whatever criterion it names.

    Returns a phrase LABEL, or None when the clause mentions a criterion without
    saying anything about how much it counts ("delivered within 10 days" names
    delivery and states a requirement, not a preference).

    Negation is tested FIRST and that ordering is the whole point of the
    function. Every dismissal in English is built out of endorsement words with a
    "not" in front, so a matcher that scans for endorsements first reads "don't
    care about price" as "care about price" and scores the criterion UP. Checking
    the negation cues before the strength phrases is what stops the parser
    inverting the one instruction the buyer gave us.
    """
    if any(cue in clause for cue in _NEGATION_CUES):
        return "does_not_matter"
    for label, phrases in _PHRASE_PATTERNS:
        if any(phrase in clause for phrase in phrases):
            return label
    return None


def _offline_scope(text: str) -> _ScopeCheck:
    """Decide scope by looking for buying words and the three required fields."""
    lowered = text.lower()
    extraction = _offline_extract(text)

    looks_like_buying = any(word in lowered for word in _BUYING_WORDS) or extraction.quantity is not None
    has_product = bool(extraction.category) or extraction.quantity is not None
    if not (looks_like_buying and has_product):
        return _ScopeCheck(verdict="out_of_scope")

    missing = []
    if not extraction.category:
        missing.append("category")
    if not extraction.quantity:
        missing.append("quantity")
    # "No rush" is an ANSWER, not a hole. The gate is still here and still asks
    # once - it just stops treating a buyer who genuinely has no deadline as
    # someone who failed to answer. That was the dead end: the reply was
    # appended to the brief and re-checked against the same regex, so the same
    # question came back forever.
    if not extraction.max_delivery_days and not extraction.delivery_is_open:
        missing.append("max_delivery_days")

    if missing:
        return _ScopeCheck(verdict="incomplete", missing_fields=missing, question=_default_question(missing))
    return _ScopeCheck(verdict="in_scope")


def _offline_extract(text: str) -> _Extraction:
    """Pull the numbers and phrases out of the sentence with regular expressions.

    Order matters here. The price cap and the delivery window are matched FIRST
    and then blanked out of a working copy of the text, so that when we go
    looking for the quantity we cannot accidentally pick up the 22 from "Rs 22
    per unit" or the 10 from "within 10 days".
    """
    lowered = text.lower()
    working = lowered

    # --- price cap ---------------------------------------------------------
    # Digits may be comma-grouped. A box costs "Rs 22" and a laptop costs
    # "Rs 65,000", and a pattern that stops at the comma reads the second one as
    # a cap of sixty-five rupees - which every laptop then "exceeds", so the pool
    # empties and the screen blames the vendors. Same grouping the quantity
    # matcher below already allows.
    cap: float | None = None
    price_match = re.search(
        r"(?:max|maximum|under|below|upto|up to|no more than|at most)\s*"
        r"(?:rs\.?|inr|₹)?\s*(\d[\d,]*(?:\.\d+)?)",
        working,
    ) or re.search(
        # The currency symbol is OPTIONAL here. "under Rs 50 each" is caught by
        # the pattern above; "50 each" - no symbol, and no cap word either
        # because the user typed "uner" - reached this line and was rejected,
        # so the 50 survived into the sentence and the quantity matcher below
        # read it as an order of fifty. A price misread as a quantity is the
        # worst kind of parse: nothing looks broken on screen. What makes this
        # safe to loosen is that "each"/"per unit" must FOLLOW the number
        # immediately, which no quantity in a real brief ever does ("12 chairs
        # each" has a noun in the way).
        r"(?:rs\.?|inr|₹)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:per unit|/\s*unit|each|apiece|a unit|a piece)",
        working,
    )
    if price_match:
        cap = float(price_match.group(1).rstrip(",").replace(",", ""))
        working = working.replace(price_match.group(0), " ", 1)

    # --- delivery window ---------------------------------------------------
    # A NUMBER ALWAYS WINS. Only when nothing numeric was said do we look for a
    # phrase that means "there is no deadline" - so "within 12 days, no rush
    # otherwise" is a twelve-day window, not an open one.
    #
    # EVERY BRANCH STRIPS WHAT IT MATCHED, and that is not tidiness. Whatever
    # survives this function is read as the QUANTITY further down (see the note
    # at the top), so an unstripped "2 weeks" becomes an order for two units.
    days: int | None = None
    delivery_is_open = False

    days_match = re.search(r"(\d+)\s*(?:working |business |calendar )?days?", working)
    if days_match:
        days = int(days_match.group(1))
        working = working.replace(days_match.group(0), " ", 1)
    else:
        for pattern, multiplier in _DELIVERY_UNITS:
            unit_match = re.search(pattern, working)
            if unit_match:
                days = int(unit_match.group(1)) * multiplier
                working = working.replace(unit_match.group(0), " ", 1)
                break
        else:
            for phrase, span in _DELIVERY_PHRASES.items():
                if phrase in lowered:
                    days = span
                    working = working.replace(phrase, " ", 1)
                    break

    if days is None and any(phrase in lowered for phrase in _NO_DEADLINE_CUES):
        delivery_is_open = True

    # --- category ----------------------------------------------------------
    # Found BEFORE specs, because which words count as a physical requirement
    # depends on what is being bought. "recycled" is a real packaging spec and
    # noise in a chair brief; extracted from the wrong category it would become
    # a hard constraint the user never stated and empty the pool on stage.
    category = ""
    for name, keywords in config.category_keywords().items():
        if any(word in lowered for word in keywords):
            category = name
            break

    # --- specs -------------------------------------------------------------
    # Whole words only. A plain substring test finds "matte" inside "matters a
    # lot" and quietly adds a finish nobody asked for, which would then be
    # enforced as a hard constraint at stage 3.
    spec_words = config.category_spec_words(category) if category else config.all_spec_words()
    specs = [word for word in spec_words if re.search(rf"\b{re.escape(word)}\b", lowered)]
    dimensions = re.search(r"\d+\s*[x×]\s*\d+\s*[x×]\s*\d+\s*(?:mm|cm)?", lowered)
    if dimensions:
        specs.append(dimensions.group(0).replace(" ", ""))
        working = working.replace(dimensions.group(0), " ", 1)

    # --- quantity ----------------------------------------------------------
    # With price, delivery and dimensions removed, the first remaining number is
    # the quantity in every brief shaped like ours ("5,000 kraft mailer boxes").
    #
    # The trailing scale word is why this is not just `(\d[\d,]*)` any more.
    # "reorder 10k mailer boxes" used to parse as a quantity of TEN, which is the
    # worst class of bug we have: the agent goes on to price, rank, authorise and
    # buy ten boxes instead of ten thousand, and every screen looks healthy while
    # it does. The \b after the optional suffix is what keeps "5,000 kraft" at
    # 5,000 - the "k" of kraft is not followed by a boundary, so it backtracks
    # out rather than multiplying the order by a thousand.
    quantity: int | None = None
    quantity_match = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|lakh|lakhs|thousand|million)?\b", working
    )
    if quantity_match:
        magnitude = _SCALE_SUFFIXES.get(quantity_match.group(2) or "", 1)
        quantity = int(float(quantity_match.group(1).replace(",", "")) * magnitude)
    else:
        # No digits anywhere. "Two hundred chairs, delivered within 10 days" used
        # to be declined as NOT A PURCHASE REQUEST - the 10 belonged to the
        # delivery window, so once that was removed nothing numeric was left and
        # the scope gate concluded there was no order in the sentence. Refusing a
        # real brief outright is worse than misreading one, because there is no
        # screen for the user to correct.
        quantity = _word_number(working)

    # --- category, when no keyword matched -----------------------------------
    # Keep the USER'S OWN WORDS instead of leaving the field blank. Blank means
    # "you did not say what you want", so the scope gate asks what they are
    # buying - and for anything outside our vocabulary the answer can never
    # satisfy it, because the next parse comes back blank too. The user gets the
    # same question forever and never learns the real reason.
    #
    # With their words kept, this reaches stage 3, finds nothing, and the
    # no-coverage path says which categories we can actually source. That is the
    # answer they needed, and it matches what the Gemini path already did - the
    # two parsers should not disagree about whether cement is a thing you can
    # ask for.
    #
    # WHICH words are the user's own is the whole difficulty. This used to take
    # everything AFTER the quantity, because our own briefs are shaped "5,000
    # kraft mailer boxes" - noun after number. "Buy me latex gloves under 50
    # each" is shaped the other way round, so that rule walked straight past the
    # product and returned "each arriving in not more than" as the category.
    # We now try the tail, then the head, and cut both at the first word that
    # opens a constraint clause.
    if not category:
        tail = working[quantity_match.end():] if quantity_match else ""
        head = working[: quantity_match.start()] if quantity_match else working
        category = _noun_phrase(tail) or _noun_phrase(head)

    # --- stated priorities -------------------------------------------------
    # We look clause by clause, so "reliability matters a lot" attaches the
    # strength to reliability and not to a criterion mentioned elsewhere in the
    # sentence.
    #
    # The splitter no longer breaks on "-". It used to, which tore hyphenated
    # words in half mid-clause, so "non-negotiable" could never match its own
    # matters_a_lot phrase - the clause ended at the hyphen.
    #
    # First mention of a criterion wins, as before. Two clauses disagreeing about
    # the same criterion is genuinely ambiguous input and we would rather apply
    # one documented rule than invent a cleverer one nobody can predict.
    priorities: list[_StatedPriority] = []
    for clause in re.split(r"[.;,—]{1,2}|\band\b", lowered):
        for criterion, keywords in _PRIORITY_WORDS.items():
            if not any(word in clause for word in keywords):
                continue
            if criterion in {item.criterion for item in priorities}:
                continue
            label = _clause_strength(clause)
            if label is not None:
                priorities.append(_StatedPriority(criterion=criterion, phrase=label))

    return _Extraction(
        category=category,
        quantity=quantity,
        specs=specs,
        max_price_per_unit_inr=cap,
        max_delivery_days=days,
        delivery_is_open=delivery_is_open,
        stated_priorities=priorities,
    )
