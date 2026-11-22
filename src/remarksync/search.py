"""The **Search index**: SQLite FTS5 over the pages of a library.

One module owns everything about the index: the schema, which documents
need (re)indexing, how a page becomes searchable text, and querying.

* **Refresh, not rebuild.** ``refresh`` diffs the library's documents
  against what the index already holds: unchanged documents are skipped,
  changed or new ones re-extracted, vanished ones purged. ``force=True``
  is the full rebuild.
* **What counts as changed.** A document's signature is its ``lastModified``
  plus content/page/scene hashes and the tablet's own text conversions, because
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
  readers never wait on the indexer). The schema is ensured once per file.
* **Names.** A document's name is its own column on every page, never part
  of the page text, so a query can mix a name with a page's words. Pages that
  match by name alone are answered as one hit for the document.
* **Rows.** A document's pages are inserted together, so ``docs`` remembers
  the rowid range they took and replacing a document deletes that range
  instead of scanning every page for its uuid (which is not indexed).

Each document commits on its own, so a cancelled or interrupted refresh
keeps every document finished before it. No Qt dependencies here.
"""

from __future__ import annotations

import logging
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
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
    signature TEXT,
    first_row INTEGER,
    last_row INTEGER
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
# A progress object may also have a ``cancelled()`` method; it is asked between
# the pages of a document, so one long document does not hold up a stop.
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
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class PageDecodeError(ValueError):
    pass


class SearchError(RuntimeError):
    """The index could not answer a query (unreadable, damaged or locked)."""


NAME_HIT = "(matches the document's name)"


