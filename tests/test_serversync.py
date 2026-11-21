"""Real HTTP transfer, integrity refusal, retained history and offline behavior."""
import copy
import hashlib
import json
import zipfile

import pytest

from remarksync import cloudsync, serversync
from remarksync.backup import scan_backup, iter_docs
from remarksync.cloud import CloudError
from .test_cloud import IDENTIFIER, source
from .test_cloudsync import Source
from .test_server import running
from .conftest import page_rm


@pytest.fixture
def hosted(tmp_path):
    central, local = tmp_path / 'central', tmp_path / 'desktop'
    client = Source(*source.__wrapped__())
    cloudsync.sync(client, central)
    return central, local, client


class RecordingClient(serversync.ServerClient):
    def __init__(self, base):
        super().__init__(base)
        self.downloads = []

    def download(self, identifier, revision, name, digest, destination):
        self.downloads.append(name)
        super().download(identifier, revision, name, digest, destination)


def test_real_download_checksum_parity_and_unchanged_cycle(hosted):
    central, local, _client = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        first = serversync.sync(remote, local)
        assert first['status'] == 'ok' and first['downloaded'] == [IDENTIFIER]
        assert remote.downloads == ['document.rmdoc', 'annotated.pdf']
        assert len(list(iter_docs(scan_backup(local / 'raw')))) == 1
        state = json.loads((central / cloudsync.STATE).read_text())
        record = state['documents'][IDENTIFIER]
        for file in record['files']:
            assert (local / 'raw' / file['path']).read_bytes() == (central / 'raw' / file['path']).read_bytes()
        pdf = cloudsync.preservation_pdf(local / 'revisions' / IDENTIFIER / record['revision'], record)
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == record['pdf_sha256']
        assert 'renderings/' in (local / 'preservation.html').read_text()
        second = serversync.sync(remote, local)
        assert second['unchanged'] == [IDENTIFIER] and not second['changed']
        assert len(remote.downloads) == 2


def test_changed_rendering_same_raw_revision_downloads_only_pdf(hosted):
    central, local, _client = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        serversync.sync(remote, local)
        state = json.loads((central / cloudsync.STATE).read_text())
        record = state['documents'][IDENTIFIER]
        target = central / 'revisions' / IDENTIFIER / record['revision']
        old = cloudsync.preservation_pdf(local / 'revisions' / IDENTIFIER / record['revision'], record)
        original = old.read_bytes()
        updated = (target / 'annotated.pdf').read_bytes() + b'\n% corrected rendering\n'
        (target / 'annotated.pdf').write_bytes(updated)
        record['pdf_sha256'] = hashlib.sha256(updated).hexdigest()
        (central / cloudsync.STATE).write_text(json.dumps(state))
        result = serversync.sync(remote, local)
        assert result['changed'] == [IDENTIFIER]
        assert remote.downloads == ['document.rmdoc', 'annotated.pdf', 'annotated.pdf']
        assert old.read_bytes() == original and (local / 'raw' / IDENTIFIER).is_dir()
        assert serversync.sync(remote, local)['unchanged'] == [IDENTIFIER]


def test_missing_source_and_offline_retain_local_copies(hosted):
    central, local, _client = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        serversync.sync(remote, local)
        metadata = (local / 'raw' / f'{IDENTIFIER}.metadata').read_bytes()
        state = json.loads((central / cloudsync.STATE).read_text())
        state['documents'] = {}
        (central / cloudsync.STATE).write_text(json.dumps(state))
        result = serversync.sync(remote, local)
        assert result['missing'] == [IDENTIFIER]
        assert (local / 'raw' / f'{IDENTIFIER}.metadata').read_bytes() == metadata
    with pytest.raises(CloudError, match='Tailscale'):
        serversync.sync(remote, local)
    assert (local / 'raw' / f'{IDENTIFIER}.metadata').read_bytes() == metadata


