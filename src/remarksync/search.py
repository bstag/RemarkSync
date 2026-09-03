"""SQLite FTS5 search index over a scanned library.

Indexes per page: document name, typed text from .rm text blocks, and text
highlighted inside base PDFs (GlyphRange). Raw handwriting is not indexed —
that needs an OCR pass and is a planned extension.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .library import iter_docs
from .rmdata import decode_page, page_text

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


@dataclass
class SearchHit:
    doc_uuid: str
    doc_name: str
    page_no: int
    snippet: str


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def build_index(items: list, db_path: Path, progress=None) -> int:
    """(Re)build the index from a scan tree. Returns number of pages indexed."""
    conn = open_db(db_path)
    count = 0
    try:
        with conn:
            conn.execute("DELETE FROM docs")
            conn.execute("DELETE FROM pages_fts")
            for doc in iter_docs(items):
                conn.execute(
                    "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)",
                    (doc.uuid, doc.name, doc.file_type, len(doc.pages), doc.last_modified),
                )
                for page in doc.pages:
                    text = page_text(decode_page(page.rm_path)) if page.rm_path is not None else ""
                    # the doc name is searchable from every page
                    body = (doc.name + "\n" + text).strip()
                    if not body:
                        continue
                    conn.execute(
                        "INSERT INTO pages_fts (doc_uuid, page_no, name, text) VALUES (?,?,?,?)",
                        (doc.uuid, page.index, doc.name, body),
                    )
                    count += 1
                if progress:
                    progress(doc.name)
    finally:
        conn.close()
    return count


def _fts_query(user_query: str) -> str:
    """Turn a free-text query into a safe FTS5 prefix-AND query."""
    terms = [t for t in user_query.replace('"', " ").split() if t]
    if not terms:
        return ""
    return " AND ".join(f'"{t}"*' for t in terms)


def search(db_path: Path, user_query: str, limit: int = 200) -> list[SearchHit]:
    fts = _fts_query(user_query)
    if not fts:
        return []
    conn = open_db(db_path)
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
