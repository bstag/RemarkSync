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
and a page with every paragraph style), see ADR-0001. The wrapping model
was finished on 2026-09-18 against exports written for the purpose: a
paragraph per character for the advances, a page per column width, and a
paragraph per letter pair for the ligatures (tests/real). No Qt here.
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
# (34 blocks) and 936 (6) -- and the third, 740, came from a worksheet typed at
# each setting in turn (tests/real/text_width.py).
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
# The three the device offers, read off pages typed at each setting on
# software 3.27. Each is centred, so the margin follows from the sheet: on a
# 1620 Paper Pro page they leave 440, 342 and 234 a side.
TEXT_WIDTHS = (740.0, 936.0, 1152.0)
# Glyphs start this far right of the box's pos_x. Measured 2026-09-18 from the
# first glyph of every line of the tablet's own exports: 4.0 px at all three
# column widths, on both the 1404 and 1620 papers, in both faces. It was 15.0
# from an early estimate, which drew typed text 11 px right of where the device
# puts it. The other 4 px of the box are at the right-hand end, where they show
# up as the wrap inset below.
TEXT_LEFT_PAD = 4.0
# Text does not get the whole box: the device breaks a line 8 px short of the
# declared width. Measured 2026-09-18 from where it broke runs of one repeated
# character, whose advances are known: at 1152 the limit lies in (1143.4,
# 1144.2), and at 740 below 733.4. That second bound is what says the inset is
# a fixed 8 px and not a proportion of the width, which would put the 740 limit
# at 734.7. The export also starts its text 4.0 px inside the box at all three
# widths, so this reads as 4 px of padding on each side.
TEXT_WRAP_INSET = 8.0
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
    # Added 2026-09-18 by tests/real/advances.py: a paragraph of each missing
    # character was compiled, put on the tablet, and its advance read off the
    # width of the lines the device drew. The 'g' already here served as the
    # control and came back within 1.1%.
    '0': 0.610, '3': 0.572, '4': 0.576, '6': 0.587, '7': 0.524, '8': 0.596,
    '9': 0.587, 'G': 0.725, 'H': 0.726, 'I': 0.272, 'J': 0.484, 'K': 0.639,
    'O': 0.749, 'V': 0.656, 'X': 0.627, 'Y': 0.594, 'Z': 0.582, 'j': 0.249,
    'z': 0.454,
    # Punctuation, same day and the same way, but read as the step onto a
    # letter rather than onto another of its own kind (see KERNING below).
    '!': 0.306, '#': 0.607, '$': 0.647, '%': 0.796, '&': 0.673, "'": 0.187,
    ')': 0.328, '*': 0.432, ';': 0.260, '<': 0.554, '=': 0.563, '>': 0.554,
    '?': 0.494, '@': 0.819, '[': 0.356, ']': 0.356, '^': 0.559, '_': 0.484,
    '`': 0.259, '{': 0.398, '|': 0.335, '}': 0.398, '~': 0.552, '–': 0.594,
    '‘': 0.192, '’': 0.192, '“': 0.367, '”': 0.359, '…': 0.651,
}
ADVANCE_SERIF: dict[str, float] = {
    ' ': 0.235, 'H': 0.727, 'R': 0.646, 'S': 0.567, 'T': 0.639, 'a': 0.523,
    'd': 0.605, 'e': 0.507, 'g': 0.528, 'h': 0.584, 'i': 0.247, 'k': 0.538,
    'l': 0.247, 'm': 0.877, 'n': 0.584, 'o': 0.581, 'p': 0.604, 'r': 0.361,
    's': 0.456, 't': 0.333, 'w': 0.748, 'x': 0.483, 'y': 0.501, 'z': 0.478,
    # Same measurement, same day, control within 0.4%. 'f' could not be read
    # from a run of its own -- the device sets 'ff' as one ligature glyph -- so
    # it was measured paired with 'x' and the known advance subtracted.
    '0': 0.577, '1': 0.322, '2': 0.514, '3': 0.513, '4': 0.567, '5': 0.511,
    '6': 0.556, '7': 0.472, '8': 0.554, '9': 0.556, 'A': 0.674, 'B': 0.617,
    'C': 0.675, 'D': 0.708, 'E': 0.574, 'F': 0.546, 'G': 0.715, 'I': 0.269,
    'J': 0.269, 'K': 0.678, 'L': 0.560, 'M': 0.905, 'N': 0.728, 'O': 0.781,
    'P': 0.588, 'Q': 0.781, 'U': 0.702, 'V': 0.674, 'W': 1.004, 'X': 0.646,
    'Y': 0.605, 'Z': 0.617, 'b': 0.605, 'c': 0.488, 'f': 0.355, 'j': 0.248,
    'q': 0.595, 'u': 0.569, 'v': 0.505,
    # 'a' was 0.521 from the first fit; a line of 21 of them on the 740 px page
    # measures 700.2 px, which is 0.523, and the difference decides that line.
    # Punctuation, of which this face had none: a heading carrying a comma
    # wrapped on the 0.52 fallback. Read as the step onto a letter (see KERNING
    # below). The grave accent is left out on purpose -- it composes with the
    # letter after it and read 1.079 em, wider than 'W', so the measurement
    # failed rather than found a wide glyph.
    '!': 0.306, '"': 0.347, '#': 0.592, '$': 0.543, '%': 0.736, '&': 0.687,
    "'": 0.193, '(': 0.318, ')': 0.318, '*': 0.436, '+': 0.542, ',': 0.248,
    '-': 0.299, '.': 0.230, '/': 0.322, ':': 0.259, ';': 0.270, '<': 0.552,
    '=': 0.559, '>': 0.552, '?': 0.445, '@': 0.848, '[': 0.341, '\\': 0.360,
    ']': 0.341, '^': 0.552, '_': 0.485, '{': 0.374, '|': 0.296, '}': 0.374,
    '~': 0.542, '–': 0.590, '—': 0.891, '‘': 0.194, '’': 0.194, '“': 0.358,
    '”': 0.358, '…': 0.678,
}
DEFAULT_ADVANCE_SANS = 0.54
DEFAULT_ADVANCE_SERIF = 0.52
# The faces kern, and this model does not. An advance above is one number per
# character, but the device's is a number per *pair*: the serif steps 0.237 em
# from one comma to the next and 0.248 from a comma to a letter, and 0.496 from
# 'x' to '!' but 0.453 from 'x' to '-'. Measured 2026-09-18 by stepping between
# glyph origins in the export (tests/real/advances.py).
#
# Every advance here is therefore the step onto an ordinary letter, that being
# what running text asks for. The residue is the kerning we leave out, which on
# the marks that kern at all is up to 0.06 em -- about 4 px in a heading, 2 in
# body text, so it takes several of them on one line to move a break.
KERNING_UNMODELLED = 0.06
# Pairs the device fonts set as a single glyph, with that glyph's advance in em.
# Measured 2026-09-18 by tests/real/ligatures.py, which put a paragraph of each
# f-pair on the tablet and read the glyph stream of its export back: a ligature
# arrives as one glyph where the pair would be two. The two faces do not agree
# about which pairs ligate -- the serif has seven, the sans only fi and fl.
#
# A ligature matters twice over. It is narrower than its letters apart (by about
# 0.026 em in the serif, where ff saves 0.067), and it cannot be divided: in
# every measured run the device ended a line rather than split one, which is why
# the three-width worksheet put a whole 'fj' on the next line where we had left
# the 'f' behind.
LIGATURES_SERIF: dict[str, float] = {
    "fb": 0.934, "ff": 0.643, "fh": 0.913, "fi": 0.576, "fj": 0.576,
    "fk": 0.873, "fl": 0.581,
}
# The sans face ligates far less: fj, ff and fh are set as plain letters here.
# ADVANCE_SANS already carried the precomposed 'ﬁ' at 0.592 from the earlier
# fit, and this measurement of the same glyph agrees within 0.3%, which is the
# control on the method.
LIGATURES_SANS: dict[str, float] = {"fi": 0.594, "fl": 0.614}
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


