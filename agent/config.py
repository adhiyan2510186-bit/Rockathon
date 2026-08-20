"""Stage-wide config reader — the single doorway between config.yaml and the code.

WHY THIS FILE EXISTS
--------------------
CLAUDE.md, "THE ONE RULE": authorisation limits live in a config file, not in a
prompt. That promise is only worth something if there is exactly one place the
limits enter the program. This is that place.

Every number that gates or scores a purchase — the Rs 1,05,000 authorisation
limit, the per-unit caps, the category weights, the 5-point substitution
threshold — is read here and nowhere else. If a judge asks "where does the
limit come from?", the honest answer is one function in one file, reading one
YAML file they can open and edit themselves.

THE RULE THIS FILE ENFORCES
---------------------------
Nothing returned by this module is ever placed into an LLM prompt. The language
model in agent/language.py receives the user's sentence and a JSON schema. It
never receives a limit, a weight, or a threshold, so no phrasing in a brief can
argue with a number it was never shown.

FAIL LOUDLY, EARLY
------------------
The config is validated the first time it is loaded — weights must sum to 1.0,
required keys must exist. A typo in config.yaml stops the app at startup with a
readable message, rather than silently producing a wrong ranking on stage in
front of judges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# config.yaml sits at the project root, one level up from this agent/ folder.
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# Loaded once and reused. Reading a small YAML file repeatedly would be harmless
# but pointless, and caching makes it obvious there is a single source of truth.
_cache: dict[str, Any] | None = None

# Bumped every time the config is re-read. Anything that caches a value DERIVED
# from config (agent/discovery.py caches normalised spec keys) takes this as part
# of its cache key, so a reload cannot leave a stale answer behind. Without it,
# the "tweak a weight live in front of judges" affordance in load() would be a
# quiet lie for any derived cache.
_generation: int = 0

# How close a set of weights must sum to 1.0 before we accept it. Floating point
# means 0.25 + 0.30 + 0.25 + 0.20 may land on 0.9999999999999999, which is fine.
_WEIGHT_SUM_TOLERANCE = 1e-6


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or internally inconsistent.

    Deliberately a loud crash rather than a quiet default. A wrong authorisation
    limit that nobody notices is far worse than an app that refuses to start.
    """


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------

def load(reload: bool = False) -> dict[str, Any]:
    """Read config.yaml (once), validate it, and return it as a plain dict.

    Pass reload=True to pick up edits without restarting — handy when we tweak a
    weight live in front of judges to show the ranking move.
    """
    global _cache, _generation
    if _cache is not None and not reload:
        return _cache

    if not CONFIG_PATH.exists():
        raise ConfigError(f"config.yaml not found at {CONFIG_PATH}")

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must contain a mapping of settings at the top level")

    _validate(raw)
    _cache = raw
    _generation += 1
    return _cache


def generation() -> int:
    """How many times the config has been loaded, for derived caches to key on."""
    load()
    return _generation


