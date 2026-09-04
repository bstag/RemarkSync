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
    md = "# Notes\n\n" + ("filler line for pagination\n" * 100)
    out = markdown_to_rmdoc(md, "Big Doc", tmp_path / "big.rmdoc")
    assert out.exists()
    from remarksync.backup import open_rmdoc

    doc = open_rmdoc(out)
    assert doc.name == "Big Doc"
    assert len(doc.pages) >= 3
    first = decode_page(doc.pages[0].rm_path)
    assert "Notes" in page_text(first)
