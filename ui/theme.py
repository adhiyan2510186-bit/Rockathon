"""Colour, type and spacing for the whole interface. One place, so nothing drifts.

WHY THIS FILE EXISTS
--------------------
app.py used to carry its own inline styling decisions, which is how four screens
end up looking like four different products. Every colour, every chip style and
every card in the app now comes from here.

WHERE THE PALETTE COMES FROM — AND WHY WE DID NOT INVENT IT
------------------------------------------------------------
These are not colours we liked the look of. They are a published, pre-validated
categorical palette, used unchanged and IN THE DOCUMENTED ORDER. That matters
because a categorical palette has to clear real accessibility gates:

  * adjacent series must stay distinguishable under colour-vision deficiency
  * they must stay distinguishable to normal vision too
  * each must hold enough contrast against the surface it sits on

Picking four colours that "look distinct" to us is exactly how a chart ends up
unreadable for the ~8% of men with red-green colour blindness — quite possibly
someone on the judging panel. Our chart forms (stacked bars and lines) are
validated on the ADJACENT pair list, and the first three slots additionally clear
the stricter all-pairs gate, which is why the line charts use only slots 1-3.

THE RELIEF RULE, AND HOW WE SATISFY IT
--------------------------------------
Two of the four slots (aqua, yellow) sit below 3:1 contrast on a light surface.
The documented mitigation is to ship visible direct labels or a table view. We
ship BOTH: every score bar is directly labelled, and the full comparison table is
on the same screen. So colour never carries meaning on its own here.

WHY WE PIN THE APP TO LIGHT MODE
--------------------------------
.streamlit/config.toml sets base="light". Streamlit would otherwise follow
whichever laptop it is running on, and a palette validated against a light
surface tells you nothing about how it renders on a dark one. One mode, validated,
identical on any machine we demo from. Determinism again — same reason stage 4.5
anchors to the feed instead of the wall clock.

STATUS COLOURS ARE RESERVED
---------------------------
The four urgency colours below are a separate, fixed status palette. They are
never reused as a series colour, so a status red can never impersonate "product
number four". And every status is shown with an icon AND a word, never colour
alone — two of them are deliberately low-contrast on light, and the icon+label
pairing is what makes them safe.
"""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Surfaces and ink
# ---------------------------------------------------------------------------

SURFACE = "#fcfcfb"        # the chart surface the palette was validated against
CANVAS = "#f6f6f4"         # page background, one step back from surface
BORDER = "#e4e4e0"
BORDER_STRONG = "#cfcfc9"

INK = "#0b0b0b"            # primary text
INK_SECONDARY = "#52514e"  # labels, captions
INK_MUTED = "#83827c"      # de-emphasised, never for anything load-bearing

# ---------------------------------------------------------------------------
# Categorical series — fixed order, never cycled, never re-assigned by rank
# ---------------------------------------------------------------------------
# Colour follows the CRITERION, not its position in the ranking. If a filter
# changes which products are on screen, reliability stays blue. Repainting
# survivors is how a reader loses track of what a colour means mid-demo.

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")   # blue, orange, aqua, yellow

# The four soft criteria, locked to the four slots above in display order.
CRITERION_COLOUR: dict[str, str] = {
    "reliability": SERIES[0],
    "price": SERIES[1],
    "replacement": SERIES[2],
    "delivery": SERIES[3],
}

# Line charts compare products rather than criteria, and only the first three
# slots clear the stricter all-pairs gate — so we plot at most three products.
PRODUCT_SERIES = SERIES[:3]

# ---------------------------------------------------------------------------
# Status — reserved, never used as a series colour
# ---------------------------------------------------------------------------

GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

# Urgency, as the UI shows it: colour + icon + word, so colour is never alone.
URGENCY_STYLE: dict[str, tuple[str, str, str]] = {
    # key            colour     icon  label
    "act_now":    (CRITICAL, "!", "Order today"),
    "order_soon": (SERIOUS,  "•", "Order this week"),
    "no_rush":    (GOOD,     "✓", "No rush"),
    "unknown":    (INK_MUTED, "?", "No history"),
}

ACCENT = "#4a3aa7"         # the one brand colour, for primary actions only

