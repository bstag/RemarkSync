"""Search index: refresh by lastModified, extractor seam, query."""

import json

from remarksync.backup import iter_docs, scan_root
from remarksync.rmdata import PageModel, TextBlock
from remarksync.search import SearchIndex, typed_text_and_highlights

from .conftest import DIARY_UUID, INK_UUID, PDF_UUID


def _docs(root):
    return list(iter_docs(scan_root(root)))


def test_refresh_then_query(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    assert not index.exists
    report = index.refresh(_docs(backup_root))
    assert index.exists
    assert report.completed
    assert report.pages >= 3  # at least the pages with content
    assert report.indexed == 4 and report.skipped == 0 and report.removed == 0

    # find a document by name
    hits = index.query("diary")
    assert any(h.doc_uuid == DIARY_UUID for h in hits)

    # find typed text inside a page
    hits = index.query("flux capacitor")
    assert hits and hits[0].doc_uuid == DIARY_UUID
    assert hits[0].page_no == 0

    # typed text in the notebook page, prefix matching, no match, empty query
    assert any(h.doc_uuid == INK_UUID for h in index.query("quantum"))
    assert index.query("quant")
    assert index.query("zzznotfound") == []
    assert index.query("") == []


def test_query_without_an_index_is_empty(tmp_path):
    assert SearchIndex(tmp_path / "never.db").query("anything") == []
    assert not (tmp_path / "never.db").exists()


def test_snippet_has_context(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    hits = index.query("welded")
    assert hits
    assert "welded" in hits[0].snippet.lower() or "«" in hits[0].snippet


def test_refresh_skips_unchanged_documents(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    again = index.refresh(_docs(backup_root))
    # Ink Notes carries a lastModified in the fixture; the others do not
    # and are conservatively re-indexed every time
    assert again.skipped == 1 and again.indexed == 3
    assert again.pages > 0


def test_refresh_reindexes_a_changed_document(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    assert index.query("photosynthesis") == []

    # edit the Ink Notes page and bump its lastModified, as the tablet does
    from .conftest import page_rm

    (backup_root / INK_UUID / "0" / "0.rm").write_bytes(page_rm(text="notes on photosynthesis"))
    meta_path = backup_root / f"{INK_UUID}.metadata"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["lastModified"] = "1800000000000"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    report = index.refresh(_docs(backup_root))
    assert report.skipped == 0  # Ink Notes changed, the rest have no lastModified
    hits = index.query("photosynthesis")
    assert hits and hits[0].doc_uuid == INK_UUID
    assert index.query("quantum") == []  # old text is gone, not duplicated


def test_refresh_purges_documents_that_left_the_library(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    assert index.query("flux capacitor")

    (backup_root / f"{DIARY_UUID}.metadata").unlink()
    report = index.refresh(_docs(backup_root))
    assert report.removed == 1
    assert index.query("flux capacitor") == []


def test_force_rebuilds_everything(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    report = index.refresh(_docs(backup_root), force=True)
    assert report.skipped == 0 and report.indexed == 4
    assert index.query("flux capacitor")


def test_refresh_can_be_cancelled_and_keeps_finished_documents(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db")
    seen = []

    def stop_after_first(name):
        seen.append(name)
        return False

    report = index.refresh(_docs(backup_root), progress=stop_after_first)
    assert not report.completed
    assert report.indexed == 1 and len(seen) == 1
    # the finished document is searchable; the next refresh picks up the rest
    rest = index.refresh(_docs(backup_root))
    assert rest.completed
    assert index.query("flux capacitor")


def test_extractor_seam_joins_every_extractor(tmp_path):
    def fake_ocr(page: PageModel) -> str:
        return "handwriting says hello"

    index = SearchIndex(tmp_path / "index.db", extractors=[typed_text_and_highlights, fake_ocr])
    page = PageModel(texts=[TextBlock(text="typed line", pos_x=0, pos_y=0, width=100)])
    assert index.page_text_of(page) == "typed line\nhandwriting says hello"
    assert SearchIndex(tmp_path / "other.db").page_text_of(page) == "typed line"


def test_extractors_feed_the_index(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db", extractors=[lambda page: "xylophone"])
    index.refresh([d for d in _docs(backup_root) if d.uuid == PDF_UUID])
    hits = index.query("xylophone")
    assert hits and hits[0].doc_uuid == PDF_UUID
