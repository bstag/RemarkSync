import zipfile

from remarksync.mdcompile import (build_rmdoc, markdown_to_rmdoc,
                                  markdown_to_text, paginate_text)
from remarksync.rmdata import page_text, parse_rm


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
        page = parse_rm(raw)
        # text is normalised onto the page: near the top, within margins
        assert 0 <= page.texts[0].pos_y <= 100
        assert 0 < page.texts[0].pos_x < 1404
    assert page_text(page) == "round trip works"


def test_markdown_to_rmdoc_end_to_end(tmp_path):
    md = "# Notes\n\n" + ("filler line for pagination\n" * 100)
    out = markdown_to_rmdoc(md, "Big Doc", tmp_path / "big.rmdoc")
    assert out.exists()
    from remarksync.library import open_rmdoc

    doc = open_rmdoc(out)
    assert doc.name == "Big Doc"
    assert len(doc.pages) >= 3
    first = parse_rm(doc.pages[0].rm_path)
    assert "Notes" in page_text(first)