def _validate(raw: dict[str, Any]) -> None:
    """Check every assumption the rest of the code is about to make."""
    required = [
        "authorisation_limit_inr",
        "substitution_threshold_points",
        "categories",
        "priority_phrase_weights",
        "weight_rounding_step",
        "llm",
        "demo_failure_injection",
        "market_signal",
    ]
    missing = [key for key in required if key not in raw]
    if missing:
        raise ConfigError(f"config.yaml is missing required key(s): {', '.join(missing)}")

    if raw["authorisation_limit_inr"] <= 0:
        raise ConfigError("authorisation_limit_inr must be a positive number")

    if raw["substitution_threshold_points"] < 0:
        raise ConfigError("substitution_threshold_points cannot be negative")

    # Stage 4.5 thresholds. "Act now" has to be a tighter window than "order
    # soon", or every product falls into whichever band is checked first and the
    # distinction stops meaning anything on screen.
    signal = raw["market_signal"]
    for key in ("act_now_cover_days", "order_soon_cover_days", "material_price_move_pct"):
        if key not in signal:
            raise ConfigError(f"market_signal is missing '{key}'")
        if signal[key] <= 0:
            raise ConfigError(f"market_signal['{key}'] must be a positive number")
    if signal["act_now_cover_days"] >= signal["order_soon_cover_days"]:
        raise ConfigError(
            "market_signal: act_now_cover_days must be SMALLER than "
            "order_soon_cover_days - 'order today' is a tighter window than "
            "'order this week'."
        )

    _validate_categories(raw["categories"])

    # Stage 2 rescales around whichever criterion the user prioritised, so a
    # stated weight of 1.0 or more would leave nothing for the other criteria.
    for phrase, weight in raw["priority_phrase_weights"].items():
        if not 0 < weight < 1:
            raise ConfigError(
                f"priority_phrase_weights['{phrase}'] is {weight}; it must be between 0 and 1 "
                f"so the remaining criteria still have room to be rescaled."
            )

    # A step of 0 would divide by zero when rounding; a step above 0.25 would
    # flatten four criteria into two or three distinct values and stop the
    # weights meaning anything.
    step = raw["weight_rounding_step"]
    if not 0 < step <= 0.25:
        raise ConfigError(
            f"weight_rounding_step is {step}; it must be greater than 0 and at most 0.25."
        )


def _validate_categories(categories: dict[str, Any]) -> None:
    """Check every category block before any stage is allowed to read one.

    Category blocks are the one part of config.yaml a non-programmer is meant to
    edit — adding a category is meant to be data entry. So this is where a typo
    has to be caught: at startup, with a readable message, rather than as a
    silently mis-weighted ranking in front of judges.
    """
    if not isinstance(categories, dict) or not categories:
        raise ConfigError("config.yaml 'categories' must be a non-empty mapping")

    # Every lookup in this file falls back to `default`, so its absence would
    # turn any unrecognised category into a KeyError mid-run.
    if "default" not in categories:
        raise ConfigError("categories needs a 'default' entry as a fallback")

    required_keys = (
        "unit_noun",
        "keywords",
        "per_unit_cap_inr",
        "default_weights",
        "spec_words",
        "spec_synonyms",
    )

    for name, block in categories.items():
        if not isinstance(block, dict):
            raise ConfigError(f"categories['{name}'] must be a mapping of settings")

        for key in required_keys:
            if key not in block:
                raise ConfigError(f"categories['{name}'] is missing '{key}'")

        if block["per_unit_cap_inr"] <= 0:
            raise ConfigError(
                f"categories['{name}']['per_unit_cap_inr'] must be a positive number"
            )

        # A set that sums to 0.9 would silently shrink every score in that
        # category and quietly break the golden numbers.
        weights = block["default_weights"]
        total = sum(weights.values())
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ConfigError(
                f"categories['{name}']['default_weights'] sums to {total}, not 1.0. "
                f"Weights are proportions of one decision; they must total exactly 1."
            )

    # A keyword may belong to exactly one category. _normalise_category takes the
    # FIRST match, so a word listed under two categories would route a brief by
    # dictionary order — invisible on screen, and wrong in a way nobody would
    # think to check. "workstation" is the live example: it is a desk and it is a
    # laptop, so it belongs to neither.
    seen: dict[str, str] = {}
    for name, block in categories.items():
        for keyword in block["keywords"]:
            word = str(keyword).strip().lower()
            if word in seen:
                raise ConfigError(
                    f"keyword '{word}' is listed under both '{seen[word]}' and "
                    f"'{name}'. A keyword decides which category's price cap and "
                    f"weights apply, so it must belong to exactly one."
                )
            seen[word] = name


# ---------------------------------------------------------------------------
# Categories — the one lookup every category-aware setting goes through
# ---------------------------------------------------------------------------