class _Cancelled(Exception):
    """Raised between pages to abandon the document being indexed."""


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
        indexed) and the report says so. If it has a ``cancelled()`` method
        that turns true, the document in hand is abandoned between pages: it
        keeps the text it had and is read again by the next refresh.
        """
        report = IndexReport()
        cancelled = getattr(progress, "cancelled", None)
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
                known = {uuid: (sig or "") for uuid, sig in conn.execute("SELECT uuid, signature FROM docs")}
                for gone in set(known) - set(current):
                    self._delete_doc(conn, gone)
                    report.removed += 1

            for doc in current.values():
                try:
                    signature = _doc_signature(doc)
                    if not force and signature and known.get(doc.uuid) == signature:
                        report.skipped += 1
                        continue
                    with conn:  # failure rolls back to the document's last good text
                        self._delete_doc(conn, doc.uuid)
                        pages = self._insert_doc(conn, doc, signature, cancelled)
                    report.pages += pages
                    report.indexed += 1
                except _Cancelled:
                    report.completed = False
                    break
                except (OSError, PageDecodeError) as exc:
                    report.failed += 1
                    report.errors.append(f"{doc.name}: {exc}")
                    log.warning("could not index %s: %s", doc.name, exc)
                    # The extractor recipe may have changed. Invalidate the
                    # previous signature even if old text remains available.
                    with conn:
                        conn.execute("UPDATE docs SET signature = NULL WHERE uuid = ?", (doc.uuid,))
                if progress is not None and progress(doc.name) is False:
                    report.completed = False
                    break
        finally:
            conn.close()
        return report

    def _extractor_fingerprint(self) -> str:
        """Names the current extractors, in order, so a change of recipe is
        visible to the next refresh."""
        return "v3|" + "|".join(_extractor_name(ex) for ex in self.extractors)

    def _insert_doc(self, conn: sqlite3.Connection, doc: DocItem, signature: str,
                    cancelled: Callable[[], bool] | None = None) -> int:
        rows: list[int] = []
        for page in doc.pages:
            if cancelled is not None and cancelled():
                raise _Cancelled
            model = decode_page(page.rm_path) if page.rm_path is not None else PageModel()
            if model.error:
                raise PageDecodeError(f"page {page.index + 1}: {model.error}")
            text = self.page_text_of(PageSource(doc, page, model))
            # every page gets a row, text or not: the name column makes the
            # document findable from each of them
            rows.append(conn.execute(
                "INSERT INTO pages_fts (doc_uuid, page_no, name, text) VALUES (?,?,?,?)",
                (doc.uuid, page.index, doc.name, text),
            ).lastrowid)
        # one writer, one transaction: the rowids are consecutive
        first, last = (rows[0], rows[-1]) if rows else (0, -1)
        conn.execute(
            "INSERT OR REPLACE INTO docs (uuid, name, file_type, page_count, last_modified, signature, first_row, last_row) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (doc.uuid, doc.name, doc.file_type, len(doc.pages), doc.last_modified, signature, first, last),
        )
        return len(rows)

    @staticmethod
    def _delete_doc(conn: sqlite3.Connection, uuid: str) -> None:
        span = conn.execute("SELECT first_row, last_row FROM docs WHERE uuid = ?", (uuid,)).fetchone()
        if span is not None and span[0] is not None:
            conn.execute("DELETE FROM pages_fts WHERE rowid BETWEEN ? AND ?", span)
        else:  # written before the range was recorded: find the pages the slow way
            conn.execute("DELETE FROM pages_fts WHERE doc_uuid = ?", (uuid,))
        conn.execute("DELETE FROM docs WHERE uuid = ?", (uuid,))

    # ----------------------------------------------------------------- query

    def query(self, user_query: str, limit: int = 200) -> list[SearchHit]:
        """Prefix-AND full-text search; empty query or no index gives no hits.

        A page whose own text matches is a hit. Pages that match only through
        the document's name are one hit for that document, at its first such
        page, so a notebook's name does not return every page of it. Raises
        :class:`SearchError` when the index cannot be read.
        """
        fts = _fts_query(user_query)
        if not fts or not self.exists:
            return []
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT f.doc_uuid, d.name, f.page_no,
                           snippet(pages_fts, 3, '«', '»', '…', 24),
                           instr(highlight(pages_fts, 3, char(1), ''), char(1)) > 0
                    FROM pages_fts f JOIN docs d ON d.uuid = f.doc_uuid
                    WHERE pages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts, limit * 10),  # room for the name-only pages that fold into one hit
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("search failed for %r: %s", user_query, exc)
            raise SearchError(f"the search index could not be read: {exc}") from exc
        hits: list[SearchHit] = []
        by_name: dict[str, SearchHit] = {}
        for uuid, name, page_no, snippet, in_text in rows:
            if in_text:
                hits.append(SearchHit(uuid, name or "", page_no, snippet or ""))
            elif uuid in by_name:
                by_name[uuid].page_no = min(by_name[uuid].page_no, page_no)
            else:
                by_name[uuid] = SearchHit(uuid, name or "", page_no, NAME_HIT)
                hits.append(by_name[uuid])
        return hits[:limit]

    # ------------------------------------------------------------ connection

    def _connect(self) -> sqlite3.Connection:
        # a file that has gone (deleted while the app was open) needs its schema again
        fresh = not self._schema_ready or not self.path.exists()
        conn = sqlite3.connect(self.path, timeout=10)
        if fresh:
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
    for column, kind in (("signature", "TEXT"), ("first_row", "INTEGER"), ("last_row", "INTEGER")):
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE docs ADD COLUMN {column} {kind}")
    conn.commit()


def _extractor_name(extractor: Extractor) -> str:
    """A stable name for an extractor, engine included where it has one, so
    that swapping handwriting engines counts as a different recipe."""
    engine = getattr(extractor, "engine", None)
    if engine:
        return f"{type(extractor).__name__}:{engine}"
    return getattr(extractor, "__name__", type(extractor).__name__)


def _doc_signature(doc: DocItem) -> str:
    """Hash the scene content and mapping as well as metadata/conversions.
    Repairs and external backup updates need not change lastModified.

    Empty when the document has no ``lastModified`` -- nothing to compare
    against, so such a document is always re-read.
    """
    if not doc.last_modified:
        return ""
    digest = hashlib.sha256(json.dumps(doc.content, sort_keys=True).encode())
    digest.update(doc.name.encode())
    for page in doc.pages:
        digest.update(f"{page.page_id}:{page.index}:{page.base_index}".encode())
        if page.rm_path is not None:
            digest.update(page.rm_path.read_bytes())
    return f"{doc.last_modified}|{conversion_signature(doc)}|{digest.hexdigest()}"


def _fts_query(user_query: str) -> str:
    """Turn a free-text query into a safe FTS5 prefix-AND query.

    A term with no letter or digit (a stray ``-`` or ``&``) has nothing the
    index could hold; ANDed in, it would make the whole query match nothing.
    """
    terms = [t for t in user_query.replace('"', " ").split() if any(ch.isalnum() for ch in t)]
    if not terms:
        return ""
    return " AND ".join(f'"{t}"*' for t in terms)
