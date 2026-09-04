"""Text-block metrics: the one place that knows how the tablet lays typed
text out — which font each paragraph style uses, where every line's
baseline falls, how paragraphs wrap and indent, and how much of a page
they take.

Three modules consume this and must agree with each other:

* the page decoder (:mod:`rmdata`) computes each text block's lines and
  the anchor positions of ink pinned to text lines;
* the Markdown compiler (:mod:`mdcompile`) places text boxes and decides
  how much text fits on a page;
* the painter (:mod:`viewer`) draws the lines.

Everything here was fitted on 2026-09-04 against the tablet's own PDF
exports of two typed pages (a plain page with ink anchored to 60 lines,
and a page with every paragraph style), see ADR-0001. No Qt here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# -- the text box ------------------------------------------------------------

# Text-box placement as the tablet itself writes it: x centre-relative,
# y from the page top. A page typed on the device (software 3.x, 2026-09)
# stored pos (-576, 234) with width 1152; compiled pages use the same.
TEXT_POS_X = -576.0
TEXT_POS_Y = 234.0
TEXT_WIDTH = 1152.0
# Glyphs start this far right of the box's pos_x.
TEXT_LEFT_PAD = 15.0
# The first line's baseline sits this far below the box's pos_y, for every style.
FIRST_BASELINE = 61.0

# -- paragraph styles --------------------------------------------------------


@dataclass(frozen=True)
class Style:
    name: str
    size: float  # font em size in tablet px
    serif: bool
    weight: int  # 400 regular, 500 medium
    indent: float  # where the text starts, right of the box's left edge
    marker: str | None  # what is drawn in the indent: bullet, bullet2, number, letter, box, box_checked


# Fonts as the tablet's PDF export names them: reMarkableSerifSmall for
# headings, reMarkableSans for body text. Indents: list text at +48, nested
# at +96; the marker sits 28 px left of the text (a checkbox is a 26 px
# square 41 px left of it).
STYLES: dict[str, Style] = {
    "HEADING": Style("HEADING", 63.7, True, 400, 0, None),
    "BOLD": Style("BOLD", 45.8, True, 400, 0, None),  # the tablet's "subheading"
    "PLAIN": Style("PLAIN", 31.9, False, 400, 0, None),
    "BASIC": Style("BASIC", 31.9, False, 400, 0, None),
    "BULLET": Style("BULLET", 31.9, False, 400, 48, "bullet"),
    "BULLET2": Style("BULLET2", 31.9, False, 400, 96, "bullet2"),
    "NUMBERED": Style("NUMBERED", 31.9, False, 400, 48, "number"),
    "NUMBERED2": Style("NUMBERED2", 31.9, False, 400, 96, "letter"),
    "CHECKBOX": Style("CHECKBOX", 31.9, False, 400, 48, "box"),
    "CHECKBOX_CHECKED": Style("CHECKBOX_CHECKED", 31.9, False, 400, 48, "box_checked"),
    "CHECKBOX2": Style("CHECKBOX2", 31.9, False, 400, 96, "box"),
}
# Paragraph style codes as the file stores them. rmscene 0.8 names 0-7;
# the rest were identified from the tablet's export of a page using them.
STYLE_CODES: dict[int, str] = {
    0: "BASIC",
    1: "PLAIN",
    2: "HEADING",
    3: "BOLD",
    4: "BULLET",
    5: "BULLET2",
    6: "CHECKBOX",
    7: "CHECKBOX_CHECKED",
    8: "CHECKBOX2",
    10: "NUMBERED",
    11: "NUMBERED2",
}
MARKER_GAP = 28.0  # marker glyph this far left of the text
CHECKBOX_SIZE = 26.0
CHECKBOX_GAP = 41.0  # checkbox square this far left of the text


def style(name: str | None) -> Style:
    """The style for a name; unknown names read as plain body text."""
    return STYLES.get(name or "PLAIN", STYLES["PLAIN"])


def style_name(code: int) -> str:
    return STYLE_CODES.get(code, "PLAIN")


# -- vertical rhythm ---------------------------------------------------------

# Baseline-to-baseline distance between two paragraphs is below(first) +
# above(second); both are linear in font size. Fitted on five measured
# transitions (body-body 69.3, heading-heading 163.2, heading-subheading
# 116, subheading-bold 84.5, bold-body 63.7) to within 0.3 px except the
# last, which is 7 px off.
ABOVE_PER_PX = 2.635
BELOW_PER_PX = 0.318
PARAGRAPH_GAP = -24.9
# Lines inside one wrapped paragraph are closer: the font's own line height.
CONTINUATION_RATIO = {63.7: 75.5 / 63.7, 31.9: 45.2 / 31.9}
DEFAULT_CONTINUATION_RATIO = 1.3
# Average glyph advance of the device fonts, in em: the sans body font
# fits 84 characters of a bullet line (1104 px at 31.9 px), the serif
# heading font wraps a heading between 37 and 41 characters.
EM_PER_CHAR_SANS = 0.412
EM_PER_CHAR_SERIF = 0.465
# Descent below the baseline, as a fraction of the font size.
DESCENT_RATIO = 0.22

BODY_PITCH = 69.3  # body paragraph to body paragraph


def above(s: Style) -> float:
    return ABOVE_PER_PX * s.size


def below(s: Style) -> float:
    return BELOW_PER_PX * s.size + PARAGRAPH_GAP


def continuation(s: Style) -> float:
    return CONTINUATION_RATIO.get(s.size, DEFAULT_CONTINUATION_RATIO) * s.size


def chars_per_line(s: Style) -> int:
    """How many characters the tablet fits on one line of this style."""
    em = EM_PER_CHAR_SERIF if s.serif else EM_PER_CHAR_SANS
    return max(1, int((TEXT_WIDTH - s.indent) / (em * s.size)))


def wrap(text: str, width: int) -> list[str]:
    """Break on spaces the way the tablet does, never inside a word."""
    out: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            out.append(current)
            current = word
        else:
            current = candidate
    out.append(current)
    return out


# -- laying paragraphs out ---------------------------------------------------


@dataclass
class TextLine:
    """One drawn line of a text block."""

    paragraph: int  # index into the block's paragraphs
    style: str
    text: str
    baseline: float  # below the block's pos_y
    first: bool  # first line of its paragraph (carries the marker)
    chars: int  # characters of the paragraph consumed up to the end of this line


def lay_out_lines(paragraphs: Iterable[tuple[str, str]]) -> list[TextLine]:
    """Every line of a block, with baselines relative to the block's pos_y."""
    lines: list[TextLine] = []
    y = FIRST_BASELINE
    previous: Style | None = None
    for k, (name, text) in enumerate(paragraphs):
        s = style(name)
        if previous is not None:
            y += below(previous) + above(s)
        consumed = 0
        for i, piece in enumerate(wrap(text, chars_per_line(s))):
            if i:
                y += continuation(s)
            consumed += len(piece) + (1 if i else 0)
            lines.append(TextLine(k, s.name, piece, y, i == 0, consumed))
        previous = s
    return lines


