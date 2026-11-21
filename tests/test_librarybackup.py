import copy
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile

import pytest

from remarksync import librarybackup as backup, server
from remarksync.__main__ import main

DOCUMENT = "b4ec01c3-61f9-4758-89b5-7ed041e732db"
FOLDER = "838893fb-cb95-4bb6-a44f-87df1ca6880c"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def add_revision(root, identifier, revision, *, folder=False, title="Notebook", pdf=None):
    target = root / "revisions" / identifier / revision
    target.mkdir(parents=True)
    kind = "CollectionType" if folder else "DocumentType"
    files = {identifier + ".metadata": json.dumps({"type": kind, "visibleName": title, "parent": ""}).encode()}
    if not folder:
        files[identifier + ".content"] = b'{"fileType":"notebook","pages":[]}'
        files[identifier + "/page.rm"] = b"retained scene " + revision.encode()
    with zipfile.ZipFile(target / "document.rmdoc", "w") as raw:
        for name, value in files.items():
            raw.writestr(name, value)
    record = {"document": identifier, "revision": revision, "name": title, "kind": kind, "file_type": "notebook",
              "archive_sha256": digest((target / "document.rmdoc").read_bytes()),
              "files": [{"path": name, "size": len(value), "sha256": digest(value)} for name, value in files.items()],
              "warnings": []}
    if pdf:
        (target / "annotated.pdf").write_bytes(pdf)
        record["pdf_sha256"] = digest(pdf)
    (target / "revision.json").write_text(json.dumps(record))
    return record, files


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "store"
    past, _ = add_revision(root, DOCUMENT, "a" * 64, title="Earlier title", pdf=b"earlier PDF")
    current, files = add_revision(root, DOCUMENT, "b" * 64, pdf=b"earlier gray PDF")
    yellow = b"corrected yellow PDF"
    current["pdf_sha256"] = digest(yellow)
    target = root / "revisions" / DOCUMENT / current["revision"]
    rendered = target / "renderings" / digest(yellow) / "annotated.pdf"
    rendered.parent.mkdir(parents=True)
    rendered.write_bytes(yellow)
    current["private_path"] = "/state/cloud/account.fernet"
    (target / "revision.json").write_text(json.dumps(current))
    folder, _ = add_revision(root, FOLDER, "c" * 64, folder=True)
    state = {"status": "ok", "last_success": "2026-10-03T23:00:00Z", "documents": {DOCUMENT: current, FOLDER: folder},
             "last_report": {"private_path": "/keys/cloud.key"}}
    (root / backup.STATE).write_text(json.dumps(state))
    (root / "credentials").mkdir()
    (root / "credentials/account.fernet").write_bytes(b"must never leave the server")
    (root / "raw").mkdir()
    (root / "raw/unpublished.pending").write_bytes(b"mutable unfinished projection")
    (target / "export.pending.pdf").write_bytes(b"unfinished rendering")
    return root, state, files, past


def download(root, state, path):
    with path.open("wb") as output:
        backup.write_snapshot(backup.snapshot(root, state), output)
    return path


def test_backup_preserves_history_and_renderings_excludes_credentials_and_restores(library, tmp_path):
    root, state, files, _past = library
    archive = download(root, state, tmp_path / "backup.zip")
    report = backup.verify(archive)
    assert report["documents"] == report["folders"] == 1
    assert report["retained_revisions"] == 3 and report["pdfs"] == 3
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        assert "RESTORE.txt" in names and "backup-manifest.json" in names
        assert not any("credentials" in name or "raw/" in name or "pending" in name for name in names)
        assert "private_path" not in zipped.read(backup.STATE).decode()
        assert "private_path" not in zipped.read(f'revisions/{DOCUMENT}/{"b" * 64}/revision.json').decode()
    destination = tmp_path / "recovered"
    result = backup.restore(archive, destination)
    assert result["retained_revisions"] == 3
    for name, value in files.items():
        assert (destination / "raw" / name).read_bytes() == value
    assert (destination / "raw" / (FOLDER + ".metadata")).is_file()
    restored_state = json.loads((destination / backup.STATE).read_text())
    record = restored_state["documents"][DOCUMENT]
    pdf = backup.preservation_pdf(destination / "revisions" / DOCUMENT / record["revision"], record)
    assert pdf.read_bytes() == b"corrected yellow PDF"
    assert (destination / "preservation.html").is_file()
    assert main(["backup", "verify", str(archive)]) == 0
    with pytest.raises(ValueError, match="new destination"):
        backup.restore(archive, destination)