def test_bad_pdf_or_changing_source_keeps_previous_publication(hosted):
    central, local, _client = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        serversync.sync(remote, local)
        previous = json.loads((local / cloudsync.STATE).read_text())['documents'][IDENTIFIER]
        state = json.loads((central / cloudsync.STATE).read_text())
        state['documents'][IDENTIFIER]['pdf_sha256'] = 'e' * 64
        (central / cloudsync.STATE).write_text(json.dumps(state))
        result = serversync.sync(remote, local)
        assert result['status'] == 'partial' and len(result['failed']) == 1
        assert json.loads((local / cloudsync.STATE).read_text())['documents'][IDENTIFIER] == previous
        assert cloudsync.preservation_pdf(local / 'revisions' / IDENTIFIER / previous['revision'], previous).is_file()


def test_source_identity_and_usb_destination_protected(hosted, tmp_path):
    central, local, _client = hosted
    with running(central) as base:
        remote = serversync.ServerClient(base)
        serversync.sync(remote, local)
        remote.base = 'https://different.tailnet.ts.net'
        with pytest.raises(CloudError, match='different source'):
            serversync.sync(remote, local)
        usb = tmp_path / 'usb'
        usb.mkdir()
        (usb / 'original').write_bytes(b'untouched')
        with pytest.raises(CloudError, match='separate'):
            serversync.sync(remote, usb)
        assert (usb / 'original').read_bytes() == b'untouched'


@pytest.mark.parametrize('value', ['http://example.com', 'https://user:password@example.com',
                                  'https://example.com/path', 'https://example.com?token=a',
                                  'file:///tmp/library', 'https://example.com:bad'])
def test_invalid_remote_addresses(value):
    with pytest.raises(CloudError):
        serversync.ServerClient(value)


def test_server_address_comes_from_the_environment(monkeypatch):
    """A private address belongs to an installation, not to the package."""
    monkeypatch.delenv(serversync.SERVER_ENV, raising=False)
    assert serversync.default_server() == ''
    monkeypatch.setenv(serversync.SERVER_ENV, '  https://server.example.ts.net:8443  ')
    assert serversync.default_server() == 'https://server.example.ts.net:8443'


def test_manifest_rejects_cross_document_files_and_archive_extra_members(hosted, tmp_path):
    central, _local, _client = hosted
    with running(central) as base:
        manifest = serversync.ServerClient(base).manifest()
    malformed = copy.deepcopy(manifest)
    malformed['documents'][IDENTIFIER]['files'][0]['path'] = '../elsewhere'
    with pytest.raises(ValueError):
        serversync.validate_manifest(malformed)
    record = manifest['documents'][IDENTIFIER]
    archive = tmp_path / 'bad.rmdoc'
    with zipfile.ZipFile(archive, 'w') as out:
        for item in record['files']:
            out.writestr(item['path'], (central / 'raw' / item['path']).read_bytes())
        out.writestr('extra.metadata', '{}')
    with pytest.raises(CloudError, match='contents differ'):
        serversync._extract(archive, tmp_path / 'extracted', record)
    assert not (tmp_path / 'extracted').exists()


def test_server_running_status_not_claimed_as_complete(hosted):
    central, local, _client = hosted
    state = json.loads((central / cloudsync.STATE).read_text())
    state['status'] = 'running'
    (central / cloudsync.STATE).write_text(json.dumps(state))
    with running(central) as base:
        result = serversync.sync(serversync.ServerClient(base), local)
    assert result['status'] == 'partial' and result['server_status'] == 'running'
    assert result['exports_available'] == 1 and not result['last_success']


