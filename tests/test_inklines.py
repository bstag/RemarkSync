"""Line cutting by stroke geometry."""

from remarksync.inklines import cut_lines, render_line
from remarksync.rmdata import PageModel, Stroke


def scribble(y, x0=100, x1=900, n=12, tall=30):
    """A line of 'writing': short strokes along y, with ascenders/descenders."""
    strokes = []
    step = (x1 - x0) / n
    for i in range(n):
        x = x0 + i * step
        dy = tall if i % 3 == 0 else 12  # some letters reach further up/down
        strokes.append(Stroke(points=[(x, y - dy), (x + 8, y + dy), (x + 16, y - dy)], color="#000", width=2))
    return strokes


def test_lines_are_cut_between_writing_lines_even_when_extents_overlap():
    # three lines 80 px apart whose ascenders (30 px) overlap the next line's descenders
    page = PageModel(strokes=scribble(200) + scribble(280) + scribble(360))
    lines = cut_lines(page)
    assert len(lines) == 3
    assert [len(ln.strokes) for ln in lines] == [12, 12, 12]
    assert lines[0].top < lines[1].top < lines[2].top
    assert all(abs((ln.top + ln.bottom) / 2 - y) < 5 for ln, y in zip(lines, (200, 280, 360)))


def test_dots_join_their_line_and_highlighter_is_ignored():
    page = PageModel(strokes=scribble(200) + [
        Stroke(points=[(500, 170), (501, 171)], color="#000", width=2),  # an i-dot above the line
        Stroke(points=[(100, 205), (900, 205)], color="#ff0", width=30, highlighter=True),
    ])
    lines = cut_lines(page)
    assert len(lines) == 1
    assert len(lines[0].strokes) == 13


def test_render_line_scales_to_the_requested_height():
    page = PageModel(strokes=scribble(200))
    img = render_line(cut_lines(page)[0], height=64)
    assert img.size[1] == 64 and img.size[0] > 64
    assert img.getextrema() == (0, 255)  # black ink on white


def test_empty_page_has_no_lines():
    assert cut_lines(PageModel()) == []
