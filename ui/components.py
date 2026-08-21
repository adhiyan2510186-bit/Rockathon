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


def status_html(kind: str, text: str = "", live: bool = False) -> str:
    """One status tag as HTML, for callers that need to place it inside a row.

    Split from `status_pill` below because a tag is wanted in two shapes: on its
    own, and sitting beside a supplier badge or a heading. Returning the string
    is what lets the second case exist without every caller writing its own span
    and inventing its own padding.

    `live` turns on the pulse, and it is the caller's job to only ask for it
    while something is genuinely still waiting on a person. See ui/styles.py.
    """
    colour, mark, word = theme.status(kind)
    palette = theme.active()
    classes = "status status-live" if live else "status"
    return (
        f'<span class="{classes}" style="--status-colour:{colour};'
        f'--status-wash:{palette.tint(colour, 0.14)}">'
        f'<span class="status-mark"></span>'
        f'<span class="status-word">{escape(text or word)}</span></span>'
    )


def status_pill(kind: str, text: str = "", live: bool = False) -> None:
    """Where this got to, as one tag: qualified, waiting, ordered, stopped.

    The word overrides the default when a screen has something more specific to
    say ("Declined" rather than "Stopped"), but the colour and the mark stay
    tied to the kind - so a caller can change the sentence and cannot
    accidentally make a stopped order green.
    """
    st.markdown(
        f'<div class="chip-row">{status_html(kind, text, live)}</div>',
        unsafe_allow_html=True,
    )


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
# The trail — what happened, in the order it happened
# ---------------------------------------------------------------------------

