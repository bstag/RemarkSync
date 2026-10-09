"""Read-only end-to-end preservation against an existing annotated backup."""

import json

import pymupdf as fitz
import pytest

from remarksync.backup import iter_docs, scan_backup
from remarksync.pdfexport import export_pdf
from remarksync.rmdata import decode_page

pytestmark = pytest.mark.real


def test_real_pdf_preservation_retains_original_and_raw_scenes(real_backup, tmp_path):
    doc = None
    for candidate in iter_docs(scan_backup(real_backup)):
        if candidate.resolve_base_pdf() and 1 <= len(candidate.pages) <= 80:
            if any(p.rm_path and decode_page(p.rm_path).has_content for p in candidate.pages):
                doc = candidate
                break
    if doc is None:
        pytest.skip("no small annotated PDF in this backup")
    original = doc.base_pdf.read_bytes()
    scenes = {p.index: p.rm_path.read_bytes() for p in doc.pages if p.rm_path}
    report = export_pdf(doc, tmp_path / "real-preservation.pdf")
    with fitz.open(report.path) as pdf, fitz.open(stream=original, filetype="pdf") as base:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert pdf.embfile_get(manifest["original_attachment"]) == original
        assert report.pages >= base.page_count
        for i in range(base.page_count):
            # Original searchable text stays intact. Extra notes are separate
            # content streams; documents without typed notes compare exactly.
            assert base[i].get_text().strip() in pdf[i].get_text()
        for index, scene in scenes.items():
            assert pdf.embfile_get(f"scene-{index + 1}.rm") == scene
        assert len(manifest["pages"]) == len(doc.pages)
    assert doc.base_pdf.read_bytes() == original
    assert all(p.rm_path.read_bytes() == scenes[p.index] for p in doc.pages if p.rm_path)
