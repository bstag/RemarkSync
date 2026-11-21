"""Editing a notebook: text is spliced, ink bytes are never re-encoded, the
page list is edited the way the tablet edits it, and the source is untouched."""
import io
import json
import zipfile

import pytest
from rmscene import CrdtId, read_blocks, write_blocks
from rmscene import scene_items as si
from rmscene.scene_stream import RootTextBlock, TreeNodeBlock
from rmscene.tagged_block_common import LwwValue

from remarksync import rmedit
from remarksync.backup import open_rmdoc
from remarksync.mdcompile import build_rmdoc
from remarksync.rmdata import decode_page
from .conftest import line, page_rm

DOC = "0a1b2c3d-1111-4222-8333-444455556666"
PAGES = ["11111111-aaaa-4bbb-8ccc-000000000001", "11111111-aaaa-4bbb-8ccc-000000000002",
         "11111111-aaaa-4bbb-8ccc-000000000003"]
STROKE = line(si.PenColor.BLUE, si.Pen.BALLPOINT_1, [(100, 200), (300, 260)])


def notebook(extra_pages=()):
    content = {"fileType": "notebook", "formatVersion": 2, "pageCount": 3, "cPages": {
        "lastOpened": {"timestamp": "1:9", "value": PAGES[2]},
        "pages": [{"id": pid, "idx": {"timestamp": "1:%d" % (i + 1), "value": "b" + "abc"[i]},
                   "template": {"timestamp": "1:1", "value": "P Lines medium"}} for i, pid in enumerate(PAGES)]
                 + list(extra_pages),
        "uuids": [{"first": "4785ed0b-0b42-580c-a159-33e76b41f3fe", "second": 1}]}}
    return {DOC + ".metadata": json.dumps({"visibleName": "Field notes", "parent": "folder", "type": "DocumentType"}).encode(),
            DOC + ".content": json.dumps(content).encode(),
            f"{DOC}/{PAGES[0]}.rm": page_rm(text="first line\nsecond line"),
            f"{DOC}/{PAGES[1]}.rm": page_rm(text="typed beside ink", strokes=[STROKE]),
            f"{DOC}/{PAGES[2]}.rm": page_rm(strokes=[STROKE])}


def members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def test_text_lines_round_trip_exactly_including_marker_lookalikes():
    paragraphs = [("HEADING", "Title"), ("SUBHEADING", "Part"), ("BOLD", "strong"), ("PLAIN", "snake_case *stays* `put`"),
                  ("PLAIN", "# not a heading"), ("PLAIN", "\\ backslash"), ("PLAIN", ""), ("BULLET", "one"),
                  ("BULLET2", "nested"), ("NUMBERED", "first"), ("NUMBERED2", "inner"), ("CHECKBOX", "todo"),
                  ("CHECKBOX_CHECKED", "done"), ("CHECKBOX2", "sub"), ("PLAIN", "3. plain that looks numbered")]
    assert rmedit.text_to_paragraphs(rmedit.paragraphs_to_text(paragraphs)) == paragraphs


def test_replacing_text_keeps_every_other_byte_of_the_page():
    before = notebook()[f"{DOC}/{PAGES[1]}.rm"]
    after = rmedit.replace_page_text(before, [("HEADING", "New title"), ("PLAIN", "body"), ("BULLET", "point")])
    assert rmedit.page_paragraphs(after) == [("HEADING", "New title"), ("PLAIN", "body"), ("BULLET", "point")]
    kept = lambda data: [data[a:b] for a, b, kind in rmedit._spans(data) if kind not in (0x07, 0x0A)]  # noqa: E731
    assert kept(after) == kept(before)
    model = decode_page(after)
    assert model.error is None and len(model.strokes) == 1 and model.texts[-1].text == "New title\nbody\npoint"


def test_new_text_ids_come_after_everything_already_on_the_page():
    before = page_rm(text="old", strokes=[STROKE, STROKE])  # ink ids 100, 101 share the text's author
    after = rmedit.replace_page_text(before, [("PLAIN", "replacement")])
    block = next(b for b in read_blocks(io.BytesIO(after)) if isinstance(b, RootTextBlock))
    assert min(item.item_id.part2 for item in block.value.items.sequence_items()) > 101


def test_page_with_ink_pinned_to_text_is_locked_not_rewritten():
    blocks = list(read_blocks(io.BytesIO(page_rm(text="anchor me"))))
    for block in blocks:
        if isinstance(block, TreeNodeBlock) and block.group.node_id == CrdtId(0, 11):
            block.group.anchor_id = LwwValue(CrdtId(1, 20), CrdtId(1, 17))
            block.group.anchor_type = LwwValue(CrdtId(1, 20), 2)
            block.group.anchor_threshold = LwwValue(CrdtId(1, 20), 100.0)
            block.group.anchor_origin_x = LwwValue(CrdtId(1, 20), 0.0)
    out = io.BytesIO()
    write_blocks(out, blocks)
    files = notebook()
    files[f"{DOC}/{PAGES[0]}.rm"] = out.getvalue()
    draft = rmedit.Draft(DOC, files)
    assert "pinned" in draft.pages[0].locked
    with pytest.raises(rmedit.EditError):
        draft.set_text(0, "anything")


def test_unedited_draft_builds_the_same_files_and_revision():
    files = notebook()
    draft = rmedit.Draft(DOC, files)
    assert [p.locked for p in draft.pages] == [None, None, None]
    assert not draft.changed and members(draft.build()) == files
    draft.set_text(0, "first line\r\nsecond line")  # same text, other line endings
    assert not draft.changed


