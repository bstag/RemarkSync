"""Page frame: where a decoded page lands on the canvas.

Every rule here is a plain function of a PageModel; no .rm file, no Qt.
The policy was verified against the reMarkable desktop app: content stays
where the tablet put it and the canvas grows around the paper.
"""

import pytest

from remarksync.layout import BOTTOM_MARGIN, PDF_SCALE, base_page_rect, lay_out
from remarksync.rmdata import PAGE_HEIGHT, PAGE_WIDTH, GlyphHighlight, PageModel, Stroke, TextBlock

W, H = PAGE_WIDTH, PAGE_HEIGHT


def stroke(*points):
    return Stroke(points=list(points), color="#1a1a1a", width=2.0)


# ------------------------------------------------------------- positions kept


def test_content_on_the_page_stays_exactly_where_it_was():
    page = PageModel(strokes=[stroke((169, 198), (888, 268))])
    frame = lay_out(page, template="ruled")
    assert frame.page.strokes[0].points == [(169, 198), (888, 268)]
    assert frame.canvas_size == (W, H)
    assert frame.paper == (0, 0, W, H)
    assert frame.origin == (0, 0)
    assert frame.template == "ruled"
    assert frame.underlay is None
    assert frame.dividers == []


def test_text_blocks_stay_put_and_count_toward_extent():
    page = PageModel(texts=[TextBlock(text="hi", pos_x=234, pos_y=300, width=936, height=70)])
    frame = lay_out(page)
    assert (frame.page.texts[0].pos_x, frame.page.texts[0].pos_y) == (234, 300)
    assert frame.canvas_size == (W, H)


def test_empty_page_is_one_blank_page():
    frame = lay_out(PageModel(), template="grid")
    assert frame.canvas_size == (W, H)
    assert frame.template == "grid"
    assert frame.page.strokes == []


def test_lay_out_does_not_mutate_input():
    page = PageModel(strokes=[stroke((-50, 900), (700, 3000))])
    lay_out(page)
    assert page.strokes[0].points == [(-50, 900), (700, 3000)]


# ------------------------------------------------------------- canvas growth


def test_bottom_overflow_grows_canvas_and_marks_paper_edge():
    page = PageModel(strokes=[stroke((100, 2100), (400, 2200), (700, 2400))])
    frame = lay_out(page, template="ruled", page_index=0)
    assert frame.page.strokes[0].points[0] == (100, 2100)  # untouched
    assert frame.height == 2400 + BOTTOM_MARGIN
    assert frame.width == W
    assert frame.paper == (0, 0, W, H)
    assert frame.dividers == [((0, H), (W, H))]


def test_endless_canvas_shows_template_only_on_first_page():
    page = PageModel(strokes=[stroke((100, 100), (100, 3000))])
    assert lay_out(page, template="ruled", page_index=0).template == "ruled"
    assert lay_out(page, template="ruled", page_index=3).template is None


def test_side_overflow_shifts_paper_right_and_adds_dividers():
    # ink from x=-498 (left of the paper) to x=1802 (right of it)
    page = PageModel(strokes=[stroke((-498, 100), (1802, 200))])
    frame = lay_out(page)
    assert frame.origin == (498, 0)
    assert frame.paper == (498, 0, W, H)
    assert frame.width == 1802 + 498
    assert frame.height == H
    assert frame.page.strokes[0].points[0] == (0, 100)  # shifted onto the canvas
    assert frame.page.strokes[0].points[1] == (1802 + 498, 200)
    assert ((498, 0), (498, H)) in frame.dividers
    assert ((498 + W, 0), (498 + W, H)) in frame.dividers
    assert len(frame.dividers) == 2


def test_top_overflow_shifts_paper_down():
    page = PageModel(strokes=[stroke((100, -300), (200, 50))])
    frame = lay_out(page)
    assert frame.origin == (0, 300)
    assert frame.page.strokes[0].points[0] == (100, 0)
    assert frame.height == H + 300
    assert frame.dividers == [((0, 300), (W, 300))]


def test_highlights_are_shifted_with_the_ink():
    hl = GlyphHighlight(text="verse", rectangles=[(102, 1480, 380, 34)])
    page = PageModel(strokes=[stroke((-100, 10), (10, 10))], highlights=[hl])
    frame = lay_out(page)
    assert frame.page.highlights[0].rectangles == [(202, 1480, 380, 34)]


# ------------------------------------------------------------- base pages

# The tablet places a base PDF at a fixed scale (tablet px per PDF point),
# centred on the screen width with its top at y=0. The fitted value comes
# from the tablet's own snap-highlight rectangles; these numbers are what
# those rectangles say.


def test_base_page_is_the_paper_and_the_underlay():
    frame = lay_out(PageModel(), template="ruled", base_size=(432, 576))  # the NIV bible, 3:4
    w, h = 432 * PDF_SCALE, 576 * PDF_SCALE
    assert frame.template is None
    assert frame.paper == pytest.approx((0, 0, w, h))  # canvas starts at the page's left edge
    assert frame.underlay == frame.paper
    assert frame.canvas_size == pytest.approx((w, h))
    assert frame.dividers == []


def test_base_page_placement_in_page_coordinates():
    # a 3:4 page is 1362 px wide, so it sits 21 px in from the screen edge
    x, y, w, h = base_page_rect((432, 576))
    assert (w, h) == pytest.approx((1362.0, 1816.0), abs=0.5)
    assert x == pytest.approx((PAGE_WIDTH - w) / 2)
    assert y == 0


def test_ink_stays_aligned_with_a_base_page_that_starts_off_screen():
    # US Letter is wider than the screen: the page starts 263 px left of x=0
    px, py, pw, ph = base_page_rect((612, 792))
    assert px == pytest.approx(-262.7, abs=0.5)
    page = PageModel(strokes=[stroke((100, 100), (200, 200))])
    frame = lay_out(page, base_size=(612, 792))
    assert frame.paper == pytest.approx((0, 0, pw, ph))
    # ink written at screen x=100 is 100 - px from the page's left edge
    assert frame.page.strokes[0].points[0] == pytest.approx((100 - px, 100))
    assert frame.canvas_size == pytest.approx((pw, ph))


def test_narrow_book_page_is_narrower_than_the_screen():
    px, py, pw, ph = base_page_rect((379, 613))
    assert pw < PAGE_WIDTH and px > 0
    frame = lay_out(PageModel(), base_size=(379, 613))
    assert frame.paper == pytest.approx((0, 0, pw, ph))
    assert frame.height == pytest.approx(ph)  # taller than the screen; canvas follows the page


def test_content_overflowing_a_base_page_grows_the_canvas():
    px, py, pw, ph = base_page_rect((432, 576))
    page = PageModel(strokes=[stroke((1500, 100), (1700, ph + 200))])
    frame = lay_out(page, base_size=(432, 576))
    assert frame.width == pytest.approx(1700 - px)
    assert frame.height == pytest.approx(ph + 200 + BOTTOM_MARGIN)
    assert ((pw, 0), (pw, ph)) in [tuple(map(tuple, d)) for d in frame.dividers] or any(
        abs(d[0][0] - pw) < 1e-6 for d in frame.dividers
    )
    assert any(abs(d[0][1] - ph) < 1e-6 for d in frame.dividers)


def test_highlights_are_shifted_with_the_base_page():
    hl = GlyphHighlight(text="verse", rectangles=[(945, 1599, 103, 34)])
    frame = lay_out(PageModel(highlights=[hl]), base_size=(432, 576))
    px = base_page_rect((432, 576))[0]
    assert frame.page.highlights[0].rectangles[0] == pytest.approx((945 - px, 1599, 103, 34))
