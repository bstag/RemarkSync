"""The Library: an opened backup folder with its tree, index and search.

These run with no Qt; the window and the CLI are two callers of the same
interface.
"""

import pytest

from remarksync.library import INDEX_NAME, Library, NoIndexError

from .conftest import (DELETED_UUID, DIARY_UUID, INK_UUID, JOURNAL_UUID,
                       PDF_UUID, page_rm, write_content, write_metadata)


def test_open_library_scans_the_backup(backup_root):
    lib = Library(backup_root)
    assert lib.root == backup_root
    assert len(lib.tree) == 2  # Research folder + Diary doc
    assert {d.uuid for d in lib.documents} == {INK_UUID, PDF_UUID, DIARY_UUID, JOURNAL_UUID}
    assert len(lib) == 4
    assert lib.document(INK_UUID).name == "Ink Notes"
    assert lib.document(DELETED_UUID) is None
    assert lib.document("not-a-uuid") is None


def test_refresh_picks_up_new_documents(backup_root):
    lib = Library(backup_root)
    new_uuid = "99999999-1111-2222-3333-444444444444"
    write_metadata(backup_root, new_uuid, {"visibleName": "Late Arrival", "parent": "", "type": "DocumentType"})
    write_content(backup_root, new_uuid, {"fileType": "notebook", "pages": ["0"]})
    (backup_root / new_uuid / "0").mkdir(parents=True)
    (backup_root / new_uuid / "0" / "0.rm").write_bytes(page_rm(text="late"))
    assert lib.document(new_uuid) is None
    lib.refresh()
    assert lib.document(new_uuid).name == "Late Arrival"


def test_index_lives_in_the_backup_folder(backup_root):
    lib = Library(backup_root)
    assert lib.index_path == backup_root / INDEX_NAME
    assert not lib.has_index


def test_search_before_indexing_is_an_explicit_error(backup_root):
    lib = Library(backup_root)
    with pytest.raises(NoIndexError):
        lib.search("diary")


def test_rebuild_index_then_search(backup_root):
    lib = Library(backup_root)
    seen: list[str] = []
    report = lib.rebuild_index(progress=lambda name: seen.append(name) or True)
    assert report.completed
    assert report.pages >= 3
    assert lib.has_index
    assert set(seen) == {d.name for d in lib.documents}

    hits = lib.search("flux capacitor")
    assert hits and hits[0].doc_uuid == DIARY_UUID
    assert lib.document(hits[0].doc_uuid).name == "Diary 2026"
    assert lib.search("zzznotfound") == []


def test_refresh_index_only_touches_changed_documents(backup_root):
    lib = Library(backup_root)
    first = lib.refresh_index()
    assert first.completed and first.indexed == 4
    second = lib.refresh_index()
    assert second.skipped >= 1  # Ink Notes has a lastModified and is unchanged
    assert lib.search("quantum")


def test_rebuild_index_can_be_cancelled(backup_root):
    lib = Library(backup_root)
    calls: list[str] = []

    def stop_after_first(name: str) -> bool:
        calls.append(name)
        return False

    report = lib.rebuild_index(progress=stop_after_first)
    assert not report.completed
    assert len(calls) == 1
    assert lib.has_index  # what was indexed is kept