def test_page_without_a_text_block_is_locked():
    files = notebook()
    out = io.BytesIO()
    write_blocks(out, [b for b in read_blocks(io.BytesIO(page_rm(strokes=[STROKE]))) if not isinstance(b, RootTextBlock)])
    files[f"{DOC}/{PAGES[2]}.rm"] = out.getvalue()
    draft = rmedit.Draft(DOC, files)
    assert draft.pages[2].locked == "this page has no typed text block"
    draft.duplicate(2)
    assert members(draft.build())[f"{DOC}/{draft.pages[3].page_id}.rm"] == out.getvalue()


def test_text_edit_changes_only_that_page_file():
    files = notebook()
    loaded = dict(files)
    draft = rmedit.Draft(DOC, files)
    draft.set_text(1, "## Renamed\ntyped beside ink")
    built = members(draft.build())
    changed = {name for name in files if built[name] != files[name]}
    assert changed == {f"{DOC}/{PAGES[1]}.rm"} and set(built) == set(files)
    assert files == loaded  # the loaded files are not edited in place


def test_page_edits_mark_reorder_and_copy_like_the_tablet(tmp_path):
    files = notebook()
    draft = rmedit.Draft(DOC, files)
    draft.duplicate(1)
    draft.move(0, 3)  # page 1 to the end
    draft.delete(2)  # the ink-only page
    copy = draft.pages[1].page_id
    assert [p.page_id for p in draft.pages] == [PAGES[1], copy, PAGES[0]]
    built = members(draft.build())
    content = json.loads(built[DOC + ".content"])
    entries = {p["id"]: p for p in content["cPages"]["pages"]}
    assert entries[PAGES[2]]["deleted"]["value"] == 1 and f"{DOC}/{PAGES[2]}.rm" in built  # marked, files kept
    assert built[f"{DOC}/{copy}.rm"] == files[f"{DOC}/{PAGES[1]}.rm"]
    assert entries[copy]["template"]["value"] == "P Lines medium"
    assert content["pageCount"] == 3 and content["cPages"]["lastOpened"]["value"] == PAGES[1]
    author = next(a["second"] for a in content["cPages"]["uuids"] if a["first"] == rmedit.AUTHOR)
    assert author == 2 and entries[copy]["idx"]["timestamp"].startswith("2:")
    stamps = [p[k]["timestamp"] for p in entries.values() for k in ("idx", "deleted", "template") if k in p
              and p[k]["timestamp"].startswith("2:")] + [content["cPages"]["lastOpened"]["timestamp"]]
    assert len(stamps) == len(set(stamps))  # every edit has its own timestamp
    path = tmp_path / "edited.rmdoc"
    path.write_bytes(draft.build())
    assert [p.page_id for p in open_rmdoc(path, tmp_path / "x").pages] == [PAGES[1], copy, PAGES[0]]


def test_already_deleted_pages_stay_deleted_and_last_page_cannot_go():
    gone = {"id": "99999999-aaaa-4bbb-8ccc-000000000009", "idx": {"timestamp": "1:7", "value": "bd"},
            "deleted": {"timestamp": "2:1", "value": 1}}
    draft = rmedit.Draft(DOC, notebook([gone]))
    assert len(draft.pages) == 3
    draft.delete(0)
    draft.delete(0)
    with pytest.raises(rmedit.EditError, match="at least one page"):
        draft.delete(0)
    content = json.loads(members(draft.build())[DOC + ".content"])
    assert sum(1 for p in content["cPages"]["pages"] if not p.get("deleted")) == 1


def test_new_page_and_copy_under_a_new_identity(tmp_path):
    draft = rmedit.Draft(DOC, notebook())
    draft.add_page(0)
    draft.set_text(1, "# Added\n- [ ] check")
    built = members(draft.build(new_identity=True, name="Field notes (edited)"))
    identifier = next(n for n in built if n.endswith(".content"))[:-len(".content")]
    assert identifier != DOC and all(n.startswith(identifier) for n in built)
    assert json.loads(built[identifier + ".metadata"])["visibleName"] == "Field notes (edited)"
    path = tmp_path / "copy.rmdoc"
    path.write_bytes(draft.build(new_identity=True, name="Copy"))
    doc = open_rmdoc(path, tmp_path / "x")
    assert len(doc.pages) == 4 and decode_page(doc.pages[1].rm_path).texts[-1].paragraphs == [
        ("HEADING", "Added"), ("CHECKBOX", "check")]


def test_compiled_notebook_can_be_edited_and_reopened(tmp_path):
    source = tmp_path / "compiled.rmdoc"
    source.write_bytes(build_rmdoc("Compiled", [[("HEADING", "One"), ("PLAIN", "alpha")], [("PLAIN", "beta")]]))
    draft = rmedit.Draft.from_archive(source)
    assert [p.text for p in draft.pages] == ["# One\nalpha", "beta"]
    draft.set_text(1, "beta\ngamma")
    out = tmp_path / "out.rmdoc"
    out.write_bytes(draft.build())
    reopened = rmedit.Draft.from_document(open_rmdoc(out, tmp_path / "x"))
    assert [p.text for p in reopened.pages] == ["# One\nalpha", "beta\ngamma"]


def test_only_notebooks_with_a_modern_page_list_are_editable():
    files = notebook()
    files[DOC + ".content"] = json.dumps({"fileType": "pdf", "cPages": {"pages": [{"id": PAGES[0]}]}}).encode()
    with pytest.raises(rmedit.EditError, match="only notebooks"):
        rmedit.Draft(DOC, files)
    files[DOC + ".content"] = json.dumps({"fileType": "notebook", "pages": ["0"]}).encode()
    with pytest.raises(rmedit.EditError, match="older page list"):
        rmedit.Draft(DOC, files)
