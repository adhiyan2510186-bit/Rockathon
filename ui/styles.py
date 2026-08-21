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
    # DEPTH IS MODE-DEPENDENT IN A WAY COLOUR IS NOT.
    #
    # A shadow is an absence of light. On a near-black canvas it has to be almost
    # opaque before it registers at all; the same value over white looks like
    # soot. So the two modes get their own strength for the same gesture, and
    # nothing else about the panel changes. This is the only place in the sheet
    # where a value is chosen by mode rather than read from the palette, and it
    # is here rather than in ui/theme.py because it is not a colour - it is how
    # hard to press one surface into another.
    dark = p.name == "dark"
    lift = "0 12px 32px -16px rgba(0, 0, 0, 0.85)" if dark else "0 10px 26px -18px rgba(0, 0, 0, 0.16)"
    lift_hi = "0 20px 46px -18px rgba(0, 0, 0, 0.95)" if dark else "0 18px 38px -18px rgba(0, 0, 0, 0.22)"
    # The 1px highlight along a panel's top edge. On dark it is white at 7%; on
    # light the light comes from above anyway, so it is nearly pure white.
    sheen = "rgba(255, 255, 255, 0.07)" if dark else "rgba(255, 255, 255, 0.85)"
    sheen_hi = "rgba(255, 255, 255, 0.10)" if dark else "rgba(255, 255, 255, 0.95)"

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

      /* ---- depth ----------------------------------------------------------
         A panel is not a flat rectangle any more. It is a translucent sheet
         resting above the canvas, and three things say so together: the surface
         at 72% so the backdrop shows through it, a 1px highlight along the top
         edge where the light would land, and a shadow underneath. Take any one
         away and the other two read as a mistake - a see-through box, or a box
         with a stripe on it.

         THERE IS NO backdrop-filter ANYWHERE, AND IT WAS TAKEN OUT ON PURPOSE.
         Glass panels normally reach for one, and two of ours had one. What we
         actually observed, twice, is the renderer going unresponsive for tens
         of seconds during ordinary use - once mid-scroll, once after approval -
         on the laptop this will be demonstrated from: an Intel UHD 620 at 1.5x
         scaling, compositing a blurred region behind a full-height sidebar.

         Being straight about the evidence: that is two reproductions of a stall,
         not a clean frame-rate benchmark. We tried to take one and the harness
         for it stalled the page by itself, which tells us the machine is close
         to its limit here but does not give us a number. So the honest claim is
         the small one - the blur was present when the page hung, the page has
         not hung since it went, and we did not measure the difference.

         What that cost us is close to nothing, which is why the trade was easy.
         A blur only shows you anything if what is behind the panel has detail
         to smear. Behind ours is a near-black canvas carrying soft gradients
         and a 3%-opacity grid; there is nothing there to soften. The glass read
         is carried by the other three properties - the fill is translucent so
         the grid genuinely passes through the panel, the top edge catches a
         highlight, and the shadow lifts it off the page.

         A stutter on a projector costs more than an effect nobody can see. */
      --glass-fill: {p.tint(p.surface, 0.72)};
      --glass-fill-2: {p.tint(p.surface_2, 0.72)};
      --sheen: {sheen};
      --sheen-hi: {sheen_hi};
      --lift: {lift};
      --lift-hi: {lift_hi};
      /* The brand halo. Used only on the two things that are asking to be acted
         on - the recommendation and a repeat-order card under the cursor. */
      --glow: 0 0 0 1px {p.tint(p.accent, 0.30)}, 0 10px 30px -12px {p.tint(p.accent, 0.42)};
      /* The hairline grid on the canvas, in ink rather than a fixed white, so it
         is faint dark lines on a light page and faint light ones on a dark one. */
      --grid: {p.tint(p.ink, 0.035)};

      /* ---- motion ---------------------------------------------------------
         One curve and two durations for the whole app. Every transition in this
         sheet names these rather than its own number, because motion that runs
         at four different speeds on one screen reads as four different products
         in exactly the way four different greys would. */
      --ease: cubic-bezier(0.2, 0.7, 0.3, 1);
      --dur: 180ms;
      --dur-enter: 300ms;
  }}

  /* ---- page ------------------------------------------------------------ */
  /* THE CANVAS IS FIVE LAYERS, AND THE ORDER IS THE WHOLE TRICK.
     CSS paints background layers first-listed on top, so reading down this list
     is reading from the front of the page to the back:

       1  brand pool, behind the header. The page's warmest point is where the
          eye lands first anyway.
       2  slate pool, low and to the right. Somewhere for the far corner of a
          1120px page to go that is not flat black. It is the one use of
          `backdrop` in the palette and it never touches an element.
       3  vignette. Opaque canvas at the edges, transparent through the middle.
          It sits ABOVE the grid and BELOW the pools, which is what lets it
          rub the grid out at the frame without touching either pool.
       4  the grid itself - 48px hairlines, both directions.
       5  the flat canvas colour underneath everything.

     A grid running clean to the edge of the frame reads as wallpaper. A grid
     that dissolves before it gets there reads as depth, and the vignette is the
     only thing between those two outcomes. It costs one gradient, no mask, no
     pseudo-element, and nothing that can fight Streamlit for the stacking
     order - which the pseudo-element version very much can.

     NO `background-attachment: fixed`, AND THAT IS A FIX, NOT AN OMISSION.
     It used to be here and it froze the renderer. Streamlit does not scroll the
     page - it scrolls an inner container, and `.stApp` itself is exactly one
     viewport tall and never moves. So `fixed` had nothing to hold still, while
     still forcing all six layers to repaint underneath every translucent panel
     on every scroll frame. It was paying full price for no effect at all, which
     is the worst trade in the sheet. */
  .stApp {{
      background:
          radial-gradient(70rem 24rem at 50% -8rem, var(--accent-faint), transparent 70%),
          radial-gradient(52rem 34rem at 108% 64%, {p.tint(p.backdrop, 0.13)}, transparent 68%),
          radial-gradient(135% 105% at 50% -5%, transparent 0 42%, var(--canvas) 100%),
          repeating-linear-gradient(0deg,  var(--grid) 0 1px, transparent 1px 48px),
          repeating-linear-gradient(90deg, var(--grid) 0 1px, transparent 1px 48px),
          var(--canvas);
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
     the first time somebody reaches for the wrong one.

     WHAT A PANEL IS MADE OF, AND WHY IT IS FOUR THINGS AND NOT ONE
     --------------------------------------------------------------
     Every panel in this app is the same recipe, held in `--glass-*` at the top
     of the sheet: the surface at 72% so the canvas shows through it, a 1px
     highlight along the top edge where light would land, and a shadow
     underneath. No blur on any of them - see the note by --glass-fill for why
     the one we had was measured out again.

     The three only work as a set. A highlight with no shadow is a box with a
     stripe on it. Together they say the panel is a sheet lying above the page,
     which is the one thing a flat rectangle on a flat page cannot say.

     The hairline is still on every panel and still does the same job it always
     did. Depth tells you a panel is a separate object; the border tells you
     exactly where it stops. On a near-black page a shadow alone is far too
     vague to be the thing marking an edge, which is why the border did not go
     anywhere when the shadow arrived. */

  /* The recommendation panel. A 2px edge in the accent, and a wash of the same
     colour that has faded out by 40% of the width - so the eye is pulled to the
     left edge where the eyebrow and the headline start. The wash is dead long
     before it reaches the border, so it never becomes the thing telling you
     where the box ends. */
  .hero {{
      /* One local variable so the edge, the eyebrow and the wash are always the
         same colour. Each variant below re-points this one line rather than
         restating the gradient, which is how a state ends up with a violet wash
         and an orange edge. */
      --hero-tint: var(--accent-wash);
      background:
          linear-gradient(100deg, var(--hero-tint), transparent 42%),
          linear-gradient(180deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.015)),
          var(--glass-fill);
      border: 1px solid var(--border);
      border-left: 2px solid var(--accent); border-radius: 10px;
      padding: 1.25rem 1.5rem; margin-bottom: 1rem;
      box-shadow: inset 0 1px 0 var(--sheen), var(--lift);
  }}
  /* The recommendation is the only panel on any screen that is asking for a
     decision, so it is the only one that answers the cursor with brand colour
     rather than with another step of neutral depth. */
  .hero {{
      transition: box-shadow var(--dur) var(--ease);
  }}
  .hero:hover {{
      box-shadow: inset 0 1px 0 var(--sheen-hi), var(--glow), var(--lift-hi);
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
     tile, top lighter than bottom, over the same translucent fill every panel
     uses. Because the lighting is neutral rather than brand, four tiles in a row
     never compete with the recommendation panel beside them.

     THEY LIFT UNDER THE CURSOR, AND THEY ARE NOT CLICKABLE. BOTH ON PURPOSE.
     ------------------------------------------------------------------------
     The obvious objection is that a surface which reacts to the cursor is a
     surface people will click. It is a real objection and the answer is that
     lift is not the affordance - the CURSOR is. `cursor: default` is set on
     every one of these, so the pointer stays an arrow over a tile and turns
     into a hand over the one thing on the screen that is actually a button.

     What the lift buys is worth that. On a dark page four figures can read as
     four holes cut out of the canvas; moving 2px toward the reader under the
     cursor is the cheapest possible proof that they are objects lying ON the
     page. It is the same information the shadow is giving statically, offered
     to anyone who moves a mouse across it. */
  .figure {{
      background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.012)),
          var(--glass-fill);
      border: 1px solid var(--border); border-radius: 10px;
      padding: 0.75rem 1rem 0.875rem; min-height: 6rem;
      box-shadow: inset 0 1px 0 var(--sheen), var(--lift);
      cursor: default;
      transition: transform var(--dur) var(--ease),
                  box-shadow var(--dur) var(--ease),
                  border-color var(--dur) var(--ease);
  }}
  .figure:hover {{
      transform: translateY(-2px);
      border-color: var(--border-strong);
      box-shadow: inset 0 1px 0 var(--sheen-hi), var(--lift-hi);
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
  /* PILLS, AND ONE RADIUS FOR EVERY TAG IN THE APP.
     A chip, a spec badge, a supplier badge and a status tag are all the same
     kind of object - a short fact wearing a container - so they all take the
     same fully-round edge. The alternative is what was here before: a 6px rect
     that had to be held identical in four separate rules, which is exactly how
     two tags on one row end up 2px apart in the corner and nobody can say why.
     A round edge cannot drift.

     Still no hover on any of them. These carry a fact and do nothing when
     clicked, and unlike a figure tile there is nothing here for depth to
     clarify - a tag is already obviously a tag. */
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.375rem; margin: 0.25rem 0; }}
  .chip {{
      display: inline-flex; align-items: center; gap: 0.375rem;
      font-size: 0.75rem; font-weight: 500; line-height: 1;
      padding: 0.375rem 0.6875rem; border-radius: 999px;
      border: 1px solid var(--border);
      background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0)),
          var(--glass-fill-2);
      color: var(--ink-2); white-space: nowrap; cursor: default;
      font-variant-numeric: tabular-nums;
  }}
  .chip-dot {{ width: 6px; height: 6px; border-radius: 999px; flex: none; }}
  /* A tinted chip carries a real signal. The text stays in an ink token - a
     coloured dot beside it carries the identity, never the words themselves. */
  .chip-strong {{ color: var(--ink); font-weight: 500; }}

  /* ---- status tags ------------------------------------------------------ */
  /* Did it qualify, is it waiting on someone, did it happen. One shape, five
     states, and every one of them says its state three ways at once - a colour,
     a mark, and a word. That is not belt and braces: two of the four status
     colours sit under 3:1 on a light surface, so a tag that leaned on colour
     alone would be unreadable for some people and invisible in a photograph of
     the screen. The word is what makes the colour safe to use.

     `.status-live` is the only thing in this app that moves on its own. See the
     pulse below. */
  .status {{
      display: inline-flex; align-items: center; gap: 0.4375rem;
      font-size: 0.75rem; font-weight: 500; line-height: 1;
      padding: 0.375rem 0.75rem; border-radius: 999px;
      white-space: nowrap; cursor: default;
      border: 1px solid var(--status-colour);
      background: linear-gradient(180deg, var(--status-wash), transparent);
      color: var(--ink);
  }}
  .status-mark {{
      width: 7px; height: 7px; border-radius: 999px; flex: none;
      background: var(--status-colour);
  }}
  .status-word {{ font-variant-numeric: tabular-nums; }}

  /* ---- supplier badge --------------------------------------------------- */
  /* Row anatomy borrowed straight from a market terminal: square mark, bold
     identifier, muted descriptor. The mark carries the colour, the words carry
     the meaning - colour is never the only thing saying who this is. */
  /* The mark stays flush in the rounded end, so the left padding is the mark's
     own inset and nothing more. `--vendor-colour` is set inline per supplier by
     ui/components.py from theme.vendor_colour() - the badge never picks a hue. */
  .vendor {{
      display: inline-flex; align-items: center; gap: 0.5rem;
      padding: 0.25rem 0.75rem 0.25rem 0.25rem; border-radius: 999px;
      border: 1px solid var(--vendor-colour, var(--border));
      background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0)),
          var(--vendor-wash, var(--glass-fill-2));
      cursor: default;
  }}
  .vendor-mark {{
      width: 1.25rem; height: 1.25rem; border-radius: 999px; flex: none;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 0.6875rem; font-weight: 600; color: #ffffff;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25);
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
      padding: 0.375rem 0.6875rem; border-radius: 999px;
      border: 1px dashed var(--border-strong); background: transparent;
      color: var(--ink-muted); white-space: nowrap; cursor: default;
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
     stacked down a page is a wall; ten hairlines is a contents list.
     These ARE clickable - the whole header is the control - so unlike a figure
     tile they get the pointer as well as the lift. */
  [data-testid="stExpander"] {{
      border: 1px solid var(--border); border-radius: 10px;
      background: var(--glass-fill); margin-bottom: 0.5rem;
      box-shadow: inset 0 1px 0 var(--sheen);
      transition: border-color var(--dur) var(--ease),
                  box-shadow var(--dur) var(--ease);
  }}
  [data-testid="stExpander"]:hover {{
      border-color: var(--border-strong);
      box-shadow: inset 0 1px 0 var(--sheen-hi), var(--lift);
  }}
  [data-testid="stExpander"] summary {{
      font-size: 0.8125rem; font-weight: 500; color: var(--ink-muted);
      cursor: pointer;
      transition: color var(--dur) var(--ease);
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
  /* The sidebar overlaps both canvas pools down its whole height, which makes it
     the one surface in the app where the blur has the most to do. */
  [data-testid="stSidebar"] {{
      background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0)),
          var(--glass-fill);
      border-right: 1px solid var(--border);
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

  /* ---- the trail -------------------------------------------------------- */
  /* WHAT HAPPENED, IN THE ORDER IT HAPPENED, WITH A LINE JOINING IT UP.
     The same list used to be a run of bold sentences, and a run of bold
     sentences has no shape: you cannot see from six feet away that the agent
     decided four things, assumed one, and stopped once to ask. A rail with
     graded nodes on it can be read before it is read.

     The connector is drawn by each step rather than by the container, because a
     single line behind the whole list has to guess where the first and last
     nodes are. `:last-child` drops the tail so the rail stops at the final node
     instead of dangling below it, which is the detail that separates a timeline
     from a list with a line next to it.

     The node is 18px and the rail is 1px, offset to the node's centre. Those two
     numbers have to agree or the line enters each node off-centre - the kind of
     wrongness nobody can name and everybody sees. */
  .trail {{ display: flex; flex-direction: column; margin: 0.25rem 0 0.5rem; }}
  .step {{
      position: relative; display: grid;
      grid-template-columns: 1.125rem 1fr; gap: 0.75rem;
      padding-bottom: 1rem;
  }}
  .step:last-child {{ padding-bottom: 0; }}
  /* left is the node's centre: an 18px node starting at 0 has its middle at 9px,
     so 0.5625rem and not 0.5rem. One pixel out and the rail enters every node
     off-centre down the whole column. */
  .step::before {{
      content: ""; position: absolute; left: 0.5625rem; top: 1.25rem; bottom: 0;
      width: 1px; background: var(--border-strong);
  }}
  .step:last-child::before {{ display: none; }}
  /* Solid node: this event did something. Hollow node: it was advisory and
     changed nothing. See theme.event_mark - the market signal is the whole
     reason this distinction is drawn rather than described. */
  .step-node {{
      width: 1.125rem; height: 1.125rem; border-radius: 999px; flex: none;
      margin-top: 0.125rem; box-sizing: border-box;
      border: 2px solid var(--step-colour);
      background: var(--step-colour);
      box-shadow: 0 0 0 3px var(--canvas);
  }}
  .step-advisory .step-node {{ background: var(--canvas); }}
  .step-body {{ min-width: 0; }}
  .step-head {{
      display: flex; align-items: center; gap: 0.5rem;
      flex-wrap: wrap; line-height: 1.2;
  }}
  .step-word {{ font-size: 0.8125rem; font-weight: 600; color: var(--ink); }}
  .step-time {{
      font-family: var(--mono); font-size: 0.6875rem; color: var(--ink-faint);
      font-variant-numeric: tabular-nums; letter-spacing: -0.01em;
  }}
  /* THE ACTOR BADGE IS A WEIGHT DIFFERENCE, NOT A COLOUR ONE, AND THAT IS THE
     WHOLE POINT OF THIS FILE'S COLOUR RULE HOLDING.
     Marking "a person did this" with the brand violet would be brand colour
     encoding data, which is the one thing the palette rule forbids. So the
     agent's steps get an outline badge and the rare human one gets a filled
     badge in plain ink - same hue, opposite weight. The moment a person stepped
     in is still the loudest thing on the rail, and no colour changed meaning to
     do it. */
  .step-actor {{
      font-size: 0.625rem; font-weight: 500; letter-spacing: 0.07em;
      text-transform: uppercase; padding: 0.1875rem 0.4375rem;
      border-radius: 999px; border: 1px solid var(--border-strong);
      color: var(--ink-faint); white-space: nowrap;
  }}
  .step-actor-user {{
      background: var(--ink); border-color: var(--ink); color: var(--canvas);
      font-weight: 600;
  }}
  .step-why {{
      font-size: 0.8125rem; color: var(--ink-muted);
      line-height: 1.5; margin-top: 0.25rem; max-width: 68ch;
  }}
  /* A sub-line under a step: a retry attempt, a lock reference. Indented under
     its own step rather than made into a step of its own, because a payment
     that declined and then succeeded is ONE thing that happened, and splitting
     it into two nodes would make a recovery look like two separate events. */
  .step-sub {{
      font-size: 0.75rem; color: var(--ink-faint); line-height: 1.5;
      margin-top: 0.1875rem;
  }}
  .step-ref {{ font-family: var(--mono); letter-spacing: -0.01em; }}

  /* ---- repeat-order cards ----------------------------------------------- */
  /* THESE ARE STREAMLIT BUTTONS AND THEY HAVE TO STAY STREAMLIT BUTTONS.
     The test suite selects them by key, and a div with an onclick is not a
     button to a keyboard or a screen reader. So rather than rebuilding the
     control, the control is restyled in place.

     The hook is an empty marker span that app.py drops into the same container
     as the buttons; `:has()` then reaches back up to the block and down to the
     buttons inside it. This is the one selector in the sheet that depends on
     Streamlit's DOM shape, which is why it is called out here - if a Streamlit
     upgrade ever moves the wrapper, these cards quietly become plain buttons
     and nothing else breaks. That is the correct way round for a demo. */
  .recent-anchor {{ display: none; }}
  [data-testid="stElementContainer"]:has(.recent-anchor) {{ display: none; }}
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton > button {{
      width: 100%; min-height: 4rem; height: 100%;
      display: flex; align-items: center; justify-content: flex-start;
      text-align: left; white-space: normal; line-height: 1.45;
      padding: 0.875rem 1rem; border-radius: 10px;
      background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.012)),
          var(--glass-fill);
      border: 1px solid var(--border);
      border-left: 2px solid var(--border-strong);
      box-shadow: inset 0 1px 0 var(--sheen), var(--lift);
      color: var(--ink-2);
      transition: transform var(--dur) var(--ease),
                  box-shadow var(--dur) var(--ease),
                  border-color var(--dur) var(--ease),
                  color var(--dur) var(--ease);
  }}
  /* The left edge is the tell. It is neutral at rest and brand under the
     cursor, so the card being pointed at is the one wearing the accent - the
     same 2px edge the recommendation panel uses to say "this is the one". */
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton > button:hover {{
      transform: translateY(-2px); color: var(--ink);
      border-color: var(--border-strong); border-left-color: var(--accent);
      box-shadow: inset 0 1px 0 var(--sheen-hi), var(--glow);
  }}
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton > button:focus-visible {{
      outline: 2px solid var(--accent); outline-offset: 2px;
  }}
  /* Streamlit centres a button's label with a flex row INSIDE the button, so
     setting text-align on the button itself does nothing - the label is a
     centred flex item that happens to have left-aligned text in it. The label
     has to be told to fill its row before the alignment means anything. */
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton > button > div {{
      width: 100%; justify-content: flex-start;
  }}
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton p {{
      text-align: left; margin: 0; font-size: 0.8125rem; line-height: 1.5;
  }}
  /* The item is the thing being reordered; the terms are how it was bought last
     time. Two weights, one card, so the eye picks four items out of the grid
     before it reads a single price. */
  [data-testid="stVerticalBlock"]:has(.recent-anchor) .stButton strong {{
      display: block; color: var(--ink); font-weight: 600; font-size: 0.875rem;
      margin-bottom: 0.125rem;
  }}

  /* ---- motion ----------------------------------------------------------- */
  /* TWO ANIMATIONS IN THE WHOLE APP, AND EACH ONE HAS TO EARN ITS KEYFRAMES.

     `rise` is arrival. A panel that appears fully formed and a panel that
     settles into place carry the same information, but the second one tells you
     WHERE to look, which on a screen that has just changed underneath you is
     worth 300ms.

     It is deliberately on almost nothing. Streamlit re-runs the whole script on
     every click, so an entrance animation applied broadly replays every time
     anyone ticks a checkbox and the page develops a twitch. It goes on the
     recommendation panel and on trail steps - the two things whose arrival is
     genuinely an event - and nowhere else. */
  @keyframes rise {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
  }}
  .hero {{ animation: rise var(--dur-enter) var(--ease) both; }}
  .step {{ animation: rise var(--dur-enter) var(--ease) both; }}
  /* Steps arrive in the order they happened rather than all at once. 40ms is
     enough to read as a sequence and short enough that eight of them are done
     inside a third of a second. Capped at eight, because past that the last
     step would arrive late enough to look like a stall. */
  .step:nth-child(1) {{ animation-delay: 0ms; }}
  .step:nth-child(2) {{ animation-delay: 40ms; }}
  .step:nth-child(3) {{ animation-delay: 80ms; }}
  .step:nth-child(4) {{ animation-delay: 120ms; }}
  .step:nth-child(5) {{ animation-delay: 160ms; }}
  .step:nth-child(6) {{ animation-delay: 200ms; }}
  .step:nth-child(7) {{ animation-delay: 240ms; }}
  .step:nth-child(n+8) {{ animation-delay: 280ms; }}

  /* `pulse` is the only thing in this interface that moves on its own, and it
     means exactly one thing: THIS IS WAITING ON A PERSON. It rides on the
     status tag's own colour, so a waiting tag pulses in the same amber it is
     already drawn in and nothing has to agree with anything.

     Where it is NOT used matters more than where it is. It never appears on a
     finished order or a saved record. A record that throbs implies something is
     still happening to it, and an audit trail that implies live activity it does
     not have is lying in the one place this product cannot afford to.

     A CSS ring rather than an emoji. An emoji renders in a different typeface on
     every machine, cannot take the palette, and cannot be animated - which on a
     projector we have never seen before is three ways to look unfinished. */
  @keyframes pulse-ring {{
      0%   {{ box-shadow: 0 0 0 0 var(--status-colour); opacity: 1; }}
      70%  {{ box-shadow: 0 0 0 7px transparent; opacity: 0.85; }}
      100% {{ box-shadow: 0 0 0 0 transparent; opacity: 1; }}
  }}
  .status-live .status-mark {{ animation: pulse-ring 2s var(--ease) infinite; }}

  /* Last rule in the sheet, on purpose - anything added below it would escape
     it. Two declarations switch off every transition and every animation above,
     including both of the ones just defined. */
  @media (prefers-reduced-motion: reduce) {{
      * {{ transition: none !important; animation: none !important; }}
  }}
</style>
"""
