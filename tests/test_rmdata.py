"""Page decoder: bytes in, PageModel in page coordinates out."""

import pytest

from remarksync.backup import iter_docs, scan_root
from remarksync.rmdata import PAGE_WIDTH, decode_page, page_text
from rmscene import scene_items as si

from .conftest import INK_UUID


def _ink_doc(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_root(backup_root))}
    return docs[INK_UUID]


def test_decode_page_strokes(backup_root):
    doc = _ink_doc(backup_root)
    page = decode_page(doc.pages[0].rm_path)
    # eraser line is dropped, 2 ink strokes remain
    assert len(page.strokes) == 2
    ballpoint = page.strokes[0]
    assert ballpoint.color == "#1a1a1a"
    assert ballpoint.highlighter is False
    assert ballpoint.tool == "BALLPOINT_1"
    assert ballpoint.widths == [2.25, 2.25, 2.25]  # the tablet's per-point width
    assert ballpoint.width == 2.25
    assert len(ballpoint.points) == 3
    # page coordinates: v6 x is centre-relative and gets the +W/2 shift,
    # y is already measured from the page top
    (x0, y0), (x1, y1), (x2, y2) = ballpoint.points
    assert (x0, y0) == (100 + PAGE_WIDTH / 2, 200)
    assert x1 > x0 and x2 > x1 and y2 > y0  # down-right slope kept


def test_highlighter_flag_and_scale(backup_root):
    doc = _ink_doc(backup_root)
    page = decode_page(doc.pages[0].rm_path)
    hl = page.strokes[1]
    assert hl.highlighter is True
    assert hl.color == "#f2b800"
    assert hl.width == 30.0  # the tablet's stored width, thickness already applied
    assert page.strokes[1].points[0][1] > page.strokes[0].points[0][1]  # below ballpoint


def test_typed_text_extracted(backup_root):
    doc = _ink_doc(backup_root)
    page = decode_page(doc.pages[0].rm_path)
    assert "quantum computing" in page_text(page)
    assert any("quantum" in t.text for t in page.texts)
    block = next(t for t in page.texts if "quantum" in t.text)
    assert block.height == pytest.approx(61 + 0.22 * 31.9)  # first baseline plus the descent
    # text y is measured from the page top, like ink: no centre shift
    assert block.pos_y == 234.0  # rmscene's simple_text_document default


def test_pressure_varying_widths_are_kept_per_point():
    from .conftest import line, page_rm

    blob = page_rm(strokes=[line(si.PenColor.BLACK, si.Pen.BALLPOINT_2, [(0, 0), (10, 0), (20, 0)], widths=[2.0, 3.0, 4.5])])
    stroke = decode_page(blob).strokes[0]
    assert stroke.widths == [2.0, 3.0, 4.5]
    assert stroke.width == pytest.approx(9.5 / 3)
    assert not stroke.uniform


def test_line_colour_comes_from_the_file_when_present():
    from .conftest import line, page_rm

    ln = line(si.PenColor.HIGHLIGHT, si.Pen.HIGHLIGHTER_2, [(0, 0), (10, 0)], point_width=30.0)
    ln.color_rgba = (190, 234, 254, 255)  # the blue highlighter
    shader = line(si.PenColor.HIGHLIGHT, si.Pen.SHADER, [(0, 20), (10, 20)], point_width=8.0)
    shader.color_rgba = (33, 30, 28, 64)
    marker = line(si.PenColor.RED, si.Pen.MARKER_2, [(0, 40), (10, 40)], point_width=6.0)
    page = decode_page(page_rm(strokes=[ln, shader, marker]))
    blue, grey, red = page.strokes
    assert blue.color == "#beeafe" and blue.highlighter and blue.alpha == pytest.approx(110 / 255)
    assert grey.color == "#211e1c" and grey.highlighter and grey.alpha == pytest.approx(64 / 255)
    assert red.color == "#c23132" and not red.highlighter and red.alpha == 1.0  # marker is opaque


def test_pen_opacity_and_marker_width_factor():
    from .conftest import line, page_rm

    pencil = line(si.PenColor.BLACK, si.Pen.PENCIL_2, [(0, 0), (10, 0)], point_width=6.0)
    marker = line(si.PenColor.BLACK, si.Pen.MARKER_2, [(0, 20), (10, 20)], point_width=20.0)
    ball = line(si.PenColor.BLACK, si.Pen.BALLPOINT_2, [(0, 40), (10, 40)], point_width=2.25)
    page = decode_page(page_rm(strokes=[pencil, marker, ball]))
    p, m, b = page.strokes
    assert p.alpha == 0.7 and not p.highlighter and p.widths == [6.0, 6.0]
    assert m.alpha == 1.0 and m.widths == [17.0, 17.0]  # chisel tip: 85% of the stored width
    assert b.alpha == 1.0 and b.widths == [2.25, 2.25]


def test_points_without_widths_fall_back_to_the_pen_table():
    from .conftest import line, page_rm

    blob = page_rm(strokes=[line(si.PenColor.BLACK, si.Pen.FINELINER_2, [(0, 0), (10, 0)], thickness_scale=2.0, point_width=0.0)])
    stroke = decode_page(blob).strokes[0]
    assert stroke.widths == [9.0, 9.0]  # 4.5 * 2.0
    assert stroke.uniform


def test_garbage_decodes_to_blank_model(tmp_path):
    bad = tmp_path / "garbage.rm"
    bad.write_bytes(b"\x00\x01not an rm file")
    page = decode_page(bad)
    assert page.error is not None
    assert page.strokes == [] and page.texts == []


