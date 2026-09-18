"""The **Search index**: SQLite FTS5 over the pages of a library.

One module owns everything about the index: the schema, which documents
need (re)indexing, how a page becomes searchable text, and querying.

* **Refresh, not rebuild.** ``refresh`` diffs the library's documents
  against what the index already holds: unchanged documents are skipped,
  changed or new ones re-extracted, vanished ones purged. ``force=True``
  is the full rebuild.
* **What counts as changed.** A document's signature is its ``lastModified``
  from its ``.metadata`` *plus* the tablet's own text conversions, because
  the device writes those beside a document without touching its metadata
  and they would otherwise stay invisible. A document with no
  ``lastModified`` has no signature and is always re-read.
* **What counts as stale.** A page's text is whatever the extractors made of
  it, so the set of extractors is recorded too. Turning handwriting on or
  changing engine means every indexed page was built to a different recipe,
  and the next refresh rebuilds rather than skipping.
* **Extractor seam.** A page's searchable text is the newline-joined output
  of the index's extractors, each a ``PageSource -> str`` (the document,
  the page and its decoded model). The default is typed text plus
  PDF-highlight text; :mod:`recognize` adds the tablet's own handwriting
  conversions and, on Windows, ink recognition.
* **Connections.** SQLite connections cannot cross threads, so every
  operation opens its own connection on the calling thread (WAL mode, so
  readers never wait on the indexer). The schema is ensured once.

Each document commits on its own, so a cancelled or interrupted refresh
keeps every document finished before it. No Qt dependencies here.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .backup import DocItem
from .recognize import PageSource, conversion_signature
from .rmdata import PageModel, decode_page, page_text

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    file_type TEXT,
    page_count INTEGER,
    last_modified TEXT,
    signature TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    doc_uuid UNINDEXED,
    page_no UNINDEXED,
    name,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
"""

Extractor = Callable[[PageSource], str]
# progress(doc_name) -> keep going? Returning False stops the refresh.
Progress = Callable[[str], bool | None]


def typed_text_and_highlights(source: PageSource) -> str:
    """Typed text blocks plus text highlighted inside a base PDF."""
    return page_text(source.model)


DEFAULT_EXTRACTORS: tuple[Extractor, ...] = (typed_text_and_highlights,)


@dataclass
class SearchHit:
    doc_uuid: str
    doc_name: str
    page_no: int
    snippet: str


@dataclass
class IndexReport:
    pages: int = 0  # pages (re)indexed in this run
    indexed: int = 0  # documents (re)indexed
    skipped: int = 0  # documents unchanged since last time
    removed: int = 0  # documents purged because they left the library
    completed: bool = True  # False when the progress callback cancelled


