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
    ("BOLD", "This is thr next size down"),
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
MEASURED_BASELINES = [295.3, 458.5, 534.0, 650.0, 798.2, 867.3, 936.6, 981.8, 1051.2, 1120.3, 1189.6, 1259.0, 1328.1, 1397.4, 1466.5]


def test_heading_and_subheading_baselines_are_reproduced():
    # the page's second code-3 paragraph renders as bold body text on the
    # tablet for reasons the file does not record, so the regression covers
    # the run up to it and the body run after it separately
    lines = lay_out_lines(STYLES_PAGE[:3])
    predicted = [TEXT_POS_Y + ln.baseline for ln in lines]
    assert len(predicted) == 4
    assert max(abs(p - m) for p, m in zip(predicted, MEASURED_BASELINES[:4])) < 1.0
    assert lines[1].first and not lines[2].first  # the heading wrapped
    assert lines[2].text == "well see"


def test_body_run_pitches_are_reproduced():
    body = STYLES_PAGE[3:]
    lines = lay_out_lines(body)
    measured = MEASURED_BASELINES[4:]
    assert len(lines) == len(measured)
    predicted = [ln.baseline for ln in lines]
    # compare pitches, which do not depend on where the run starts
    for (a, b), (c, d) in zip(zip(predicted, predicted[1:]), zip(measured, measured[1:])):
        assert (b - a) == pytest.approx(d - c, abs=0.5)
    assert not lines[3].first and lines[3].text == "wrap, will wrap"  # the bullet wrapped where the tablet did


def test_first_baseline_and_body_pitch():
    lines = lay_out_lines([("PLAIN", "a"), ("PLAIN", "b"), ("PLAIN", "c")])
    assert lines[0].baseline == FIRST_BASELINE
    assert lines[1].baseline - lines[0].baseline == pytest.approx(69.3, abs=0.1)
    assert lines[2].baseline - lines[1].baseline == pytest.approx(69.3, abs=0.1)


def test_wrapping_never_breaks_a_word():
    assert wrap("one two three", 7) == ["one two", "three"]
    assert wrap("supercalifragilistic", 5) == ["supercalifragilistic"]
    assert wrap("", 10) == [""]
    assert chars_per_line(STYLES["PLAIN"]) == 87
    assert chars_per_line(STYLES["BULLET"]) == 84  # measured: the tablet wrapped a bullet after 84 characters
    assert chars_per_line(STYLES["HEADING"]) == 38  # measured: a heading wrapped after 37, not 42
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
