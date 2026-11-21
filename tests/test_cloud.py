"""Read-only transport, credential isolation, integrity and preservation gates."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

import fitz
import pytest

from remarksync import cloud
from .conftest import page_rm

IDENTIFIER = "b4ec01c3-61f9-4758-89b5-7ed041e732db"


@pytest.fixture
def source():
    with fitz.open() as pdf:
        pdf.new_page().insert_text((80, 120), "Original cloud PDF body text")
        base = pdf.tobytes()
    content = {"fileType": "pdf", "pages": ["page"], "cloudUnknownField": {"keep": True}}
    blobs = {IDENTIFIER + ".metadata": json.dumps({"type": "DocumentType", "visibleName": "Cloud sample"}).encode(),
             IDENTIFIER + ".content": json.dumps(content).encode(), IDENTIFIER + ".pdf": base,
             IDENTIFIER + "/page.rm": page_rm(text="Preserved cloud note")}
    files = [{"DocumentID": name, "Hash": hashlib.sha256(data).hexdigest(), "Size": len(data)}
             for name, data in blobs.items()]
    entry = {"DocumentID": IDENTIFIER, "Hash": "a" * 64, "Subfiles": len(files), "Files": files,
             "Metadata": {"type": "DocumentType", "visibleName": "Cloud sample"},
             "Content": {"fileType": "pdf", "pages": ["page"]}}
    return entry, blobs


class FakeCloud(cloud.CloudClient):
    def __init__(self, entry, blobs):
        self.entry, self.blobs = entry, blobs
        self.reads, self.downloads = 0, 0
        self.change = False

    @contextlib.contextmanager
    def snapshot(self, code=None):
        self.reads += 1
        entry = copy.deepcopy(self.entry)
        if self.change and self.reads > 1:
            entry["Hash"] = "b" * 64
        yield {IDENTIFIER: entry}, "fake-user-token"

    def download(self, entry, token, destination):
        self.downloads += 1
        with zipfile.ZipFile(destination, "x") as archive:
            for name, data in self.blobs.items():
                archive.writestr(name, data)


def test_cloud_trial_preserves_raw_original_and_notes_and_reuses_revision(tmp_path, source):
    entry, blobs = source
    client = FakeCloud(entry, blobs)
    result = cloud.trial(client, IDENTIFIER, tmp_path / "trial")
    assert result["cloud_document_writes"] == 0 and not result["cached"]
    with fitz.open(result["path"]) as pdf:
        assert "Original cloud PDF body text" in pdf[0].get_text()
        assert any("Preserved cloud note" in page.get_text() for page in pdf)
        assert pdf.embfile_get("original.pdf") == blobs[IDENTIFIER + ".pdf"]
        assert pdf.embfile_get("scene-1.rm") == blobs[IDENTIFIER + "/page.rm"]
    with zipfile.ZipFile(Path(result["path"]).with_name("document.rmdoc")) as archive:
        assert archive.read(IDENTIFIER + ".content") == blobs[IDENTIFIER + ".content"]
    repeated = cloud.trial(client, IDENTIFIER, tmp_path / "trial")
    assert repeated["cached"] and client.downloads == 1


def test_changed_revision_never_publishes(tmp_path, source):
    client = FakeCloud(*source)
    client.change = True
    with pytest.raises(cloud.CloudError, match="changed"):
        cloud.trial(client, IDENTIFIER, tmp_path)
    assert not list(tmp_path.glob("**/annotated.pdf"))
    assert not list(tmp_path.glob(".trial-*"))


def test_corrupt_cached_archive_is_retained_and_refused(tmp_path, source):
    client = FakeCloud(*source)
    result = cloud.trial(client, IDENTIFIER, tmp_path)
    archive = Path(result["path"]).with_name("document.rmdoc")
    archive.write_bytes(b"damaged")
    with pytest.raises(cloud.CloudError, match="checksum"):
        cloud.trial(client, IDENTIFIER, tmp_path)
    assert archive.read_bytes() == b"damaged" and client.downloads == 1


def test_failed_new_revision_keeps_previous(tmp_path, source):
    client = FakeCloud(*source)
    first = cloud.trial(client, IDENTIFIER, tmp_path)
    original = Path(first["path"]).read_bytes()
    client.entry["Hash"] = "b" * 64
    client.blobs[IDENTIFIER + "/page.rm"] = b"bad scene"
    with pytest.raises(ValueError):
        cloud.trial(client, IDENTIFIER, tmp_path)
    assert Path(first["path"]).read_bytes() == original
    assert not list(tmp_path.glob(".trial-*"))


def _fake_http(monkeypatch, blobs, corrupt=False):
    by_hash = {hashlib.sha256(data).hexdigest(): data for data in blobs.values()}
    requests = []

    class Reader:
        def open(self, request, timeout):
            requests.append(request)
            assert request.get_method() == "GET"
            assert request.full_url.startswith("https://internal.cloud.remarkable.com/sync/v3/files/")
            data = by_hash[request.full_url.rsplit("/", 1)[1]]
            return io.BytesIO(data + b"corrupt" if corrupt else data)

    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *_args: Reader())
    return requests


def test_download_hash_verified_gets_only(tmp_path, source, monkeypatch):
    entry, blobs = source
    requests = _fake_http(monkeypatch, blobs)
    cloud.CloudClient(profile=tmp_path / "profile").download(entry, "fake-user-token", tmp_path / "document.rmdoc")
    with zipfile.ZipFile(tmp_path / "document.rmdoc") as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == blobs
    assert len(requests) == len(blobs)


def test_download_bad_bytes_removes_partial_only(tmp_path, source, monkeypatch):
    entry, blobs = source
    _fake_http(monkeypatch, blobs, corrupt=True)
    target = tmp_path / "document.rmdoc"
    with pytest.raises(cloud.CloudError):
        cloud.CloudClient(profile=tmp_path).download(entry, "token", target)
    assert not target.exists()
    target.write_bytes(b"previous")
    with pytest.raises(FileExistsError):
        cloud.CloudClient(profile=tmp_path).download(entry, "token", target)
    assert target.read_bytes() == b"previous"


@pytest.mark.parametrize("name", ["../../escape", IDENTIFIER + "/../escape", IDENTIFIER + "/CON.rm",
                                 IDENTIFIER + "/page.rm.", IDENTIFIER + "/page\n.rm", "different.pdf"])
def test_bad_cloud_paths_fail_before_network(tmp_path, source, monkeypatch, name):
    entry, _blobs = source
    entry["Files"].append({"DocumentID": name, "Hash": "b" * 64, "Size": 3})
    entry["Subfiles"] += 1
    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *_args: pytest.fail("no network for unsafe paths"))
    with pytest.raises((cloud.CloudError, ValueError)):
        cloud.CloudClient(profile=tmp_path).download(entry, "token", tmp_path / "document.rmdoc")


def test_no_credential_redirect():
    with pytest.raises(cloud.CloudError, match="credentials"):
        cloud._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")


def test_matching_size_wrong_hash_is_refused(tmp_path, source, monkeypatch):
    entry, blobs = source
    first = next(iter(blobs))
    changed = dict(blobs)
    changed[first] = bytes([blobs[first][0] ^ 1]) + blobs[first][1:]
    # Serve different bytes at the originally requested hash.
    class Reader:
        def open(self, request, timeout):
            return io.BytesIO(changed[first])
    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *_args: Reader())
    with pytest.raises(cloud.CloudError, match="checksum"):
        cloud.CloudClient(profile=tmp_path).download(entry, "token", tmp_path / "document.rmdoc")
    assert not (tmp_path / "document.rmdoc").exists()


def test_environment_isolates_cache_trace_and_endpoint_overrides(tmp_path, monkeypatch):
    for key in ("RMAPI_TRACE", "RMAPI_AUTH", "RMAPI_DOC", "RMAPI_HOST", "RMAPI_FORCE_SCHEMA_VERSION"):
        monkeypatch.setenv(key, "danger")
    env = cloud._environment(tmp_path)
    assert env["RMAPI_TRACE"] == "0" and env["LOCALAPPDATA"] == str(tmp_path)
    assert env["TEMP"] == env["TMP"] == str(tmp_path)
    assert env["RMAPI_CONFIG"] == str(tmp_path / "tokens.yaml")
    assert all(key not in env for key in ("RMAPI_AUTH", "RMAPI_DOC", "RMAPI_HOST", "RMAPI_FORCE_SCHEMA_VERSION"))


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI/ACL")
def test_dpapi_and_private_profile(tmp_path):
    profile = tmp_path / "private"
    cloud._private_profile(profile)
    secret = b"devicetoken: example-device\nusertoken: example-user\n"
    encrypted = cloud._dpapi(secret)
    assert secret not in encrypted and cloud._dpapi(encrypted, decrypt=True) == secret


@pytest.mark.skipif(os.name != "nt", reason="Windows token protection")
def test_pairing_code_stdin_only_and_fresh_session_cleanup(tmp_path, source, monkeypatch):
    entry, _blobs = source
    executable = tmp_path / "rmapi.exe"
    executable.touch()
    client = cloud.CloudClient(executable, tmp_path / "profile")
    cache = {"CacheVersion": 3, "SchemaVersion": "4", "Hash": "f" * 64, "Docs": [entry]}
    runs = []
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command[0] != str(executable):
            return real_run(command, **kwargs)
        runs.append((command, kwargs))
        work = Path(kwargs["cwd"])
        (work / "tokens.yaml").write_text("devicetoken: fake-device\nusertoken: fake-user\n")
        (work / "rmapi").mkdir()
        (work / "rmapi" / "tree.cache").write_text(json.dumps(cache))
        listing = json.dumps([{"id": IDENTIFIER, "type": "DocumentType"}]).encode()
        return subprocess.CompletedProcess(command, 0, stdout=listing, stderr=b"")

    monkeypatch.setattr(cloud.subprocess, "run", fake_run)
    assert client.list("abcd1234")[0]["id"] == IDENTIFIER
    assert client.paired and b"fake-device" not in client.secret.read_bytes()
    client.list()
    assert runs[0][0] == [str(executable), "-json", "find", "/"]
    assert runs[0][1]["input"] == b"abcd1234\n"
    assert runs[1][0] == [str(executable), "-ni", "-json", "find", "/"]
    assert runs[1][1]["input"] == b""
    assert not list(client.profile.glob("session-*"))


def test_listing_cache_contract_and_warnings(source):
    entry, _blobs = source
    cache = {"CacheVersion": 3, "SchemaVersion": "4", "Hash": "f" * 64, "Docs": [entry]}
    documents = cloud._documents(cache)
    output = json.dumps([{"id": "", "type": "CollectionType"}, {"id": IDENTIFIER, "type": "DocumentType"}]).encode()
    assert cloud._listed_documents(output, documents, pairing=False) == documents
    with pytest.raises(cloud.CloudError, match="warnings"):
        cloud._listed_documents(b"warning: incomplete\n" + output, documents, pairing=False)
    with pytest.raises(cloud.CloudError, match="match"):
        cloud._listed_documents(output, {}, pairing=False)
    cache["SchemaVersion"] = "future"
    with pytest.raises(cloud.CloudError, match="schema"):
        cloud._documents(cache)


def test_account_in_use_is_a_clear_cloud_error(tmp_path):
    from remarksync.backupstore import writer_lock
    executable = tmp_path / "rmapi.exe"
    executable.touch()
    client = cloud.CloudClient(executable, tmp_path / "profile")
    client.profile.mkdir()
    client.secret.write_bytes(b"not read: the lock is refused first")
    with writer_lock(client.profile):
        with pytest.raises(cloud.CloudError, match="Another RemarkSync cloud operation"):
            with client.snapshot():
                pass
    assert not list(client.profile.glob("session-*"))


@pytest.mark.parametrize("name,inside,safe", [
    (IDENTIFIER + ".content", True, True),
    (IDENTIFIER + "/0/0.rm", True, True),
    (IDENTIFIER + "x.content", False, True),
    ("sibling.content", False, True),
    (IDENTIFIER, False, True),
    (IDENTIFIER + "/a b.rm", True, False),
    (IDENTIFIER + "/con.rm", True, False),
    (IDENTIFIER + "/LPT1", True, False),
    (IDENTIFIER + "/../escape", True, False),
    (IDENTIFIER + "/a\\b", True, False),
    (IDENTIFIER + "/caf\u00e9.rm", True, False),
    (IDENTIFIER + "/line\nbreak", True, False),
    (IDENTIFIER + "/trailing.", True, False),
    (IDENTIFIER + "//empty", True, False),
])
def test_one_member_name_policy_for_download_and_send(name, inside, safe):
    assert cloud._member_inside(name, IDENTIFIER) is inside
    assert cloud._member_safe(name) is safe
