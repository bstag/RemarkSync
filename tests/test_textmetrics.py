"""Text metrics: the tablet's line rhythm, fitted against its PDF exports.

The measured baselines below come from the desktop app's export of a page
typed with every paragraph style (2026-09-04); they are the regression
surface for the model.
"""

import pytest

from remarksync.textmetrics import (FIRST_BASELINE, STYLES, TEXT_POS_Y, block_height, chars_per_line,
                                    lay_out_lines, lines_per_page, paragraph_advance, style, style_name, wrap)

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


def test_first_baseline_and_body_pitch():
    lines = lay_out_lines([("PLAIN", "a"), ("PLAIN", "b"), ("PLAIN", "c")])
    assert lines[0].baseline == FIRST_BASELINE
    assert lines[1].baseline - lines[0].baseline == pytest.approx(69.3, abs=0.1)
    assert lines[2].baseline - lines[1].baseline == pytest.approx(69.3, abs=0.1)


def test_wrapping_measures_glyph_widths_and_never_breaks_a_word():
    from remarksync.textmetrics import text_width

    plain = STYLES["PLAIN"]
    first = "A local-first library, viewer, search index and format compiler for reMarkable"
    assert text_width(first, plain) < 1152 < text_width(first + " tablets", plain)
    # the README's first paragraph wrapped on the tablet before "tablets"
    lines = wrap(first + " tablets \u2014 everything works from your own raw backups, no reMarkable Cloud required.", plain)
    assert lines[0] == first
    assert lines[1].startswith("tablets")
    assert wrap("supercalifragilistic" * 20, plain) == ["supercalifragilistic" * 20]
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
    assert lines_per_page() == 22
    assert paragraph_advance("PLAIN", "short") == pytest.approx(69.3, abs=0.1)
    assert paragraph_advance("HEADING", "short") > paragraph_advance("PLAIN", "short")