def test_changed_raw_revision_retains_old_pdf_archive_and_raw_history(hosted):
    central, local, cloud = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        first = serversync.sync(remote, local)
        old_pdf = open(first['preserved'][0], 'rb').read()
        entry = cloud.remote[IDENTIFIER]
        entry['Hash'] = 'b' * 64
        name = IDENTIFIER + '.metadata'
        cloud.blobs[name] = json.dumps({'type': 'DocumentType', 'visibleName': 'Updated central PDF'}).encode()
        for item in entry['Files']:
            if item['DocumentID'] == name:
                item.update(Hash=hashlib.sha256(cloud.blobs[name]).hexdigest(), Size=len(cloud.blobs[name]))
        assert cloudsync.sync(cloud, central)['changed'] == [IDENTIFIER]
        second = serversync.sync(remote, local)
        assert second['changed'] == [IDENTIFIER] and second['status'] == 'ok'
        assert open(first['preserved'][0], 'rb').read() == old_pdf
        assert (local / 'revisions' / IDENTIFIER / ('a' * 64) / 'document.rmdoc').is_file()
        assert list((local / 'raw' / '.remarksync-history' / IDENTIFIER).glob('*/*.metadata'))
        assert json.loads((local / 'raw' / name).read_text())['visibleName'] == 'Updated central PDF'


def test_download_checks_actual_response_digest(hosted, tmp_path):
    central, _local, _client = hosted
    with running(central) as base:
        remote = serversync.ServerClient(base)
        record = remote.manifest()['documents'][IDENTIFIER]
        with pytest.raises(CloudError, match='checksum mismatch'):
            remote.download(IDENTIFIER, record['revision'], 'document.rmdoc', 'f' * 64, tmp_path / 'wrong.rmdoc')


def test_rendering_symlink_refused_before_writing_outside(hosted, tmp_path):
    central, local, _client = hosted
    with running(central) as base:
        remote = RecordingClient(base)
        serversync.sync(remote, local)
        state = json.loads((central / cloudsync.STATE).read_text())
        record = state['documents'][IDENTIFIER]
        outside = tmp_path / 'outside'
        outside.mkdir()
        record['pdf_sha256'] = 'e' * 64
        (central / cloudsync.STATE).write_text(json.dumps(state))
        rendered = local / 'revisions' / IDENTIFIER / record['revision'] / 'renderings' / record['pdf_sha256']
        try:
            rendered.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip('Symlinks unavailable')
        result = serversync.sync(remote, local)
        assert result['status'] == 'partial' and 'link' in result['failed'][0]['error']
        assert not list(outside.iterdir())


def test_redirect_is_refused():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', 'https://example.com/manifest')
            self.end_headers()
        def log_message(self, *_args):
            pass
    with ThreadingHTTPServer(('127.0.0.1', 0), Redirect) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(CloudError, match='redirected'):
                serversync.ServerClient(f'http://127.0.0.1:{listener.server_port}').manifest()
        finally:
            listener.shutdown()
            thread.join()


def test_folder_and_notebook_archives_download_without_rendering(tmp_path):
    folder, notebook = '7f3de675-5744-4a46-a7a8-a866eea9d4e5', IDENTIFIER
    central, local = tmp_path / 'central', tmp_path / 'desktop'
    records = {}
    for identifier, kind in ((folder, 'CollectionType'), (notebook, 'DocumentType')):
        metadata = {'type': kind, 'visibleName': 'Folder' if kind == 'CollectionType' else 'Notebook',
                    'parent': '' if kind == 'CollectionType' else folder}
        blobs = {identifier + '.metadata': json.dumps(metadata).encode()}
        if kind == 'DocumentType':
            blobs[identifier + '.content'] = json.dumps({'fileType': 'notebook', 'pages': ['page']}).encode()
            blobs[identifier + '/page.rm'] = page_rm(text='Notebook note')
        target = central / 'revisions' / identifier / ('a' * 64)
        target.mkdir(parents=True)
        with zipfile.ZipFile(target / 'document.rmdoc', 'w') as out:
            for name, data in blobs.items():
                out.writestr(name, data)
        records[identifier] = {'document': identifier, 'revision': 'a' * 64, 'name': metadata['visibleName'],
                               'kind': kind, 'file_type': 'notebook', 'warnings': [],
                               'archive_sha256': hashlib.sha256((target / 'document.rmdoc').read_bytes()).hexdigest(),
                               'files': [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                         for name, data in blobs.items()]}
    (central / cloudsync.STATE).write_text(json.dumps({'status': 'ok', 'documents': records}))
    with running(central) as base:
        remote = RecordingClient(base)
        result = serversync.sync(remote, local)
        assert result['status'] == 'ok' and len(result['downloaded']) == 2
        assert remote.downloads == ['document.rmdoc', 'document.rmdoc']
        assert serversync.sync(remote, local)['unchanged'] == [folder, notebook]
    tree = scan_backup(local / 'raw')
    assert tree[0].kind == 'folder' and tree[0].children[0].name == 'Notebook'


