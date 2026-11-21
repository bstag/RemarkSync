import zipfile

from remarksync.mdcompile import build_rmdoc, markdown_to_paragraphs, markdown_to_rmdoc, paginate
from remarksync.layout import lay_out
from remarksync.rmdata import decode_page, page_text


def test_markdown_flattening():
    md = "# Title\n\nsome **bold** and _ital_ and `code`\n\n![img](x.png)\n\nsee [site](https://x.y)\n"
    paragraphs = markdown_to_paragraphs(md)
    assert paragraphs[0] == ("HEADING", "Title")
    text = "\n".join(t for _, t in paragraphs)
    assert "**" not in text and "_" not in text and "`" not in text
    assert "[image: img]" in text
    assert "site (https://x.y)" in text


def test_hard_wrapped_paragraphs_are_joined_for_the_tablet_to_wrap():
    md = (
        "# Title\n"
        "\n"
        "First line of a paragraph\n"
        "continues on the next source line\n"
        "and ends here.\n"
        "\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "Second paragraph.\n"
    )
    paragraphs = markdown_to_paragraphs(md)
    assert paragraphs[0] == ("HEADING", "Title")
    assert paragraphs[2] == ("PLAIN", "First line of a paragraph continues on the next source line and ends here.")
    assert paragraphs[4:6] == [("BULLET", "item one"), ("BULLET", "item two")]
    assert paragraphs[7] == ("PLAIN", "Second paragraph.")


def test_each_heading_level_keeps_its_own_style():
    """``##`` is the toolbar's Subheading and ``###`` the medium-weight Bold. A
    text flattening that predated Subheading wrote ``###`` back as ``##`` and
    ``##`` back as nothing; the paragraphs are the one record of it now."""
    assert markdown_to_paragraphs("# a\n\n## b\n\n### c\n\n#### d\n") == [
        ("HEADING", "a"), ("PLAIN", ""), ("SUBHEADING", "b"), ("PLAIN", ""), ("BOLD", "c"), ("PLAIN", ""), ("BOLD", "d"),
    ]


def test_only_prose_is_joined_to_the_line_before():
    md = "- item\nprose after a list\nmore prose\n    indented\nafter code\n"
    assert markdown_to_paragraphs(md) == [
        ("BULLET", "item"), ("PLAIN", "prose after a list more prose"), ("PLAIN", "    indented"), ("PLAIN", "after code"),
    ]


def test_pagination_fills_one_screen_per_page_without_wrapping():
    from remarksync.textmetrics import lines_per_page

    pages = paginate([("PLAIN", f"line {i}") for i in range(200)])
    assert lines_per_page() == 23  # 61 + 22*69.3 + a descender ends at 1827 of 1872
    assert len(pages) == 9  # 200 short paragraphs, 23 per page
    assert all(len(page) <= 23 for page in pages)

    # a long paragraph is never broken, and costs the lines it wraps onto: four
    # of these fill a page where 23 short ones do
    long = ("word " * 100).strip()  # ~500 chars -> 6 tablet lines
    pages = paginate([("PLAIN", long)] * 5)
    assert [len(page) for page in pages] == [4, 1]
    assert all(paragraph == ("PLAIN", long) for page in pages for paragraph in page)
    assert paginate([]) == [[("PLAIN", "")]]  # nothing to say is still one page


def test_no_page_is_filled_past_the_paper():
    """The property that matters, and the one a per-paragraph charge got wrong:
    a page has to fit the paper it was compiled for.

    The old reckoning billed every paragraph for the space above a *body* line,
    so a page carrying a heading was undercharged. A 1872 page broke before the
    error could show; a Paper Pro's 2160 holds enough paragraphs that it did,
    and the tablet grew the canvas to 2257 to fit what we had overfilled.
    """
    from remarksync.rmdata import PAPER_SIZES
    from remarksync.textmetrics import block_height, page_block_room

    # headings among the body text, which is where the charge went wrong
    paragraphs = [("HEADING" if i % 3 == 0 else "PLAIN", f"paragraph {i} " * 4) for i in range(40)]

    for paper in PAPER_SIZES:
        room = page_block_room(paper[1])
        pages = paginate(paragraphs, paper)
        assert pages, paper
        for page in pages:
            # a page holding a single paragraph may exceed it: those never split
            assert len(page) == 1 or block_height(page) <= room, (paper, page[0])
        assert sum(len(page) for page in pages) == len(paragraphs)

    # and the taller paper takes fewer pages for the same text
    assert len(paginate(paragraphs, (1620, 2160))) < len(paginate(paragraphs, (1404, 1872)))


def test_compiled_styles_round_trip_through_the_decoder():
    import io

    md = (
        "# Big heading\n"
        "## Smaller heading\n"
        "### Bold line\n"
        "Body text that is plain.\n"
        "- first bullet\n"
        "  - nested bullet\n"
        "1. first number\n"
        "   1. nested number\n"
        "- [ ] open box\n"
        "- [x] ticked box\n"
    )
    paras = markdown_to_paragraphs(md)
    assert [s for s, _ in paras] == [
        "HEADING", "SUBHEADING", "BOLD", "PLAIN", "BULLET", "BULLET2", "NUMBERED", "NUMBERED2", "CHECKBOX", "CHECKBOX_CHECKED",
    ]
    assert paras[0][1] == "Big heading" and paras[8][1] == "open box"  # markers stripped

    blob = build_rmdoc("Styled", [paras])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        raw = zf.read(next(n for n in zf.namelist() if n.endswith(".rm")))
    decoded = decode_page(raw).texts[0]
    assert decoded.paragraphs == paras
    assert [ln.style for ln in decoded.lines] == [s for s, _ in paras]


