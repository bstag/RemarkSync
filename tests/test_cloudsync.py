"""Incremental local publication, history, recovery and PDF retry semantics."""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import zipfile
import urllib.error

import pytest

from remarksync.backup import iter_docs, scan_backup
from remarksync.cloud import CloudError
from remarksync import cloudsync
from .test_cloud import IDENTIFIER, source


@pytest.fixture
def data():
    return source.__wrapped__()


class Source:
    def __init__(self, entry, blobs):
        self.remote = {IDENTIFIER: entry}
        self.blobs = dict(blobs)
        self.downloads = 0
        self.reads = 0
        self.change = False
        self.fail = False

    @contextlib.contextmanager
    def snapshot(self, code=None):
        self.reads += 1
        remote = copy.deepcopy(self.remote)
        if self.change and self.reads % 2 == 0:
            remote[IDENTIFIER]["Hash"] = "c" * 64
        yield remote, "fake-token"

    def download(self, entry, token, destination):
        self.downloads += 1
        if self.fail:
            raise CloudError("Synthetic download failure")
        with zipfile.ZipFile(destination, "x") as archive:
            for file in entry["Files"]:
                archive.writestr(file["DocumentID"], self.blobs[file["DocumentID"]])


def test_first_sync_and_unchanged_cycle(data, tmp_path):
    client = Source(*data)
    progress = []
    first = cloudsync.sync(client, tmp_path, progress=progress.append)
    assert first["status"] == "ok" and first["downloaded"] == [IDENTIFIER]
    assert first["preserved"] and first["cloud_document_writes"] == 0
    catalog = (tmp_path / "preservation.html").read_text()
    assert "Cloud sample" in catalog and "annotated.pdf" in catalog
    assert len(list(iter_docs(scan_backup(Path(first["raw_library"]))))) == 1
    assert progress[0]["total"] == 1
    repeated = cloudsync.sync(client, tmp_path)
    assert repeated["unchanged"] == [IDENTIFIER] and not repeated["downloaded"]
    assert client.downloads == 1


def test_reexport_preserves_prior_pdf_and_reuses_corrected_rendering(data, tmp_path):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    old_pdf = Path(first['preserved'][0])
    original_bytes = old_pdf.read_bytes()
    result = cloudsync.reexport_pdf(tmp_path, IDENTIFIER, 'a' * 64)
    assert old_pdf.read_bytes() == original_bytes
    assert result['previous_sha256'] == hashlib.sha256(original_bytes).hexdigest()
    state = json.loads((tmp_path / cloudsync.STATE).read_text())
    record = state['documents'][IDENTIFIER]
    corrected = cloudsync.preservation_pdf(old_pdf.parent, record)
    assert corrected.parent.parent.name == 'renderings' and corrected.is_file()
    assert hashlib.sha256(corrected.read_bytes()).hexdigest() == record['pdf_sha256']
    repeated = cloudsync.sync(client, tmp_path)
    assert repeated['unchanged'] == [IDENTIFIER] and client.downloads == 1
    assert 'renderings/' in (tmp_path / 'preservation.html').read_text()


def test_reexport_failure_keeps_prior_publication(data, tmp_path, monkeypatch):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    old_pdf = Path(first['preserved'][0])
    original_bytes = old_pdf.read_bytes()
    original_state = (tmp_path / cloudsync.STATE).read_bytes()
    monkeypatch.setattr(cloudsync, 'export_pdf', lambda *_a, **_kw: (_ for _ in ()).throw(ValueError('synthetic export failure')))
    with pytest.raises(ValueError, match='synthetic export failure'):
        cloudsync.reexport_pdf(tmp_path, IDENTIFIER, 'a' * 64)
    assert old_pdf.read_bytes() == original_bytes
    assert (tmp_path / cloudsync.STATE).read_bytes() == original_state
    with pytest.raises(CloudError, match='changed'):
        cloudsync.reexport_pdf(tmp_path, IDENTIFIER, 'b' * 64)