def test_snapshot_remains_valid_after_acquisition_publishes_new_revision(library, tmp_path):
    root, state, _files, _past = library
    plan = backup.snapshot(root, state)
    newer, _ = add_revision(root, DOCUMENT, "d" * 64, title="Newer title")
    state["documents"][DOCUMENT] = newer
    (root / backup.STATE).write_text(json.dumps(state))
    archive = tmp_path / "snapshot.zip"
    with archive.open("wb") as output:
        backup.write_snapshot(plan, output)
    assert backup.verify(archive)["retained_revisions"] == 3
    with zipfile.ZipFile(archive) as zipped:
        assert json.loads(zipped.read(backup.STATE))["documents"][DOCUMENT]["revision"] == "b" * 64
        assert not any("d" * 64 in name for name in zipped.namelist())


def test_changed_source_aborts_stream_without_completed_backup(library, tmp_path):
    root, state, _files, _past = library
    plan = backup.snapshot(root, state)
    archive = root / "revisions" / DOCUMENT / ("a" * 64) / "document.rmdoc"
    archive.write_bytes(b"corrupted after capture")
    output = io.BytesIO()
    with pytest.raises(ValueError, match="changed"):
        backup.write_snapshot(plan, output)
    incomplete = tmp_path / "incomplete.zip"
    incomplete.write_bytes(output.getvalue())
    # A nested .rmdoc's footer can be recognized as a ZIP, but it can never
    # masquerade as a completed whole-library backup without its manifest.
    with pytest.raises((zipfile.BadZipFile, KeyError)):
        backup.verify(incomplete)


def test_corrupt_source_refused_before_snapshot_download(library):
    root, state, _files, _past = library
    (root / "revisions" / DOCUMENT / ("a" * 64) / "document.rmdoc").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="verification"):
        backup.snapshot(root, state)


def test_inner_raw_checksum_failure_is_detected_even_with_matching_outer_hash(library, tmp_path):
    root, state, _files, _past = library
    record = state["documents"][DOCUMENT]
    archive = root / "revisions" / DOCUMENT / record["revision"] / "document.rmdoc"
    with zipfile.ZipFile(archive) as raw:
        contents = {name: raw.read(name) for name in raw.namelist()}
    contents[DOCUMENT + "/page.rm"] = b"different annotation source"
    with zipfile.ZipFile(archive, "w") as raw:
        for name, value in contents.items():
            raw.writestr(name, value)
    record["archive_sha256"] = digest(archive.read_bytes())
    # Preserve the length so the inner checksum, rather than member set, fails.
    next(item for item in record["files"] if item["path"].endswith("page.rm"))["size"] = len(contents[DOCUMENT + "/page.rm"])
    backup_path = download(root, state, tmp_path / "backup.zip")
    with pytest.raises(ValueError, match="source checksum"):
        backup.verify(backup_path)


def test_corrupt_or_unexpected_backup_never_publishes_restore(library, tmp_path):
    root, state, _files, _past = library
    archive = download(root, state, tmp_path / "backup.zip")
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr("../escaped.txt", b"unexpected source")
    destination = tmp_path / "recovered"
    with pytest.raises(ValueError, match="file set"):
        backup.restore(archive, destination)
    assert not destination.exists() and not (tmp_path / "escaped.txt").exists()


def test_truncated_zip_cli_reports_failure(library, tmp_path, capsys):
    root, state, _files, _past = library
    archive = download(root, state, tmp_path / "backup.zip")
    archive.write_bytes(archive.read_bytes()[:-50])
    assert main(["backup", "verify", str(archive)]) == 1
    assert "could not be verified" in capsys.readouterr().err


