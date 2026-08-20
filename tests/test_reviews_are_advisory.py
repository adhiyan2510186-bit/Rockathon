"""The guardrail on the newest temptation in the catalog: buyer reviews.

WHY THIS FILE EXISTS
--------------------
The marketplace feeds carry thousands of words of opinion per listing, and there
is an obvious, demo-friendly thing to do with them: hand the review text to the
language model, ask "how good is this really?", and fold the answer into the
score. It would look impressive for about ten seconds, and it would break the one
rule this project is built on.

    The LLM interprets language. It never decides the purchase.

Review text is unstructured, adversarial (sellers write some of it) and
unreproducible — three properties that would poison a number a finance manager
has to defend in a meeting. The star RATING is different and IS scored, because
it is already a number and every source publishes it the same way. The sentences
next to it are for the human.

This file is the same shape of guarantee as the Stage 4.5 guardrail in
tests/test_signals.py: an advisory input is only advisory if something fails when
it stops being.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent import discovery, language, ranking, weights
from agent.models import Product, ReviewSnippet

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"

# The two files that are ALLOWED to name the review fields, and why:
#   models.py   defines them
#   sources.py  fills them in from the feeds
# Anything else naming them is a decision path that has learned to read reviews.
REVIEW_FIELDS = ("sample_reviews", "review_count")
ALLOWED_TO_NAME_REVIEWS = {"models.py", "sources.py"}

HEADPHONE_BRIEF = (
    "25 wireless noise-cancelling headsets, over-ear, max Rs 4,000 each, "
    "delivered within 12 days. Reliability matters a lot."
)


def build_pool():
    """The headphones pool, which is the one with real reviews attached."""
    extraction = language._offline_extract(HEADPHONE_BRIEF)
    brief = language._to_brief(HEADPHONE_BRIEF, extraction, audit=None)
    computed = weights.compute(brief)
    results = discovery.run(brief)
    eligible = [result.product for result in results if result.passed]
    return brief, computed, eligible


# ---------------------------------------------------------------------------
# Removing every review must not move a single number
# ---------------------------------------------------------------------------

def test_stripping_all_reviews_leaves_the_ranking_identical():
    """Delete every review from every product; the scores must not move at all."""
    brief, computed, eligible = build_pool()

    with_reviews = ranking.rank(
        eligible, computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )
    stripped = [
        product.model_copy(update={"sample_reviews": (), "review_count": 0})
        for product in eligible
    ]
    without_reviews = ranking.rank(
        stripped, computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )

    assert [(item.product.product_id, item.score, item.rank) for item in with_reviews] == [
        (item.product.product_id, item.score, item.rank) for item in without_reviews
    ]


def test_the_pool_being_tested_actually_has_reviews_to_strip():
    """Otherwise the test above passes by comparing nothing with nothing."""
    _, _, eligible = build_pool()

    assert any(product.sample_reviews for product in eligible)
    assert any(product.review_count > 0 for product in eligible)


def test_replacing_glowing_reviews_with_furious_ones_changes_nothing():
    """The sharper version: not absence, but the opposite sentiment.

    The winner is rewritten to carry three one-star reviews calling it a fraud,
    and it still wins with the same score, because nothing in the decision path
    can read them. If this ever fails, someone has wired sentiment into a number.
    """
    brief, computed, eligible = build_pool()
    before = ranking.rank(
        eligible, computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )

    furious = tuple(
        ReviewSnippet(stars=1.0, text=text)
        for text in (
            "Absolute fraud, arrived broken and the seller vanished.",
            "Worst purchase our company has ever made. Avoid.",
            "Do not buy this under any circumstances.",
        )
    )
    poisoned = [
        product.model_copy(update={"sample_reviews": furious})
        if product.product_id == before[0].product.product_id
        else product
        for product in eligible
    ]
    after = ranking.rank(
        poisoned, computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )

    assert after[0].product.product_id == before[0].product.product_id
    assert after[0].score == before[0].score


def test_a_product_with_no_reviews_is_not_penalised_for_it():
    """Our B2B vendors publish none, and silence must not read as a bad signal.

    A scoring rule that quietly rewarded review VOLUME would hand every
    marketplace listing an advantage over every contract vendor, for a reason
    that has nothing to do with the purchase.
    """
    brief, computed, eligible = build_pool()

    loud = eligible[0].model_copy(update={"review_count": 90000})
    silent = eligible[0].model_copy(update={"review_count": 0, "sample_reviews": ()})

    rest = eligible[1:]
    loud_score = ranking.rank(
        [loud, *rest], computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )
    silent_score = ranking.rank(
        [silent, *rest], computed, brief.max_price_per_unit_inr, brief.max_delivery_days
    )

    assert loud_score[0].score == silent_score[0].score


# ---------------------------------------------------------------------------
# No decision module is even allowed to mention them
# ---------------------------------------------------------------------------

def live_strings_and_attributes(path: Path) -> set[str]:
    """Every name the module actually evaluates — attribute access and literals.

    Docstrings and comments are excluded on purpose. Explaining in prose why we
    do NOT score reviews is exactly what these files should be doing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and getattr(node, "body", None)
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.add(node.value)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


DECISION_MODULES = sorted(
    path for path in AGENT_DIR.glob("*.py") if path.name not in ALLOWED_TO_NAME_REVIEWS
)


@pytest.mark.parametrize("path", DECISION_MODULES, ids=lambda p: p.name)
def test_no_decision_module_reads_a_review(path):
    """ranking, discovery, signals, authorisation, escalation, audit — none of them.

    A static check rather than a behavioural one, because the behavioural tests
    above can only catch a review that changes an answer TODAY. This catches the
    line that reads one at all, which is where the change would start.
    """
    names = live_strings_and_attributes(path)
    offenders = sorted(field for field in REVIEW_FIELDS if field in names)

    assert not offenders, (
        f"{path.name} reads {', '.join(offenders)}. Review text is advisory: it is "
        f"shown to the human and read by nothing that decides anything. If this "
        f"module genuinely needs buyer feedback, the answer is a number every "
        f"source publishes the same way, not a sentence."
    )


def test_the_exemptions_really_do_only_define_and_fill():
    """Guard against the allowance outliving the reason for it."""
    models = (AGENT_DIR / "models.py").read_text(encoding="utf-8")
    sources = (AGENT_DIR / "sources.py").read_text(encoding="utf-8")

    assert "class ReviewSnippet" in models          # defines the shape
    assert "sample_reviews=tuple(" in sources       # fills it from a feed
    assert "sample_reviews" in Product.model_fields