def test_reexport_state_switch_failure_keeps_old_pdf_readable(data, tmp_path, monkeypatch):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    old_pdf = Path(first['preserved'][0])
    original_bytes = old_pdf.read_bytes()
    original_state = (tmp_path / cloudsync.STATE).read_bytes()
    real_write = cloudsync.write_json

    def fail_state_switch(path, data):
        if path == tmp_path / cloudsync.STATE:
            raise OSError('synthetic state-switch failure')
        real_write(path, data)

    monkeypatch.setattr(cloudsync, 'write_json', fail_state_switch)
    with pytest.raises(OSError, match='state-switch'):
        cloudsync.reexport_pdf(tmp_path, IDENTIFIER, 'a' * 64)
    assert old_pdf.read_bytes() == original_bytes
    assert (tmp_path / cloudsync.STATE).read_bytes() == original_state
    monkeypatch.setattr(cloudsync, 'write_json', real_write)
    assert cloudsync.sync(client, tmp_path)['unchanged'] == [IDENTIFIER]


def test_reexport_rejects_rendering_symlink_before_creating_files(data, tmp_path):
    first = cloudsync.sync(Source(*data), tmp_path)
    old_pdf = Path(first['preserved'][0])
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    try:
        (old_pdf.parent / 'renderings').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Symlinks unavailable')
    with pytest.raises(CloudError, match='Unsafe rendering'):
        cloudsync.reexport_pdf(tmp_path, IDENTIFIER, 'a' * 64)
    assert not list(outside.iterdir())


def test_changed_revision_retains_raw_history_and_old_pdf(data, tmp_path):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    old_pdf = Path(first["preserved"][0]).read_bytes()
    entry = client.remote[IDENTIFIER]
    entry["Hash"] = "b" * 64
    metadata = IDENTIFIER + ".metadata"
    client.blobs[metadata] = json.dumps({"type": "DocumentType", "visibleName": "Renamed cloud PDF"}).encode()
    for item in entry["Files"]:
        if item["DocumentID"] == metadata:
            item.update(Hash=hashlib.sha256(client.blobs[metadata]).hexdigest(), Size=len(client.blobs[metadata]))
    second = cloudsync.sync(client, tmp_path)
    assert second["changed"] == [IDENTIFIER] and second["status"] == "ok"
    assert Path(first["preserved"][0]).read_bytes() == old_pdf
    assert list((tmp_path / "raw" / ".remarksync-history" / IDENTIFIER).glob("*/*.metadata"))


def test_disappearance_retains_local_library(data, tmp_path):
    client = Source(*data)
    cloudsync.sync(client, tmp_path)
    client.remote.clear()
    result = cloudsync.sync(client, tmp_path)
    assert result["missing"] == [IDENTIFIER]
    assert (tmp_path / "raw" / (IDENTIFIER + ".metadata")).exists()


def test_changed_cloud_source_not_published(data, tmp_path):
    client = Source(*data)
    client.change = True
    result = cloudsync.sync(client, tmp_path)
    assert result["status"] == "partial" and result["failed"]
    assert not (tmp_path / "raw" / (IDENTIFIER + ".metadata")).exists()
    client.change = False
    recovered = cloudsync.sync(client, tmp_path)
    assert recovered["status"] == "ok" and client.downloads == 1  # cached exact revision retry


def test_failed_new_acquisition_retains_last_success(data, tmp_path):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    client.remote[IDENTIFIER]["Hash"] = "b" * 64
    client.fail = True
    failed = cloudsync.sync(client, tmp_path)
    assert failed["status"] == "partial" and failed["last_success"] == first["last_success"]
    state = json.loads((tmp_path / cloudsync.STATE).read_text())
    assert state["documents"][IDENTIFIER]["revision"] == "a" * 64
    assert (tmp_path / "raw" / (IDENTIFIER + ".pdf")).is_file()


def test_pdf_failure_retains_raw_and_retry_uses_cached_archive(data, tmp_path, monkeypatch):
    client = Source(*data)
    original = cloudsync.export_pdf
    monkeypatch.setattr(cloudsync, "export_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unsupported scene")))
    failed = cloudsync.sync(client, tmp_path)
    assert failed["pdf_failed"] and not failed["failed"] and failed["downloaded"] == [IDENTIFIER]
    assert (tmp_path / "raw" / (IDENTIFIER + ".pdf")).exists()
    monkeypatch.setattr(cloudsync, "export_pdf", original)
    retry = cloudsync.sync(client, tmp_path)
    assert retry["status"] == "ok" and retry["preserved"] and client.downloads == 1


