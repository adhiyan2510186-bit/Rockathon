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
