"""Text metrics: the tablet's line rhythm, fitted against its PDF exports.

The measured baselines below come from the desktop app's export of a page
typed with every paragraph style (2026-09-04); they are the regression
surface for the model.
"""

import pytest

from remarksync.textmetrics import (FIRST_BASELINE, STYLES, TEXT_POS_Y, block_height, chars_per_line,
                                    lay_out_lines, lines_per_page, style, style_name, wrap)

STYLES_PAGE = [
    ("HEADING", "Heading i think this is"),
    ("HEADING", "This is me doing a heading that wraps well see"),
    ("SUBHEADING", "This is thr next size down"),  # a 46 px serif line whose style code is still unknown
    ("BOLD", "This is the next size dien "),  # code 3: 36 px medium sans
    ("PLAIN", "This is normal text"),
    ("BULLET", "Bullets"),
    ("BULLET", "Bulllets that " + ", ".join(["will wrap"] * 8)),  # 100 characters, as typed on the tablet
    ("BULLET2", "Nested"),
    ("NUMBERED", "Number 1 bullet"),
    ("NUMBERED", "Number 2"),
    ("NUMBERED2", "Nested"),
    ("CHECKBOX", "Check box bullets"),
    ("CHECKBOX_CHECKED", "And a second"),
    ("CHECKBOX2", "Nested"),
]
# baselines measured in the export, page coordinates; the wrapped heading and
# wrapped bullet each contribute two lines
MEASURED_BASELINES = [295.3, 458.5, 534.0, 650.0, 734.5, 798.2, 867.3, 936.6, 981.8, 1051.2, 1120.3, 1189.6, 1259.0, 1328.1, 1397.4, 1466.5]


def test_every_measured_baseline_is_reproduced():
    lines = lay_out_lines(STYLES_PAGE)
    assert len(lines) == len(MEASURED_BASELINES)
    predicted = [TEXT_POS_Y + ln.baseline for ln in lines]
    assert max(abs(p - m) for p, m in zip(predicted, MEASURED_BASELINES)) < 1.0
    assert lines[1].first and not lines[2].first  # the heading wrapped
    assert lines[2].text == "well see"
    assert not lines[8].first and lines[8].text.startswith("wrap")  # the bullet wrapped


def test_readme_export_rhythm():
    # measured on the tablet's export of the compiled README: heading to
    # body 70.9, body to bold 88.9, bold to body 63.7, and 72.2 after a line
    # drawn with fallback glyphs (box drawing), 69.3 otherwise
    lines = lay_out_lines([("HEADING", "RemarkSync"), ("PLAIN", ""), ("PLAIN", "text"), ("PLAIN", "\u251c\u2500\u2500 tree"),
                           ("PLAIN", "plain after art"), ("PLAIN", ""), ("BOLD", "Install"), ("PLAIN", "")])
    b = [ln.baseline for ln in lines]
    assert b[1] - b[0] == pytest.approx(70.9)
    assert b[2] - b[1] == pytest.approx(69.3)
    assert b[3] - b[2] == pytest.approx(69.3)
    assert b[4] - b[3] == pytest.approx(72.2)
    assert b[5] - b[4] == pytest.approx(69.3)
    assert b[6] - b[5] == pytest.approx(88.9)
    assert b[7] - b[6] == pytest.approx(63.7)
    # measured on the export of the compiled README once ## became a subheading
    sub = [ln.baseline for ln in lay_out_lines([("PLAIN", ""), ("SUBHEADING", "Install"), ("PLAIN", "")])]
    assert sub[1] - sub[0] == pytest.approx(113.9)
    assert sub[2] - sub[1] == pytest.approx(65.8)


def test_first_baseline_and_body_pitch():
    lines = lay_out_lines([("PLAIN", "a"), ("PLAIN", "b"), ("PLAIN", "c")])
    assert lines[0].baseline == FIRST_BASELINE
    assert lines[1].baseline - lines[0].baseline == pytest.approx(69.3, abs=0.1)
    assert lines[2].baseline - lines[1].baseline == pytest.approx(69.3, abs=0.1)