def test_tablet_delete_is_marked_through_to_the_desktop_and_unmarked_on_return(hosted):
    """A document removed on the tablet stays preserved at every hop, carries a
    marker saying so, and loses the marker if the tablet has it again."""
    from remarksync.library import Library
    central, local, client = hosted
    entry = client.remote.pop(IDENTIFIER)  # deleted on the tablet
    assert cloudsync.sync(client, central)['missing'] == [IDENTIFIER]
    since = json.loads((central / cloudsync.STATE).read_text())['documents'][IDENTIFIER]['absent_since']
    cloudsync.sync(client, central)
    assert json.loads((central / cloudsync.STATE).read_text())['documents'][IDENTIFIER]['absent_since'] == since  # first seen
    with running(central) as base:
        import urllib.request
        assert json.load(urllib.request.urlopen(base + '/manifest'))['documents'][IDENTIFIER]['absent_since'] == since
        assert json.load(urllib.request.urlopen(base + '/status'))['removed_from_tablet'] == 1
        assert b'Removed from the tablet' in urllib.request.urlopen(base + '/').read()
        serversync.sync(serversync.ServerClient(base), local)
        library = Library(local / 'raw')
        assert library.absent == {IDENTIFIER: since} and library.document(IDENTIFIER) is not None  # kept, and known gone
        client.remote[IDENTIFIER] = entry  # back on the tablet
        cloudsync.sync(client, central)
        serversync.sync(serversync.ServerClient(base), local)
        library.rescan()
        assert library.absent == {}
        assert 'absent_since' not in json.loads((local / cloudsync.STATE).read_text())['documents'][IDENTIFIER]


def test_document_the_server_stops_listing_is_marked_locally(hosted):
    central, local, _client = hosted
    with running(central) as base:
        serversync.sync(serversync.ServerClient(base), local)
        state = json.loads((central / cloudsync.STATE).read_text())
        state['documents'] = {}
        (central / cloudsync.STATE).write_text(json.dumps(state))
        serversync.sync(serversync.ServerClient(base), local)
    assert json.loads((local / cloudsync.STATE).read_text())['documents'][IDENTIFIER]['absent_since']
    assert (local / 'raw' / f'{IDENTIFIER}.metadata').is_file()


def test_manifest_with_a_malformed_absence_marker_is_refused(hosted):
    central, _local, _client = hosted
    state = json.loads((central / cloudsync.STATE).read_text())
    manifest = {'format': 'remarksync-library-v1', 'status': 'ok', 'documents': copy.deepcopy(state['documents'])}
    serversync.validate_manifest(copy.deepcopy(manifest))
    for bad in (5, '', 'x' * 41, ['2026']):
        manifest['documents'][IDENTIFIER]['absent_since'] = bad
        with pytest.raises(ValueError, match='absence'):
            serversync.validate_manifest(copy.deepcopy(manifest))


def test_unexpected_failure_is_recorded_as_error_not_left_running(hosted, monkeypatch):
    central, local, _client = hosted
    with running(central) as base:
        remote = serversync.ServerClient(base)

        def fail(*_args):
            raise OSError('synthetic failure after the downloads')

        monkeypatch.setattr(serversync, 'mark_absent', fail)
        with pytest.raises(OSError, match='synthetic'):
            serversync.sync(remote, local)
        assert json.loads((local / cloudsync.STATE).read_bytes())['status'] == 'error'
        assert (local / 'raw' / (IDENTIFIER + '.metadata')).is_file()
        monkeypatch.undo()
        assert serversync.sync(remote, local)['status'] == 'ok'
