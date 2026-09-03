"""The **Library**: an opened backup folder, its document tree, and the
search index that lives beside it.

This is the module the window and the CLI both talk to. It owns:

* the backup root and the document tree scanned from it (:mod:`backup`);
* lookup of a document by uuid (search hits resolve through it);
* the search index file and its location inside the backup folder;
* building the index with progress and cancellation, and querying it.

Everything here is synchronous and Qt-free; the window decides whether to
run a long operation on a worker thread. No Qt dependencies here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backup import DocItem, iter_docs, scan_root
from .search import SearchHit, build_index, search

INDEX_NAME = ".remarksync-index.db"

# progress(doc_name) -> keep going? Returning False stops the build.
Progress = Callable[[str], bool | None]


class NoIndexError(LookupError):
    """The library has not been indexed yet; call ``rebuild_index`` first."""


@dataclass
class IndexReport:
    pages: int  # pages indexed
    completed: bool  # False when the progress callback cancelled the build


class Library:
    """An opened backup folder. Scans on construction; ``refresh`` rescans."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.tree: list = []
        self._docs: dict[str, DocItem] = {}
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
        return self.index_path.exists()

    def rebuild_index(self, progress: Progress | None = None) -> IndexReport:
        """Index every page of every document from scratch.

        ``progress`` is called with each document's name once it is indexed;
        returning ``False`` stops early and the report says so.
        """
        cancelled = False

        def step(name: str) -> bool:
            nonlocal cancelled
            if progress is not None and progress(name) is False:
                cancelled = True
                return False
            return True

        pages = build_index(self.tree, self.index_path, progress=step)
        return IndexReport(pages=pages, completed=not cancelled)

    def search(self, query: str, limit: int = 200) -> list[SearchHit]:
        """Full-text search over the index. Raises ``NoIndexError`` if the
        library has never been indexed."""
        if not self.has_index:
            raise NoIndexError(f"{self.root} has no search index yet")
        return search(self.index_path, query, limit=limit)