# ---------------------------------------------------------------------------
# The stylesheet
# ---------------------------------------------------------------------------

_CSS = f"""
<style>
  /* ---- page ------------------------------------------------------------ */
  .stApp {{ background: {CANVAS}; }}
  .block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

  h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.015em; }}
  h1 {{ font-size: 1.55rem !important; font-weight: 640 !important; }}

  /* Streamlit's tab bar, made to read as product navigation rather than steps */
  .stTabs [data-baseweb="tab-list"] {{
      gap: 0.35rem; border-bottom: 1px solid {BORDER}; padding-bottom: 0;
  }}
  .stTabs [data-baseweb="tab"] {{
      height: 2.6rem; padding: 0 1rem; font-size: 0.92rem; font-weight: 520;
      color: {INK_SECONDARY};
  }}
  .stTabs [aria-selected="true"] {{ color: {INK}; font-weight: 620; }}

  /* ---- cards ----------------------------------------------------------- */
  .card {{
      background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
      padding: 1.15rem 1.3rem; margin-bottom: 0.85rem;
  }}
  .card-tight {{ padding: 0.85rem 1rem; }}

  /* The recommendation card. One accent edge, so the eye lands here first. */
  .hero {{
      background: {SURFACE}; border: 1px solid {BORDER};
      border-left: 3px solid {ACCENT}; border-radius: 12px;
      padding: 1.35rem 1.5rem; margin-bottom: 0.9rem;
  }}
  .hero-eyebrow {{
      font-size: 0.72rem; font-weight: 620; letter-spacing: 0.07em;
      text-transform: uppercase; color: {INK_MUTED}; margin-bottom: 0.4rem;
  }}
  .hero-title {{ font-size: 1.5rem; font-weight: 650; color: {INK}; line-height: 1.2; }}
  .hero-sub {{ font-size: 0.95rem; color: {INK_SECONDARY}; margin-top: 0.35rem; }}

  /* An escalation card is the same shape in a different key - never a
     red-alert banner. Being asked to approve something is normal, not a fault. */
  .hero-escalated {{ border-left-color: {SERIOUS}; }}
  .hero-done {{ border-left-color: {GOOD}; }}

  /* ---- figures (the number-first tiles) -------------------------------- */
  .figure-label {{
      font-size: 0.74rem; font-weight: 560; letter-spacing: 0.04em;
      text-transform: uppercase; color: {INK_MUTED};
  }}
  .figure-value {{
      font-size: 1.42rem; font-weight: 640; color: {INK};
      font-variant-numeric: tabular-nums; line-height: 1.25;
  }}
  .figure-note {{ font-size: 0.8rem; color: {INK_SECONDARY}; }}

  /* ---- chips ----------------------------------------------------------- */
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.15rem 0 0.2rem; }}
  .chip {{
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-size: 0.79rem; font-weight: 520; line-height: 1;
      padding: 0.34rem 0.6rem; border-radius: 999px;
      border: 1px solid {BORDER_STRONG}; background: {SURFACE}; color: {INK_SECONDARY};
      white-space: nowrap;
  }}
  .chip-dot {{ width: 7px; height: 7px; border-radius: 999px; flex: none; }}
  .chip-strong {{ color: {INK}; font-weight: 580; }}

  /* ---- misc ------------------------------------------------------------ */
  .rule {{ height: 1px; background: {BORDER}; margin: 1.1rem 0; border: 0; }}
  .provenance {{
      font-size: 0.75rem; color: {INK_MUTED}; font-style: italic; margin-top: 0.3rem;
  }}
  .stButton > button[kind="primary"] {{
      background: {ACCENT}; border-color: {ACCENT}; font-weight: 580;
  }}
  /* The chat transcript should read like a conversation, not a log dump. */
  [data-testid="stChatMessage"] {{ background: transparent; padding: 0.35rem 0; }}
</style>
"""


def inject() -> None:
    """Apply the stylesheet. Called once, at the top of app.py."""
    st.markdown(_CSS, unsafe_allow_html=True)


def urgency(key: str) -> tuple[str, str, str]:
    """(colour, icon, label) for an urgency state. Unknown states fail quietly."""
    return URGENCY_STYLE.get(key, URGENCY_STYLE["unknown"])
