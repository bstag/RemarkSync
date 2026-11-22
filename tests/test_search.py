"""Search index: refresh by lastModified, extractor seam, query."""

import json

from remarksync.backup import DocItem, PageRef, iter_docs, scan_backup
from remarksync.recognize import PageSource
from remarksync.rmdata import PageModel, TextBlock
from remarksync.search import SearchIndex, typed_text_and_highlights

from .conftest import DIARY_UUID, INK_UUID, PDF_UUID


def _docs(root):
    return list(iter_docs(scan_backup(root)))


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
    def fake_ocr(source: PageSource) -> str:
        return "handwriting says hello"

    index = SearchIndex(tmp_path / "index.db", extractors=[typed_text_and_highlights, fake_ocr])
    page = PageModel(texts=[TextBlock(text="typed line", pos_x=0, pos_y=0, width=100)])
    source = PageSource(DocItem(uuid="d"), PageRef(index=0, rm_path=None), page)
    assert index.page_text_of(source) == "typed line\nhandwriting says hello"
    assert SearchIndex(tmp_path / "other.db").page_text_of(source) == "typed line"


def test_extractors_feed_the_index(backup_root, tmp_path):
    index = SearchIndex(tmp_path / "index.db", extractors=[lambda source: "xylophone"])
    index.refresh([d for d in _docs(backup_root) if d.uuid == PDF_UUID])
    hits = index.query("xylophone")
    assert hits and hits[0].doc_uuid == PDF_UUID


def test_refresh_reindexes_when_the_extractor_set_changes(backup_root, tmp_path):
    """What a page's text *is* depends on which extractors ran. Turning
    handwriting on adds an extractor, so every document already in the index
    was built from a different recipe and has to be read again -- otherwise
    ticking Read Handwriting and pressing Update does nothing.
    """
    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    assert index.query("xylophone") == []

    index.extractors = list(index.extractors) + [lambda source: "xylophone"]
    report = index.refresh(_docs(backup_root))
    assert report.skipped == 0
    assert index.query("xylophone")


OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    file_type TEXT,
    page_count INTEGER,
    last_modified TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    doc_uuid UNINDEXED,
    page_no UNINDEXED,
    name,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def test_refresh_upgrades_an_index_written_before_signatures(backup_root, tmp_path):
    """Someone's backup already holds an index built by an older version.
    ``CREATE TABLE IF NOT EXISTS`` will not add a column to it, so a refresh
    has to migrate rather than explode or silently write to a column that
    is not there.
    """
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO docs VALUES (?,?,?,?,?)",
        (INK_UUID, "Ink Notes", "notebook", 2, "1700000000000"),
    )
    conn.commit()
    conn.close()

    index = SearchIndex(path)
    report = index.refresh(_docs(backup_root))
    assert report.indexed >= 1
    assert index.query("quantum")  # the old row was re-read, not trusted


def test_cancelling_between_pages_abandons_the_document_in_hand(backup_root, tmp_path):
    """A stop request does not wait for a long document: the document being
    read keeps what it had and is picked up by the next refresh."""
    index = SearchIndex(tmp_path / "index.db")

    class StopAtOnce:
        seen = []

        def __call__(self, name):
            self.seen.append(name)

        def cancelled(self):
            return True

    progress = StopAtOnce()
    report = index.refresh(_docs(backup_root), progress=progress)
    assert not report.completed
    assert report.indexed == 0 and report.failed == 0 and progress.seen == []
    assert not index.query("flux capacitor")
    rest = index.refresh(_docs(backup_root))
    assert rest.completed and rest.indexed == 4
    assert index.query("flux capacitor")


def test_stray_punctuation_does_not_empty_a_search(backup_root, tmp_path):
    from remarksync.search import _fts_query

    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    assert index.query("flux - capacitor") and index.query("flux & capacitor")
    assert _fts_query("- & *") == "" and index.query("- & *") == []


def test_a_documents_name_is_one_hit_not_every_page(backup_root, tmp_path):
    from remarksync.search import NAME_HIT

    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    ink = next(d for d in _docs(backup_root) if d.uuid == INK_UUID)
    assert len(ink.pages) > 1
    hits = [h for h in index.query("ink notes") if h.doc_uuid == INK_UUID]
    assert len(hits) == 1 and hits[0].snippet == NAME_HIT and hits[0].page_no == 0
    # a name and a page's own words still combine, and that page is the hit
    mixed = index.query("ink quantum")
    assert mixed and all(h.doc_uuid == INK_UUID and h.snippet != NAME_HIT for h in mixed)


def test_replacing_a_document_removes_exactly_its_own_pages(backup_root, tmp_path):
    import sqlite3

    index = SearchIndex(tmp_path / "index.db")
    docs = _docs(backup_root)
    index.refresh(docs)
    index.refresh(docs, force=True)
    index.refresh(list(reversed(docs)), force=True)
    conn = sqlite3.connect(index.path)
    try:
        counts = dict(conn.execute("SELECT doc_uuid, count(*) FROM pages_fts GROUP BY doc_uuid"))
        spans = {uuid: (first, last) for uuid, first, last in conn.execute("SELECT uuid, first_row, last_row FROM docs")}
    finally:
        conn.close()
    assert counts == {d.uuid: len(d.pages) for d in docs if d.pages}
    assert all(spans[d.uuid][1] - spans[d.uuid][0] + 1 == len(d.pages) for d in docs)


def test_index_deleted_under_the_app_is_rebuilt_and_a_damaged_one_is_an_error(backup_root, tmp_path):
    import pytest

    from remarksync.search import SearchError

    index = SearchIndex(tmp_path / "index.db")
    index.refresh(_docs(backup_root))
    for leftover in tmp_path.glob("index.db*"):
        leftover.unlink()
    assert index.query("flux") == []
    assert index.refresh(_docs(backup_root)).indexed == 4 and index.query("flux capacitor")

    damaged = SearchIndex(tmp_path / "damaged.db")
    damaged.path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(SearchError):
        damaged.query("flux")
