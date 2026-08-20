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
SCOPE_STATEMENT = (
    "I handle procurement briefs only: what to buy, how many, your price ceiling "
    "and your delivery window. I can't help with that one, but send me a buying "
    "brief and I'll take it from there."
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

def check_scope(text: str, audit: AuditLogger | None = None) -> ScopeOutcome:
    """Decide whether to start work on this message at all.

    Three outcomes and no others: refuse politely (not procurement), ask ONE
    question (a real brief with a hole in it), or proceed. CLAUDE.md is blunt
    about the middle one: no discovery starts on a guess.

    The one question is logged as a DECISION, not an ESCALATION. Escalation is
    reserved for the three stage-5 triggers — if a routine clarifying question
    counted as an escalation, the boundary we are demonstrating would blur on the
    very first screen.
    """
    check, source, note = _read_scope(text)

    if check.verdict == "out_of_scope":
        verdict = ScopeVerdict(verdict="out_of_scope", message=SCOPE_STATEMENT, missing_fields=[])
        if audit:
            audit.decision(
                STAGE_SCOPE,
                "Message is not a procurement brief, so no discovery was started.",
                {"user_message": text[:200], "action": "declined, scope stated"},
            )
    elif check.verdict == "incomplete":
        missing = [field for field in check.missing_fields if field in REQUIRED_FOR_DISCOVERY]
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


def _default_question(missing: list[str]) -> str:
    """Turn missing field names into ONE sentence a human would actually ask.

    One question, never a form. Asking three separate things is how a chat
    assistant turns into paperwork.
    """
    asks = {
        "category": "what you're buying",
        "quantity": "how many units you need",
        "max_delivery_days": "your delivery deadline in days",
        "max_price_per_unit_inr": "your per-unit price ceiling",
    }
    wanted = [asks[field] for field in missing if field in asks] or ["a bit more detail"]
    if len(wanted) == 1:
        return f"Before I start looking - {wanted[0]}?"
    return f"Before I start looking - {', '.join(wanted[:-1])} and {wanted[-1]}?"


# ---------------------------------------------------------------------------
# Stage 1 — requirement extraction (and the labels stage 2 needs)
# ---------------------------------------------------------------------------

def extract_brief(text: str, audit: AuditLogger | None = None) -> BriefOutcome:
    """Turn the sentence into a Brief. The handover from English to arithmetic.

    Everything before this line is interpretation of language. Everything after
    it is plain Python. Once this function returns, the model has no further say
    in the run — it is not consulted again at any stage.
    """
    extraction, source, note = _read_extraction(text)
    brief = _to_brief(text, extraction, audit)

    if audit:
        audit.decision(
            STAGE_EXTRACTION,
            f"Brief parsed into structured requirements using the {source} parser.",
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
    known = set(config.priority_phrase_labels())
    priorities = {
        item.criterion: item.phrase
        for item in extraction.stated_priorities
        if item.phrase in known
    }

    return Brief(
        raw_text=raw_text,
        category=category,
        quantity=extraction.quantity or 0,
        specs=extraction.specs,
        max_price_per_unit_inr=cap,
        max_delivery_days=extraction.max_delivery_days or 0,
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
    "bought, or how many, or by when.\n"
    "- in_scope: it says what, how many, and a delivery deadline.\n\n"
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
        "it. 'Reliability matters a lot' is a stated priority; simply mentioning a "
        "price ceiling is not.\n\n"
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

def _read_scope(text: str) -> tuple[_ScopeCheck, Literal["gemini", "offline"], str]:
    """Stage 0's reading of the sentence, via Gemini if we can, offline if we must."""
    if is_online():
        try:
            check = _call_gemini(_build_prompt(text, _SCOPE_TASK), _ScopeCheck)
            return check, "gemini", f"read by {config.llm_model()}"
        except Exception as exc:  # noqa: BLE001 — any SDK failure means the same thing to us
            if not config.allow_offline_fallback():
                raise
            return _offline_scope(text), "offline", _offline_note(exc)
    return _offline_scope(text), "offline", "offline mode - no API key, no AI used"


def _read_extraction(text: str) -> tuple[_Extraction, Literal["gemini", "offline"], str]:
    """Stage 1's reading of the sentence, same two paths."""
    if is_online():
        try:
            extraction = _call_gemini(_build_prompt(text, _extraction_task()), _Extraction)
            return extraction, "gemini", f"read by {config.llm_model()}"
        except Exception as exc:  # noqa: BLE001
            if not config.allow_offline_fallback():
                raise
            return _offline_extract(text), "offline", _offline_note(exc)
    return _offline_extract(text), "offline", "offline mode - no API key, no AI used"


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
_PHRASE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("matters_a_lot", ("matters a lot", "really matters", "very important", "critical",
                       "top priority", "non-negotiable", "matters most", "hugely")),
    ("matters", ("matters", "important", "care about", "priority", "prefer")),
    ("nice_to_have", ("nice to have", "would be nice", "bonus", "if possible", "ideally")),
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
    if not extraction.max_delivery_days:
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
    days: int | None = None
    days_match = re.search(r"(\d+)\s*(?:working |business |calendar )?days?", working)
    if days_match:
        days = int(days_match.group(1))
        working = working.replace(days_match.group(0), " ", 1)

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
    quantity: int | None = None
    quantity_match = re.search(r"(\d[\d,]*)", working)
    if quantity_match:
        quantity = int(quantity_match.group(1).replace(",", ""))

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
    priorities: list[_StatedPriority] = []
    for clause in re.split(r"[.;,—\-]{1,2}|\band\b", lowered):
        for criterion, keywords in _PRIORITY_WORDS.items():
            if any(word in clause for word in keywords):
                for label, phrases in _PHRASE_PATTERNS:
                    if any(phrase in clause for phrase in phrases):
                        if criterion not in {item.criterion for item in priorities}:
                            priorities.append(_StatedPriority(criterion=criterion, phrase=label))
                        break

    return _Extraction(
        category=category,
        quantity=quantity,
        specs=specs,
        max_price_per_unit_inr=cap,
        max_delivery_days=days,
        stated_priorities=priorities,
    )
