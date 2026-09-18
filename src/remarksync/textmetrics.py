"""Text-block metrics: the one place that knows how the tablet lays typed
text out — which font each paragraph style uses, where every line's
baseline falls, how paragraphs wrap and indent, and how much of a page
they take.

Three modules consume this and must agree with each other:

* the page decoder (:mod:`rmdata`) computes each text block's lines and
  the anchor positions of ink pinned to the lines of a text block;
* the Markdown compiler (:mod:`mdcompile`) places text blocks and decides
  how much text fits on a page;
* the painter (:mod:`viewer`) draws the lines.

Everything here was fitted on 2026-09-04 against the tablet's own PDF
exports of two typed pages (a plain page with ink anchored to 60 lines,
and a page with every paragraph style), see ADR-0001. No Qt here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# -- the text block ----------------------------------------------------------

# Text-block placement as the tablet itself writes it: x centre-relative,
# y from the page top. A page typed on the device (software 3.x, 2026-09)
# stored pos (-576, 234) with width 1152; compiled pages use the same.
TEXT_POS_Y = 234.0
#
# The width is a *setting*, not a constant. The device offers three column
# widths in its text UI, and the file records the chosen one:
# ``RootText.settings.textWidth``, which a scene dump from software 3.27 showed
# as an explicit 1152. Two of the three appear in the reference backup -- 1152
# (34 blocks) and 936 (6) -- and the third has not been seen yet.
#
# A block is always centred on its own page, so pos_x is -width/2 and the
# margin follows from the sheet: 1152 on a 1404 sheet leaves 126 a side, and on
# a 1620 Paper Pro sheet 234. That is why both x values turn up in real files.
#
# TEXT_WIDTH is therefore a default for callers with nothing better -- the
# compiler, and a block whose own width is missing -- and every function here
# takes the width as an argument. Reading is width-agnostic; only writing has
# to choose.
TEXT_WIDTH = 1152.0
TEXT_POS_X = -TEXT_WIDTH / 2  # blocks are centred, so this follows the width
# Widths seen in real files. Not exhaustive: the UI offers three.
KNOWN_TEXT_WIDTHS = (936.0, 1152.0)
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
    # style code 3: a medium-weight body line (what our own compiled ## lines
    # rendered as on the device)
    "BOLD": Style("BOLD", 35.8, False, 500, 0, None),
    # the toolbar's Subheading: style code 3 with a variant field (see
    # rmformat), reported under a code of our own
    "SUBHEADING": Style("SUBHEADING", 45.8, True, 400, 0, None),
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
    103: "SUBHEADING",  # rmformat.SUBHEADING_CODE
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
# Glyph advances of the device fonts in em, measured character by
# character from the tablet's PDF exports (reMarkableSans regular and
# reMarkableSerifSmall). Characters not seen use the mean.
ADVANCE_SANS: dict[str, float] = {
    ' ': 0.236, '"': 0.348, '(': 0.327, '+': 0.537, ',': 0.211, '-': 0.342,
    '.': 0.211, '/': 0.358, '1': 0.420, '2': 0.584, '5': 0.573, ':': 0.251,
    'A': 0.649, 'B': 0.654, 'C': 0.688, 'D': 0.692, 'E': 0.593, 'F': 0.573,
    'L': 0.530, 'M': 0.871, 'N': 0.738, 'P': 0.619, 'Q': 0.740, 'R': 0.654,
    'S': 0.640, 'T': 0.578, 'U': 0.717, 'W': 0.972, '\\': 0.398, 'a': 0.546,
    'b': 0.592, 'c': 0.536, 'd': 0.592, 'e': 0.547, 'f': 0.331, 'g': 0.545,
    'h': 0.581, 'i': 0.248, 'k': 0.525, 'l': 0.266, 'm': 0.862, 'n': 0.583,
    'o': 0.568, 'p': 0.592, 'q': 0.592, 'r': 0.360, 's': 0.498, 't': 0.361,
    'u': 0.580, 'v': 0.502, 'w': 0.765, 'x': 0.499, 'y': 0.512, '—': 0.922,
    'ﬁ': 0.592,
}
ADVANCE_SERIF: dict[str, float] = {
    ' ': 0.235, 'H': 0.727, 'R': 0.646, 'S': 0.567, 'T': 0.639, 'a': 0.521,
    'd': 0.605, 'e': 0.507, 'g': 0.528, 'h': 0.584, 'i': 0.247, 'k': 0.538,
    'l': 0.247, 'm': 0.877, 'n': 0.584, 'o': 0.581, 'p': 0.604, 'r': 0.361,
    's': 0.456, 't': 0.333, 'w': 0.748, 'x': 0.483, 'y': 0.501, 'z': 0.478,
}
DEFAULT_ADVANCE_SANS = 0.54
DEFAULT_ADVANCE_SERIF = 0.52
# Glyphs the device fonts lack (box drawing, arrows, geometric shapes) come
# from a fallback font with a deeper line box: the line after such a line
# sits 72.2 px below it instead of 69.3.
FALLBACK_LINE_EXTRA = 2.9
# Descent below the baseline, as a fraction of the font size.
DESCENT_RATIO = 0.22

BODY_PITCH = 69.3  # body paragraph to body paragraph

# Baseline-to-baseline pitches measured on the tablet's exports, keyed by
# (font size of the first paragraph, font size of the second). Pairs not
# measured fall back to the linear ramp.
MEASURED_PITCH: dict[tuple[float, float], float] = {
    (31.9, 31.9): 69.3,
    (63.7, 63.7): 163.2,
    (63.7, 45.8): 116.0,
    (63.7, 31.9): 70.9,
    (45.8, 35.8): 84.5,
    (35.8, 31.9): 63.7,
    (31.9, 35.8): 88.9,
    (31.9, 45.8): 113.9,
    (45.8, 31.9): 65.8,
}


def above(s: Style) -> float:
    return ABOVE_PER_PX * s.size


def below(s: Style) -> float:
    return BELOW_PER_PX * s.size + PARAGRAPH_GAP


def pitch(first: Style, second: Style) -> float:
    """Baseline-to-baseline distance from a paragraph's last line to the
    next paragraph's first line."""
    return MEASURED_PITCH.get((first.size, second.size), below(first) + above(second))


