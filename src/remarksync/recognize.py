"""Handwriting as searchable text: the extractors that feed the search
index for pages of ink, and the cache that keeps every recognition.

Three sources, in order of preference:

* **Tablet conversions.** When a page is converted to text on the device
  and shared, the result lands beside the document in
  ``<uuid>.textconversion/<page>.json`` (MyScript's recognition, the best
  available for this ink). Converting with "add as new page" instead puts
  the text into the notebook as a typed page, which the index reads like
  any typed text. Neither costs anything to index.
* **TrOCR.** Microsoft's handwriting model (``trocr-large-handwritten``,
  free, offline, runs on the GPU when there is one) reads one written
  line at a time; :mod:`inklines` cuts the page into lines by geometry.
  Measured against MyScript on a 532-word journal page: 80% of the words
  at the top reading, 83% with runner-up readings, a tenth of a second per
  line on an RTX 4060 Ti. The default engine when PyTorch and
  transformers are installed (``pip install -e .[handwriting]``).
* **Windows ink recognition.** The stroke-based recogniser behind the
  Windows handwriting panel. Good on short pages, but it fails or stalls on
  long ones and a stall wedges it machine-wide for minutes, so it is only
  an alternative engine, never the default.

Every recognition is cached by a hash of the strokes it came from, in a
small database beside the search index, so a page is recognised once and
an edit to one page never re-recognises the rest. No Qt here.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .backup import DocItem, PageRef
from .rmdata import PageModel

log = logging.getLogger(__name__)

CACHE_NAME = ".remarksync-recognition.db"
ENGINE_WINDOWS = "windows-ink"
ENGINE_TROCR = "trocr-large-handwritten"
TROCR_MODEL = "microsoft/trocr-large-handwritten"
RECOGNITION_TIMEOUT = 30.0  # seconds per page before giving up on the recogniser
FAILED = "\x00failed"  # cache marker: the recogniser stalled on this ink; never retry it


@dataclass
class PageSource:
    """What an extractor sees: the document, the page, and its decoded model."""

    doc: DocItem
    page: PageRef
    model: PageModel


# ---------------------------------------------------------- tablet conversions


def tablet_conversion(source: PageSource) -> str:
    """Text the tablet itself recognised for this page, if the page was
    converted on the device."""
    doc, page = source.doc, source.page
    if page.rm_path is None:
        return ""
    folder = _conversion_dir(doc)
    if folder is None:
        return ""
    candidate = folder / (page.rm_path.stem + ".json")
    if not candidate.exists():
        return ""
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("bad text conversion %s: %s", candidate, exc)
        return ""
    return conversion_text(data.get("text", ""))


def conversion_signature(doc: DocItem) -> str:
    """A fingerprint of the tablet's conversions for ``doc``, empty if none.

    The device writes a conversion beside a document *without* touching that
    document's metadata (see :mod:`remarksync.pull`), so anything keyed on
    ``lastModified`` alone cannot tell that new text has arrived. Content
    hashes also notice replacements that preserve size and timestamps.
    """
    folder = _conversion_dir(doc)
    if folder is None:
        return ""
    parts = []
    for path in sorted(folder.glob("*.json")):
        try:
            content = path.read_bytes()
        except OSError:  # vanished mid-scan; treat it as absent
            continue
        parts.append(f"{path.name}:{hashlib.sha256(content).hexdigest()}")
    return ";".join(parts)


def _conversion_dir(doc: DocItem) -> Path | None:
    base = doc.files_root
    if base is None:
        return None
    folder = base / f"{doc.uuid}.textconversion" if doc.uuid else base / ".textconversion"
    return folder if folder.is_dir() else None


def conversion_text(raw: str) -> str:
    """The tablet stores its conversion as paragraphs in light HTML."""
    paragraphs = re.findall(r"<p>(.*?)</p>", raw, flags=re.S)
    if not paragraphs:
        paragraphs = [raw]
    lines = [html.unescape(re.sub(r"<[^>]+>", "", p)).strip() for p in paragraphs]
    return "\n".join(line for line in lines if line and line not in (".", "^", "i", "-"))


# ------------------------------------------------------------ recognition cache


class RecognitionCache:
    """Recognised text by stroke hash, one sqlite file beside the index."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS recognition (
        stroke_hash TEXT NOT NULL,
        engine TEXT NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (stroke_hash, engine)
    );
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        if not self._ready:
            conn.executescript(self.SCHEMA)
            self._ready = True
        return conn

    def get(self, stroke_hash: str, engine: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT text FROM recognition WHERE stroke_hash = ? AND engine = ?", (stroke_hash, engine)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def put(self, stroke_hash: str, engine: str, text: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("INSERT OR REPLACE INTO recognition VALUES (?,?,?)", (stroke_hash, engine, text))
        finally:
            conn.close()

    def __len__(self) -> int:
        if not self.path.exists():
            return 0
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM recognition").fetchone()[0]
        finally:
            conn.close()


def stroke_hash(model: PageModel) -> str:
    """Identity of a page's ink: the same strokes give the same hash
    wherever the page sits, so a moved or re-laid-out page is not
    re-recognised."""
    h = hashlib.sha1()
    for s in model.strokes:
        if s.highlighter or not s.layer_visible:
            continue
        h.update(f"{s.width:.3f}:{s.tool}|".encode())
        h.update(",".join(f"{w:.3f}" for w in s.widths).encode())
        for x, y in s.points:
            h.update(f"{x:.1f},{y:.1f};".encode())
        h.update(b"|")
    return h.hexdigest()


# --------------------------------------------------------- windows recogniser


def windows_ink_available() -> bool:
    try:
        import winrt.windows.ui.input.inking  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class RecognisedWord:
    text: str
    alternatives: list[str]
    x: float
    y: float


def recognise_strokes_here(strokes: list[list[tuple[float, float]]]) -> list[RecognisedWord]:
    """Ask the Windows ink recogniser about strokes, on the calling thread.

    Only reliable on a process's main thread: WinRT completes the
    recognition through a COM callback that other threads (and any thread
    Qt has initialised) may never pump. :class:`WindowsInkService` runs
    this in a helper process for that reason.
    """
    import asyncio

    import winrt.windows.ui.input.inking as ink
    from winrt.windows.foundation import Point
    from winrt.windows.foundation.numerics import Matrix3x2

    builder = ink.InkStrokeBuilder()
    identity = Matrix3x2(1, 0, 0, 1, 0, 0)
    container = ink.InkStrokeContainer()
    built = []
    for points in strokes:
        if len(points) < 2:
            continue
        pts = [ink.InkPoint(Point(x, y), 0.5) for x, y in points]
        built.append(builder.create_stroke_from_ink_points(pts, identity))
    if not built:
        return []
    container.add_strokes(built)

    async def run():
        recogniser = ink.InkRecognizerContainer()
        return await recogniser.recognize_async(container, ink.InkRecognitionTarget.ALL)

    words = []
    for r in asyncio.run(run()):
        candidates = list(r.get_text_candidates())
        if not candidates:
            continue
        rect = r.bounding_rect
        words.append(RecognisedWord(str(candidates[0]), [str(c) for c in candidates[1:4]], float(rect.x), float(rect.y)))
    return _reading_order(words)


LINE_GAP = 25.0  # words whose tops differ by more than this start a new line


def _reading_order(words: list[RecognisedWord]) -> list[RecognisedWord]:
    """Top to bottom, then left to right within a line."""
    lines: list[list[RecognisedWord]] = []
    for w in sorted(words, key=lambda w: w.y):
        if lines and abs(w.y - lines[-1][-1].y) <= LINE_GAP:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [w for line in lines for w in sorted(line, key=lambda w: w.x)]


def ink_strokes(model: PageModel) -> list[list[tuple[float, float]]]:
    """The strokes worth recognising: pen strokes, not highlighter bands."""
    return [list(s.points) for s in model.strokes if s.layer_visible and not s.highlighter and len(s.points) >= 2]


class WindowsInkService:
    """The Windows ink recogniser in a helper process, one request per
    page over a pipe (JSON lines). The helper's main thread is the one
    place the recogniser works reliably; the app's threads never touch
    WinRT, and a recogniser crash costs one page, not the app."""

    def __init__(self, timeout: float = RECOGNITION_TIMEOUT):
        self.timeout = timeout
        self._proc = None
        self._lock = threading.Lock()

    def _start(self):
        import subprocess
        import sys

        self._proc = subprocess.Popen(
            [sys.executable, "-m", "remarksync.recognize", "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
        )

    def recognise(self, model: PageModel) -> list[RecognisedWord]:
        strokes = ink_strokes(model)
        if not strokes:
            return []
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            request = json.dumps({"strokes": [[(round(x, 1), round(y, 1)) for x, y in s] for s in strokes]})
            reply = self._exchange(request)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return [RecognisedWord(w["text"], w["alternatives"], w["x"], w["y"]) for w in reply["words"]]

    def _exchange(self, request: str) -> dict:
        proc = self._proc
        answer: dict = {}

        def read():
            answer["line"] = proc.stdout.readline()

        proc.stdin.write(request + "\n")
        proc.stdin.flush()
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(self.timeout)
        if reader.is_alive() or not answer.get("line"):
            self.close()
            raise TimeoutError(f"ink recognition helper gave no answer within {self.timeout} s")
        return json.loads(answer["line"])

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(5)
            except Exception:
                proc.kill()


def serve(stdin, stdout) -> None:
    """The helper process: one JSON request per line in, one reply out."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            words = recognise_strokes_here([[tuple(p) for p in s] for s in request["strokes"]])
            reply = {"words": [{"text": w.text, "alternatives": w.alternatives, "x": w.x, "y": w.y} for w in words]}
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            reply = {"error": f"{type(exc).__name__}: {exc}"}
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()


_service: WindowsInkService | None = None


def windows_ink_service() -> WindowsInkService:
    """The process-wide helper, started on first use."""
    global _service
    if _service is None:
        _service = WindowsInkService()
        import atexit

        atexit.register(_service.close)
    return _service


def recognise_windows(model: PageModel) -> list[RecognisedWord]:
    """Handwriting on a page, via the helper process."""
    return windows_ink_service().recognise(model)


def words_to_text(words: list[RecognisedWord]) -> str:
    """Top readings as text, then the alternatives on a marked line so a
    search still finds a word the top reading got wrong."""
    if not words:
        return ""
    words = _reading_order(words)
    lines: list[list[str]] = []
    last_y = None
    for w in words:
        if last_y is None or abs(w.y - last_y) > LINE_GAP:
            lines.append([])
        lines[-1].append(w.text)
        last_y = w.y
    text = "\n".join(" ".join(line) for line in lines)
    alternatives = sorted({a for w in words for a in w.alternatives if a and a.lower() != w.text.lower()})
    if alternatives:
        text += "\n(alternatives: " + " ".join(alternatives) + ")"
    return text


class WindowsInkExtractor:
    """Handwriting on a page as searchable text, via Windows ink recognition,
    cached by stroke hash so each page is recognised once.

    The Windows engine can stall on some ink, and a stalled request wedges
    it for every caller for minutes, so: one stall marks that page's ink as
    failed in the cache (never retried) and trips a breaker that skips the
    recogniser for the rest of this extractor's life (one index run); the
    next run starts fresh.
    """

    engine = ENGINE_WINDOWS

    def __init__(self, cache: RecognitionCache, service: WindowsInkService | None = None):
        self.cache = cache
        self.service = service
        self.tripped: str | None = None  # why the recogniser was switched off

    def __call__(self, source: PageSource) -> str:
        model = source.model
        if not ink_strokes(model):
            return ""
        key = stroke_hash(model)
        cached = self.cache.get(key, self.engine)
        if cached is not None:
            return "" if cached == FAILED else cached
        if self.tripped:
            return ""
        try:
            words = (self.service or windows_ink_service()).recognise(model)
        except TimeoutError as exc:
            log.warning("ink recognition stalled on %s page %d; skipping it for good and the rest of this run: %s",
                        source.doc.name, source.page.index, exc)
            self.cache.put(key, self.engine, FAILED)
            self.tripped = str(exc)
            return ""
        except Exception as exc:
            log.warning("handwriting recognition failed for %s page %d: %s", source.doc.name, source.page.index, exc)
            return ""
        text = words_to_text(words)
        self.cache.put(key, self.engine, text)
        return text


# ------------------------------------------------------------------- TrOCR


def trocr_available() -> bool:
    """Whether TrOCR's packages are installed. Looked up, not imported: the
    window asks this on its own thread, and importing PyTorch takes from
    seconds to most of a minute. The import itself happens when the model is
    loaded, on the indexing worker."""
    from importlib.util import find_spec

    try:
        return all(find_spec(name) is not None for name in ("torch", "transformers", "PIL"))
    except (ImportError, ValueError):
        return False


class TrOCRModel:
    """The handwriting model, loaded on first use, kept for the process.
    Reads a batch of line images and returns their text (plus runner-up
    readings when ``beams`` > 1 and ``alternatives`` > 0)."""

    def __init__(self, name: str = TROCR_MODEL, beams: int = 1, alternatives: int = 0, batch: int = 16):
        self.name = name
        self.beams = beams
        self.alternatives = alternatives if beams > 1 else 0
        self.batch = batch
        self._processor = None
        self._model = None
        self.device = "cpu"

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> None:
        import torch
        import transformers
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        transformers.logging.set_verbosity_error()  # the checkpoint's unused-weights notice is expected
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._processor = TrOCRProcessor.from_pretrained(self.name)
        self._model = VisionEncoderDecoderModel.from_pretrained(self.name).to(self.device).eval()
        log.info("loaded %s on %s", self.name, self.device)

    def read_lines(self, images) -> list[tuple[str, list[str]]]:
        """(top reading, runner-up readings) per line image."""
        import torch

        if self._model is None:
            self._load()
        returns = 1 + self.alternatives
        out: list[tuple[str, list[str]]] = []
        with torch.no_grad():
            for i in range(0, len(images), self.batch):
                chunk = [img.convert("RGB") for img in images[i : i + self.batch]]
                pixels = self._processor(images=chunk, return_tensors="pt").pixel_values.to(self.device)
                ids = self._model.generate(pixels, max_new_tokens=96, num_beams=self.beams, num_return_sequences=returns)
                texts = self._processor.batch_decode(ids, skip_special_tokens=True)
                for j in range(0, len(texts), returns):
                    out.append((texts[j].strip(), [t.strip() for t in texts[j + 1 : j + returns] if t.strip()]))
        return out


_trocr: TrOCRModel | None = None


def trocr_model() -> TrOCRModel:
    global _trocr
    if _trocr is None:
        _trocr = TrOCRModel()
    return _trocr


class TrOCRExtractor:
    """Handwriting on a page as searchable text via TrOCR, cached by stroke
    hash so each page is read once. Lines come from :mod:`inklines`.

    A model that cannot be loaded (not downloaded and no network, not enough
    memory) fails the same way for every page, and each attempt is slow. One
    such failure trips a breaker that skips the model for the rest of this
    extractor's life (one index run); the next run tries again.
    """

    engine = ENGINE_TROCR

    def __init__(self, cache: RecognitionCache, model: TrOCRModel | None = None, min_strokes: int = 2):
        self.cache = cache
        self.model = model
        self.min_strokes = min_strokes  # skip lines that are just a dot or a tick
        self.tripped: str | None = None  # why the model was switched off

    def __call__(self, source: PageSource) -> str:
        from .inklines import cut_lines, render_line

        model = source.model
        if not ink_strokes(model):
            return ""
        key = stroke_hash(model)
        cached = self.cache.get(key, self.engine)
        if cached is not None:
            return "" if cached == FAILED else cached
        if self.tripped:
            return ""
        lines = [ln for ln in cut_lines(model) if len(ln.strokes) >= self.min_strokes]
        if not lines:
            return ""
        reader = self.model or trocr_model()
        try:
            readings = reader.read_lines([render_line(ln) for ln in lines])
        except Exception as exc:
            if getattr(reader, "loaded", True):
                log.warning("TrOCR failed for %s page %d: %s", source.doc.name, source.page.index, exc)
            else:
                log.warning("TrOCR could not be loaded; handwriting is skipped for the rest of this run: %s", exc)
                self.tripped = str(exc)
            return ""
        top = "\n".join(t for t, _ in readings if t)
        alternatives = sorted({a for _, alts in readings for a in alts})
        text = top + ("\n(alternatives: " + " ".join(alternatives) + ")" if alternatives else "")
        self.cache.put(key, self.engine, text)
        return text


ENGINES = {ENGINE_TROCR: (lambda: trocr_available(), lambda cache: TrOCRExtractor(cache)),
           ENGINE_WINDOWS: (lambda: windows_ink_available(), lambda cache: WindowsInkExtractor(cache))}


def handwriting_extractor(cache: RecognitionCache, engine: str | None = None):
    """The extractor for ``engine`` (or the best available: TrOCR, then
    Windows ink), or None when none is available."""
    names = [engine] if engine else list(ENGINES)
    for name in names:
        ok, make = ENGINES.get(name, (lambda: False, None))
        if ok():
            return make(cache)
    return None


if __name__ == "__main__":
    import sys

    if "--serve" in sys.argv:
        serve(sys.stdin, sys.stdout)
