"""Text-block metrics: the one place that knows how tall a typed line is,
where a text box sits, and how the painter should size its font.

Three modules consume these numbers and must agree with each other:

* the page decoder (:mod:`rmdata`) computes a text block's height and the
  anchor positions of ink pinned to text lines;
* the Markdown compiler (:mod:`mdcompile`) places text boxes and decides
  how much text fits on a page;
* the painter (:mod:`viewer`) draws the text.

Values were fitted on 2026-09-04 against the tablet's own PDF export of a
page with typed text and ink anchored to its lines (see ADR-0001); rmc's
figures are noted where they differ. No Qt dependencies here.
"""

from __future__ import annotations

from typing import Iterable

# Vertical offset from a text block's pos_y (measured from the page top,
# like ink) to the top of its first line box, which is what anchored ink
# layers are pinned to. Fitted: +60 (rmc uses -88).
TEXT_TOP_Y = 60.0

# Per-paragraph-style line heights in tablet pixels. PLAIN was fitted at
# 69.3 (rmc rounded its 69.5 to 70); the other styles are rmc's values.
LINE_HEIGHTS: dict[str, float] = {
    "PLAIN": 69.3,
    "BULLET": 35,
    "BULLET2": 35,
    "BOLD": 69.3,
    "HEADING": 150,
    "CHECKBOX": 35,
    "CHECKBOX_CHECKED": 35,
}
DEFAULT_LINE_HEIGHT = LINE_HEIGHTS["PLAIN"]


def line_height(style: str | None) -> float:
    """Height of one paragraph of the given style (unknown styles are PLAIN)."""
    return LINE_HEIGHTS.get(style or "PLAIN", DEFAULT_LINE_HEIGHT)


def paragraphs_height(styles: Iterable[str | None]) -> float:
    """Total height of a sequence of paragraphs, one style name each."""
    return float(sum(line_height(s) for s in styles))


# -- painter ---------------------------------------------------------------

# Font pixel size in tablet units, tuned to look close to on-device text.
TEXT_FONT_PX = 30
TEXT_MIN_BOX_WIDTH = 50.0
# Where glyphs sit inside a line box: the tablet draws cap tops 39 px below
# the line's anchor; Qt places a 30 px Segoe UI line so its caps start about
# 10 px below the line position, hence 29. Text starts 15 px right of pos_x.
TEXT_LINE_PAD = 29.0
TEXT_LEFT_PAD = 15.0

# -- compiler --------------------------------------------------------------

# Text-box placement as the tablet itself writes it: x centre-relative,
# y from the page top. A page typed on the device (software 3.x, 2026-09)
# stored pos (-576, 234) with width 1152; compiled pages use the same so
# they look like native text pages.
TEXT_POS_X = -576.0
TEXT_POS_Y = 234.0
TEXT_WIDTH = 1152.0

# Conservative page fill: the tablet wraps text inside the 936-unit-wide text
# box; the compiler breaks lines itself so pages stay predictable.
CHARS_PER_LINE = 62
LINES_PER_PAGE = 40


def lines_per_page() -> int:
    """How many wrapped lines the compiler puts on one page.

    A fixed count today. The alternative is a height budget using
    :func:`paragraphs_height`, which would let headings cost more than
    bullets; switch here once compiled pages have been checked on-device.
    """
    return LINES_PER_PAGE
