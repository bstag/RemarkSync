"""The **Search index**: SQLite FTS5 over the pages of a library.

One module owns everything about the index: the schema, which documents
need (re)indexing, how a page becomes searchable text, and querying.

* **Refresh, not rebuild.** ``refresh`` diffs the library's documents
  against what the index already holds, keyed on each document's
  ``lastModified`` from its ``.metadata``: unchanged documents are skipped,
  changed or new ones re-extracted, vanished ones purged. ``force=True``
  is the full rebuild.
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

from .backup import DocItem, PageRef
from .recognize import PageSource
from .rmdata import PageModel, decode_page, page_text

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    file_type TEXT,
    page_count INTEGER,
    last_modified TEXT
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
            with conn:
                if force:
                    conn.execute("DELETE FROM docs")
                    conn.execute("DELETE FROM pages_fts")
                    known: dict[str, str] = {}
                else:
                    known = {uuid: (lm or "") for uuid, lm in conn.execute("SELECT uuid, last_modified FROM docs")}
                for gone in set(known) - set(current):
                    self._delete_doc(conn, gone)
                    report.removed += 1

            for doc in current.values():
                if not force and doc.last_modified and known.get(doc.uuid) == doc.last_modified:
                    report.skipped += 1
                    continue
                with conn:  # one document per transaction
                    self._delete_doc(conn, doc.uuid)
                    report.pages += self._insert_doc(conn, doc)
                report.indexed += 1
                if progress is not None and progress(doc.name) is False:
                    report.completed = False
                    break
        finally:
            conn.close()
        return report

    def _insert_doc(self, conn: sqlite3.Connection, doc: DocItem) -> int:
        conn.execute(
            "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)",
            (doc.uuid, doc.name, doc.file_type, len(doc.pages), doc.last_modified),
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
            self._schema_ready = True
        return conn


def _fts_query(user_query: str) -> str:
    """Turn a free-text query into a safe FTS5 prefix-AND query."""
    terms = [t for t in user_query.replace('"', " ").split() if t]
    if not terms:
        return ""
    return " AND ".join(f'"{t}"*' for t in terms)
