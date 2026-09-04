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
    long = "word " * 100  # ~500 chars -> 6 tablet lines
    pages = paginate_text("\n".join([long] * 5))
    assert all(long.strip() in p or long in p for p in pages)
    assert len(pages) == 2  # 5 x 6 = 30 lines over a 22-line budget
    assert all(p.count("\n") <= 2 for p in pages)


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
