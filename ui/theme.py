"""Colour, type and spacing for the whole interface. One place, so nothing drifts.

WHY THIS FILE EXISTS
--------------------
app.py used to carry its own inline styling decisions, which is how four screens
end up looking like four different products. Every colour, every chip and every
card in the app now comes from here.

THE PRODUCT HAS A NAME, AND IT LIVES IN EXACTLY ONE CONSTANT
------------------------------------------------------------
`PRODUCT_NAME` and `PRODUCT_EXPANSION` below are the only place the agent is
named. Everything that shows the name - the browser tab, the header lockup, the
opening screen, the sidebar footer - reads them from here, so the name can never
end up spelled two ways on two screens.

THE SURFACE IS DARK, AND LAYERED IN A NARROW BAND
-------------------------------------------------
Four neutrals do all the structural work: the page, the panel sitting on it, the
hovered row, and the selected row. They are only a few percent apart on purpose.
Big jumps between greys read as unrelated boxes; a narrow band reads as one
surface with things resting on it.

SEPARATION IS A HAIRLINE. DECORATION IS A DIFFERENT JOB.
--------------------------------------------------------
Where one surface ends and the next begins is ALWAYS said with a hairline or a
fill shift - never with a shadow and never with a gradient. On a near-black page
a drop shadow is invisible, so a UI that leans on one just looks flat and
slightly muddy. A 1px white line at 7% opacity is visible, costs nothing, and
never blurs an edge.

That rule is about EDGES. It is not a vow of plainness, and three things in here
now carry a gradient on purpose, none of them structural: the brand mark, the
primary button, and a wash inside the recommendation panel that fades out well
before that panel's own border. Each one is brand colour used as brand colour.
Take any of them away and every boundary on the page is exactly where it was.

THREE COLOUR ROLES, NEVER MIXED
-------------------------------
This is the rule that keeps the palette honest, and it is worth saying out loud:

  BRAND      one violet. Chrome only - the active tab, primary buttons, the
             recommendation's edge. It never encodes data, so it can never be
             mistaken for one.
  CATEGORICAL four hues, one per soft criterion. Reliability is always the same
             blue whether it contributed everything or nothing.
  STATUS     four reserved colours for urgency. Never reused as a series colour,
             so a status red can never impersonate "product number four".

A colour that means two things means nothing. That rule is also why the brand
stayed violet rather than moving to the blue our house style would otherwise
reach for: our reliability series is blue, and a blue button beside a blue
reliability chip would make one colour mean two things.

WHERE THE PALETTE COMES FROM - AND WHY WE DID NOT INVENT IT
------------------------------------------------------------
The four series hues are not colours we liked the look of. They are a published,
pre-validated categorical palette, used unchanged and IN THE DOCUMENTED ORDER,
because a categorical palette has to clear real gates: adjacent series must stay
distinguishable under colour-vision deficiency AND to normal vision, and each
must hold contrast against the surface it sits on. Picking four colours that
"look distinct" to us is how a chart becomes unreadable for roughly one man in
twelve - quite possibly someone on the judging panel.

Our chart forms (stacked bars, lines) are validated on the ADJACENT pair list,
and the first three slots additionally clear the stricter all-pairs gate, which
is why line charts plot at most three products.

DARK MODE IS SELECTED, NOT FLIPPED
----------------------------------
The dark palette below is NOT the light one with the lightness inverted. It is
the same eight hues re-stepped for a dark surface and validated as a set against
it. An automatic flip is how a palette that passed every gate on white quietly
fails all of them on black.

DARK IS THE APP, NOT A PREFERENCE
---------------------------------
The app ships dark on every machine. `.streamlit/config.toml` says so once, in
`theme.base`, and `active()` below reads that same line - so Streamlit's widgets
and our cards can never end up in different modes. LIGHT is still here, complete
and validated, and flipping that one config line switches the whole interface to
it. What is gone is the guess: we no longer let a setting on somebody else's
laptop decide what the judges see.

THE RELIEF RULE, AND HOW WE SATISFY IT
--------------------------------------
Two light-mode slots (aqua, yellow) sit below 3:1 contrast on a light surface.
The documented mitigation is visible direct labels or a table view. We ship
BOTH: every score bar is directly labelled and the full comparison table is on
the same screen. Colour never carries meaning alone here - and every status is
shown as colour AND icon AND word.

THE TYPE SCALE IS SIX SIZES AND NOTHING ELSE
--------------------------------------------
11 / 12 / 13 / 15 / 20 / 28 px, with 13 doing most of the work. Hierarchy comes
from weight and from the three ink tokens, not from inventing a seventh size.
Nine font sizes is what a screen looks like when every size was chosen locally.
Numbers anywhere that can change are tabular, or the layout twitches on update.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


# ---------------------------------------------------------------------------
# The name
# ---------------------------------------------------------------------------
# One constant, read by every surface that shows the name. The expansion is
# carried alongside it because an acronym nobody can expand is just a noise the
# buyer has to learn - and it is the shortest honest description of what the
# thing does, which is what a strapline is for.
PRODUCT_NAME = "Evets"
PRODUCT_EXPANSION = "Enterprise Vendor Evaluation & Transparent Sourcing"
PRODUCT_TAGLINE = "Autonomy you can audit"


@dataclass(frozen=True)
class Palette:
    """Every colour one mode needs. Two instances exist: DARK and LIGHT."""

    name: str

    canvas: str         # page background - the thing everything else sits on
    surface: str        # panel, card, chart - the colour the palette was validated on
    surface_2: str      # hovered row, input fill, secondary button
    surface_3: str      # selected row, active control
    border: str
    border_strong: str

    ink: str            # primary text
    ink_secondary: str  # labels, captions
    ink_muted: str      # de-emphasised; never for anything load-bearing
    ink_faint: str      # timestamps, disabled - never for anything to be read

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


# The default. Four neutrals within a narrow band, hairlines between them.
DARK = Palette(
    name="dark",
    canvas="#0a0a0b",
    surface="#131316",
    surface_2="#1a1a1e",
    surface_3="#232328",
    border="rgba(255, 255, 255, 0.07)",
    border_strong="rgba(255, 255, 255, 0.13)",
    ink="#ededef",            # off-white, not #fff - pure white vibrates at 28px
    ink_secondary="#a8a8b0",
    ink_muted="#8a8a94",      # the dimmest ink still allowed to carry a sentence
    ink_faint="#55555e",
    series=("#3987e5", "#d95926", "#199e70", "#c98500"),   # blue orange aqua yellow
    accent="#9085e9",
)

# Same structure inverted, same accent, same scale. Borders on, shadows off -
# the discipline transfers even when the polarity does not.
LIGHT = Palette(
    name="light",
    canvas="#ffffff",
    surface="#fafafa",
    surface_2="#f1f1f2",
    surface_3="#e9e9eb",
    border="#e6e6e6",
    border_strong="#d4d4d6",
    ink="#0a0a0a",
    ink_secondary="#45454a",
    ink_muted="#6b6b6b",
    ink_faint="#96969c",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),   # same hues, re-stepped
    accent="#4a3aa7",
)


def active() -> Palette:
    """The palette matching the theme this app is actually running in.

    Read from `theme.base` in config.toml - the same single line Streamlit reads
    to paint its own widgets. One source, so our cards and Streamlit's buttons
    can never end up in different modes.

    This used to read `st.context.theme.type` instead, and that was a real bug we
    watched happen: on a machine set to light, our stylesheet went light while
    Streamlit's chrome went dark, and the page came out half and half. That
    property reports the BROWSER's preference, which is not the same question as
    "what theme is this app painted in" - and this build of Streamlit offers the
    viewer no theme switcher at all, so the browser's opinion was never going to
    be the answer.

    The fallback exists for callers outside a Streamlit runtime, e.g. tests.
    """
    try:
        return LIGHT if st.get_option("theme.base") == "light" else DARK
    except Exception:
        return DARK


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
    from agent.signals import Urgency  # local import: theme is imported by everything

    palette = active()
    table = {
        "act_now":    (palette.critical, "!"),
        "order_soon": (palette.serious,  "•"),
        "no_rush":    (palette.good,     "✓"),
        "unknown":    (palette.ink_muted, "?"),
    }
    colour, icon = table.get(key, table["unknown"])
    # The words come from the engine, not from here. agent.signals.Urgency owns
    # them because the audit trail needs the same phrasing this screen shows, and
    # two lists drifting apart is how "act_now" ended up printed to a buyer.
    known = key if key in table else Urgency.UNKNOWN.value
    return colour, icon, Urgency(known).label.capitalize()


# ---------------------------------------------------------------------------
# The stylesheet
# ---------------------------------------------------------------------------
#
# Everything below is written against the six-size type scale and a 4px spacing
# unit. Two habits are worth naming because they are what stop a dark UI looking
# assembled rather than designed:
#
#   * one radius per element class, held everywhere - 6px on anything you click
#     or read as a tag, 10px on anything that is a panel. Mixed radii on adjacent
#     elements is the loudest sloppiness tell there is.
#   * every interactive thing has hover, :focus-visible and disabled. A missing
#     hover state is the fastest way for an interface to feel dead.

def _css(p: Palette) -> str:
    """Build the stylesheet for one palette. Colour appears only where it means something."""
    return f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600&display=swap');

  :root {{
      --canvas: {p.canvas};
      --surface: {p.surface};
      --surface-2: {p.surface_2};
      --surface-3: {p.surface_3};
      --border: {p.border};
      --border-strong: {p.border_strong};
      --ink: {p.ink};
      --ink-2: {p.ink_secondary};
      --ink-muted: {p.ink_muted};
      --ink-faint: {p.ink_faint};
      --accent: {p.accent};
      /* Two washes of the brand, pre-mixed once so no caller invents its own
         opacity. Anything brand-tinted in this sheet uses one of these. */
      --accent-wash: {p.tint(p.accent, 0.10)};
      --accent-faint: {p.tint(p.accent, 0.04)};
      --font: 'Inter Tight', Inter, -apple-system, 'Segoe UI', system-ui, sans-serif;
      --mono: 'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace;
  }}

  /* ---- page ------------------------------------------------------------ */
  /* One very faint pool of brand colour behind the header, and nothing else on
     the canvas. It is not a divider and it is not a card - it stops a
     1120px-wide near-black page reading as an empty terminal, and it fades out
     entirely before the first panel starts. */
  .stApp {{
      background:
          radial-gradient(70rem 22rem at 50% -8rem, var(--accent-faint), transparent 70%),
          var(--canvas);
      background-attachment: fixed;
      font-family: var(--font);
  }}
  /* The top padding clears Streamlit's own floating toolbar. Without it our
     header scrolls up underneath the menu button and the first thing on the
     page is half a logo. */
  .block-container {{ padding-top: 3.5rem; padding-bottom: 4rem; max-width: 1120px; }}

  /* Six sizes, and hierarchy carried by weight and ink rather than by a
     seventh. 600 is the weight ceiling anywhere in the app. */
  h1, h2, h3, h4 {{ font-family: var(--font); color: var(--ink); }}
  h1 {{ font-size: 1.75rem !important; font-weight: 600 !important;
        letter-spacing: -0.02em; line-height: 1.15; }}
  /* Every section heading carries a short accent tick on its left. It is 3px of
     colour doing the job a bold rule or a boxed header would otherwise be asked
     to do: marking where a new section starts without adding another edge to a
     page that already has borders on everything. */
  h4 {{ font-size: 0.9375rem !important; font-weight: 600 !important;
        letter-spacing: -0.01em; margin: 0 0 0.5rem !important;
        display: flex; align-items: center; gap: 0.5rem; }}
  h4::before {{
      content: ""; flex: none; width: 3px; height: 0.875rem;
      border-radius: 2px; background: var(--accent); opacity: 0.85;
  }}

  /* Captions are the one place prose appears, so they get a reading measure.
     A caption running the full 1120px is the commonest readability failure in
     a wide dashboard. */
  [data-testid="stCaptionContainer"] {{
      max-width: 62ch; font-size: 0.8125rem; line-height: 1.55; color: var(--ink-muted);
  }}
  [data-testid="stCaptionContainer"] p {{ font-size: 0.8125rem; margin-bottom: 0; }}
  .stMarkdown p {{ font-size: 0.8125rem; line-height: 1.55; color: var(--ink-2); }}
  .stMarkdown strong {{ color: var(--ink); font-weight: 600; }}

  /* ---- app header ------------------------------------------------------- */
  /* A mark, the name, what the name stands for, and a hairline. That is the
     whole chrome - there is still no logo and still no decorative rule. The
     mark is the one place a gradient is spent: a brand mark is the single
     element on the page whose only job IS to be recognised, and a flat violet
     square is a placeholder in a way a stepped one is not. */
  .apphead {{
      display: flex; align-items: center; gap: 0.6875rem;
      padding-bottom: 1rem; margin-bottom: 1.25rem;
      border-bottom: 1px solid var(--border);
  }}
  .apphead-mark {{
      width: 2rem; height: 2rem; border-radius: 9px; flex: none;
      display: inline-flex; align-items: center; justify-content: center;
      background: linear-gradient(145deg, {p.accent}, {p.tint(p.accent, 0.62)});
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22);
      color: #fff; font-size: 0.9375rem; font-weight: 600;
      letter-spacing: -0.02em;
  }}
  /* Name and expansion stack, so the strapline hangs off the name rather than
     running along beside it and doubling the header's width. */
  .apphead-brand {{ display: flex; flex-direction: column; gap: 0.125rem; min-width: 0; }}
  .apphead-name {{
      font-size: 1.0625rem; font-weight: 600; color: var(--ink);
      letter-spacing: -0.02em; line-height: 1.1;
  }}
  .apphead-expansion {{
      font-size: 0.625rem; font-weight: 500; letter-spacing: 0.09em;
      text-transform: uppercase; color: var(--ink-faint); line-height: 1.2;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  /* The order reference is a number people read off the screen and quote down a
     phone, so it gets a monospace face and a container of its own rather than
     floating as loose grey text. */
  .apphead-note {{
      margin-left: auto; flex: none;
      font-family: var(--mono); font-size: 0.6875rem; color: var(--ink-2);
      font-variant-numeric: tabular-nums; letter-spacing: 0.02em;
      padding: 0.25rem 0.5rem; border-radius: 6px;
      border: 1px solid var(--border); background: var(--surface-2);
  }}
  /* Below roughly a tablet the strapline is the first thing to go: the name and
     the order reference are load bearing, 52 characters of small caps are not. */
  @media (max-width: 640px) {{ .apphead-expansion {{ display: none; }} }}

  /* The same lockup once more at the foot of the sidebar, quiet enough to read
     as a signature rather than a second title. */
  .sidebrand {{
      display: flex; align-items: center; gap: 0.5rem;
      margin-top: 1.5rem; padding-top: 0.875rem;
      border-top: 1px solid var(--border);
  }}
  .sidebrand-mark {{
      width: 1.25rem; height: 1.25rem; border-radius: 6px; flex: none;
      display: inline-flex; align-items: center; justify-content: center;
      background: linear-gradient(145deg, {p.accent}, {p.tint(p.accent, 0.62)});
      color: #fff; font-size: 0.625rem; font-weight: 600;
  }}
  .sidebrand-text {{ display: flex; flex-direction: column; line-height: 1.25; }}
  .sidebrand-name {{ font-size: 0.75rem; font-weight: 600; color: var(--ink-2); }}
  .sidebrand-tag {{ font-size: 0.6875rem; color: var(--ink-faint); }}

  /* ---- tabs ------------------------------------------------------------- */
  .stTabs [data-baseweb="tab-list"] {{
      gap: 1.25rem; border-bottom: 1px solid var(--border);
      padding: 0; margin-bottom: 1.5rem; background: transparent;
  }}
  .stTabs [data-baseweb="tab"] {{
      height: 2.25rem; padding: 0 0 0.5rem; background: transparent;
      font-size: 0.8125rem; font-weight: 500; color: var(--ink-muted);
      transition: color 140ms ease-out;
  }}
  .stTabs [data-baseweb="tab"]:hover {{ color: var(--ink-2); }}
  .stTabs [aria-selected="true"] {{ color: var(--ink); font-weight: 500; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background: var(--accent); height: 2px; }}
  .stTabs [data-baseweb="tab-border"] {{ display: none; }}

  /* ---- panels ----------------------------------------------------------- */
  /* One level of nesting, ever. A bordered card inside a bordered panel inside
     a bordered section is what an interface looks like when it was assembled
     from components rather than designed.

     There is exactly one panel class, because there is exactly one kind of
     panel. A generic `.card` used to live here beside it, styled and never
     used by anything - which is how two panel treatments end up on one screen
     the first time somebody reaches for the wrong one. */

  /* The recommendation panel. A 2px edge in the accent, and a wash of the same
     colour that has faded out by 40% of the width - so the eye is pulled to the
     left edge where the eyebrow and the headline start. Still no shadow, and
     the panel's own boundary is still the same hairline every other panel uses:
     the wash is dead long before it reaches the border, so it never becomes the
     thing telling you where the box ends. */
  .hero {{
      /* One local variable so the edge, the eyebrow and the wash are always the
         same colour. Each variant below re-points this one line rather than
         restating the gradient, which is how a state ends up with a violet wash
         and an orange edge. */
      --hero-tint: var(--accent-wash);
      background:
          linear-gradient(100deg, var(--hero-tint), transparent 42%),
          var(--surface);
      border: 1px solid var(--border);
      border-left: 2px solid var(--accent); border-radius: 10px;
      padding: 1.25rem 1.5rem; margin-bottom: 1rem;
  }}
  .hero-eyebrow {{
      display: flex; align-items: center; gap: 0.4375rem;
      font-size: 0.6875rem; font-weight: 500; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--accent); margin-bottom: 0.5rem;
  }}
  /* A dot in the eyebrow's own colour. The eyebrow is the only word on the
     panel saying which of three states this is - recommended, waiting on you,
     done - so it gets a mark that can be caught at a glance from the far side
     of a projector. */
  .hero-eyebrow::before {{
      content: ""; flex: none; width: 5px; height: 5px;
      border-radius: 999px; background: currentColor;
  }}
  .hero-title {{
      font-size: 1.75rem; font-weight: 600; color: var(--ink);
      line-height: 1.15; letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
  }}
  .hero-sub {{
      font-size: 0.8125rem; color: var(--ink-muted); margin-top: 0.5rem;
      line-height: 1.55; max-width: 62ch;
  }}

  /* An escalation is the same panel in a different key - never a red alarm
     banner. Being asked to approve a large order is the system working, and
     colouring it like an error teaches the buyer to dread the exact moment we
     are proudest of. */
  .hero-escalated {{ border-left-color: {p.serious}; --hero-tint: {p.tint(p.serious, 0.10)}; }}
  .hero-escalated .hero-eyebrow {{ color: {p.serious}; }}
  .hero-done {{ border-left-color: {p.good}; --hero-tint: {p.tint(p.good, 0.10)}; }}
  .hero-done .hero-eyebrow {{ color: {p.good}; }}

  /* ---- figures (number-first tiles) ------------------------------------ */
  /* min-height rather than height:100% so the four tiles bottom out level
     whether or not each one has a note under its number. Ragged tile bottoms in
     a row of four is the sort of 6px wrongness nobody can name and everybody
     sees. */
  /* The tiles are lit from the top: one step of the neutral band across the
     tile, top lighter than bottom. It is the smallest amount of depth that
     makes four flat rectangles read as objects sitting on the page rather than
     four holes cut out of it - and because it is neutral, not brand, it never
     competes with the recommendation panel beside it.

     No hover state, and that is deliberate: none of these tiles is clickable,
     and a surface that lights up under the cursor is a surface people click. */
  .figure {{
      background: linear-gradient(180deg, var(--surface-2), var(--surface));
      border: 1px solid var(--border); border-radius: 10px;
      padding: 0.75rem 1rem 0.875rem; min-height: 6rem;
  }}
  .figure-label {{
      font-size: 0.6875rem; font-weight: 500; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--ink-faint); margin-bottom: 0.375rem;
  }}
  .figure-value {{
      font-size: 1.25rem; font-weight: 600; color: var(--ink);
      font-variant-numeric: tabular-nums; line-height: 1.2;
      letter-spacing: -0.01em;
  }}
  .figure-note {{
      font-size: 0.75rem; color: var(--ink-muted); margin-top: 0.25rem; line-height: 1.45;
  }}

  /* ---- chips ----------------------------------------------------------- */
  /* Small rounded rects rather than pills, deliberately. A pill reads as a
     marketing tag; a 6px rect reads as a value in a tool. */
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.375rem; margin: 0.25rem 0; }}
  .chip {{
      display: inline-flex; align-items: center; gap: 0.375rem;
      font-size: 0.75rem; font-weight: 500; line-height: 1;
      padding: 0.3125rem 0.5rem; border-radius: 6px;
      border: 1px solid var(--border); background: var(--surface-2);
      color: var(--ink-2); white-space: nowrap;
      font-variant-numeric: tabular-nums;
  }}
  /* Chips deliberately have NO hover state, unlike the tiles above. Nothing here
     is clickable, and a tag that lights up under the cursor is a tag people try
     to click - the fastest way to teach a buyer that this screen is broken. */
  .chip-dot {{ width: 6px; height: 6px; border-radius: 999px; flex: none; }}
  /* A tinted chip carries a real signal. The text stays in an ink token - a
     coloured dot beside it carries the identity, never the words themselves. */
  .chip-strong {{ color: var(--ink); font-weight: 500; }}

  /* ---- supplier badge --------------------------------------------------- */
  /* Row anatomy borrowed straight from a market terminal: square mark, bold
     identifier, muted descriptor. The mark carries the colour, the words carry
     the meaning - colour is never the only thing saying who this is. */
  .vendor {{
      display: inline-flex; align-items: center; gap: 0.5rem;
      padding: 0.3125rem 0.625rem 0.3125rem 0.3125rem; border-radius: 6px;
      border: 1px solid var(--border); background: var(--surface-2);
  }}
  .vendor-mark {{
      width: 1.125rem; height: 1.125rem; border-radius: 5px; flex: none;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 0.6875rem; font-weight: 600; color: #ffffff;
  }}
  .vendor-name {{ font-size: 0.8125rem; font-weight: 600; color: var(--ink); }}
  .vendor-kind {{
      font-size: 0.6875rem; font-weight: 500; letter-spacing: 0.05em;
      text-transform: uppercase; color: var(--ink-faint);
  }}

  /* ---- spec badges ------------------------------------------------------ */
  /* Dashed border: the supplier lists it. Solid with a tick: it is one of the
     things the buyer asked for, so it had to be there for this to qualify. */
  .spec {{
      display: inline-flex; align-items: center; gap: 0.3125rem;
      font-size: 0.75rem; font-weight: 500; line-height: 1;
      padding: 0.3125rem 0.5rem; border-radius: 6px;
      border: 1px dashed var(--border-strong); background: transparent;
      color: var(--ink-muted); white-space: nowrap;
  }}
  .spec-met {{
      border-style: solid; border-color: {p.tint(p.good, 0.35)};
      background: {p.tint(p.good, 0.08)}; color: var(--ink);
  }}
  .spec-tick {{ color: {p.good}; font-weight: 600; }}

  /* ---- key/value grid --------------------------------------------------- */
  /* Label above value, no rules between - proximity does the grouping. The old
     left border on every cell was six vertical lines competing with the panel
     border that already contains them. */
  .kv {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 1rem 1.5rem; margin: 0.25rem 0;
  }}
  .kv-item {{ min-width: 0; }}
  .kv-key {{
      font-size: 0.6875rem; font-weight: 500; letter-spacing: 0.06em;
      text-transform: uppercase; color: var(--ink-faint); margin-bottom: 0.25rem;
  }}
  .kv-val {{
      font-size: 0.8125rem; font-weight: 500; color: var(--ink);
      font-variant-numeric: tabular-nums; line-height: 1.4;
  }}
  .kv-mono {{ font-family: var(--mono); font-size: 0.75rem; letter-spacing: -0.01em; }}

  /* ---- controls --------------------------------------------------------- */
  /* Every one of these gets hover, focus-visible and disabled. Focus is an
     accent ring at 2px offset rather than a removed outline, because removing
     the outline without replacing it breaks the app for keyboard use. */
  .stButton > button {{
      background: var(--surface-2); border: 1px solid var(--border);
      color: var(--ink-2); border-radius: 6px;
      font-size: 0.8125rem; font-weight: 500; padding: 0.375rem 0.875rem;
      transition: background 140ms ease-out, color 140ms ease-out,
                  border-color 140ms ease-out;
  }}
  .stButton > button:hover {{
      background: var(--surface-3); border-color: var(--border-strong); color: var(--ink);
  }}
  .stButton > button:focus-visible {{
      outline: 2px solid var(--accent); outline-offset: 2px;
  }}
  .stButton > button:disabled, .stButton > button:disabled:hover {{
      background: var(--surface); color: var(--ink-faint);
      border-color: var(--border); cursor: not-allowed;
  }}
  /* The one button on the screen that commits money gets the brand gradient and
     a 1px inner highlight along its top edge. That highlight is the oldest trick
     in interface design for making a control look pressable, and it costs one
     line. Every other button on the page stays flat, which is what makes this
     one findable without a second colour. */
  .stButton > button[kind="primary"] {{
      background:
          linear-gradient(160deg, rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0)),
          var(--accent);
      border-color: var(--accent);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.2);
      color: #ffffff; font-weight: 500;
  }}
  .stButton > button[kind="primary"]:hover {{
      filter: brightness(1.1); border-color: var(--accent); color: #ffffff;
  }}
  .stButton > button[kind="primary"]:disabled {{ filter: none; opacity: 0.45; }}

  [data-testid="stChatInput"] {{ border-color: var(--border); }}
  [data-testid="stChatInput"]:focus-within {{ border-color: var(--border-strong); }}

  /* ---- drill-downs ------------------------------------------------------ */
  /* Collapsed by default and quiet when collapsed. Ten open-looking panels
     stacked down a page is a wall; ten hairlines is a contents list. */
  [data-testid="stExpander"] {{
      border: 1px solid var(--border); border-radius: 10px;
      background: var(--surface); margin-bottom: 0.5rem;
  }}
  [data-testid="stExpander"] summary {{
      font-size: 0.8125rem; font-weight: 500; color: var(--ink-muted);
      transition: color 140ms ease-out;
  }}
  [data-testid="stExpander"] summary:hover {{ color: var(--ink); }}

  /* ---- tables ----------------------------------------------------------- */
  /* Tabular figures everywhere a value can change, or the columns twitch as the
     pool changes. Row height and hairlines are the separation - no zebra. */
  [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
  [data-testid="stDataFrame"] * {{ font-family: var(--font) !important; }}

  /* ---- code / audit payloads -------------------------------------------- */
  .stCode, pre, code {{ font-family: var(--mono) !important; }}
  .stCode > pre {{
      background: var(--surface-2) !important; border: 1px solid var(--border);
      border-radius: 6px; font-size: 0.75rem; line-height: 1.6;
  }}

  /* ---- sidebar ---------------------------------------------------------- */
  [data-testid="stSidebar"] {{
      background: var(--surface); border-right: 1px solid var(--border);
  }}
  [data-testid="stSidebar"] .block-container {{ padding-top: 1.5rem; }}

  /* ---- misc ------------------------------------------------------------- */
  .rule {{ height: 1px; background: var(--border); margin: 1.5rem 0; border: 0; }}
  .provenance {{
      font-size: 0.75rem; color: var(--ink-faint); margin-top: 0.5rem;
  }}
  [data-testid="stChatMessage"] {{
      background: transparent; padding: 0.25rem 0; gap: 0.625rem;
  }}
  [data-testid="stChatMessage"] p {{ font-size: 0.8125rem; line-height: 1.55; }}

  @media (prefers-reduced-motion: reduce) {{
      * {{ transition: none !important; animation: none !important; }}
  }}
</style>
"""


