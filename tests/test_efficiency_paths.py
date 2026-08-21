"""EFFICIENCY.md promises we can open the code the moment a judge asks.

That promise is only as good as the line numbers, and line numbers rot on every
edit above them. Twelve of the sixteen references in that file had drifted -
`buyer_reviews()` was cited 115 lines from where it lives, and several pointed
at blank lines. Nobody noticed, because nothing checked.

This is the cheapest possible guard: every `path.py:N` in the file must exist and
must point at a line of actual code. It cannot tell you the reference is
CORRECT - only that it is not obviously rotten. That is enough to catch the
failure we actually had.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = re.compile(r"`?((?:agent|ui|tests)/[a-z_]+\.py|app\.py):(\d+)`?")


def references() -> list[tuple[str, int]]:
    text = (ROOT / "EFFICIENCY.md").read_text(encoding="utf-8")
    return [(match.group(1), int(match.group(2))) for match in REFERENCE.finditer(text)]


def test_the_file_actually_cites_code():
    """A guard that matched nothing would make the test below vacuous."""
    assert len(references()) >= 10


@pytest.mark.parametrize("path, line_number", references())
def test_every_citation_points_at_real_code(path: str, line_number: int):
    target = ROOT / path
    assert target.exists(), f"EFFICIENCY.md cites {path}, which does not exist"

    lines = target.read_text(encoding="utf-8").split("\n")
    assert line_number <= len(lines), (
        f"EFFICIENCY.md cites {path}:{line_number}, but the file has "
        f"{len(lines)} lines"
    )

    line = lines[line_number - 1].strip()
    assert line and not line.startswith("#"), (
        f"EFFICIENCY.md cites {path}:{line_number}, which is "
        f"{'blank' if not line else 'a comment'}. The reference has drifted - "
        f"find where the thing it names actually lives and update the number."
    )


def _string_literal_lines(path: Path) -> set[int]:
    """Every line occupied by a string literal - docstrings included.

    Uses the parsed tree rather than a regex because a docstring is not
    lexically distinguishable from any other expression statement, and the
    thing we care about spans many lines.
    """
    import ast

    lines: set[int] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


@pytest.mark.parametrize("path, line_number", references())
def test_no_citation_has_slid_into_a_docstring(path: str, line_number: int):
    """A reference that has drifted usually lands in prose, not on nothing.

    The test above only asks whether the cited line is blank or a comment, and
    that turned out to be far too weak. Two references drifted during the
    interface work and BOTH still passed it: one had slid off `_language_switch`
    into the middle of that function's own docstring, the other onto a lone
    closing `\"\"\"` 160 lines from the function it named. Both are "a line of
    real code" as far as the old check is concerned.

    They are not, though, and the tell is the same in both cases - EFFICIENCY.md
    cites things a judge should be able to OPEN: a def, a call, a branch. It
    never cites a sentence. So a citation landing inside a string literal is
    always drift, and this catches the exact failure we actually had rather than
    the one we imagined.
    """
    target = ROOT / path
    assert line_number not in _string_literal_lines(target), (
        f"EFFICIENCY.md cites {path}:{line_number}, which is inside a docstring "
        f"or string literal. References name code a judge can open, never prose - "
        f"so this one has drifted off whatever it used to point at. Find where "
        f"that thing lives now and update the number."
    )
