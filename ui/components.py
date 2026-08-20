"""The reusable pieces every screen is built from.

WHY THIS FILE EXISTS
--------------------
app.py used to build its own headings, captions and layouts inline, screen by
screen. That is how four screens end up looking like four products - and how
"just one more caption" keeps getting added until the interface is explaining
itself instead of doing its job.

Everything visual is now one of the pieces below. If a screen wants to say
something, it says it through a hero, a figure, a chip or a table.

THE PRODUCT BAR, ENFORCED HERE
------------------------------
CLAUDE.md bans stage numbers, implementation commentary and self-justifying
captions from the default surface. None of these components has a slot for that
kind of text. The explanation lives in a `detail()` block, which is collapsed
until someone asks - that is the progressive disclosure rule made physical.

Meena is an ops manager. She has never heard of "stage 4" and must never see the
words. If a sentence is aimed at a judge rather than a buyer, it belongs in a
drill-down, a docstring, or presentation.txt.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from html import escape

import streamlit as st

from agent.models import ScoredProduct
from ui import theme


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def rule() -> None:
    """A quiet horizontal divider. Structure without a heading."""
    st.markdown('<hr class="rule">', unsafe_allow_html=True)


def section(title: str) -> None:
    """A small section heading. Sentence case - a product, not a report."""
    st.markdown(f"#### {escape(title)}")


def provenance(text: str) -> None:
    """The 'simulated market data' line that sits under every history chart.

    Small and italic, but always present. CLAUDE.md: we do not draw a convincing
    chart and let a reader assume it came from somewhere real.
    """
    st.markdown(f'<div class="provenance">{escape(text)}</div>', unsafe_allow_html=True)


@contextmanager
def detail(label: str):
    """A collapsed drill-down. The ONLY place implementation detail may appear.

    Everything a judge wants to see and a buyer does not - score arithmetic,
    rejection reasons, normalisation methods, the raw audit payload - goes inside
    one of these. Collapsed by default, on purpose: we open it deliberately during
    the demo, which lands far better than a wall of text nobody was asked to read.
    """
    with st.expander(label):
        yield


# ---------------------------------------------------------------------------
# Hero — the recommendation, the escalation, the receipt
# ---------------------------------------------------------------------------

def hero(eyebrow: str, title: str, sub: str = "", variant: str = "") -> None:
    """The one thing on the screen that should be read first.

    `variant` shifts only the accent edge: "" for a recommendation, "escalated"
    when a human is being asked, "done" when the order is closed. Deliberately
    not a red alarm banner for escalations - being asked to approve a large
    order is the system working, not a fault, and colouring it like an error
    teaches the user to dread the exact moment we are proud of.
    """
    classes = "hero" + (f" hero-{variant}" if variant else "")
    sub_html = f'<div class="hero-sub">{escape(sub)}</div>' if sub else ""
    st.markdown(
        f'<div class="{classes}">'
        f'<div class="hero-eyebrow">{escape(eyebrow)}</div>'
        f'<div class="hero-title">{escape(title)}</div>'
        f"{sub_html}</div>",
        unsafe_allow_html=True,
    )


def figures(items: Sequence[tuple[str, str, str]]) -> None:
    """A row of number-first tiles: (label, value, note).

    Numbers before words. A buyer scanning this row should get the cost, the
    timing and the decision without reading a sentence.
    """
    for column, (label, value, note) in zip(st.columns(len(items)), items):
        with column:
            note_html = f'<div class="figure-note">{escape(note)}</div>' if note else ""
            st.markdown(
                f'<div class="figure">'
                f'<div class="figure-label">{escape(label)}</div>'
                f'<div class="figure-value">{escape(value)}</div>'
                f"{note_html}</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Chips — short, scannable facts
# ---------------------------------------------------------------------------

def chips(items: Iterable[tuple[str, str | None]], strong_first: bool = False) -> None:
    """A row of chips. Each item is (text, dot colour or None).

    Chips exist so a fact can be scanned rather than read. "3 days of cover left"
    is a chip; a paragraph explaining stock depletion is not - that is what the
    drill-down is for.

    A chip that carries a colour gets a faint wash of it and a matching border,
    so a signal is visible at a glance across the page. The TEXT stays in an ink
    token either way: a coloured dot beside the words carries the identity, and
    the words themselves never have to fight a tinted background for contrast.
    """
    palette = theme.active()
    parts = []

    for index, (text, colour) in enumerate(items):
        if colour:
            dot = f'<span class="chip-dot" style="background:{colour}"></span>'
            style = (f' style="background:{palette.tint(colour, 0.12)};'
                     f'border-color:{palette.tint(colour, 0.45)}"')
        else:
            dot, style = "", ""
        emphasis = " chip-strong" if strong_first and index == 0 else ""
        parts.append(f'<span class="chip{emphasis}"{style}>{dot}{escape(text)}</span>')

    if parts:
        st.markdown(f'<div class="chip-row">{"".join(parts)}</div>', unsafe_allow_html=True)


def urgency_chips(signal) -> None:
    """The timing read for one product: the verdict chip first, then its evidence.

    The verdict always carries a coloured dot AND a word. Two of our status
    colours are low-contrast on a light surface by design, so colour is never
    allowed to carry the meaning on its own.
    """
    if signal is None:
        return

    colour, icon, label = theme.urgency(signal.urgency.value)
    items: list[tuple[str, str | None]] = [(f"{icon} {label}", colour)]
    items += [(chip, None) for chip in signal.chips]
    chips(items, strong_first=True)


# ---------------------------------------------------------------------------
# The comparison table
# ---------------------------------------------------------------------------

def comparison_table(ranked: Sequence[ScoredProduct], market=None) -> None:
    """What qualified, in order, with the figures a buyer compares on.

    A table, not a chart, because this IS a list of values - the reader wants to
    look one up, not see a shape. The shape question ("why did this win?") is the
    stacked bar sitting above it.

    It also discharges the accessibility obligation on our palette: two of the
    four chart colours are low-contrast on light, and the documented mitigation is
    a table view or visible labels. We ship both.
    """
    import pandas as pd

    rows = []
    for scored in ranked:
        product = scored.product
        signal = market.for_product(scored) if market else None
        _colour, icon, label = theme.urgency(signal.urgency.value) if signal else ("", "", "")

        rows.append({
            "Product": product.name,
            "Vendor": product.source,
            "₹/unit": product.price_per_unit_inr,
            "Delivery": product.delivery_days,
            "Rating": product.reliability_rating,
            "Replacement": product.replacement_window_days,
            "Match": scored.score,
            "Timing": f"{icon} {label}" if label else "—",
        })

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "₹/unit": st.column_config.NumberColumn(format="₹%.2f"),
            "Delivery": st.column_config.NumberColumn(format="%d days"),
            "Rating": st.column_config.NumberColumn(format="%.1f ★"),
            "Replacement": st.column_config.NumberColumn(format="%d days"),
            # Progress column so the score gap is visible without reading digits.
            # In the demo the winner leads by 9.3 points and the eye should catch
            # that before the number does.
            "Match": st.column_config.ProgressColumn(
                format="%.1f", min_value=0, max_value=100, help="Fit against your brief"
            ),
        },
    )


def score_breakdown(scored: ScoredProduct) -> None:
    """Every term of one product's score, so nothing has to be taken on trust.

    Lives inside a drill-down. This is the arithmetic from the deck - weight x
    normalised = contribution, four terms summing to the score - and a judge who
    opens it can check the total by hand.
    """
    import pandas as pd

    st.dataframe(
        pd.DataFrame([
            {
                "Criterion": term.criterion,
                "Your weight": term.weight,
                "Product's figure": term.raw_value,
                "Scaled 0-1": term.normalised,
                "Points": term.contribution * 100,
                "How it was scaled": term.method,
            }
            for term in scored.terms
        ]),
        hide_index=True,
        width="stretch",
        column_config={
            "Your weight": st.column_config.NumberColumn(format="%.2f"),
            "Scaled 0-1": st.column_config.NumberColumn(format="%.3f"),
            "Points": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.caption(f"Total: {scored.score} out of 100")
