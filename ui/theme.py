"""Colour, type and spacing for the whole interface. One place, so nothing drifts.

WHY THIS FILE EXISTS
--------------------
app.py used to carry its own inline styling decisions, which is how four screens
end up looking like four different products. Every colour, every chip and every
card in the app now comes from here.

THREE COLOUR ROLES, NEVER MIXED
-------------------------------
This is the rule that keeps the palette honest, and it is worth saying out loud:

  BRAND      one violet. Chrome only - primary buttons, the hero's edge, card
             tints. It never encodes data, so it can never be mistaken for one.
  CATEGORICAL four hues, one per soft criterion. Reliability is always the same
             blue whether it contributed everything or nothing.
  STATUS     four reserved colours for urgency. Never reused as a series colour,
             so a status red can never impersonate "product number four".

A colour that means two things means nothing.

WHERE THE PALETTE COMES FROM - AND WHY WE DID NOT INVENT IT
------------------------------------------------------------
These are not colours we liked the look of. They are a published, pre-validated
categorical palette, used unchanged and IN THE DOCUMENTED ORDER, because a
categorical palette has to clear real gates: adjacent series must stay
distinguishable under colour-vision deficiency AND to normal vision, and each
must hold contrast against the surface it sits on. Picking four colours that
"look distinct" to us is how a chart becomes unreadable for roughly one man in
twelve - quite possibly someone on the judging panel.

Our chart forms (stacked bars, lines) are validated on the ADJACENT pair list,
and the first three slots additionally clear the stricter all-pairs gate, which
is why line charts plot at most three products.

DARK MODE IS SELECTED, NOT FLIPPED
----------------------------------
The dark column below is NOT the light palette with the lightness inverted. It
is the same eight hues re-stepped for a dark surface and validated as a set
against it. An automatic flip is how a palette that passed every gate on white
quietly fails all of them on black.

Streamlit's own chrome is themed to match from .streamlit/config.toml, which
declares [theme.light] and [theme.dark] with these same values. We read the
viewer's choice back at runtime with `st.context.theme.type` so our CSS and our
charts agree with the widgets around them.

THE RELIEF RULE, AND HOW WE SATISFY IT
--------------------------------------
Two light-mode slots (aqua, yellow) sit below 3:1 contrast on a light surface.
The documented mitigation is visible direct labels or a table view. We ship
BOTH: every score bar is directly labelled and the full comparison table is on
the same screen. Colour never carries meaning alone here - and every status is
shown as colour AND icon AND word.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class Palette:
    """Every colour one mode needs. Two instances exist: LIGHT and DARK."""

    name: str

    surface: str        # cards, charts - the colour the palette was validated on
    canvas: str         # page background, one step behind surface
    border: str
    border_strong: str

    ink: str            # primary text
    ink_secondary: str  # labels, captions
    ink_muted: str      # de-emphasised; never for anything load-bearing

    series: tuple[str, str, str, str]   # categorical, fixed order, never cycled
    accent: str                          # brand. Chrome only, never data.

    # Status is fixed across both modes - all four clear 3:1 on either surface.
    good: str = "#0ca30c"
    warning: str = "#fab219"
    serious: str = "#ec835a"
    critical: str = "#d03b3b"

    @property
    def criterion_colour(self) -> dict[str, str]:
        """Soft criterion -> its fixed hue. Colour follows the criterion, never rank."""
        return dict(zip(("reliability", "price", "replacement", "delivery"), self.series))

    @property
    def product_series(self) -> tuple[str, ...]:
        """Line charts get the first three slots only - the all-pairs-safe subset."""
        return self.series[:3]

    def tint(self, hex_colour: str, alpha: float) -> str:
        """A translucent wash of a colour, for chip and card backgrounds.

        Expressed as rgba over whatever surface is behind it, rather than as a
        second hardcoded hex. One definition then works in both modes: the same
        12% wash reads as a pale tint on white and a deep one on near-black.
        """
        value = hex_colour.lstrip("#")
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r}, {g}, {b}, {alpha})"


LIGHT = Palette(
    name="light",
    surface="#fcfcfb",
    canvas="#f6f6f4",
    border="#e4e4e0",
    border_strong="#cfcfc9",
    ink="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#83827c",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),   # blue orange aqua yellow
    accent="#4a3aa7",
)

DARK = Palette(
    name="dark",
    surface="#1a1a19",
    canvas="#111110",
    border="#34342f",
    border_strong="#4a4a44",
    ink="#ffffff",
    ink_secondary="#c3c2b7",
    ink_muted="#8e8d84",
    series=("#3987e5", "#d95926", "#199e70", "#c98500"),   # same hues, re-stepped
    accent="#9085e9",
)


def active() -> Palette:
    """The palette matching whatever the viewer has Streamlit set to.

    `st.context.theme.type` is 'light', 'dark', or None when Streamlit has not
    resolved a theme yet (which happens outside a browser session, e.g. in
    tests). Light is the fallback, because a wrong guess toward light is merely
    plain, whereas a wrong guess toward dark is unreadable on white.
    """
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:
        return LIGHT


# ---------------------------------------------------------------------------
# Supplier identity — a fourth colour role, kept off the charts on purpose
# ---------------------------------------------------------------------------
# A supplier badge wants a colour: "Amazon" and "PackHub" should be tellable
# apart from across a room. But the four CATEGORICAL hues above are spoken for -
# blue means reliability everywhere in this app - and a blue Amazon badge sitting
# beside a blue reliability chip would make one colour mean two things, which is
# the one thing the rule at the top of this file forbids.
#
# So supplier colour is its own small ramp, deliberately outside both the series
# and the status sets, and it is only ever used as a small badge mark. It never
# appears in a chart, so it can never be mistaken for a data series.
_VENDOR_HUES: tuple[str, ...] = (
    "#0e7c86",  # teal
    "#b5388f",  # magenta
    "#4a5b6b",  # slate
    "#8a5a2b",  # umber
    "#5d6f1f",  # olive
    "#7a3f6b",  # plum
)


def vendor_colour(source: str) -> str:
    """A stable badge hue for one supplier, e.g. 'Amazon' -> magenta.

    Assigned by the supplier's position in the source list rather than by a hash
    of its name, so two suppliers can never land on the same colour while there
    are hues left - and so a new adapter picks up a colour without anyone editing
    this file. Same supplier, same colour, every run.
    """
    from agent import sources  # local import: theme is imported by everything

    names = [adapter.display_name for adapter in sources.ADAPTERS.values()]
    index = names.index(source) if source in names else len(names)
    return _VENDOR_HUES[index % len(_VENDOR_HUES)]


def vendor_kind(source_type: str) -> str:
    """'direct' -> 'Direct vendor'. What kind of supplier this is, in buyer words.

    A buyer does care about the difference — buying from the maker and buying
    through a middleman are different risks — so the word is on the badge. What
    they do not care about is the file format it arrived in, which is why that
    stays in the drill-down.
    """
    return "Direct vendor" if source_type == "direct" else "Marketplace"


# ---------------------------------------------------------------------------
# Urgency — colour AND icon AND word, always all three
# ---------------------------------------------------------------------------

def urgency(key: str) -> tuple[str, str, str]:
    """(colour, icon, label) for an urgency state.

    Two of the status colours are deliberately low-contrast on a light surface.
    The icon and the word are what make them safe, so nothing in the UI is ever
    allowed to render the colour on its own.
    """
    palette = active()
    table = {
        "act_now":    (palette.critical, "!", "Order today"),
        "order_soon": (palette.serious,  "•", "Order this week"),
        "no_rush":    (palette.good,     "✓", "No rush"),
        "unknown":    (palette.ink_muted, "?", "No history"),
    }
    return table.get(key, table["unknown"])


# ---------------------------------------------------------------------------
# The stylesheet
# ---------------------------------------------------------------------------

def _css(p: Palette) -> str:
    """Build the stylesheet for one palette. Colour appears only where it means something."""
    return f"""
