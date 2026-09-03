"""The **Library**: an opened backup folder, its document tree, and the
search index that lives beside it.

This is the module the window and the CLI both talk to. It owns:

* the backup root and the document tree scanned from it (:mod:`backup`);
* lookup of a document by uuid (search hits resolve through it);
* the search index file, its location inside the backup folder, keeping it
  up to date with progress and cancellation, and querying it (:mod:`search`).

Everything here is synchronous and Qt-free; the window decides whether to
run a long operation on a worker thread. No Qt dependencies here.
"""

from __future__ import annotations

from pathlib import Path

from .backup import DocItem, iter_docs, scan_root
from .search import IndexReport, Progress, SearchHit, SearchIndex

INDEX_NAME = ".remarksync-index.db"

__all__ = ["INDEX_NAME", "IndexReport", "Library", "NoIndexError", "Progress", "SearchHit"]


class NoIndexError(LookupError):
    """The library has not been indexed yet; call ``refresh_index`` first."""


class Library:
    """An opened backup folder. Scans on construction; ``refresh`` rescans."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.tree: list = []
        self._docs: dict[str, DocItem] = {}
        self._index = SearchIndex(self.index_path)
        self.refresh()

    # ------------------------------------------------------------ documents

    def refresh(self) -> None:
        """Rescan the backup folder (new, changed and deleted documents)."""
        self.tree = scan_root(self.root)
        self._docs = {d.uuid: d for d in iter_docs(self.tree)}

    @property
    def documents(self) -> list[DocItem]:
        """Every document in the tree, depth-first."""
        return list(self._docs.values())

    def document(self, uuid: str) -> DocItem | None:
        return self._docs.get(uuid)

    def __len__(self) -> int:
        return len(self._docs)

    # ---------------------------------------------------------------- index

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    @property
    def has_index(self) -> bool:
        return self._index.exists

    def refresh_index(self, progress: Progress | None = None) -> IndexReport:
        """Index documents that are new or changed since the last run and
        purge ones that left the library.

        ``progress`` is called with each document's name once it is indexed;
        returning ``False`` stops early and the report says so.
        """
        return self._index.refresh(self.documents, progress)

    def rebuild_index(self, progress: Progress | None = None) -> IndexReport:
        """Index every page of every document from scratch."""
        return self._index.refresh(self.documents, progress, force=True)

    def search(self, query: str, limit: int = 200) -> list[SearchHit]:
        """Full-text search over the index. Raises ``NoIndexError`` if the
        library has never been indexed."""
        if not self.has_index:
            raise NoIndexError(f"{self.root} has no search index yet")
        return self._index.query(query, limit=limit)
