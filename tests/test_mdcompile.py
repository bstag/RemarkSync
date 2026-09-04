import zipfile

from remarksync.mdcompile import (build_rmdoc, markdown_to_rmdoc,
                                  markdown_to_text, paginate_text)
from remarksync.layout import lay_out
from remarksync.rmdata import decode_page, page_text


def test_markdown_flattening():
    md = "# Title\n\nsome **bold** and _ital_ and `code`\n\n![img](x.png)\n\nsee [site](https://x.y)\n"
    text = markdown_to_text(md)
    assert "Title" in text
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
    text = markdown_to_text(md).splitlines()
    assert text[0] == "# Title"
    assert text[2] == "First line of a paragraph continues on the next source line and ends here."
    assert text[4:6] == ["- item one", "- item two"]
    assert text[7] == "Second paragraph."


def test_pagination_fills_one_screen_per_page_without_wrapping():
    from remarksync.textmetrics import lines_per_page

    text = "\n".join(f"line {i}" for i in range(200))
    pages = paginate_text(text)
    assert lines_per_page() == 22
    assert len(pages) == 10  # 200 short paragraphs, 22 per page
    assert all(p.count("\n") < 22 for p in pages)

    # a long paragraph is never broken, but it costs the lines it will wrap onto
    from remarksync.textmetrics import page_text_budget, paragraph_advance

    long = ("word " * 100).strip()  # ~500 chars -> 6 tablet lines
    per_page = int(page_text_budget() // paragraph_advance("PLAIN", long))
    pages = paginate_text("\n".join([long] * (per_page + 1)))
    assert len(pages) == 2
    assert pages[0].count("\n") == per_page - 1 and pages[1] == long


def test_compiled_styles_round_trip_through_the_decoder():
    import io

    from remarksync.mdcompile import markdown_to_paragraphs

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
    from rmscene import read_tree
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
    import io
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
