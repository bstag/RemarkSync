"""Read-only verification of a completed hosted library and PDF attachments."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import pymupdf as fitz
from remarksync.cloudsync import preservation_pdf

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('root', nargs='?', type=Path, default=Path('/data/library'))
root = parser.parse_args().root
state = json.loads((root / 'sync-state.json').read_bytes())  # bytes: the file is UTF-8 whatever the locale
assert state['status'] == 'ok', 'Wait for a complete successful sync before verification'
counts = dict(documents=0, folders=0, archives=0, raw_files=0, pdfs=0, original_attachments=0, scene_attachments=0)
warnings = []

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

for identifier, record in state['documents'].items():
    counts['documents' if record['kind'] == 'DocumentType' else 'folders'] += 1
    revision = root / 'revisions' / identifier / record['revision']
    archive = revision / 'document.rmdoc'
    assert file_digest(archive) == record['archive_sha256'], 'Archive checksum mismatch'
    counts['archives'] += 1
    hashes = {}
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {item['path'] for item in record['files']}, 'Archive file set differs from cloud manifest'
        for item in record['files']:
            with zipped.open(item['path']) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            assert actual == item['sha256'], 'Archived source checksum mismatch'
            current = root / 'raw' / item['path']
            assert current.stat().st_size == item['size'] and file_digest(current) == actual, 'Current raw source checksum mismatch'
            hashes[item['path']] = actual
            counts['raw_files'] += 1
        if record.get('pdf_sha256'):
            pdf_path = preservation_pdf(revision, record)
            assert file_digest(pdf_path) == record['pdf_sha256'], 'Preservation PDF checksum mismatch'
            with fitz.open(pdf_path) as pdf:
                manifest = json.loads(pdf.embfile_get('remarksync-manifest.json'))
                assert manifest['document_uuid'] == identifier
                assert pdf.page_count == record['pages'] and pdf.page_count >= manifest['original_page_count']
                original = pdf.embfile_get(manifest['original_attachment'])
                assert digest(original) == manifest['original_sha256'] == hashes[identifier + '.pdf'], 'Original PDF attachment differs from cloud source'
                counts['original_attachments'] += 1
                for entry in manifest['pages']:
                    if attachment := entry.get('scene_attachment'):
                        source = identifier + '/' + entry['page_id'] + '.rm'
                        assert digest(pdf.embfile_get(attachment)) == hashes[source], 'Raw scene attachment differs from cloud source'
                        counts['scene_attachments'] += 1
                for source in manifest['sources']:
                    assert any(Path(name).name == source['name'] and value == source['sha256'] for name, value in hashes.items()), 'PDF provenance source differs from archived source'
                if manifest['warnings']:
                    warnings.append({'document': record['name'], 'warning_count': len(manifest['warnings']),
                                     'decoder_pages': sum(bool(entry.get('decoder_diagnostics')) for entry in manifest['pages'])})
            counts['pdfs'] += 1

print(json.dumps({'verified': counts, 'export_warnings': warnings}, indent=2))