class SearchIndex:
    """An FTS5 index stored at ``path``. Nothing is created until the first
    refresh, so ``exists`` says whether a library has ever been indexed."""

    def __init__(self, path: Path, extractors: Iterable[Extractor] | None = None):
        self.path = Path(path)
        self.extractors: list[Extractor] = list(extractors if extractors is not None else DEFAULT_EXTRACTORS)
        self._schema_ready = False

    @property
    def exists(self) -> bool:
        return self.path.exists()

    # ------------------------------------------------------------ extraction

    def page_text_of(self, source: PageSource) -> str:
        """The searchable text of a page: every extractor's output, joined."""
        parts = [ex(source) for ex in self.extractors]
        return "\n".join(p for p in parts if p).strip()

    # --------------------------------------------------------------- refresh

    def refresh(self, docs: Iterable[DocItem], progress: Progress | None = None, *, force: bool = False) -> IndexReport:
        """Bring the index up to date with ``docs``.

        ``progress`` is called with a document's name after it is indexed;
        returning ``False`` stops early (documents already done stay
        indexed) and the report says so.
        """
        report = IndexReport()
        current = {d.uuid: d for d in docs}
        conn = self._connect()
        try:
            recipe = self._extractor_fingerprint()
            with conn:
                stored = conn.execute("SELECT value FROM meta WHERE key = 'extractors'").fetchone()
                if stored is not None and stored[0] != recipe:
                    # every indexed page was built by a different set of
                    # extractors, so none of them can be trusted to be current
                    force = True
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('extractors', ?)", (recipe,))
                if force:
                    conn.execute("DELETE FROM docs")
                    conn.execute("DELETE FROM pages_fts")
                    known: dict[str, str] = {}
                else:
                    known = {uuid: (sig or "") for uuid, sig in conn.execute("SELECT uuid, signature FROM docs")}
                for gone in set(known) - set(current):
                    self._delete_doc(conn, gone)
                    report.removed += 1

            for doc in current.values():
                signature = _doc_signature(doc)
                if not force and signature and known.get(doc.uuid) == signature:
                    report.skipped += 1
                    continue
                with conn:  # one document per transaction
                    self._delete_doc(conn, doc.uuid)
                    report.pages += self._insert_doc(conn, doc, signature)
                report.indexed += 1
                if progress is not None and progress(doc.name) is False:
                    report.completed = False
                    break
        finally:
            conn.close()
        return report

    def _extractor_fingerprint(self) -> str:
        """Names the current extractors, in order, so a change of recipe is
        visible to the next refresh."""
        return "|".join(_extractor_name(ex) for ex in self.extractors)

    def _insert_doc(self, conn: sqlite3.Connection, doc: DocItem, signature: str) -> int:
        conn.execute(
            "INSERT OR REPLACE INTO docs (uuid, name, file_type, page_count, last_modified, signature) "
            "VALUES (?,?,?,?,?,?)",
            (doc.uuid, doc.name, doc.file_type, len(doc.pages), doc.last_modified, signature),
        )
        count = 0
        for page in doc.pages:
            text = self.page_text_of(PageSource(doc, page, decode_page(page.rm_path))) if page.rm_path is not None else ""
            # the doc name is searchable from every page
            body = (doc.name + "\n" + text).strip()
            if not body:
                continue
            conn.execute(
                "INSERT INTO pages_fts (doc_uuid, page_no, name, text) VALUES (?,?,?,?)",
                (doc.uuid, page.index, doc.name, body),
            )
            count += 1
        return count

    @staticmethod
    def _delete_doc(conn: sqlite3.Connection, uuid: str) -> None:
        conn.execute("DELETE FROM pages_fts WHERE doc_uuid = ?", (uuid,))
        conn.execute("DELETE FROM docs WHERE uuid = ?", (uuid,))

    # ----------------------------------------------------------------- query

    def query(self, user_query: str, limit: int = 200) -> list[SearchHit]:
        """Prefix-AND full-text search; empty query or no index gives no hits."""
        fts = _fts_query(user_query)
        if not fts or not self.exists:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT f.doc_uuid, d.name, f.page_no,
                       snippet(pages_fts, 3, '«', '»', '…', 24)
                FROM pages_fts f JOIN docs d ON d.uuid = f.doc_uuid
                WHERE pages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts, limit),
            ).fetchall()
            return [SearchHit(r[0], r[1] or "", r[2], r[3] or "") for r in rows]
        except sqlite3.OperationalError as exc:
            log.warning("search failed for %r: %s", user_query, exc)
            return []
        finally:
            conn.close()

    # ------------------------------------------------------------ connection

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        if not self._schema_ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            _migrate(conn)
            self._schema_ready = True
        return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an index written by an older version up to this schema.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table exactly as it
    was, so a column added later has to be added by hand. Rows predating a
    column keep NULL there, which no signature matches, so every document is
    re-read once and nothing stale is trusted.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(docs)")}
    if columns and "signature" not in columns:
        conn.execute("ALTER TABLE docs ADD COLUMN signature TEXT")
        conn.commit()


def _extractor_name(extractor: Extractor) -> str:
    """A stable name for an extractor, engine included where it has one, so
    that swapping handwriting engines counts as a different recipe."""
    engine = getattr(extractor, "engine", None)
    if engine:
        return f"{type(extractor).__name__}:{engine}"
    return getattr(extractor, "__name__", type(extractor).__name__)


def _doc_signature(doc: DocItem) -> str:
    """What has to be unchanged for a document's indexed text to still be
    current: its own ``lastModified`` plus the tablet's conversions, which
    arrive without the metadata changing.

    Empty when the document has no ``lastModified`` -- nothing to compare
    against, so such a document is always re-read.
    """
    if not doc.last_modified:
        return ""
    return f"{doc.last_modified}|{conversion_signature(doc)}"


def _fts_query(user_query: str) -> str:
    """Turn a free-text query into a safe FTS5 prefix-AND query."""
    terms = [t for t in user_query.replace('"', " ").split() if t]
    if not terms:
        return ""
    return " AND ".join(f'"{t}"*' for t in terms)