def test_wrapping_measures_glyph_widths_and_prefers_spaces():
    from remarksync.textmetrics import text_width

    plain = STYLES["PLAIN"]
    first = "A local-first library, viewer, search index and format compiler for reMarkable"
    assert text_width(first, plain) < 1152 < text_width(first + " tablets", plain)
    # the README's first paragraph wrapped on the tablet before "tablets"
    lines = wrap(first + " tablets \u2014 everything works from your own raw backups, no reMarkable Cloud required.", plain)
    assert lines[0] == first
    assert lines[1].startswith("tablets")
    # This used to assert that a 400-character run came back as one line, on the
    # belief that the tablet never breaks inside a word. It does: the device's own
    # export of an unbroken heading came back as several lines. So the run is
    # broken, into pieces that each fit, losing nothing.
    run = "supercalifragilistic" * 20
    pieces = wrap(run, plain)
    assert len(pieces) > 1
    assert "".join(pieces) == run
    assert all(text_width(piece, plain) <= 1152 for piece in pieces)
    assert wrap("", plain) == [""]
    assert wrap("    cd E:\\Source", plain) == ["    cd E:\\Source"]  # code keeps its indent
    # the heading on the styles page wrapped after "wraps"
    assert wrap("This is me doing a heading that wraps well see", STYLES["HEADING"]) == ["This is me doing a heading that wraps", "well see"]
    assert chars_per_line(STYLES["BULLET2"]) < chars_per_line(STYLES["BULLET"])


def test_style_codes_and_unknowns():
    assert style_name(2) == "HEADING" and style_name(10) == "NUMBERED" and style_name(8) == "CHECKBOX2"
    assert style_name(99) == "PLAIN"
    assert style("nonsense").name == "PLAIN"
    assert style("BULLET2").indent == 96


def test_block_height_and_page_budget():
    assert block_height([("PLAIN", "one line")]) == pytest.approx(FIRST_BASELINE + 0.22 * 31.9)
    assert lines_per_page() == 22 + 1  # 23: the old count forgot the first line's own room
    assert lines_per_page(2160) == 27  # a Paper Pro page holds four more
    # a heading takes more room than a body line, which is what a per-paragraph
    # charge used to get wrong when it assumed body text came next
    assert block_height([("HEADING", "h"), ("PLAIN", "p")]) > block_height([("PLAIN", "p")] * 2)


def test_wrapping_follows_the_block_width_not_the_usual_one():
    """A typed block is a fixed width, but not always the same fixed width.
    Real files carry 1152 (34 blocks) and 936 (6 blocks), each centred on the
    page. Wrapping against the common value would fit too much on a line in a
    narrower block, giving it too few lines, too short a height, and baselines
    the device does not use -- and anchored ink is pinned to those baselines.
    """
    from remarksync.textmetrics import TEXT_WIDTH, lay_out_lines, wrap

    plain = style("PLAIN")
    # enough words that the difference cannot round away: at 1152 this is 8
    # lines and at 936 it is 10, where 40 words would be 4 lines either way
    text = " ".join(["alpha"] * 100)

    wide = wrap(text, plain, TEXT_WIDTH)
    narrow = wrap(text, plain, 936.0)
    assert len(narrow) > len(wide), "a narrower block must wrap onto more lines"

    # and the default is still the usual width, so existing callers are unmoved
    assert wrap(text, plain) == wide

    lines_wide = lay_out_lines([("PLAIN", text)], TEXT_WIDTH)
    lines_narrow = lay_out_lines([("PLAIN", text)], 936.0)
    assert len(lines_narrow) > len(lines_wide)
    assert lines_narrow[-1].baseline > lines_wide[-1].baseline


def test_a_narrow_block_is_taller_than_a_wide_one():
    """Block height follows from the wrap, so it has to follow the width too."""
    from remarksync.textmetrics import TEXT_WIDTH, block_height

    paragraphs = [("PLAIN", " ".join(["alpha"] * 100))]
    assert block_height(paragraphs, 936.0) > block_height(paragraphs, TEXT_WIDTH)


def test_a_word_too_long_for_the_column_is_broken_mid_word():
    """The tablet breaks inside a word when the word alone will not fit.

    Measured on the device: a heading of repeated 'g' in a 936 px column came
    back as six lines whose first held 27 characters. 27 g's measure 908.1 px
    and 28 measure 941.7, so the device breaks at the last character that fits.
    'g' is one of the characters whose advance was measured from the tablet's
    own export, which is what makes this a real number rather than a guess.

    Before this, wrap() only ever broke on spaces, so an unbroken run became a
    single line several times wider than its column -- and since the line count
    sets the block's height, the baselines and the compiler's pagination, the
    error did not stay in the text.
    """
    from remarksync.textmetrics import text_width, wrap

    heading = style("HEADING")
    lines = wrap("g" * 60, heading, 936.0)
    assert len(lines) > 1, "an unbreakable run must still be broken"
    assert len(lines[0]) == 27, f"expected the device's 27 characters, got {len(lines[0])}"
    assert all(text_width(line, heading) <= 936.0 for line in lines)
    assert "".join(lines) == "g" * 60, "no characters may be lost or added"


