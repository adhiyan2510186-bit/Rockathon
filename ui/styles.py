"""The stylesheet. Every rule for every surface, in one string.

WHY THIS FILE EXISTS
--------------------
It used to be four hundred lines of CSS living inside ui/theme.py, wedged between
the palette that feeds it and the header markup that uses it. That works right up
until the sheet grows, and then one file owns three unrelated jobs and nobody can
find anything in it.

So the split is by JOB, not by size:

  ui/theme.py    what the colours ARE - the palette, the name, the vendor ramp,
                 the urgency table. Things other modules ask questions of.
  ui/styles.py   how those colours are PAINTED. One function, one string, no
                 decisions. Nothing outside this file writes CSS.

The import only goes one way. This file names `Palette` for typing and never
imports theme at runtime, so there is no cycle to trip over.

WHAT THE SHEET IS WRITTEN AGAINST
---------------------------------
The six-size type scale and a 4px spacing unit, both defined in ui/theme.py. Two
habits are worth naming because they are what stop a dark UI looking assembled
rather than designed:

  * one radius per element class, held everywhere - a pill on anything that is a
    tag, 10px on anything that is a panel. Mixed radii on adjacent elements is
    the loudest sloppiness tell there is.
  * every interactive thing has hover, :focus-visible and disabled. A missing
    hover state is the fastest way for an interface to feel dead.

ONE PLACE MOTION IS ALLOWED TO BE SWITCHED OFF
----------------------------------------------
The `prefers-reduced-motion` block at the very bottom kills every transition and
every animation in the sheet with two declarations. It is the last rule on
purpose: anything added below it would escape it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.theme import Palette


def css(p: "Palette") -> str:
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
