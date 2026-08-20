"""The three charts. Each one answers a question a table answers badly.

WHY THESE THREE AND NOT MORE
----------------------------
A chart earns its place by making a SHAPE visible. If the answer is one number,
we show one number; if the answer is a list, we show a table. Adding charts
because a dashboard looks impressive with charts is how a screen stops being
readable. These three each carry something a table genuinely cannot:

  score_composition   WHY this product won. The eye sees instantly that
                      reliability is most of the winner's bar and none of the
                      cheapest option's. In a table that is four numbers you have
                      to mentally add up.
  price_history       WHICH WAY the price is moving. A slope is a shape.
  stock_burndown      WHEN this vendor stops being able to fill THIS order. The
                      moment the line crosses the buyer's quantity is the whole
                      point, and a table cannot show a crossing.

RULES WE HOLD TO (they are what stop a chart from lying)
--------------------------------------------------------
* ONE axis, always. Never two y-scales on one chart. Price and stock are
  different measures, so they are two charts - overlaying them would let us draw
  any correlation we liked by choosing the scales.
* Colour follows the ENTITY, never its rank. Reliability is blue whether it is
  the biggest contribution or the smallest, and a product keeps its colour when
  the pool changes. Repainting on re-rank is how a reader loses the thread.
* Direct labels on the bars. Two of our four palette slots are low-contrast on a
  light surface, and the documented mitigation is visible labels - which we ship,
  alongside the full comparison table on the same screen. Colour never carries
  meaning alone.
* Recessive grid and axes. The data is the darkest thing on the chart.
* Line charts plot at most three products, because only the first three palette
  slots clear the stricter all-pairs accessibility gate.

EVERY NUMBER HERE IS READ, NEVER COMPUTED
-----------------------------------------
These functions take objects the engine already produced - `ScoredProduct.terms`,
`Product.price_history` - and draw them. Nothing in this file adds, weights, or
derives anything. If a chart could disagree with the audit log we would have two
answers to one question, and the log is supposed to be the answer.
"""

from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go

from agent.models import Product, ScoredProduct
from ui import theme

_FONT_FAMILY = "ui-sans-serif, -apple-system, 'Segoe UI', system-ui, sans-serif"


def _base_layout(p, height: int, **kwargs) -> dict:
    """Layout every chart starts from: recessive chrome, no clutter, one axis.

    Takes the palette rather than reading a module constant, so a chart drawn in
    dark mode is drawn on the dark surface its colours were validated against.
    """
    layout = dict(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor=p.surface,
        plot_bgcolor=p.surface,
        font=dict(family=_FONT_FAMILY, color=p.ink_secondary, size=12),
        hoverlabel=dict(bgcolor=p.surface, bordercolor=p.border_strong,
                        font=dict(color=p.ink, size=12)),
        showlegend=False,
    )
    layout.update(kwargs)
    return layout


def _axis(p, **kwargs) -> dict:
    """A deliberately quiet axis. Gridlines a shade above the surface, no spikes."""
    axis = dict(
        showgrid=True, gridcolor=p.border, gridwidth=1,
        zeroline=False, showline=False, ticks="",
        tickfont=dict(size=11, color=p.ink_muted),
    )
    axis.update(kwargs)
    return axis


# ---------------------------------------------------------------------------
# 1 · Score composition — the "why did this win?" chart
# ---------------------------------------------------------------------------

def score_composition(ranked: Sequence[ScoredProduct]) -> go.Figure:
    """Stacked horizontal bars: each product's score, split into its four terms.

    This is the chart that carries our central claim. Corusafe's bar is mostly
    blue because reliability contributed 0.450 of its 0.580; EcoMail's bar has no
    blue at all. The buyer said reliability mattered, and the picture says the
    ranking listened.

    Stacked rather than grouped because the parts sum to a meaningful whole - the
    score itself. Horizontal because product names are words, and words read
    better along a bar than rotated under one.
    """
    p = theme.active()
    fig = go.Figure()

    # Reversed so the winner sits at the TOP of the chart. Plotly's category axis
    # builds upward from the first entry, which would otherwise bury the winner.
    products = list(reversed(ranked))
    labels = [scored.product.name for scored in products]

    for criterion, colour in p.criterion_colour.items():
        contributions = [scored.contribution(criterion) for scored in products]
        # Only label a segment that is big enough to hold text. A number crammed
        # into a 2-pixel sliver is noise, and the drill-down has the exact figures.
        text = [f"{value * 100:.1f}" if value * 100 >= 4 else "" for value in contributions]

        fig.add_bar(
            y=labels,
            x=[value * 100 for value in contributions],
            name=criterion,
            orientation="h",
            marker=dict(
                color=colour,
                # A 2px surface-coloured gap between segments, so adjacent fills
                # never blend into one another.
                line=dict(color=p.surface, width=2),
            ),
            text=text,
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(color="#ffffff", size=11),
            hovertemplate=(f"<b>{criterion}</b><br>contributes %{{x:.1f}} points"
                           "<extra>%{y}</extra>"),
        )

    fig.update_layout(**_base_layout(
        p,
        height=58 * len(products) + 70,
        barmode="stack",
        bargap=0.42,
        xaxis=_axis(p, title=dict(text="score contribution", font=dict(size=11)), range=[0, 100]),
        yaxis=_axis(p, showgrid=False, tickfont=dict(size=12, color=p.ink)),
    ))
    return fig