def ligatures(s: Style) -> dict[str, float]:
    """The pairs this style's font sets as one glyph."""
    return LIGATURES_SERIF if s.serif else LIGATURES_SANS


def clusters(text: str, s: Style) -> list[str]:
    """``text`` split into the units the device actually sets: one character
    each, except where a pair ligates into a single glyph.

    Greedy from the left, the way shaping works, so serif 'fff' is 'ff' then a
    lone 'f'. A cluster is both the unit of width and the smallest thing a line
    break can fall between.
    """
    ligs = ligatures(s)
    out: list[str] = []
    i = 0
    while i < len(text):
        pair = text[i:i + 2]
        if pair in ligs:
            out.append(pair)
            i += 2
        else:
            out.append(text[i])
            i += 1
    return out


def text_width(text: str, s: Style) -> float:
    """Width of ``text`` in tablet px when set in the style's font, counting a
    ligature as the one glyph it is rather than the letters that went into it."""
    table, default = (ADVANCE_SERIF, DEFAULT_ADVANCE_SERIF) if s.serif else (ADVANCE_SANS, DEFAULT_ADVANCE_SANS)
    ligs = ligatures(s)
    return sum(ligs[c] if len(c) == 2 else table.get(c, default)
               for c in clusters(text, s)) * s.size


def line_width(s: Style, box_width: float = TEXT_WIDTH) -> float:
    """Room for text on one line of this style, inside a box ``box_width``
    wide. Blocks are a fixed width but not all the same one: real files carry
    1152 and 936, each centred on the page."""
    return box_width - TEXT_WRAP_INSET - s.indent


def chars_per_line(s: Style) -> int:
    """Rough capacity of a line in average characters (the wrap itself
    measures real glyph widths)."""
    default = DEFAULT_ADVANCE_SERIF if s.serif else DEFAULT_ADVANCE_SANS
    return max(1, int(line_width(s) / (default * s.size)))


def uses_fallback_font(text: str) -> bool:
    return any(0x2190 <= ord(c) <= 0x25FF for c in text)


def _break_oversized(word: str, s: Style, limit: float) -> list[str]:
    """Split a word that will not fit a line of its own, at the last glyph that
    fits, repeatedly.

    The tablet does this rather than letting a word run off the page: a heading
    of repeated 'g' in a 936 px column came back from its own export as lines of
    27 characters, and 27 g's measure 908 px where 28 measure 942.

    Glyph, not character: a ligature is indivisible, so a line ends before one
    that will not fit rather than keeping its first letter back.
    """
    pieces: list[str] = []
    current = ""
    for unit in clusters(word, s):
        if current and text_width(current + unit, s) > limit:
            pieces.append(current)
            current = unit
        else:
            current += unit
    if current:
        pieces.append(current)
    return pieces


def wrap(text: str, s: Style, box_width: float = TEXT_WIDTH) -> list[str]:
    """Break the way the tablet does: between words where it can, and inside a
    word when the word alone is wider than the column. Uses the measured glyph
    advances, and keeps a line's leading whitespace."""
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
        # the word may be wider than a whole line on its own, in which case no
        # amount of breaking between words helps
        if text_width(current, s) > limit:
            *full, current = _break_oversized(current, s, limit)
            out.extend(full)
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
