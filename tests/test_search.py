from remarksync.library import iter_docs, scan_root
from remarksync.search import build_index, search

from .conftest import INK_UUID, DIARY_UUID


def test_index_and_search(backup_root, tmp_path):
    tree = scan_root(backup_root)
    db = tmp_path / "index.db"
    n = build_index(tree, db)
    assert n >= 3  # at least the pages with content

    # find a document by name
    hits = search(db, "diary")
    assert any(h.doc_uuid == DIARY_UUID for h in hits)

    # find typed text inside a page
    hits = search(db, "flux capacitor")
    assert hits and hits[0].doc_uuid == DIARY_UUID
    assert hits[0].page_no == 0

    # typed text in the notebook page
    hits = search(db, "quantum")
    assert any(h.doc_uuid == INK_UUID for h in hits)

    # prefix matching works
    assert search(db, "quant")

    # no match / empty query
    assert search(db, "zzznotfound") == []
    assert search(db, "") == []


def test_search_snippet_has_context(backup_root, tmp_path):
    tree = scan_root(backup_root)
    db = tmp_path / "index.db"
    build_index(tree, db)
    hits = search(db, "welded")
    assert hits
    assert "welded" in hits[0].snippet.lower() or "«" in hits[0].snippet
