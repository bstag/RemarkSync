import contextlib
import hashlib
from http.server import ThreadingHTTPServer
import json
import threading
import urllib.error
import urllib.request

import pytest

from remarksync import server

IDENTIFIER = "b4ec01c3-61f9-4758-89b5-7ed041e732db"
REVISION = "a" * 64


@pytest.fixture
def store(tmp_path):
    target = tmp_path / "revisions" / IDENTIFIER / REVISION
    target.mkdir(parents=True)
    # Transport tests use opaque bytes; they do not generate or render PDFs.
    pdf, archive = b"preserved PDF bytes", b"raw notebook and annotation bytes"
    (target / "annotated.pdf").write_bytes(pdf)
    (target / "document.rmdoc").write_bytes(archive)
    state = {"status": "ok", "last_success": "2026-10-03T20:00:00Z", "documents": {
        IDENTIFIER: {"name": '<script>unsafe name</script>', "kind": "DocumentType", "revision": REVISION,
                     "pdf_sha256": hashlib.sha256(pdf).hexdigest(), "archive_sha256": hashlib.sha256(archive).hexdigest()}}}
    (tmp_path / server.STATE).write_text(json.dumps(state))
    return tmp_path, target, state


@contextlib.contextmanager
def running(root):
    with ThreadingHTTPServer(("127.0.0.1", 0), server.handler(root)) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.server_port}"
        finally:
            listener.shutdown()
            thread.join()


def test_private_library_index_status_and_downloads(store):
    root, target, _state = store
    with running(root) as base:
        with urllib.request.urlopen(base) as response:
            content = response.read().decode()
            assert '&lt;script&gt;unsafe name&lt;/script&gt;' in content and '<script>unsafe' not in content
            assert 'document.rmdoc' in content and 'annotated.pdf' in content
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        status = json.load(urllib.request.urlopen(base + "/status"))
        assert status["documents"] == status["pdfs"] == 1 and "last_success" in status
        assert "documents" not in status or isinstance(status["documents"], int)
        for name in ("annotated.pdf", "document.rmdoc"):
            with urllib.request.urlopen(f"{base}/revisions/{IDENTIFIER}/{REVISION}/{name}") as response:
                assert response.read() == (target / name).read_bytes()
                assert response.headers["Accept-Ranges"] == "bytes"


def test_range_and_head_requests(store):
    root, target, _state = store
    with running(root) as base:
        url = f"{base}/revisions/{IDENTIFIER}/{REVISION}/annotated.pdf"
        request = urllib.request.Request(url, headers={"Range": "bytes=2-6"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.read() == (target / "annotated.pdf").read_bytes()[2:7]
            assert response.headers["Content-Range"].startswith("bytes 2-6/")
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as response:
            assert response.read() == b"" and int(response.headers["Content-Length"]) == (target / "annotated.pdf").stat().st_size
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=99999-"}))
        assert error.value.code == 416


def test_manifest_allowlist_never_exposes_local_state_paths(store):
    root, _target, state = store
    record = state['documents'][IDENTIFIER]
    record.update(document=IDENTIFIER, files=[], file_type='pdf', private_path='/state/cloud/account.fernet')
    state['last_report'] = {'raw_library': '/data/library/raw', 'secret': 'not public'}
    (root / server.STATE).write_text(json.dumps(state))
    with running(root) as base:
        manifest = json.load(urllib.request.urlopen(base + '/manifest'))
    assert manifest['format'] == 'remarksync-library-v1'
    assert manifest['documents'][IDENTIFIER]['archive_sha256'] == record['archive_sha256']
    assert 'private_path' not in manifest['documents'][IDENTIFIER] and 'last_report' not in manifest


def test_current_pdf_can_use_a_retained_versioned_rendering(store):
    root, target, state = store
    old_bytes = (target / 'annotated.pdf').read_bytes()
    corrected = b'corrected yellow preservation rendering'
    digest = hashlib.sha256(corrected).hexdigest()
    rendered = target / 'renderings' / digest / 'annotated.pdf'
    rendered.parent.mkdir(parents=True)
    rendered.write_bytes(corrected)
    state['documents'][IDENTIFIER]['pdf_sha256'] = digest
    (root / server.STATE).write_text(json.dumps(state))
    with running(root) as base:
        url = f'{base}/revisions/{IDENTIFIER}/{REVISION}/annotated.pdf'
        assert urllib.request.urlopen(url).read() == corrected
        rendered.write_bytes(b'corrupt corrected rendering')
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url)
        assert error.value.code == 503
    assert (target / 'annotated.pdf').read_bytes() == old_bytes


@pytest.mark.parametrize("path", ["/sync-state.json", "/raw/document.pdf", "/keys/cloud.key", "/../cloud/account.fernet",
                                  f"/revisions/{IDENTIFIER}/{REVISION}/revision.json",
                                  f"/revisions/{IDENTIFIER}/{'b' * 64}/document.rmdoc",
                                  f"/revisions/{IDENTIFIER}/{REVISION}/%2e%2e%2faccount.fernet"])
def test_unpublished_and_arbitrary_paths_are_not_served(store, path):
    with running(store[0]) as base, pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(base + path)
    assert error.value.code == 404


def test_corrupt_artifact_refused_and_retained(store):
    root, target, _state = store
    (target / "document.rmdoc").write_bytes(b"damaged")
    with running(root) as base, pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{base}/revisions/{IDENTIFIER}/{REVISION}/document.rmdoc")
    assert error.value.code == 503 and (target / "document.rmdoc").read_bytes() == b"damaged"


def test_symlink_artifact_never_served(store):
    root, target, _state = store
    original = target / "annotated.pdf"
    outside = root.parent / "outside.pdf"
    outside.write_bytes(original.read_bytes())
    original.unlink()
    try:
        original.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with running(root) as base, pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{base}/revisions/{IDENTIFIER}/{REVISION}/annotated.pdf")
    assert error.value.code == 404


def test_notebooks_have_raw_download_without_pdf(store):
    root, _target, state = store
    state["documents"][IDENTIFIER].pop("pdf_sha256")
    state["documents"][IDENTIFIER]["pdf_error"] = "renderer failed"
    (root / server.STATE).write_text(json.dumps(state))
    with running(root) as base:
        content = urllib.request.urlopen(base).read().decode()
        assert "Download raw archive" in content and "Open PDF" not in content
        assert "raw archive preserved" in content


def test_first_sync_and_bad_state_have_clear_status(tmp_path):
    with running(tmp_path) as base:
        assert json.load(urllib.request.urlopen(base + "/status"))["status"] == "not synced"
        (tmp_path / server.STATE).write_text("invalid json")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/status")
        assert error.value.code == 503


@pytest.mark.parametrize("header,expected", [(None, (0, 9, False)), ("bytes=3-", (3, 9, True)),
                                           ("bytes=-4", (6, 9, True)), ("bytes=0-999", (0, 9, True))])
def test_byte_ranges(header, expected):
    assert server._range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=-0", "bytes=9-1", "bytes=-", "bytes=1-2,3-4", "garbage"])
def test_invalid_ranges(header):
    with pytest.raises(ValueError):
        server._range(header, 10)


def test_backup_filename_does_not_depend_on_a_fraction_of_a_second():
    expected = "remarksync-library-20261121T164100Z.zip"
    assert server._backup_name("2026-11-21T16:41:00+00:00") == expected
    assert server._backup_name("2026-11-21T16:41:00.250000+00:00") == expected