def test_breaking_mid_word_still_prefers_spaces():
    """Character-level breaking is a last resort, not the first choice: a run
    of ordinary words must still break between them."""
    from remarksync.textmetrics import wrap

    plain = style("PLAIN")
    lines = wrap(" ".join(["alpha"] * 40), plain, 936.0)
    assert all(" " in line or len(line.split()) == 1 for line in lines)
    assert not any(line.startswith("lpha") or line.endswith("alph") for line in lines)


def test_a_long_word_after_short_ones_breaks_without_losing_text():
    from remarksync.textmetrics import wrap

    heading = style("HEADING")
    text = "short " + "z" * 80
    lines = wrap(text, heading, 936.0)
    assert " ".join(lines).replace("  ", " ").replace(" ", "") == text.replace(" ", "")
    assert lines[0].startswith("short")


def test_a_line_stops_eight_px_short_of_the_declared_width():
    """The tablet does not give text the whole box. Its own export of a heading
    of repeated 'a' in the 740 px column put 21 on a line, where 22 measure
    733 px and would fit a limit of 740; and in the 1152 column a line of 36
    characters 1140 px wide stayed whole, so the limit is not far below."""
    from remarksync.textmetrics import line_width, text_width, wrap

    heading = style("HEADING")
    assert line_width(heading, 740.0) == 732.0
    assert text_width("a" * 22, heading) < 740.0
    assert [len(line) for line in wrap("a" * 60, heading, 740.0)] == [21, 21, 18]

    kept = "dzbgdzbgdzbgdzhgdzbgfzhdzjgfzjfgzjfg"
    assert wrap(kept + "znv", heading, 1152.0)[0] == kept


def test_the_inset_is_fixed_not_a_share_of_the_width():
    """At 1152 the limit was pinned to 1143.4-1144.2. Scaled down to the 740
    column that would be 734.7, which lets a 22nd 'a' in; the tablet refused it."""
    from remarksync.textmetrics import line_width, text_width

    heading = style("HEADING")
    proportional = 740.0 * line_width(heading, 1152.0) / 1152.0
    assert line_width(heading, 740.0) < text_width("a" * 22, heading) < proportional


def test_a_ligature_is_one_glyph_and_the_faces_disagree_about_which():
    """Measured from the tablet's export of a paragraph per pair: the serif
    ligates seven f-pairs, the sans only fi and fl."""
    from remarksync.textmetrics import clusters, text_width

    heading, plain = style("HEADING"), style("PLAIN")
    assert clusters("fjord", heading) == ["fj", "o", "r", "d"]
    assert clusters("fjord", plain) == ["f", "j", "o", "r", "d"]
    # greedy from the left, the way shaping works
    assert clusters("fff", heading) == ["ff", "f"]

    # the serif ligature is narrower than its letters apart; the sans face,
    # which does not ligate the pair at all, is not
    assert text_width("fj", heading) < text_width("f", heading) + text_width("j", heading)
    assert text_width("fj", plain) == pytest.approx(text_width("f", plain) + text_width("j", plain))


def test_a_line_break_never_splits_a_ligature():
    """The case the three-width worksheet caught: the tablet ended a line at
    'hjh' though another 'f' would have fitted, because the 'f' belonged to an
    'fj' that would not."""
    from remarksync.textmetrics import line_width, text_width, wrap

    heading = style("HEADING")
    head = "ggvggggvvvvvvvvvvvvvvvvvvhjh"
    limit = line_width(heading, 936.0)
    assert text_width(head + "f", heading) <= limit < text_width(head + "fj", heading)

    lines = wrap(head + "fjmgf", heading, 936.0)
    assert lines[0] == head
    assert lines[1].startswith("fj")