def _entries(blob):
    import io
    import json

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        content_name = next(n for n in names if n.endswith(".content"))
        doc_uuid = content_name[: -len(".content")]
        return doc_uuid, names, json.loads(zf.read(content_name)), json.loads(zf.read(f"{doc_uuid}.metadata")), zf


def test_build_rmdoc_follows_the_software_3_layout():
    import io
    import json

    blob = build_rmdoc("My Doc", ["page one text", "page two text"])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        content_name = next(n for n in names if n.endswith(".content"))
        doc_uuid = content_name[: -len(".content")]
        content = json.loads(zf.read(content_name))
        meta = json.loads(zf.read(f"{doc_uuid}.metadata"))
        assert content["formatVersion"] == 2
        assert content["fileType"] == "notebook"
        assert content["pageCount"] == 2
        page_ids = [p["id"] for p in content["cPages"]["pages"]]
        assert len(page_ids) == 2 and all(len(pid) == 36 for pid in page_ids)
        keys = [p["idx"]["value"] for p in content["cPages"]["pages"]]
        assert keys == sorted(keys)  # page order is a lexicographic key
        assert set(names) == {content_name, f"{doc_uuid}.metadata", *(f"{doc_uuid}/{pid}.rm" for pid in page_ids)}
        assert meta["visibleName"] == "My Doc" and meta["type"] == "DocumentType"


def test_page_order_keys_sort_however_many_pages_there_are():
    from remarksync.mdcompile import _page_order_key

    # the keys a tablet has already accepted stay exactly as they were
    assert [_page_order_key(i) for i in (0, 1, 25, 26, 675)] == ["baa", "bab", "baz", "bba", "bzz"]
    assert all(_page_order_key(i) == f"b{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(676))
    # past 676 the two letters used to run on into "b{a" and then out of ASCII
    keys = [_page_order_key(i) for i in range(676 + 26 ** 3 + 50)]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert all(key.isascii() and key.isalpha() and key.islower() for key in keys)
    assert keys[676] == "caaa" and keys[676 + 26 ** 3] == "daaaa"


def test_compiled_page_carries_scene_info():
    import io

    from rmscene import SceneInfo, read_blocks

    blob = build_rmdoc("T", ["one line"])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        raw = zf.read(next(n for n in zf.namelist() if n.endswith(".rm")))
    infos = [b for b in read_blocks(io.BytesIO(raw)) if isinstance(b, SceneInfo)]
    assert len(infos) == 1
    assert infos[0].paper_size == (1404, 1872)


def test_compiled_pages_roundtrip():
    import io

    blob = build_rmdoc("T", ["round trip works"])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        raw = zf.read(next(n for n in zf.namelist() if n.endswith(".rm")))
        page = decode_page(raw)
        # the compiled text box lands near the top of the page, within margins
        placed = lay_out(page).page
        assert 0 <= placed.texts[0].pos_y <= 300
        assert 0 < placed.texts[0].pos_x < 1404
    assert page_text(page) == "round trip works"


def test_markdown_to_rmdoc_end_to_end(tmp_path):
    md = "# Notes\n\n" + ("filler line for pagination\n\n" * 100)  # 100 separate paragraphs
    out = markdown_to_rmdoc(md, "Big Doc", tmp_path / "big.rmdoc")
    assert out.exists()
    from remarksync.backup import open_rmdoc

    doc = open_rmdoc(out)
    assert doc.name == "Big Doc"
    assert len(doc.pages) >= 3
    first = decode_page(doc.pages[0].rm_path)
    assert "Notes" in page_text(first)


def test_subheading_format_entry_matches_the_device_bytes():
    """The tablet stores Subheading as style 3 plus two extra fields; Bold
    is the bare code. rmscene skips the fields, so RemarkSync reads and
    writes them itself (rmformat)."""
    import struct

    from remarksync.mdcompile import page_rm_bytes

    raw = page_rm_bytes([("SUBHEADING", "sub"), ("BOLD", "bold")], None)
    # walk the blocks to the root text block (type 7) and pull its format sub-blocks
    pos = 43
    entries = []
    while pos < len(raw):
        length, = struct.unpack_from("<I", raw, pos)
        if raw[pos + 7] == 0x07:
            body = raw[pos + 8: pos + 8 + length]
            sub = bytes([0x11, 0x03, 0x21, 0x02, 0x34, 0x03, 0x00, 0x00, 0x00])  # what the device wrote for a subheading
            assert sub in body
            assert body.count(bytes([0x11, 0x03])) == 2  # the bold entry has the bare code
            entries.append(body)
        pos += 8 + length
    assert entries
    decoded = decode_page(raw).texts[0]
    assert [s for s, _ in decoded.paragraphs] == ["SUBHEADING", "BOLD"]