# ---------------------------------------------------------------------------
# 2 · Price history — the "which way is it moving?" chart
# ---------------------------------------------------------------------------

def price_history(products: Sequence[Product]) -> go.Figure | None:
    """One line per product. Returns None when no source published a history.

    Returning None rather than an empty chart is deliberate: an empty pair of
    axes reads as "something broke". A source that publishes nothing should
    produce silence, and the caller simply draws nothing.
    """
    plotted = [item for item in products if len(item.price_history) >= 2][:3]
    if not plotted:
        return None

    p = theme.active()
    fig = go.Figure()
    for product, colour in zip(plotted, p.product_series):
        fig.add_scatter(
            x=[point.on for point in product.price_history],
            y=[point.value for point in product.price_history],
            mode="lines+markers",
            name=product.name,
            line=dict(color=colour, width=2),
            marker=dict(size=8, color=colour, line=dict(color=p.surface, width=2)),
            hovertemplate="₹%{y:.2f} per unit on %{x|%d %b}<extra>" + product.name + "</extra>",
        )
        # Direct label at the last point, so identity never depends on colour.
        fig.add_annotation(
            x=product.price_history[-1].on, y=product.price_history[-1].value,
            text=f"  {product.name}", showarrow=False,
            xanchor="left", font=dict(size=11, color=p.ink_secondary),
        )

    fig.update_layout(**_base_layout(
        p,
        height=250,
        xaxis=_axis(p, showgrid=False),
        yaxis=_axis(p, title=dict(text="₹ per unit", font=dict(size=11)), tickformat=".2f"),
        hovermode="x unified",
        margin=dict(l=8, r=110, t=8, b=8),   # room for the direct labels
    ))
    return fig


# ---------------------------------------------------------------------------
# 3 · Stock burn-down — the "when does this stop being an option?" chart
# ---------------------------------------------------------------------------

def stock_burndown(products: Sequence[Product], quantity: int) -> go.Figure | None:
    """Stock over time, with the buyer's own order quantity drawn across it.

    The reference line is the point of the chart. Stock falling is mildly
    interesting; stock falling TOWARD THE LINE YOU NEED is the thing worth
    acting on, and where a line crosses it is a date. That is exactly the fact
    stage 4.5 computes, drawn rather than asserted.
    """
    plotted = [item for item in products if len(item.stock_history) >= 2][:3]
    if not plotted:
        return None

    p = theme.active()
    fig = go.Figure()
    for product, colour in zip(plotted, p.product_series):
        fig.add_scatter(
            x=[point.on for point in product.stock_history],
            y=[point.value for point in product.stock_history],
            mode="lines+markers",
            name=product.name,
            line=dict(color=colour, width=2),
            marker=dict(size=8, color=colour, line=dict(color=p.surface, width=2)),
            hovertemplate="%{y:,.0f} units on %{x|%d %b}<extra>" + product.name + "</extra>",
        )
        fig.add_annotation(
            x=product.stock_history[-1].on, y=product.stock_history[-1].value,
            text=f"  {product.name}", showarrow=False,
            xanchor="left", font=dict(size=11, color=p.ink_secondary),
        )

    # The buyer's requirement. Dashed and grey so it reads as a threshold rather
    # than a fourth product.
    fig.add_hline(
        y=quantity, line=dict(color=p.ink_muted, width=1.5, dash="dot"),
        annotation=dict(text=f"you need {quantity:,}", font=dict(size=11, color=p.ink_secondary)),
        annotation_position="top left",
    )

    fig.update_layout(**_base_layout(
        p,
        height=250,
        xaxis=_axis(p, showgrid=False),
        yaxis=_axis(p, title=dict(text="units in stock", font=dict(size=11)), tickformat=",.0f"),
        hovermode="x unified",
        margin=dict(l=8, r=110, t=8, b=8),
    ))
    return fig