def test_both_faces_measure_the_punctuation_a_note_carries():
    """The serif face had no punctuation at all until 2026-09-18, so a heading
    with a comma in it wrapped on the fallback average."""
    from remarksync.textmetrics import ADVANCE_SANS, ADVANCE_SERIF

    for table in (ADVANCE_SERIF, ADVANCE_SANS):
        for char in ".,;:'\"!?()-/&%#@[]{}<>=+*_|~" + "–—‘’“”…":
            assert char in table, f"{char!r} is still on the fallback"

    # the grave accent is the one deliberate hole: it composes with the letter
    # after it, so the measurement read it wider than 'W' and was left out
    assert "`" not in ADVANCE_SERIF
    assert "`" in ADVANCE_SANS


def test_punctuation_is_narrow_where_the_fallback_was_average():
    """Why it mattered: the marks that actually recur in prose are half the
    width the fallback assumed, so a line of them wrapped far too early."""
    from remarksync.textmetrics import DEFAULT_ADVANCE_SERIF, text_width

    heading = style("HEADING")
    for char in ".,'":
        measured = text_width(char, heading) / heading.size
        assert measured < DEFAULT_ADVANCE_SERIF / 1.8

    # a quoted sentence in a heading: 10 marks that were each 0.52 before
    quoted = '"It is," she said, "quite so."'
    assert text_width(quoted, heading) < 0.80 * DEFAULT_ADVANCE_SERIF * heading.size * len(quoted)


def test_glyphs_start_four_pixels_inside_the_block():
    """Measured from the first glyph of every line the tablet drew, on both
    papers, all three column widths and both faces: pos_x + 4.0. The other four
    of the eight-pixel inset are at the right-hand end, where the wrap stops."""
    from remarksync.textmetrics import TEXT_LEFT_PAD, TEXT_WRAP_INSET

    assert TEXT_LEFT_PAD == 4.0
    assert TEXT_LEFT_PAD * 2 == TEXT_WRAP_INSET


def test_a_line_broken_inside_a_word_consumes_no_separator():
    """``TextLine.chars`` says how far into the paragraph a line reaches, and
    anchored ink is placed by it. A break between words swallows a space; a
    break inside a word swallows nothing. Charging one for every break left
    each line after a mid-word break a character late."""
    word = "g" * 300  # one unbroken word, several lines long
    lines = lay_out_lines([("PLAIN", word)])
    assert len(lines) > 2
    assert "".join(ln.text for ln in lines) == word
    reached = 0
    for ln in lines:
        reached += len(ln.text)
        assert ln.chars == reached
    assert lines[-1].chars == len(word)

    # words still pay for the space the line broke on
    text = ("word " * 100).strip()
    lines = lay_out_lines([("PLAIN", text)])
    assert len(lines) > 1 and lines[-1].chars == len(text)
    for ln in lines:
        assert text[ln.chars - len(ln.text):ln.chars] == ln.text

    # and both kinds of break in one paragraph
    mixed = "short words then " + "g" * 200 + " and more words after it"
    lines = lay_out_lines([("PLAIN", mixed)])
    assert lines[-1].chars == len(mixed)
    for ln in lines:
        assert mixed[ln.chars - len(ln.text):ln.chars] == ln.text


def test_lines_can_be_broken_by_another_wrap_with_the_same_rhythm():
    def halves(text, style, width):
        return [text[: len(text) // 2], text[len(text) // 2:]]

    ours = lay_out_lines([("PLAIN", "abcdef"), ("HEADING", "uvwxyz")], wrap_fn=halves)
    assert [ln.text for ln in ours] == ["abc", "def", "uvw", "xyz"]
    assert [ln.chars for ln in ours] == [3, 6, 3, 6]
    assert [ln.first for ln in ours] == [True, False, True, False]
    assert ours[0].baseline == FIRST_BASELINE


def test_list_markers_count_up_and_start_again():
    from remarksync.textmetrics import marker_label, numbered

    lines = lay_out_lines([("NUMBERED", "a"), ("NUMBERED", "b " * 80), ("NUMBERED2", "x"), ("NUMBERED2", "y"),
                           ("NUMBERED", "again"), ("BULLET", "dot"), ("CHECKBOX", "box"), ("PLAIN", "text")])
    labels = [marker_label(st.marker, number) for ln, st, number in numbered(lines) if ln.first]
    assert labels == ["1.", "2.", "a.", "b.", "1.", "\u2022", None, None]
    # a wrapped paragraph keeps its number on every line of it
    assert [number for ln, st, number in numbered(lines) if ln.paragraph == 1] == [2, 2]
    assert marker_label("letter", 27) == "a." and marker_label("bullet2") == "\u25e6"