def continuation(s: Style) -> float:
    return CONTINUATION_RATIO.get(s.size, DEFAULT_CONTINUATION_RATIO) * s.size


def text_width(text: str, s: Style) -> float:
    """Width of ``text`` in tablet px when set in the style's font."""
    table, default = (ADVANCE_SERIF, DEFAULT_ADVANCE_SERIF) if s.serif else (ADVANCE_SANS, DEFAULT_ADVANCE_SANS)
    return sum(table.get(c, default) for c in text) * s.size


def line_width(s: Style, box_width: float = TEXT_WIDTH) -> float:
    """Room for text on one line of this style, inside a box ``box_width``
    wide. Blocks are a fixed width but not all the same one: real files carry
    1152 and 936, each centred on the page."""
    return box_width - s.indent


def chars_per_line(s: Style) -> int:
    """Rough capacity of a line in average characters (the wrap itself
    measures real glyph widths)."""
    default = DEFAULT_ADVANCE_SERIF if s.serif else DEFAULT_ADVANCE_SANS
    return max(1, int(line_width(s) / (default * s.size)))


def uses_fallback_font(text: str) -> bool:
    return any(0x2190 <= ord(c) <= 0x25FF for c in text)


def wrap(text: str, s: Style, box_width: float = TEXT_WIDTH) -> list[str]:
    """Break on spaces the way the tablet does: never inside a word, by the
    measured glyph widths, keeping a line's leading whitespace."""
    limit = line_width(s, box_width)
    stripped = text.lstrip(" ")
    lead = text[: len(text) - len(stripped)]
    out: list[str] = []
    current = lead
    started = False
    for word in stripped.split(" "):
        candidate = word if not started else current + " " + word
        if started and text_width(candidate, s) > limit:
            out.append(current)
            current = word
        else:
            current = lead + candidate if not started else candidate
        started = True
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


def lay_out_lines(paragraphs: Iterable[tuple[str, str]],
                  box_width: float = TEXT_WIDTH) -> list[TextLine]:
    """Every line of a block ``box_width`` wide, with baselines relative to
    the block's pos_y."""
    lines: list[TextLine] = []
    y = FIRST_BASELINE
    previous: Style | None = None
    previous_fallback = False
    for k, (name, text) in enumerate(paragraphs):
        s = style(name)
        if previous is not None:
            y += pitch(previous, s)
        consumed = 0
        for i, piece in enumerate(wrap(text, s, box_width)):
            if i:
                y += continuation(s)
            if previous_fallback and lines:
                y += FALLBACK_LINE_EXTRA
            previous_fallback = uses_fallback_font(piece)
            consumed += len(piece) + (1 if i else 0)
            lines.append(TextLine(k, s.name, piece, y, i == 0, consumed))
        previous = s
    return lines


def block_height(paragraphs: Iterable[tuple[str, str]],
                 box_width: float = TEXT_WIDTH) -> float:
    """From the block's pos_y to the bottom of its last line."""
    lines = lay_out_lines(paragraphs, box_width)
    if not lines:
        return FIRST_BASELINE
    last = lines[-1]
    return last.baseline + DESCENT_RATIO * style(last.style).size


def paragraph_advance(name: str, text: str) -> float:
    """Vertical room a paragraph takes when followed by body text: its own
    lines plus the spacing to the next paragraph."""
    s = style(name)
    n = len(wrap(text, s))
    return (n - 1) * continuation(s) + pitch(s, STYLES["PLAIN"])


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
    return len(wrap(paragraph, style(name)))
