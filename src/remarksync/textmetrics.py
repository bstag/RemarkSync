"""Text-block metrics: the one place that knows how tall a typed line is,
where a text box sits, and how the painter should size its font.

Three modules consume these numbers and must agree with each other:

* the page decoder (:mod:`rmdata`) computes a text block's height and the
  anchor positions of ink pinned to text lines;
* the Markdown compiler (:mod:`mdcompile`) places text boxes and decides
  how much text fits on a page;
* the painter (:mod:`viewer`) draws the text.

Values come from rmc, the maintained reference exporter, and from tuning
against a real device. No Qt dependencies here.
"""

from __future__ import annotations

from typing import Iterable

# Vertical offset from a text block's pos_y to its first line anchor.
TEXT_TOP_Y = -88

# Per-paragraph-style line heights in tablet pixels (rmc measured 69.5 for
# PLAIN and rounded to 70).
LINE_HEIGHTS: dict[str, int] = {
    "PLAIN": 70,
    "BULLET": 35,
    "BULLET2": 35,
    "BOLD": 70,
    "HEADING": 150,
    "CHECKBOX": 35,
    "CHECKBOX_CHECKED": 35,
}
DEFAULT_LINE_HEIGHT = LINE_HEIGHTS["PLAIN"]


def line_height(style: str | None) -> int:
    """Height of one paragraph of the given style (unknown styles are PLAIN)."""
    return LINE_HEIGHTS.get(style or "PLAIN", DEFAULT_LINE_HEIGHT)


def paragraphs_height(styles: Iterable[str | None]) -> float:
    """Total height of a sequence of paragraphs, one style name each."""
    return float(sum(line_height(s) for s in styles))


# -- painter ---------------------------------------------------------------

# Font pixel size in tablet units, tuned to look close to on-device text.
TEXT_FONT_PX = 30
TEXT_MIN_BOX_WIDTH = 50.0

# -- compiler --------------------------------------------------------------

# reMarkable text-box coordinates are centre-relative, y downwards. Device
# text pages anchor boxes near the top; rmscene's simple_text_document
# default (pos_y=234) lands mid/lower page, so compiled pages override it.
TEXT_POS_X = -468.0
TEXT_POS_Y = -780.0
TEXT_WIDTH = 936.0

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
