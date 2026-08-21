"""Turn the logo we were handed into the mark the interface actually uses.

WHY THIS FILE EXISTS
--------------------
`assets/Evets_LOGO.jpeg` is the logo as it was drawn: a 1024x1024 square with a
navy background baked in, the word EVETS baked in under the glyph, and roughly
sixty percent of the canvas empty. That is a fine picture and a bad interface
asset, for three separate reasons:

  * The navy is opaque. Our page is #0a0b0e and our sidebar is #131316, so the
    same tile would be visibly wrong against both, and wrong in two different
    directions.
  * The wordmark is part of the image. The header and the sidebar signature
    already print "Evets" as live text right next to the mark, so using the
    whole picture would print the product name twice.
  * The glyph occupies x 350-673, y 255-631. Shrink the full square down to the
    32 pixels the header gives it and the glyph lands at about 12 - a smudge.

So we derive a second asset from the first: the glyph alone, cropped square,
with the navy replaced by real transparency. That is `assets/logo_mark.png`, and
it is what ui/theme.py embeds.

WHY IT IS A SCRIPT AND NOT PART OF THE APP
------------------------------------------
This runs once, by hand, and its output is committed. The app never imports it.
That matters twice over: the demo never spends startup time doing image
processing, and Pillow stays out of requirements.txt as a thing we depend on
(it arrives with Streamlit anyway, but we do not ask for it).

It is checked in rather than being a thing someone did once in a notebook,
because "where did this asset come from?" should have a file for an answer. We
hold the price and stock history to the same rule - authored data, declared as
authored. An asset nobody can regenerate is the same problem wearing a nicer
coat.

Run it with:  python tools/make_logo_mark.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "Evets_LOGO.jpeg"
TARGET = ROOT / "assets" / "logo_mark.png"

# The navy the logo was drawn on, read straight off its corners. Not guessed.
BACKGROUND = np.array([17.0, 27.0, 36.0])

# The glyph's own colour, taken as the median of every pixel furthest from that
# navy WITHIN the crop below. Sampling the whole image gives #fcfcfc instead -
# the white of the wordmark, which is further from navy than the mint is and is
# exactly the part we are cutting away.
MINT = (0x56, 0xD8, 0xBF)

# The glyph's bounding box in the source, measured rather than eyeballed: every
# pixel more than 40 (summed channel distance) away from the background, ignoring
# everything below y=700 where the wordmark starts.
GLYPH_BOX = (350, 255, 673, 631)

# Breathing room around the glyph, as a fraction of its longer side. A mark that
# touches its own edges looks cropped by accident.
MARGIN = 0.06

# How hard the alpha ramp climbs, as a fraction of the maximum distance found in
# the crop. Lower means more pixels reach full opacity. 0.60 was picked by
# measuring: it leaves 73% of the square cleanly transparent, 19% solid ink, and
# a 7% edge ramp that is antialiasing plus a little JPEG ringing.
RAMP = 0.60

# Everything below this much opacity is snapped to fully transparent.
#
# This is not a nicety. The source is a JPEG, so its "flat" navy is not flat -
# it is navy plus compression noise, and the ramp above turns that noise into a
# haze of alpha 1-25 covering 38% of the square. Two things go wrong. It is a
# faint grey fog around the mark, and because PNG compresses runs of identical
# pixels, a field where no two pixels agree does not compress at all: the file
# came out at 80KB before this line and 36KB after it, for an image that looks
# the same only cleaner.
FLOOR = 0.12

# The mark is drawn at 32px in the header, 20px in the sidebar, and 32-64px in a
# browser tab. 128 gives every one of those at least a 2x retina allowance and
# most of them 4x, and it is where the size curve flattens: 256px costs 36KB and
# buys headroom nothing on this page will ever use, 128px costs 15KB. We measured
# rather than guessed, and picked the smallest one that is still generous.
OUTPUT_SIZE = 128


def square_crop_box() -> tuple[int, int, int, int]:
    """The glyph's box, grown to a centred square with a margin.

    Square on purpose. A square source cannot be distorted by a container that
    is not square - `object-fit: contain` in the stylesheet does the rest - so
    the mark is safe at any size we later decide to draw it.
    """
    x0, y0, x1, y1 = GLYPH_BOX
    side = max(x1 - x0, y1 - y0)
    half = side // 2 + int(side * MARGIN)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return cx - half, cy - half, cx + half, cy + half


def build() -> Image.Image:
    """Crop to the glyph, turn the navy into transparency, flatten to one colour."""
    source = Image.open(SOURCE).convert("RGB")
    crop = np.asarray(source.crop(square_crop_box())).astype(float)

    # How far each pixel is from the background it was drawn on. Mint is far,
    # navy is zero, and the antialiased edge between them lands in between - which
    # is what makes this a soft cut rather than a jagged one.
    distance = np.sqrt(((crop - BACKGROUND) ** 2).sum(axis=2))
    alpha = np.clip(distance / (distance.max() * RAMP), 0.0, 1.0)

    # Cut the compression haze, then stretch what survives back over the full
    # range so the real edge of the glyph keeps its soft ramp instead of being
    # left dimmed by the amount we just subtracted.
    alpha = np.clip((alpha - FLOOR) / (1.0 - FLOOR), 0.0, 1.0)

    # The colour is set flat, and alpha alone carries the shape. This is the step
    # that is easy to skip and wrong to skip: keeping the JPEG's own pixels would
    # leave every semi-transparent edge pixel part navy, so the mark would carry a
    # dark fringe - invisible on our dark page, obvious the moment it is ever put
    # on a light one, or on a browser tab.
    height, width = alpha.shape
    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = MINT
    out[..., 3] = (alpha * 255).round().astype(np.uint8)

    return Image.fromarray(out, mode="RGBA").resize(
        (OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS
    )


def main() -> None:
    mark = build()
    mark.save(TARGET, format="PNG", optimize=True)

    # Say what was produced, so running this is self-verifying. The corner check
    # is the one that matters: four transparent corners means the navy is gone.
    last = OUTPUT_SIZE - 1
    corners = [
        mark.getpixel(xy)[3]
        for xy in ((0, 0), (last, 0), (0, last), (last, last))
    ]
    print(f"wrote {TARGET.relative_to(ROOT)}  {mark.size[0]}x{mark.size[1]} {mark.mode}")
    print(f"  {TARGET.stat().st_size:,} bytes")
    print(f"  corner alpha: {corners}  (all 0 means the background is fully cut)")


if __name__ == "__main__":
    main()
