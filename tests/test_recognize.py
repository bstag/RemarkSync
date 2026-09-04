"""Handwriting extractors: the tablet's own conversions, the Windows ink
recogniser behind a stroke-hash cache."""

import json
import os

import pytest

from remarksync.backup import iter_docs, scan_root
from remarksync.library import Library
from remarksync.recognize import (PageSource, RecognisedWord, RecognitionCache, WindowsInkExtractor, conversion_text,
                                  stroke_hash, tablet_conversion, windows_ink_available, words_to_text)
from remarksync.rmdata import PageModel, Stroke, decode_page

from .conftest import INK_UUID


def _ink_source(backup_root):
    doc = {d.uuid: d for d in iter_docs(scan_root(backup_root))}[INK_UUID]
    page = doc.pages[0]
    return PageSource(doc, page, decode_page(page.rm_path))


def test_tablet_conversion_is_read_from_the_textconversion_folder(backup_root):
    source = _ink_source(backup_root)
    assert tablet_conversion(source) == ""
    folder = backup_root / f"{INK_UUID}.textconversion"
    folder.mkdir()
    (folder / "0.json").write_text(json.dumps({"checksum": "x", "text": "<p>\nHistory of Baldrige\n</p>\n<p>\n.\n</p>\n<p>\nsecond line &amp; more\n</p>"}))
    assert tablet_conversion(source) == "History of Baldrige\nsecond line & more"


def test_conversion_text_strips_markup_and_noise():
    assert conversion_text("<p>a</p><p>^</p><p>b</p>") == "a\nb"
    assert conversion_text("plain") == "plain"


def test_library_indexes_tablet_conversions(backup_root, tmp_path):
    folder = backup_root / f"{INK_UUID}.textconversion"
    folder.mkdir()
    (folder / "0.json").write_text(json.dumps({"text": "<p>zebra crossing</p>"}))
    lib = Library(backup_root)
    lib.refresh_index()
    hits = lib.search("zebra")
    assert hits and hits[0].doc_uuid == INK_UUID


def test_stroke_hash_ignores_highlighter_and_position_noise():
    a = PageModel(strokes=[Stroke(points=[(1.00, 2.00), (3.0, 4.0)], color="#000", width=2)])
    b = PageModel(strokes=[Stroke(points=[(1.02, 2.01), (3.0, 4.0)], color="#000", width=2),
                           Stroke(points=[(5, 5), (6, 6)], color="#ff0", width=30, highlighter=True)])
    c = PageModel(strokes=[Stroke(points=[(1.0, 9.0), (3.0, 4.0)], color="#000", width=2)])
    assert stroke_hash(a) == stroke_hash(b)
    assert stroke_hash(a) != stroke_hash(c)


def test_words_to_text_keeps_reading_order_and_alternatives():
    words = [RecognisedWord("Prayer", ["Proyer"], 300, 100), RecognisedWord("Associated", ["Assoc"], 100, 102),
             RecognisedWord("Priority", [], 100, 200)]
    text = words_to_text(words)
    assert text.splitlines()[0] == "Associated Prayer"
    assert text.splitlines()[1] == "Priority"
    assert "(alternatives: Assoc Proyer)" in text


def test_recognition_cache_round_trip(tmp_path):
    cache = RecognitionCache(tmp_path / "rec.db")
    assert len(cache) == 0
    assert cache.get("abc", "engine") is None
    cache.put("abc", "engine", "hello")
    assert cache.get("abc", "engine") == "hello"
    assert len(cache) == 1


def test_windows_extractor_uses_the_cache(tmp_path, backup_root):
    calls = []

    class FakeService:
        def recognise(self, model):
            calls.append(1)
            return [RecognisedWord("hello", ["hullo"], 0, 0)]

    cache = RecognitionCache(tmp_path / "rec.db")
    extractor = WindowsInkExtractor(cache, service=FakeService())
    source = _ink_source(backup_root)
    assert extractor(source).startswith("hello")
    assert extractor(source).startswith("hello")
    assert len(calls) == 1  # the second call came from the cache
    empty = PageSource(source.doc, source.page, PageModel())
    assert extractor(empty) == ""  # nothing to recognise, nothing called
    assert len(calls) == 1


def test_a_stall_marks_the_page_and_trips_the_breaker(tmp_path, backup_root):
    from remarksync.recognize import FAILED, stroke_hash

    class Stalling:
        calls = 0

        def recognise(self, model):
            self.calls += 1
            raise TimeoutError("no answer")

    service = Stalling()
    cache = RecognitionCache(tmp_path / "rec.db")
    extractor = WindowsInkExtractor(cache, service=service)
    source = _ink_source(backup_root)
    assert extractor(source) == ""
    assert extractor.tripped
    assert cache.get(stroke_hash(source.model), extractor.engine) == FAILED
    # the same page is never sent again, and nothing else is for this run
    other = PageSource(source.doc, source.page, PageModel(strokes=[Stroke(points=[(1, 1), (9, 9)], color="#000", width=2)]))
    assert extractor(source) == "" and extractor(other) == ""
    assert service.calls == 1
    # a fresh extractor (the next index run) tries other pages again but not the failed one
    again = WindowsInkExtractor(cache, service=service)
    assert again(source) == "" and service.calls == 1
    again(other)
    assert service.calls == 2


@pytest.mark.skipif(not windows_ink_available() or not os.environ.get("REMARKSYNC_INK_TESTS"),
                    reason="set REMARKSYNC_INK_TESTS=1 to exercise the real Windows recogniser")
def test_windows_recogniser_answers_for_real_strokes(backup_root):
    from remarksync.recognize import recognise_windows

    source = _ink_source(backup_root)
    words = recognise_windows(source.model)
    assert isinstance(words, list)
    assert all(isinstance(w.text, str) for w in words)


def test_trocr_extractor_reads_lines_and_caches(tmp_path, backup_root):
    from remarksync.recognize import TrOCRExtractor

    class FakeModel:
        calls = 0

        def read_lines(self, images):
            self.calls += 1
            return [("hello world", ["hallo world"]) for _ in images]

    model = FakeModel()
    cache = RecognitionCache(tmp_path / "rec.db")
    extractor = TrOCRExtractor(cache, model=model, min_strokes=1)  # the fixture line is one stroke
    source = _ink_source(backup_root)
    text = extractor(source)
    assert text.startswith("hello world")
    assert "(alternatives: hallo world)" in text
    assert extractor(source) == text and model.calls == 1  # cached
    assert extractor(PageSource(source.doc, source.page, PageModel())) == ""


def test_engine_selection_prefers_trocr_and_falls_back(monkeypatch, tmp_path):
    import remarksync.recognize as rec

    cache = RecognitionCache(tmp_path / "rec.db")
    monkeypatch.setattr(rec, "trocr_available", lambda: False)
    monkeypatch.setattr(rec, "windows_ink_available", lambda: True)
    assert rec.handwriting_extractor(cache).engine == rec.ENGINE_WINDOWS
    monkeypatch.setattr(rec, "trocr_available", lambda: True)
    assert rec.handwriting_extractor(cache).engine == rec.ENGINE_TROCR
    assert rec.handwriting_extractor(cache, rec.ENGINE_WINDOWS).engine == rec.ENGINE_WINDOWS
    monkeypatch.setattr(rec, "windows_ink_available", lambda: False)
    monkeypatch.setattr(rec, "trocr_available", lambda: False)
    assert rec.handwriting_extractor(cache) is None