def trace(entries: Sequence, reasons: bool = True) -> None:
    """Every recorded event as one rail, read straight off the audit log.

    NOTHING HERE IS NARRATED. Each step is an entry some stage wrote at the
    moment it happened - its own timestamp, its own one-sentence reason, its own
    actor. This function reads six fields and draws them. It does not know what
    a stage is, it cannot reorder anything, and there is no code path by which
    the picture and the saved file could disagree, because they are the same
    list.

    That matters more than it sounds. A progress display that is written
    separately from the record is a story about the run; this one IS the record,
    which is the difference between showing the work and illustrating it.

    THE STAGE FIELD IS NEVER READ. Every entry carries one - "5 · decision &
    authorisation" - and it is exactly the vocabulary CLAUDE.md bans from the
    buyer's screen. It stays in the JSONL for the finance system. What a person
    gets is the word from theme.event_mark and the sentence the stage wrote.

    `reasons=False` gives the compact rail: what happened and when, without the
    sentences. Used where the trail is context beside something else rather than
    the thing being read.
    """
    if not entries:
        return

    rows = []
    for entry in entries:
        colour, word, advisory = theme.event_mark(entry.event_type.value)
        # "User" rather than "You", and never a name. CLAUDE.md: the record says
        # a person acted, without pretending we know which person is signed in.
        actor = entry.actor.value.title()
        is_user = entry.actor.value.upper() == "USER"

        classes = "step step-advisory" if advisory else "step"
        actor_class = "step-actor step-actor-user" if is_user else "step-actor"
        why = (
            f'<div class="step-why">{escape(entry.reasoning)}</div>'
            if reasons and entry.reasoning else ""
        )
        rows.append(
            f'<div class="{classes}" style="--step-colour:{colour}">'
            f'<span class="step-node"></span>'
            f'<div class="step-body"><div class="step-head">'
            f'<span class="step-word">{escape(word)}</span>'
            f'<span class="{actor_class}">{escape(actor)}</span>'
            f'<span class="step-time">{entry.timestamp.strftime("%H:%M:%S")}</span>'
            f"</div>{why}</div></div>"
        )

    st.markdown(f'<div class="trail">{"".join(rows)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Identity — who is selling it, and what it actually is
# ---------------------------------------------------------------------------

def source_badge(product, extra: str = "") -> None:
    """Who this is being bought from, as one badge that can be read at a glance.

    A coloured mark for the supplier, the supplier's name, and what kind of
    supplier it is. All three are on the badge because two of them are load
    bearing: "who" answers the invoice question, and "direct vendor" vs
    "marketplace" answers a different one a buyer actually asks - am I buying
    from the maker, or through a middleman who can substitute on me?

    The colour comes from theme.vendor_colour, which is a separate ramp from the
    chart palette on purpose. See ui/theme.py.
    """
    palette = theme.active()
    colour = theme.vendor_colour(product.source)
    mark = escape(product.source[:1].upper())
    tail = f'<span class="vendor-kind">{escape(extra)}</span>' if extra else ""
    # The badge is tinted in the supplier's own hue rather than the neutral every
    # other tag uses, because this is the one tag on the row that answers "who".
    # Both custom properties are set here rather than in the sheet: the stylesheet
    # cannot know how many suppliers there are, and theme.vendor_colour is the
    # only thing allowed to decide which hue each one gets.
    style = (f'--vendor-colour:{palette.tint(colour, 0.55)};'
             f'--vendor-wash:{palette.tint(colour, 0.13)}')

    st.markdown(
        f'<div class="chip-row"><span class="vendor" style="{style}">'
        f'<span class="vendor-mark" style="background:{colour}">{mark}</span>'
        f'<span class="vendor-name">{escape(product.source)}</span>'
        f'<span class="vendor-kind">{escape(theme.vendor_kind(product.source_type))}</span>'
        f"{tail}</span></div>",
        unsafe_allow_html=True,
    )


def spec_badges(product, required: Sequence[str] = ()) -> None:
    """What this item is, spec by spec, with the buyer's own requirements ticked.

    Two states, and the difference matters. A ticked badge is something the buyer
    asked for and this product has - it is why the product is on the screen at
    all. An unticked one is something the supplier lists that nobody asked about.
    Showing them as one flat list would hide which specs did the qualifying.

    Matching is the same set membership the eligibility check uses, so a tick
    here can never disagree with the reason the product qualified.
    """
    if not product.specs:
        st.caption("This supplier lists no specification for the item.")
        return

    wanted = {spec.strip().lower() for spec in required}
    parts = []
    for spec in product.specs:
        met = spec.strip().lower() in wanted
        tick = '<span class="spec-tick">✓</span>' if met else ""
        classes = "spec spec-met" if met else "spec"
        parts.append(f'<span class="{classes}">{tick}{escape(spec)}</span>')

    st.markdown(f'<div class="chip-row">{"".join(parts)}</div>', unsafe_allow_html=True)
    if wanted:
        st.caption("✓ marks something you asked for. Anything missing one of those was not considered.")


def keyvalues(items: Sequence[tuple[str, str]], mono: bool = False) -> None:
    """A compact grid of (label, value) facts. Numbers aligned, labels quiet.

    Used where a reader wants to look one figure up rather than compare a shape -
    the item's terms on the recommendation, the references on the receipt.
    `mono` puts the values in a monospace face, which is what a reference number
    wants and a price does not.
    """
    cell = "kv-val kv-mono" if mono else "kv-val"
    parts = [
        f'<div class="kv-item"><div class="kv-key">{escape(label)}</div>'
        f'<div class="{cell}">{escape(value)}</div></div>'
        for label, value in items
    ]
    st.markdown(f'<div class="kv">{"".join(parts)}</div>', unsafe_allow_html=True)


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


def score_breakdown(scored: ScoredProduct, weights=None, weight_note: str = "") -> None:
    """Every term of one product's score, so nothing has to be taken on trust.

    Lives inside a drill-down. This is the arithmetic - your weight times the
    product's scaled figure, four terms adding up to the score - laid out twice
    on purpose:

      the table   one row per criterion, with the product's real figure next to
                  the scaled one, for a reader checking WHERE a number came from
      the sum     the same four terms as one worked calculation, for a reader
                  checking THAT it adds up

    A reader with a calculator can reproduce the total from either. That is the
    whole claim of this screen: the same request produces the same four lines and
    the same total on every run, and none of it was written by a language model.

    `weights` and `weight_note` are passed in so the arithmetic names its own
    inputs - which weight came from the buyer's own words and which from the
    default for this kind of purchase. A weight with no stated origin is exactly
    the kind of number a finance manager cannot sign off.
    """
    import pandas as pd

    if weight_note:
        st.caption(weight_note)

    st.dataframe(
        pd.DataFrame([
            {
                "Criterion": term.criterion,
                "Your weight": term.weight,
                "Where the weight came from": (
                    weights.sources.get(term.criterion, "") if weights else ""
                ),
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

    st.code(_score_sum(scored), language="text")


def _score_sum(scored: ScoredProduct) -> str:
    """The four terms written out as one calculation a reader can follow by hand.

    Kept as plain text rather than a chart or a second table because addition is
    the one thing here that a reader should be able to check with their finger on
    the screen.
    """
    width = max(len(term.criterion) for term in scored.terms)
    lines = [
        f"{term.criterion:<{width}}   {term.weight:.2f} × {term.normalised:.3f}"
        f"  =  {term.contribution:.3f}"
        for term in scored.terms
    ]
    total = sum(term.contribution for term in scored.terms)
    lines.append("-" * (width + 26))
    lines.append(f"{'total':<{width}}{'':>15}  =  {total:.3f}   ×100  =  {scored.score}")
    return "\n".join(lines)


def buyer_reviews(product) -> None:
    """What buyers actually said, shown to the person and scored by nothing.

    This sits under the score breakdown deliberately. The table above is every
    number that produced the recommendation; this is the part that did NOT. A
    reader can see a one-star review here and still see it contributed zero
    points, which is the honest way round: we surface the warning without
    pretending an algorithm weighed it.

    The seller's star rating IS in the table above, because it is a number every
    source publishes the same way. The sentences are not, because they are not
    comparable, not verifiable, and half-written by the seller.
    """
    if not product.sample_reviews:
        st.caption("This supplier publishes no buyer reviews.")
        return

    st.caption(
        f"{product.review_count:,} buyer ratings · {product.reliability_rating:.1f} "
        f"out of 5 average. Shown for context — reviews are not scored."
    )
    for review in product.sample_reviews:
        stars = "★" * int(round(review.stars)) + "☆" * (5 - int(round(review.stars)))
        st.markdown(f"{stars}  {review.text}")