def _category(name: str) -> dict[str, Any]:
    """The config block for a category, or the `default` block if we have none.

    Every category-aware setting in this file goes through here, so "what happens
    for a category we have never heard of?" has exactly one answer instead of one
    answer per setting. An unknown category is not an error — the agent still has
    to be able to read a cement brief far enough to say it cannot source cement.
    """
    categories = load()["categories"]
    return categories.get(name, categories["default"])


def category_keywords() -> dict[str, tuple[str, ...]]:
    """Every category's trigger words, for stage 1's category normaliser.

    The `default` block is excluded: its keyword list is empty by definition, and
    a category named "default" is not something a user can ask for. Returned in
    config order, which is the order the first-match-wins loop will use — the
    startup collision check is what makes that order safe to rely on.
    """
    return {
        name: tuple(block["keywords"])
        for name, block in load()["categories"].items()
        if name != "default" and block["keywords"]
    }


def category_spec_words(category: str) -> tuple[str, ...]:
    """Physical-requirement vocabulary the offline parser looks for, per category.

    Per-category rather than one global list on purpose. "recycled" is a real
    packaging spec and noise in a chair brief; pulling it out of a furniture
    sentence would invent a hard constraint the user never stated and empty the
    pool for no visible reason.
    """
    return tuple(_category(category)["spec_words"])


def all_spec_words() -> tuple[str, ...]:
    """Every category's spec vocabulary at once, for when the category is unknown.

    The offline parser identifies the category before it extracts specs, so this
    is only reached when nothing matched — a brief we cannot place. Casting a
    wide net there is the right trade: a spurious spec surfaces as a near-miss,
    a missed spec surfaces as a wrong purchase.
    """
    words: list[str] = []
    for block in load()["categories"].values():
        for word in block["spec_words"]:
            if word not in words:
                words.append(word)
    return tuple(words)


def spec_synonyms() -> dict[str, str]:
    """Trade shorthand from every category, merged into one lookup for stage 3.

    Merged rather than per-category because the gate compares a brief's specs
    against a product's specs and both sides are already known to be in the same
    category — the category filter ran first. One flat table keeps spec_key()
    a cache-friendly single lookup.
    """
    merged: dict[str, str] = {}
    for block in load()["categories"].values():
        merged.update(block["spec_synonyms"] or {})
    return merged


def unit_noun(category: str) -> str:
    """What one item is called on screen — "boxes", "chairs", "laptops".

    Display only. Nothing downstream branches on this; it exists so a chip reads
    "12 chairs" instead of "12 units", which is the difference between a product
    and a form.
    """
    return str(_category(category)["unit_noun"])


# ---------------------------------------------------------------------------
# Stage 5 — authorisation
# ---------------------------------------------------------------------------

def authorisation_limit_inr() -> float:
    """Total order value the agent may commit WITHOUT a human (Rs 1,05,000).

    Stage 5 compares quantity x unit price against this. Exceeding it escalates;
    it never rejects and never silently proceeds. This is the boundary the whole
    project exists to demonstrate.
    """
    return float(load()["authorisation_limit_inr"])


def substitution_threshold_points() -> float:
    """Score gap above which the agent refuses to silently swap #1 for #2 (5 points).

    If the winner becomes unavailable and #2 trails by more than this, #2 is a
    meaningfully worse fit for what the user asked for, so a human decides.
    In our demo the gap is 9.3 points, so the agent escalates rather than swaps.
    """
    return float(load()["substitution_threshold_points"])


# ---------------------------------------------------------------------------
# Stage 4.5 — market signal thresholds
# ---------------------------------------------------------------------------
# Advisory numbers only. Nothing read through these three functions may change
# eligibility, score, ranking or the authorisation decision — see CLAUDE.md,
# "urgency changes priority, never authority".

def act_now_cover_days() -> float:
    """At or below this many days of stock cover, we say "order today" (3)."""
    return float(load()["market_signal"]["act_now_cover_days"])