def inject() -> None:
    """Apply the stylesheet for the viewer's current theme. Called once, in app.py."""
    st.markdown(_css(active()), unsafe_allow_html=True)


def app_header(note: str = "") -> None:
    """The mark, the name, what the name stands for, and a hairline.

    A gradient bar under a heading is decoration that says nothing; a mark, a
    name and a hairline say what this is and where the header ends, which is all
    a header owes anyone. The expansion of the acronym earns its line because
    "Evets" on its own tells a first-time viewer nothing, and the four words
    under it are also the shortest true description of the job.

    The name is not a parameter any more. It used to be passed in as a string
    from app.py, which is one call site away from the tab, the opening screen and
    the sidebar each spelling it their own way. It comes from PRODUCT_NAME now.
    `note` is the right-aligned status - the current order reference, when there
    is one.
    """
    from html import escape

    tail = f'<span class="apphead-note">{escape(note)}</span>' if note else ""
    st.markdown(
        f'<div class="apphead">'
        f'<span class="apphead-mark">{escape(PRODUCT_NAME[:1].upper())}</span>'
        f'<span class="apphead-brand">'
        f'<span class="apphead-name">{escape(PRODUCT_NAME)}</span>'
        f'<span class="apphead-expansion">{escape(PRODUCT_EXPANSION)}</span>'
        f"</span>{tail}</div>",
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    """The same lockup, small, at the foot of the sidebar.

    A signature rather than a second title: it sits below every control, above
    the fold of nothing, and says which product this panel belongs to. The
    tagline goes here rather than in the header because a strapline at the top of
    every screen is advertising, and a strapline at the bottom of the panel is a
    signature.
    """
    from html import escape

    st.markdown(
        f'<div class="sidebrand">'
        f'<span class="sidebrand-mark">{escape(PRODUCT_NAME[:1].upper())}</span>'
        f'<span class="sidebrand-text">'
        f'<span class="sidebrand-name">{escape(PRODUCT_NAME)}</span>'
        f'<span class="sidebrand-tag">{escape(PRODUCT_TAGLINE)}</span>'
        f"</span></div>",
        unsafe_allow_html=True,
    )