def test_local_damage_repaired_from_verified_archive(data, tmp_path):
    client = Source(*data)
    cloudsync.sync(client, tmp_path)
    path = tmp_path / "raw" / (IDENTIFIER + ".pdf")
    path.write_bytes(b"damage")
    fixed = cloudsync.sync(client, tmp_path)
    assert fixed["changed"] == [IDENTIFIER] and client.downloads == 1
    assert path.read_bytes() == data[1][IDENTIFIER + ".pdf"]


def test_corrupt_authoritative_archive_refused(data, tmp_path):
    client = Source(*data)
    first = cloudsync.sync(client, tmp_path)
    archive = Path(first["preserved"][0]).with_name("document.rmdoc")
    archive.write_bytes(b"damage")
    result = cloudsync.sync(client, tmp_path)
    assert result["status"] == "partial" and result["failed"] and archive.read_bytes() == b"damage"


def test_folder_and_notebook_preserve_hierarchy(tmp_path, data):
    client = Source(*data)
    folder = "1e17351e-7bd9-4628-ac50-c5cd21c2dc71"
    folder_meta = folder + ".metadata"
    client.blobs[folder_meta] = json.dumps({"type": "CollectionType", "visibleName": "Cloud folder"}).encode()
    client.remote[folder] = {"DocumentID": folder, "Hash": "d" * 64, "Metadata": {"type": "CollectionType"}, "Content": {},
                             "Files": [{"DocumentID": folder_meta, "Hash": hashlib.sha256(client.blobs[folder_meta]).hexdigest(),
                                        "Size": len(client.blobs[folder_meta])}], "Subfiles": 1}
    name = IDENTIFIER + ".metadata"
    client.blobs[name] = json.dumps({"type": "DocumentType", "visibleName": "Nested notebook", "parent": folder}).encode()
    name = IDENTIFIER + ".content"
    client.blobs[name] = json.dumps({"fileType": "notebook", "pages": ["page"]}).encode()
    client.remote[IDENTIFIER]["Content"]["fileType"] = "notebook"
    for file in client.remote[IDENTIFIER]["Files"]:
        file.update(Hash=hashlib.sha256(client.blobs[file["DocumentID"]]).hexdigest(), Size=len(client.blobs[file["DocumentID"]]))
    result = cloudsync.sync(client, tmp_path)
    assert result["status"] == "ok" and not result["preserved"]
    tree = scan_backup(tmp_path / "raw")
    assert tree[0].kind == "folder" and tree[0].children[0].name == "Nested notebook"


def test_cannot_use_existing_usb_folder(data, tmp_path):
    (tmp_path / (IDENTIFIER + ".metadata")).write_text("existing")
    with pytest.raises(CloudError, match="empty destination"):
        cloudsync.sync(Source(*data), tmp_path)
    assert (tmp_path / (IDENTIFIER + ".metadata")).read_text() == "existing"


def test_user_token_expiry_refresh_is_bounded_and_read_only(data, tmp_path):
    class Expiring(Source):
        attempts = 0
        def download(self, entry, token, destination):
            self.attempts += 1
            if self.attempts == 1:
                raise urllib.error.HTTPError("https://internal.cloud.remarkable.com/", 401, "expired", {}, None)
            super().download(entry, token, destination)
    client = Expiring(*data)
    result = cloudsync.sync(client, tmp_path)
    assert result["status"] == "ok" and client.attempts == 2 and client.reads == 3
    assert result["cloud_document_writes"] == 0


def test_network_listing_failure_records_error_keeps_last_success(data, tmp_path):
    client = Source(*data)
    success = cloudsync.sync(client, tmp_path)
    @contextlib.contextmanager
    def unavailable():
        raise CloudError("Synthetic service outage")
        yield  # contextmanager contract
    client.snapshot = unavailable
    with pytest.raises(CloudError):
        cloudsync.sync(client, tmp_path)
    state = json.loads((tmp_path / cloudsync.STATE).read_text())
    assert state["status"] == "error" and state["last_success"] == success["last_success"]
    assert (tmp_path / "raw" / (IDENTIFIER + ".pdf")).is_file()
