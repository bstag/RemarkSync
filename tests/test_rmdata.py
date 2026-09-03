from remarksync.library import iter_docs, scan_root
from remarksync.rmdata import PAGE_WIDTH, parse_rm, page_text
from rmscene import scene_items as si

from .conftest import INK_UUID


def _ink_doc(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_root(backup_root))}
    return docs[INK_UUID]


def test_parse_page_strokes(backup_root):
    doc = _ink_doc(backup_root)
    page = parse_rm(doc.pages[0].rm_path)
    # eraser line is dropped, 2 ink strokes remain
    assert len(page.strokes) == 2
    ballpoint = page.strokes[0]
    assert ballpoint.color == "#1a1a1a"
    assert ballpoint.highlighter is False
    assert abs(ballpoint.width - 2.2) < 0.01
    assert len(ballpoint.points) == 3
    # normalised page coords: on the page, original shape preserved
    (x0, y0), (x1, y1), (x2, y2) = ballpoint.points
    assert 0 <= x0 <= 1404 and 0 <= y0 <= 1872
    assert x1 > x0 and x2 > x1 and y2 > y0  # down-right slope kept


def test_highlighter_flag_and_scale(backup_root):
    doc = _ink_doc(backup_root)
    page = parse_rm(doc.pages[0].rm_path)
    hl = page.strokes[1]
    assert hl.highlighter is True
    assert hl.color == "#f2b800"
    assert abs(hl.width - 56.0) < 0.01  # 28 * thickness_scale 2.0
    assert page.strokes[1].points[0][1] > page.strokes[0].points[0][1]  # below ballpoint


def test_typed_text_extracted(backup_root):
    doc = _ink_doc(backup_root)
    page = parse_rm(doc.pages[0].rm_path)
    assert "quantum computing" in page_text(page)
    assert any("quantum" in t.text for t in page.texts)


def test_empty_page_parses_to_blank_model(tmp_path):
    from pathlib import Path

    bad = tmp_path / "garbage.rm"
    bad.write_bytes(b"\x00\x01not an rm file")
    page = parse_rm(bad)
    assert page.error is not None
    assert page.strokes == [] and page.texts == []


def test_parse_v5_lines_file():
    from .conftest import page_rm_v5

    blob = page_rm_v5(
        [
            (2, 0, 2.2, [(100, 150), (140, 160), (180, 175)]),  # ballpoint
            (0, 0, 2.0, [(100, 300), (300, 320), (500, 310)]),  # id-prefixed line
            (5, 0, 25.0, [(90, 500), (700, 500)]),  # highlighter-width line
        ]
    )
    page = parse_rm(blob)
    assert page.error is None
    assert len(page.strokes) == 3
    assert page.strokes[0].width == 2.2
    assert page.strokes[1].width == 2.0  # id record skipped, width intact
    assert page.strokes[2].highlighter is True  # wide stroke = highlighter
    assert page.strokes[0].color == "#1a1a1a"
    # all ink lands on the page after normalisation
    for s in page.strokes:
        assert all(0 <= x <= 1404 and 0 <= y <= 1872 for x, y in s.points)
    assert page.strokes[0].points[0][1] < page.strokes[1].points[0][1] < page.strokes[2].points[0][1]


def test_v5_scanner_survives_garbage_between_lines():
    from .conftest import page_rm_v5

    blob = page_rm_v5([(2, 0, 2.2, [(10, 10), (40, 40)])])
    # splice junk that is not a valid line record between header and data
    torn = blob[:59] + b"\xff" * 8 + blob[59:]
    page = parse_rm(torn)
    # junk is skipped; nothing valid remains after the layer counts
    assert page.error is None and isinstance(page.strokes, list)


def test_glyph_highlights_transformed_in_pdf_mode():
    from .conftest import page_rm

    glyph = si.GlyphRange(
        start=100, length=5, text="verse", color=si.PenColor.YELLOW,
        rectangles=[
            si.Rectangle(x=-600.0, y=1480.0, w=380.0, h=34.0),
            si.Rectangle(x=-625.0, y=1476.0, w=29.0, h=21.5),  # verse-number box
        ],
    )
    blob = page_rm(glyphs=[glyph])

    pdf_page = parse_rm(blob, pdf_mode=True)
    assert pdf_page.highlights[0].text == "verse"
    assert len(pdf_page.highlights[0].rectangles) == 1  # sub-height rect dropped
    x, y, w, h = pdf_page.highlights[0].rectangles[0]
    assert abs(x - 102.0) < 0.01  # -600 + 702 (centre-relative shift)
    assert abs(y - 1480.0) < 0.01  # y already from page top

    # text remains searchable regardless of mode
    assert "verse" in page_text(parse_rm(blob))


def test_pdf_mode_canvas_grows_for_overflow_ink():
    """Ink written past the page edges (sides and bottom) must extend the
    canvas instead of being clipped; the page origin records the offset."""
    from .conftest import line, page_rm

    blob = page_rm(strokes=[line(si.PenColor.BLACK, si.Pen.BALLPOINT_1,
                                 [(100, 2100), (400, 2200), (700, 2400)])])
    page = parse_rm(blob, pdf_mode=True)
    assert page.content_size[1] > 2400
    # positions are untouched in pdf mode (stays relative to the page)
    assert page.strokes[0].points[0][1] == 2100.0

    # side overflow: ink left of x=0 and right of x=1404
    blob = page_rm(strokes=[line(si.PenColor.BLACK, si.Pen.BALLPOINT_1,
                                 [(-1200.0, 100.0), (1100.0, 200.0)])])
    page = parse_rm(blob, pdf_mode=True)
    # raw x -1200 becomes -498 after the +702 shift -> 498 units of left margin
    assert page.origin[0] == 498.0
    assert page.content_size[0] > PAGE_WIDTH  # right side also grew (1100+702=1802)
    assert page.strokes[0].points[0][0] == 0.0  # shifted onto the canvas
    assert page.strokes[0].points[1][0] == 1802.0 + 498.0