def test_decode_v5_lines_file():
    from .conftest import page_rm_v5

    blob = page_rm_v5(
        [
            (2, 0, 2.2, [(100, 150), (140, 160), (180, 175)]),  # ballpoint
            (0, 0, 2.0, [(100, 300), (300, 320), (500, 310)]),  # id-prefixed line
            # A wide, drawable stroke. This was 25.0 px, an arbitrary value
            # chosen before the eraser rule existed; anything over
            # V5_ERASER_WIDTH now reads as an erase, and 14 px sits in the
            # band real files show as broad-but-drawable (9.5 to 19.75).
            (5, 0, 14.0, [(90, 500), (700, 500)]),
        ]
    )
    page = decode_page(blob)
    assert page.error is None
    assert len(page.strokes) == 3
    # widths now come from the float32 stored at each point, so they carry
    # float32 rounding rather than the exact float the fixture wrote
    assert page.strokes[0].width == pytest.approx(2.2)
    assert page.strokes[1].width == pytest.approx(2.0)
    assert page.strokes[2].highlighter is True  # wide stroke = highlighter
    assert page.strokes[0].color == "#1a1a1a"
    # v5 points are already page coordinates: passed through untouched
    assert page.strokes[0].points[0] == (100.0, 150.0)
    assert page.strokes[0].points[0][1] < page.strokes[1].points[0][1] < page.strokes[2].points[0][1]


def test_v5_uses_the_width_stored_at_every_point():
    """A v5 point carries its own width; the line's width is only the pen's
    thickness setting. A pressure-varying stroke must not come out uniform,
    or v5 ink renders far lighter than the tablet draws it."""
    from .conftest import page_rm_v5

    blob = page_rm_v5(
        [(0, 0, 2.0, [(100, 100), (200, 100), (300, 100)])],
        point_widths=[[2.0, 5.5, 9.0]],
    )
    stroke = decode_page(blob).strokes[0]
    assert stroke.widths == pytest.approx([2.0, 5.5, 9.0])
    assert stroke.width == pytest.approx((2.0 + 5.5 + 9.0) / 3)
    assert not stroke.uniform


def test_v5_point_data_is_never_mistaken_for_a_line_record():
    """Real pages carry a per-point width of 2.9629, whose bytes a
    mis-aligned reader turns into a 29.63-wide float64 'line' of 15 points
    pinned to y=0. Parsing the declared structure must make that impossible.
    """
    from .conftest import page_rm_v5

    written = [(0, 0, 2.0, [(50.0 + i * 7, 300.0 + i) for i in range(40)]) for _ in range(6)]
    blob = page_rm_v5(written, point_widths=[[2.9629] * 40 for _ in written])
    page = decode_page(blob)
    assert len(page.strokes) == len(written)
    assert all(s.width == pytest.approx(2.9629) for s in page.strokes)
    assert not any(s.highlighter for s in page.strokes)
    # nothing pinned to the top edge, which is what the phantoms looked like
    assert all(min(y for _, y in s.points) >= 300.0 for s in page.strokes)


def test_v5_scanner_survives_garbage_between_lines():
    from .conftest import page_rm_v5

    blob = page_rm_v5([(2, 0, 2.2, [(10, 10), (40, 40)])])
    # splice junk that is not a valid line record between header and data
    torn = blob[:59] + b"\xff" * 8 + blob[59:]
    page = decode_page(torn)
    # junk is skipped; nothing valid remains after the layer counts
    assert page.error is None and isinstance(page.strokes, list)


def test_glyph_highlights_in_page_coordinates():
    from .conftest import page_rm

    glyph = si.GlyphRange(
        start=100, length=5, text="verse", color=si.PenColor.YELLOW,
        rectangles=[
            si.Rectangle(x=-600.0, y=1480.0, w=380.0, h=34.0),
            si.Rectangle(x=-625.0, y=1476.0, w=29.0, h=21.5),  # verse-number box
        ],
    )
    page = decode_page(page_rm(glyphs=[glyph]))
    assert page.highlights[0].text == "verse"
    assert len(page.highlights[0].rectangles) == 1  # sub-height rect dropped
    x, y, w, h = page.highlights[0].rectangles[0]
    assert abs(x - 102.0) < 0.01  # -600 + 702 (centre-relative shift)
    assert abs(y - 1480.0) < 0.01  # y already from page top
    assert "verse" in page_text(page)


def test_v5_eraser_strokes_are_not_drawn():
    """The v5 eraser is recorded as a stroke of its own, very wide, and the
    tablet ignores those records when it renders: whatever survived the erase
    is already what the file still holds. Drawing them paints solid bars over
    the surviving handwriting -- on one real page six of them were 91% of the
    ink mass and hid four lines of text.
    """
    from .conftest import page_rm_v5

    blob = page_rm_v5(
        [
            (0, 0, 2.0, [(100, 100), (200, 105), (300, 100)]),  # writing
            (0, 0, 2.0, [(100, 300), (900, 320), (100, 340)]),  # an eraser sweep
        ],
        point_widths=[[3.0, 3.2, 3.0], [40.0, 40.0, 40.0]],
    )
    page = decode_page(blob)
    assert len(page.strokes) == 1
    assert page.strokes[0].points[0] == (100.0, 100.0)


def test_v5_keeps_wide_strokes_that_are_not_eraser_wide():
    """The eraser rule must not swallow genuinely broad pens. Measured against
    the tablet's own PDF export, dropping everything over 8 px wrecked two
    real pages; the erasers seen are 40 px and 57.6 px.
    """
    from .conftest import page_rm_v5

    blob = page_rm_v5(
        [(0, 0, 2.4, [(100, 300), (900, 320)])],
        point_widths=[[14.0, 14.0]],
    )
    page = decode_page(blob)
    assert len(page.strokes) == 1
    assert page.strokes[0].highlighter is True  # wide but drawable