def order_soon_cover_days() -> float:
    """At or below this many days of cover, we say "order this week" (7)."""
    return float(load()["market_signal"]["order_soon_cover_days"])


def material_price_move_pct() -> float:
    """How far a price must move across the window before we call it a trend (3%).

    Below this we say nothing about direction. A signal that fires on noise
    trains a user to ignore it, which is worse than having no signal at all.
    """
    return float(load()["market_signal"]["material_price_move_pct"])


# ---------------------------------------------------------------------------
# Stage 3 — hard constraint defaults
# ---------------------------------------------------------------------------

def per_unit_cap_default_inr(category: str) -> float:
    """Fallback per-unit price ceiling for a category, used only if the brief omits one.

    When this default is used instead of a stated cap, stage 1 logs the field as
    ASSUMED rather than CONFIRMED — the audit trail must never present our guess
    as the user's instruction.
    """
    return float(_category(category)["per_unit_cap_inr"])


# ---------------------------------------------------------------------------
# Stage 2 — weights
# ---------------------------------------------------------------------------

def category_default_weights(category: str) -> dict[str, float]:
    """The starting weights for a category when the user states no priority.

    Returned as a copy so a caller cannot accidentally mutate the loaded config
    and change the behaviour of every later stage in the same run.
    """
    return dict(_category(category)["default_weights"])


def priority_phrase_weight(phrase: str) -> float | None:
    """Turn a phrase the LLM identified ("matters_a_lot") into a number (0.45).

    This function is the hinge of the whole architecture. The language model
    reads "reliability matters a lot" and returns the LABEL matters_a_lot. It
    does not return 0.45 and is never shown 0.45. Python performs the lookup.
    That is the difference between a model interpreting language and a model
    deciding a purchase.

    Returns None for an unrecognised phrase so the caller can fall back to the
    category defaults rather than crash mid-demo.
    """
    return load()["priority_phrase_weights"].get(phrase)


def priority_phrase_labels() -> list[str]:
    """Just the phrase NAMES ('matters_a_lot', 'matters', 'nice_to_have') — never the numbers.

    This is the one thing from config.yaml that agent/language.py is allowed to
    put in front of the model, and it is deliberately the keys only. The model is
    told which labels exist so it can pick one; it is never told that
    matters_a_lot means 0.45. Returning keys from here rather than letting
    language.py read the raw config keeps that promise checkable in one place.
    """
    return sorted(load()["priority_phrase_weights"].keys())


def weight_rounding_step() -> float:
    """The step every rescaled weight is rounded to (0.05).

    Not cosmetic. Rescaling the packaging defaults around a stated reliability of
    0.45 gives price 0.2200, replacement 0.1833, delivery 0.1467; rounding those
    to 0.05 gives 0.20 / 0.20 / 0.15, which is the set in our deck and the set
    the golden ranking test asserts. Change this number and that test goes red,
    which is exactly what it is there for.
    """
    return float(load()["weight_rounding_step"])


# ---------------------------------------------------------------------------
# Stages 0-2 — language step settings
# ---------------------------------------------------------------------------

def llm_model() -> str:
    """Which Gemini model the language step calls (gemini-3.6-flash)."""
    return str(load()["llm"]["model"])


def llm_provider() -> str:
    """Which provider the language step uses. Swapping this touches one file."""
    return str(load()["llm"]["provider"])


def allow_offline_fallback() -> bool:
    """Whether language.py may drop to the offline parser if Gemini is unavailable."""
    return bool(load()["llm"]["allow_offline_fallback"])


# ---------------------------------------------------------------------------
# Stages 6-7 — mock service switches
# ---------------------------------------------------------------------------

def failure_injection() -> dict[str, bool]:
    """The pretend-failure switches for vendor confirmation and payment.

    Returned as a copy; the Streamlit sidebar overrides these per run so a judge
    can trigger the escalation path on demand.
    """
    return dict(load()["demo_failure_injection"])
