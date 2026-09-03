"""Page frame: where a decoded page lands on the canvas.

Every rule here is a plain function of a PageModel; no .rm file, no Qt.
The policy was verified against the reMarkable desktop app: content stays
where the tablet put it and the canvas grows around the paper.
"""

import pytest

from remarksync.layout import BOTTOM_MARGIN, fit_rect, lay_out
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


# ------------------------------------------------------------- underlay


def test_base_document_page_is_drawn_instead_of_template():
    frame = lay_out(PageModel(), template="ruled", base_size=(432, 576))
    assert frame.canvas_size == (W, H)
    assert frame.template is None
    assert frame.underlay == (0, 0, W, H)  # 3:4 page fills the paper exactly
    assert frame.dividers == []


def test_underlay_keeps_base_page_aspect_letter_fits_by_width():
    frame = lay_out(PageModel(), base_size=(612, 792))
    x, y, w, h = frame.underlay
    assert w == W
    assert h == pytest.approx(W * 792 / 612)
    assert x == 0
    assert y == pytest.approx((H - h) / 2)


def test_underlay_keeps_base_page_aspect_narrow_book_fits_by_height():
    frame = lay_out(PageModel(), base_size=(379, 613))
    x, y, w, h = frame.underlay
    assert h == pytest.approx(H)
    assert w == pytest.approx(H * 379 / 613)
    assert y == pytest.approx(0)
    assert x == pytest.approx((W - w) / 2)


def test_underlay_follows_paper_after_overflow():
    page = PageModel(strokes=[stroke((-100, 10), (10, 10))])
    frame = lay_out(page, base_size=(432, 576))
    assert frame.underlay == (100, 0, W, H)


def test_fit_rect_degenerate_size_returns_container():
    assert fit_rect((0, 0), (5, 5, 10, 10)) == (5, 5, 10, 10)
