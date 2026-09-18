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

from .backup import DocItem, iter_docs, scan_backup
from .recognize import CACHE_NAME, RecognitionCache, handwriting_extractor, tablet_conversion
from .search import DEFAULT_EXTRACTORS, IndexReport, Progress, SearchHit, SearchIndex

INDEX_NAME = ".remarksync-index.db"

__all__ = ["INDEX_NAME", "IndexReport", "Library", "NoIndexError", "Progress", "SearchHit"]


class NoIndexError(LookupError):
    """The library has not been indexed yet; call ``refresh_index`` first."""


class Library:
    """An opened backup folder. Scans on construction; ``refresh`` rescans."""

    def __init__(self, root: Path, handwriting: bool = False, engine: str | None = None):
        self.root = Path(root)
        self.tree: list = []
        self._docs: dict[str, DocItem] = {}
        self._index = SearchIndex(self.index_path)
        self.recognition = RecognitionCache(self.root / CACHE_NAME)
        self._engine = engine
        self.handwriting = handwriting
        self.rescan()

    @property
    def handwriting(self) -> str | None:
        """The handwriting engine indexing uses, or None (the tablet's own
        conversions are always indexed)."""
        return self._handwriting

    @handwriting.setter
    def handwriting(self, on: bool) -> None:
        extractor = handwriting_extractor(self.recognition, self._engine) if on else None
        self._handwriting = extractor.engine if extractor is not None else None
        extractors = list(DEFAULT_EXTRACTORS) + [tablet_conversion]
        if extractor is not None:
            extractors.append(extractor)
        self._index.extractors = extractors

    # ------------------------------------------------------------ documents

    def rescan(self) -> None:
        """Read the backup folder again (new, changed and deleted documents).

        Not called ``refresh``: CONTEXT.md reserves *Refresh* for bringing the
        search index up to date, and having both on one class made the word mean
        two things depending on which method you reached for.
        """
        self.tree = scan_backup(self.root)
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

    def refresh_index(self, progress: Progress | None = None, *, force: bool = False) -> IndexReport:
        """Index documents that are new or changed since the last run and
        purge ones that left the library. ``force`` re-indexes every page of
        every document instead.

        ``progress`` is called with each document's name once it is indexed;
        returning ``False`` stops early and the report says so.

        One entry point rather than two: a caller that has a rebuild-or-not
        boolean in hand can pass it, instead of choosing between two methods
        and turning the boolean back into a branch.
        """
        return self._index.refresh(self.documents, progress, force=force)

    def search(self, query: str, limit: int = 200) -> list[SearchHit]:
        """Full-text search over the index. Raises ``NoIndexError`` if the
        library has never been indexed."""
        if not self.has_index:
            raise NoIndexError(f"{self.root} has no search index yet")
        return self._index.query(query, limit=limit)