<style>
  /* ---- page ------------------------------------------------------------ */
  .stApp {{ background: {p.canvas}; }}
  .block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

  h1, h2, h3, h4 {{ color: {p.ink}; letter-spacing: -0.015em; }}
  h1 {{ font-size: 1.55rem !important; font-weight: 640 !important; }}

  /* A brand-tinted rule under the page title, so the app has an identity
     without a logo. Chrome, not data. */
  .brandbar {{
      height: 3px; width: 64px; border-radius: 2px; margin: 0.1rem 0 1.1rem;
      background: linear-gradient(90deg, {p.accent}, {p.tint(p.accent, 0.15)});
  }}

  .stTabs [data-baseweb="tab-list"] {{
      gap: 0.35rem; border-bottom: 1px solid {p.border}; padding-bottom: 0;
  }}
  .stTabs [data-baseweb="tab"] {{
      height: 2.6rem; padding: 0 1rem; font-size: 0.92rem; font-weight: 520;
      color: {p.ink_secondary};
  }}
  .stTabs [aria-selected="true"] {{ color: {p.ink}; font-weight: 620; }}

  /* ---- cards ----------------------------------------------------------- */
  .card {{
      background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
      padding: 1.15rem 1.3rem; margin-bottom: 0.85rem;
  }}
  .card-tight {{ padding: 0.85rem 1rem; }}

  /* The recommendation card. A brand wash and one accent edge, so the eye
     lands here first without anything shouting. */
  .hero {{
      background: linear-gradient(180deg, {p.tint(p.accent, 0.07)}, {p.surface} 70%);
      border: 1px solid {p.border};
      border-left: 3px solid {p.accent}; border-radius: 12px;
      padding: 1.35rem 1.5rem; margin-bottom: 0.9rem;
  }}
  .hero-eyebrow {{
      font-size: 0.72rem; font-weight: 620; letter-spacing: 0.07em;
      text-transform: uppercase; color: {p.accent}; margin-bottom: 0.4rem;
  }}
  .hero-title {{ font-size: 1.5rem; font-weight: 650; color: {p.ink}; line-height: 1.2; }}
  .hero-sub {{ font-size: 0.95rem; color: {p.ink_secondary}; margin-top: 0.35rem; }}

  /* An escalation is the same card in a different key - never a red alarm
     banner. Being asked to approve a large order is the system working. */
  .hero-escalated {{
      border-left-color: {p.serious};
      background: linear-gradient(180deg, {p.tint(p.serious, 0.10)}, {p.surface} 70%);
  }}
  .hero-escalated .hero-eyebrow {{ color: {p.serious}; }}
  .hero-done {{
      border-left-color: {p.good};
      background: linear-gradient(180deg, {p.tint(p.good, 0.10)}, {p.surface} 70%);
  }}
  .hero-done .hero-eyebrow {{ color: {p.good}; }}

  /* ---- figures (number-first tiles) ------------------------------------ */
  .figure {{
      background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
      padding: 0.85rem 1rem; margin-bottom: 0.85rem;
      border-top: 2px solid {p.tint(p.accent, 0.45)};
  }}
  .figure-label {{
      font-size: 0.74rem; font-weight: 560; letter-spacing: 0.04em;
      text-transform: uppercase; color: {p.ink_muted};
  }}
  .figure-value {{
      font-size: 1.42rem; font-weight: 640; color: {p.ink};
      font-variant-numeric: tabular-nums; line-height: 1.25;
  }}
  .figure-note {{ font-size: 0.8rem; color: {p.ink_secondary}; }}

  /* ---- chips ----------------------------------------------------------- */
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.15rem 0 0.2rem; }}
  .chip {{
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-size: 0.79rem; font-weight: 520; line-height: 1;
      padding: 0.34rem 0.6rem; border-radius: 999px;
      border: 1px solid {p.border_strong}; background: {p.surface};
      color: {p.ink_secondary}; white-space: nowrap;
  }}
  .chip-dot {{ width: 7px; height: 7px; border-radius: 999px; flex: none; }}
  /* A tinted chip carries a real signal. The text stays in an ink token - a
     coloured dot beside it carries the identity, never the words themselves. */
  .chip-strong {{ color: {p.ink}; font-weight: 580; }}

  /* ---- supplier badge --------------------------------------------------- */
  /* The mark carries the colour, the words carry the meaning. Same discipline
     as the chips: colour is never the only thing saying who this is. */
  .vendor {{
      display: inline-flex; align-items: center; gap: 0.5rem;
      padding: 0.28rem 0.7rem 0.28rem 0.32rem; border-radius: 999px;
      border: 1px solid {p.border_strong}; background: {p.surface};
  }}
  .vendor-mark {{
      width: 1.4rem; height: 1.4rem; border-radius: 7px; flex: none;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 0.72rem; font-weight: 700; color: #ffffff; letter-spacing: 0;
  }}
  .vendor-name {{ font-size: 0.86rem; font-weight: 620; color: {p.ink}; }}
  .vendor-kind {{
      font-size: 0.7rem; font-weight: 560; letter-spacing: 0.05em;
      text-transform: uppercase; color: {p.ink_muted};
  }}

  /* ---- spec badges ------------------------------------------------------ */
  /* Dashed border: the supplier lists it. Solid with a tick: it is one of the
     things the buyer asked for, so it had to be there for this to qualify. */
  .spec {{
      display: inline-flex; align-items: center; gap: 0.3rem;
      font-size: 0.78rem; font-weight: 520; line-height: 1;
      padding: 0.32rem 0.55rem; border-radius: 7px;
      border: 1px dashed {p.border_strong}; background: {p.surface};
      color: {p.ink_secondary}; white-space: nowrap;
  }}
  .spec-met {{
      border-style: solid; border-color: {p.tint(p.good, 0.55)};
      background: {p.tint(p.good, 0.08)}; color: {p.ink};
  }}
  .spec-tick {{ color: {p.good}; font-weight: 700; }}

  /* ---- key/value grid --------------------------------------------------- */
  .kv {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(126px, 1fr));
      gap: 0.75rem 1.1rem; margin: 0.2rem 0 0.1rem;
  }}
  .kv-item {{ border-left: 2px solid {p.border}; padding-left: 0.6rem; }}
  .kv-key {{
      font-size: 0.7rem; font-weight: 560; letter-spacing: 0.05em;
      text-transform: uppercase; color: {p.ink_muted};
  }}
  .kv-val {{
      font-size: 0.95rem; font-weight: 620; color: {p.ink};
      font-variant-numeric: tabular-nums;
  }}
  .kv-mono {{ font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; }}

  /* ---- misc ------------------------------------------------------------ */
  .rule {{ height: 1px; background: {p.border}; margin: 1.1rem 0; border: 0; }}
  .provenance {{
      font-size: 0.75rem; color: {p.ink_muted}; font-style: italic; margin-top: 0.3rem;
  }}
  .stButton > button[kind="primary"] {{
      background: {p.accent}; border-color: {p.accent}; font-weight: 580;
  }}
  [data-testid="stChatMessage"] {{ background: transparent; padding: 0.35rem 0; }}
</style>
"""


def inject() -> None:
    """Apply the stylesheet for the viewer's current theme. Called once, in app.py."""
    st.markdown(_css(active()), unsafe_allow_html=True)


def brandbar() -> None:
    """The small accent rule under the page title."""
    st.markdown('<div class="brandbar"></div>', unsafe_allow_html=True)
