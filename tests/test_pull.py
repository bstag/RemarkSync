"""Incremental pull: only documents whose metadata changed are fetched.

The tablet is a local directory here (the LocalTablet adapter); the SSH
adapter shares the plan and fetch logic.
"""

import json

from remarksync.pull import LocalTablet, plan, pull

from .conftest import DIARY_UUID, INK_UUID, JOURNAL_UUID, PDF_UUID, write_content, write_metadata


def test_first_pull_fetches_everything(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    report = pull(dest, LocalTablet(backup_root))
    assert report.completed
    assert set(report.new) == {u.name[:-9] for u in backup_root.glob("*.metadata")}
    assert report.changed == [] and report.removed == []
    assert (dest / f"{INK_UUID}.metadata").exists()
    assert (dest / INK_UUID / "0" / "0.rm").exists()
    assert (dest / f"{PDF_UUID}.pdf").exists()


def test_second_pull_fetches_nothing(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    report = pull(dest, LocalTablet(backup_root))
    assert report.fetched == 0
    assert len(report.unchanged) == len(list(backup_root.glob("*.metadata")))


def test_edited_document_is_fetched_again_with_its_pages_replaced(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    # the tablet rewrites metadata on edit, and here also drops a page
    meta_path = backup_root / f"{INK_UUID}.metadata"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["lastModified"] = "1800000000000"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (backup_root / INK_UUID / "1" / "1.rm").unlink()
    (backup_root / INK_UUID / "1").rmdir()
    seen = []
    report = pull(dest, LocalTablet(backup_root), progress=lambda u: seen.append(u) or True)
    assert report.changed == [INK_UUID] and report.new == []
    assert seen == [INK_UUID]
    assert json.loads((dest / f"{INK_UUID}.metadata").read_text())["lastModified"] == "1800000000000"
    assert not (dest / INK_UUID / "1").exists()  # the removed page is gone from the mirror too
    assert (dest / INK_UUID / "0" / "0.rm").exists()


def test_new_document_on_the_tablet_arrives(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    new_uuid = "99999999-1111-2222-3333-444444444444"
    write_metadata(backup_root, new_uuid, {"visibleName": "Late", "parent": "", "type": "DocumentType"})
    write_content(backup_root, new_uuid, {"fileType": "notebook", "pages": []})
    report = pull(dest, LocalTablet(backup_root))
    assert report.new == [new_uuid]
    assert (dest / f"{new_uuid}.content").exists()


def test_documents_gone_from_the_tablet_are_reported_and_pruned_only_on_request(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    (backup_root / f"{DIARY_UUID}.metadata").unlink()
    report = pull(dest, LocalTablet(backup_root))
    assert report.removed == [DIARY_UUID]
    assert (dest / f"{DIARY_UUID}.content").exists()  # kept
    report = pull(dest, LocalTablet(backup_root), prune=True)
    assert report.removed == [DIARY_UUID]
    assert not (dest / f"{DIARY_UUID}.content").exists()
    assert not (dest / DIARY_UUID).exists()


def test_force_fetches_everything_again(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    report = pull(dest, LocalTablet(backup_root), force=True)
    assert report.fetched == len(list(backup_root.glob("*.metadata")))
    assert report.unchanged == []


def test_pull_can_be_cancelled(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    report = pull(dest, LocalTablet(backup_root), progress=lambda u: False)
    assert not report.completed
    assert report.fetched >= 1  # the batch that was in flight is kept


def test_plan_is_pure(backup_root, tmp_path):
    remote = LocalTablet(backup_root).read_metadata()
    report = plan(remote, tmp_path / "empty-mirror")
    assert set(report.new) == set(remote) and report.unchanged == []
    assert JOURNAL_UUID in report.new


def test_highlights_directory_travels_with_its_document_and_is_replaced(backup_root, tmp_path):
    # the tablet keeps a document's highlights in a <uuid>.highlights directory
    hl = backup_root / f"{PDF_UUID}.highlights"
    hl.mkdir()
    (hl / "page0.json").write_text("{}")
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    assert (dest / f"{PDF_UUID}.highlights" / "page0.json").exists()
    # an edit replaces the whole document, directories included
    meta_path = backup_root / f"{PDF_UUID}.metadata"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["lastModified"] = "1800000000000"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (hl / "page0.json").unlink()
    (hl / "page1.json").write_text("{}")
    report = pull(dest, LocalTablet(backup_root))
    assert report.changed == [PDF_UUID]
    assert not (dest / f"{PDF_UUID}.highlights" / "page0.json").exists()
    assert (dest / f"{PDF_UUID}.highlights" / "page1.json").exists()
    assert not (dest / ".remarksync-pull-tmp").exists()


def test_failed_fetch_keeps_the_local_copy(backup_root, tmp_path):
    dest = tmp_path / "mirror"
    pull(dest, LocalTablet(backup_root))
    meta_path = backup_root / f"{INK_UUID}.metadata"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["lastModified"] = "1800000000000"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    class Flaky(LocalTablet):
        def fetch(self, uuids, into):
            raise RuntimeError("cable pulled")

    import pytest

    with pytest.raises(RuntimeError):
        pull(dest, Flaky(backup_root))
    assert (dest / f"{INK_UUID}.content").exists()  # untouched
    assert (dest / INK_UUID / "0" / "0.rm").exists()
    assert not (dest / ".remarksync-pull-tmp").exists()
