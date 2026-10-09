"""Column inference and note wrapping verified against the exported glyphs."""

import json
from dataclasses import asdict

import pymupdf as fitz
import pytest

from remarksync.layout import PDF_SCALE, base_page_rect
from remarksync.notelayout import body_columns
from remarksync.pdfexport import export_pdf
from remarksync.rmdata import PageModel, Stroke, TextBlock

from .test_pdfexport import _doc


def _body(doc, columns=1):
    with fitz.open() as pdf:
        page = pdf.new_page(width=500, height=650)
        page.insert_text((40, 24), "A LARGE HEADING", fontsize=19)
        text = ("Original body text defines the reading column. "
                "It wraps within these margins and leaves room beside the text. ") * 5
        rects = [fitz.Rect(60, 50, 440, 250)] if columns == 1 else [fitz.Rect(40, 50, 235, 350), fitz.Rect(275, 50, 470, 350)]
        for rect in rects:
            assert page.insert_textbox(rect, text, fontsize=10) >= 0
        page.insert_text((40, 630), "footer", fontsize=7)
        pdf.save(doc.base_pdf)


def test_body_inference_uses_body_not_heading_footer_or_gutter(tmp_path):
    doc = _doc(tmp_path)
    _body(doc, columns=2)
    with fitz.open(doc.base_pdf) as pdf:
        columns, warning = body_columns(pdf[0])
        assert warning is None
        assert len(columns) == 2
        assert columns[0].x0 == pytest.approx(40)
        assert columns[1].x0 == pytest.approx(275)
        assert columns[0].x1 < 240
        assert columns[1].x1 <= 470


@pytest.mark.parametrize("columns", [1, 2])
def test_export_reflows_notes_to_actual_column_and_preserves_raw_model(tmp_path, monkeypatch, columns):
    doc = _doc(tmp_path)
    _body(doc, columns)
    left = base_page_rect((500, 650))[0]
    text = ("Typed notes should follow the width of the original body text without overflowing. "
            "Additional details belong in the same reading column. ") * 3
    block = TextBlock(text, left + 290 * PDF_SCALE, 270 * PDF_SCALE, 1152,
                      paragraphs=[("PLAIN", text)])
    stroke = Stroke([(left + 20, 300), (left + 40, 320)], "#0000ff", 3)
    model = PageModel(texts=[block], strokes=[stroke])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "flowed.pdf", text_layout="pdf-column")
    assert model.texts[0] == block and model.texts[0].lines == []
    assert model.strokes[0] is stroke
    with fitz.open(report.path) as pdf:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert manifest["text_layout"] == "pdf-column"
        entry = manifest["pages"][0]
        assert entry["typed_notes"][0]["pos_x"] == block.pos_x
        start, end = entry["note_layout"][0]["column_points"]
        expected_start = 60 if columns == 1 else 275
        assert start == pytest.approx(expected_start)
        lines = [ln for b in pdf[0].get_text("dict")["blocks"] for ln in b.get("lines", [])
                 if abs(ln["spans"][0]["size"] - 31.9 / PDF_SCALE) < 0.01]
        assert len(lines) > 3
        assert lines[0]["spans"][0]["origin"][0] == pytest.approx(start, abs=0.1)
        assert all(ln["bbox"][2] <= end + 0.5 for ln in lines)
        assert "Typed notes should follow" in pdf[0].get_text()


def test_no_text_base_reports_margin_fallback(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    with fitz.open() as pdf:
        pdf.new_page(width=400, height=500)
        pdf.save(doc.base_pdf)
    model = PageModel(texts=[TextBlock("A note for a scanned PDF", 100, 200, 1152)])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "scanned.pdf", text_layout="pdf-column")
    assert any("page margins" in w for w in report.warnings)
    with fitz.open(report.path) as pdf:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert manifest["pages"][0]["note_layout"][0]["column_points"] == [40, 360]


def test_tablet_layout_option_keeps_original_position(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    _body(doc)
    left = base_page_rect((500, 650))[0]
    model = PageModel(texts=[TextBlock("Keep tablet position", left + 30 * PDF_SCALE, 270 * PDF_SCALE, 1152)])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "tablet.pdf", text_layout="tablet")
    with fitz.open(report.path) as pdf:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert manifest["pages"][0]["note_layout"] == []
        span = next(s for b in pdf[0].get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                    if "Keep tablet position" in s["text"])
        assert span["origin"][0] == pytest.approx(30 + 4 / PDF_SCALE, abs=0.1)


def test_long_words_lists_unicode_and_overflow_context(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    _body(doc, columns=2)
    left = base_page_rect((500, 650))[0]
    paragraphs = [("HEADING", "Readable notes"), ("PLAIN", "Supercalifragilisticexpialidocious" * 4),
                  ("BULLET", "First note in the narrow column " * 6),
                  ("CHECKBOX_CHECKED", "Remember this item"), ("PLAIN", "café and 你好"),
                  ("PLAIN", "Overflow must remain available " * 30)]
    text = "\n".join(t for _, t in paragraphs)
    block = TextBlock(text, left + 40 * PDF_SCALE, 290 * PDF_SCALE, 1152, paragraphs=paragraphs)
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: PageModel(texts=[block]))
    report = export_pdf(doc, tmp_path / "overflow.pdf", text_layout="pdf-column")
    assert report.supplementary_pages == 1
    with fitz.open(report.path) as pdf:
        manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
        assert manifest["pages"][0]["typed_notes"][0]["text"] == text
        full_text = pdf[-1].get_text()
        assert "你好" in full_text.replace(" ", "")
        assert "Remember this item" in full_text
        assert "Overflow must remain available" in full_text
        start, end = manifest["pages"][0]["note_layout"][0]["column_points"]
        # Source paper starts at x=0 on this context sheet; note lines fit the
        # first column, including fallback glyphs and split long words.
        lines = [ln for b in pdf[-1].get_text("dict")["blocks"] for ln in b.get("lines", [])
                 if ln["bbox"][1] > 285]
        assert all(ln["bbox"][2] <= end + 1 for ln in lines)


def test_hidden_notes_not_reflowed(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    _body(doc)
    block = TextBlock("Hidden", 500, 500, 1152, layer_visible=False)
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: PageModel(texts=[block]))
    report = export_pdf(doc, tmp_path / "hidden.pdf", text_layout="pdf-column")
    with fitz.open(report.path) as pdf:
        entry = json.loads(pdf.embfile_get("remarksync-manifest.json"))["pages"][0]
        assert entry["note_layout"] == []
        assert entry["typed_notes"][0] == json.loads(json.dumps(asdict(block)))