def test_backup_source_symlink_is_refused(library, tmp_path):
    root, state, _files, _past = library
    archive = root / "revisions" / DOCUMENT / ("a" * 64) / "document.rmdoc"
    outside = tmp_path / "outside.rmdoc"
    outside.write_bytes(archive.read_bytes())
    archive.unlink()
    try:
        archive.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError, match="link"):
        backup.snapshot(root, state)


def test_web_backup_button_attachment_head_and_verified_download(library, tmp_path):
    root, state, _files, _past = library
    with ThreadingHTTPServer(("127.0.0.1", 0), server.handler(root)) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{listener.server_port}"
            with urllib.request.urlopen(base) as response:
                assert 'href="/backup.zip" download' in response.read().decode()
            with urllib.request.urlopen(urllib.request.Request(base + "/backup.zip", method="HEAD")) as response:
                assert response.headers["Content-Type"] == "application/zip"
                assert "attachment; filename=" in response.headers["Content-Disposition"]
                assert response.read() == b""
            with urllib.request.urlopen(base + "/backup.zip") as response:
                archive = tmp_path / "downloaded.zip"
                archive.write_bytes(response.read())
            assert backup.verify(archive)["retained_revisions"] == 3
            bad = copy.deepcopy(state)
            bad["status"] = "running"
            (root / backup.STATE).write_text(json.dumps(bad))
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/backup.zip")
            assert error.value.code == 503
        finally:
            listener.shutdown()
            thread.join()


def test_web_limits_backups_without_blocking_status_requests(library, monkeypatch):
    root, _state, _files, _past = library
    entered, release = threading.Event(), threading.Event()
    original = server.snapshot

    def paused(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(server, "snapshot", paused)
    with ThreadingHTTPServer(("127.0.0.1", 0), server.handler(root)) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{listener.server_port}"
        results = []

        def first():
            with urllib.request.urlopen(base + "/backup.zip") as response:
                results.append(response.read())

        downloader = threading.Thread(target=first)
        downloader.start()
        try:
            assert entered.wait(5)
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/backup.zip")
            assert error.value.code == 429 and error.value.headers["Retry-After"] == "15"
            with urllib.request.urlopen(base + "/status") as response:
                assert json.load(response)["status"] == "ok"
        finally:
            release.set()
            downloader.join()
            listener.shutdown()
            thread.join()
        assert results


def test_backup_and_restore_keep_the_removed_from_tablet_mark(library, tmp_path):
    root, state, _files, _past = library
    state["documents"][DOCUMENT]["absent_since"] = "2026-11-21T10:00:00+00:00"
    archive = download(root, state, tmp_path / "backup.zip")
    assert backup.verify(archive)["documents"] == 1
    backup.restore(archive, tmp_path / "recovered")
    restored = json.loads((tmp_path / "recovered" / backup.STATE).read_bytes())["documents"]
    assert restored[DOCUMENT]["absent_since"] == "2026-11-21T10:00:00+00:00"
    assert "absent_since" not in restored[FOLDER]


def test_backup_made_before_the_mark_was_kept_still_verifies_and_restores(library, tmp_path, monkeypatch):
    root, state, _files, _past = library
    state["documents"][DOCUMENT]["absent_since"] = "2026-11-21T10:00:00+00:00"
    current = backup.RECORD_FIELDS
    monkeypatch.setattr(backup, "RECORD_FIELDS", tuple(field for field in current if field != "absent_since"))
    archive = download(root, state, tmp_path / "earlier.zip")
    monkeypatch.setattr(backup, "RECORD_FIELDS", current)
    with zipfile.ZipFile(archive) as zipped:
        assert "absent_since" not in zipped.read(backup.STATE).decode()
    assert backup.verify(archive)["retained_revisions"] == 3
    backup.restore(archive, tmp_path / "recovered")
    assert (tmp_path / "recovered" / "raw" / (DOCUMENT + ".metadata")).is_file()
