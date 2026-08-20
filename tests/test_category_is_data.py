"""The test that holds down the claim "a category is data, not code".

We say out loud that adding furniture cost a config block and some catalog rows,
and that no file in agent/ knows what it is buying. That is the kind of claim
that is true on the day it is made and quietly false three commits later, when
someone fixes a bug with `if category == "laptops"` because it was the fastest
way to make a screen look right.

So the claim is a test. It reads every module in agent/, strips the comments and
docstrings - where naming a category is fine, and useful - and fails if a
category name survives as a string the code actually evaluates.

agent/config.py is the one exemption, and deliberately so: it is the file whose
whole job is reading the category blocks. One doorway, same as the authorisation
limit. If this test ever goes red, the fix is not to add another exemption.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent import config

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"

# The reader of the category blocks is allowed to name them; nothing else is.
EXEMPT = {"config.py"}


def category_names() -> set[str]:
    """Every category in config.yaml except `default`.

    `default` is excluded because it is an ordinary English word that appears in
    plenty of honest strings, and because it is a fallback rather than a kind of
    purchase - no user ever asks to buy one.
    """
    return {name for name in config.load()["categories"] if name != "default"}


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The id() of every docstring constant, so we can ignore them.

    Comments never reach the AST at all, and docstrings are where we explain
    ourselves to judges - "PackHub and BoxBazaar sell packaging" is exactly the
    sentence a reader needs. Prose is not a branch.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def live_strings(path: Path) -> list[tuple[int, str]]:
    """Every string literal the module actually evaluates, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


AGENT_MODULES = sorted(
    path for path in AGENT_DIR.glob("*.py") if path.name not in EXEMPT
)


def test_there_are_modules_to_check():
    """A glob that silently matches nothing would make every test below vacuous."""
    assert len(AGENT_MODULES) >= 10


@pytest.mark.parametrize("path", AGENT_MODULES, ids=lambda p: p.name)
def test_no_module_names_a_category(path):
    """No file in agent/ contains a category name as a live string literal."""
    names = category_names()
    offenders = [
        f"{path.name}:{line} -> {text!r}"
        for line, text in live_strings(path)
        if text.strip().lower() in names
    ]

    assert not offenders, (
        "a category name is hardcoded in agent/, which means adding the next "
        "category will need a code change rather than a config block:\n  "
        + "\n  ".join(offenders)
    )


def test_the_exempt_file_really_does_read_the_categories():
    """Guard against the exemption outliving the reason for it.

    If config.py stopped being the file that reads category blocks, EXEMPT would
    be hiding a hardcode rather than describing a design.
    """
    source = (AGENT_DIR / "config.py").read_text(encoding="utf-8")

    assert '"categories"' in source
    assert "def _category(" in source


def test_every_stocked_category_has_a_config_block():
    """A vendor may not stock something the config has no opinion about.

    The reverse is allowed and is true today - `labels` has a block and no
    stock. That direction is honest: we hold a default we never got to use.
    This direction is not, because it would silently apply the `default` weights
    and price cap to real products and nothing on screen would say so.
    """
    from agent import discovery

    blocks = set(config.load()["categories"])
    for category in discovery.available_categories():
        assert category in blocks, (
            f"vendors stock '{category}' but config.yaml has no block for it, so it "
            f"would silently inherit the default weights and price cap"
        )
