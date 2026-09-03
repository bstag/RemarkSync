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


def test_pagination_respects_page_size():
    text = "\n".join(f"line {i}" for i in range(200))
    pages = paginate_text(text)
    assert len(pages) > 1
    assert all(p.count("\n") < 40 for p in pages)


def test_build_rmdoc_structure():
    blob = build_rmdoc("My Doc", ["page one text", "page two text"])
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert {".metadata", ".content", "pagedata", "0/0.rm", "1/1.rm"} <= names
        import json

        content = json.loads(zf.read(".content"))
        assert content["fileType"] == "notebook"
        meta = json.loads(zf.read(".metadata"))
        assert meta["visibleName"] == "My Doc"


def test_compiled_pages_roundtrip():
    from rmscene import read_tree
    import io

    blob = build_rmdoc("T", ["round trip works"])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        raw = zf.read("0/0.rm")
        page = decode_page(raw)
        # the compiled text box lands near the top of the page, within margins
        placed = lay_out(page).page
        assert 0 <= placed.texts[0].pos_y <= 300
        assert 0 < placed.texts[0].pos_x < 1404
    assert page_text(page) == "round trip works"


def test_markdown_to_rmdoc_end_to_end(tmp_path):
    md = "# Notes\n\n" + ("filler line for pagination\n" * 100)
    out = markdown_to_rmdoc(md, "Big Doc", tmp_path / "big.rmdoc")
    assert out.exists()
    from remarksync.backup import open_rmdoc

    doc = open_rmdoc(out)
    assert doc.name == "Big Doc"
    assert len(doc.pages) >= 3
    first = decode_page(doc.pages[0].rm_path)
    assert "Notes" in page_text(first)