def block_height(paragraphs: Iterable[tuple[str, str]]) -> float:
    """From the block's pos_y to the bottom of its last line."""
    lines = lay_out_lines(paragraphs)
    if not lines:
        return FIRST_BASELINE
    last = lines[-1]
    return last.baseline + DESCENT_RATIO * style(last.style).size


def paragraph_advance(name: str, text: str) -> float:
    """Vertical room a paragraph takes when followed by body text: its own
    lines plus the spacing to the next paragraph."""
    s = style(name)
    n = len(wrap(text, chars_per_line(s)))
    return (n - 1) * continuation(s) + below(s) + above(STYLES["PLAIN"])


# -- the painter -------------------------------------------------------------

FONT_SERIF = "Georgia"  # stand-ins for the device fonts
FONT_SANS = "Segoe UI"
TEXT_FONT_PX = STYLES["PLAIN"].size  # older name
TEXT_MIN_BOX_WIDTH = 50.0

# -- the compiler ------------------------------------------------------------

# Space below the last line before a page break.
PAGE_BOTTOM_MARGIN = 24.0
CHARS_PER_LINE = chars_per_line(STYLES["PLAIN"])  # older name


def page_text_budget() -> float:
    """Vertical room for text on one screen, below the first baseline."""
    from .rmdata import PAGE_HEIGHT  # local import: rmdata imports this module

    return PAGE_HEIGHT - TEXT_POS_Y - FIRST_BASELINE - PAGE_BOTTOM_MARGIN


def lines_per_page() -> int:
    """How many body paragraphs fit on one screen: 22."""
    return int(page_text_budget() // BODY_PITCH)


def estimated_lines(paragraph: str, name: str = "PLAIN") -> int:
    """Lines the tablet will wrap a paragraph onto."""
    return len(wrap(paragraph, chars_per_line(style(name))))
