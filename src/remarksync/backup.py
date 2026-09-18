"""Scan a raw reMarkable backup folder (a copy of the tablet's xochitl
directory) or a single
.rmdoc archive into a document tree.

This is the backup-folder scanner; the opened, searchable library built on
top of it lives in :mod:`remarksync.library`.

Layout on the tablet:

    <root>/<uuid>.metadata          JSON: visibleName, parent, type, deleted...
    <root>/<uuid>.content           JSON: fileType, pages, orientation...
    <root>/<uuid>.pagedata          one template id per line
    <root>/<uuid>.pdf / .epub       base document for pdf/epub files
    <root>/<uuid>/                  page data, either:
        <page-uuid>/<page-uuid>.rm  (software 2.x style) or
        0/0.rm, 1/1.rm, ...        (software 3.x style)

.rmdoc archives are the same structure zipped, with bare names
(``.metadata``, ``.content``, ``0/0.rm``) and no uuid prefixes. Passing an
empty ``uuid`` makes the same lookup paths work for both.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

TEMPLATE_BLANK = "blank"
TEMPLATE_RULED = "ruled"
TEMPLATE_DOTS = "dots"
TEMPLATE_GRID = "grid"
TEMPLATE_ISOMETRIC = "isometric"


@dataclass
class PageRef:
    index: int
    rm_path: Path | None
    template: str = TEMPLATE_BLANK


@dataclass
class DocItem:
    kind: str = "doc"
    uuid: str = ""
    name: str = "Untitled"
    parent: str = ""
    deleted: bool = False
    file_type: str = "notebook"  # notebook | pdf | epub
    pages: list[PageRef] = field(default_factory=list)
    base_path: Path | None = None  # source .pdf/.epub for annotated documents
    base_pdf: Path | None = None  # PDF to draw under the ink: the .pdf itself, or the tablet's cached render of an .epub
    doc_dir: Path | None = None
    last_modified: str = ""

    def resolve_base_pdf(self) -> Path | None:
        if self.base_pdf and self.base_pdf.exists():
            return self.base_pdf
        return None


@dataclass
class FolderItem:
    kind: str = "folder"
    uuid: str = ""
    name: str = "Untitled"
    parent: str = ""
    deleted: bool = False
    children: list = field(default_factory=list)  # FolderItem | DocItem


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("bad json %s: %s", path, exc)
        return {}


def _normalise_template(name: str | None) -> str:
    s = (name or "").strip().lower()
    if not s or s in ("blank", "p blank"):
        return TEMPLATE_BLANK
    if "dot" in s:
        return TEMPLATE_DOTS
    if "iso" in s:
        return TEMPLATE_ISOMETRIC
    if "grid" in s or "graph" in s:
        return TEMPLATE_GRID
    if "line" in s:
        return TEMPLATE_RULED
    return TEMPLATE_BLANK


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def _find_page_rm(pages_dir: Path, page_id: str, index: int) -> Path | None:
    """Locate the .rm file for a page under either naming scheme."""
    return _first_existing(
        [
            pages_dir / page_id / f"{page_id}.rm",
            pages_dir / str(index) / f"{index}.rm",
            pages_dir / f"{page_id}.rm",
            pages_dir / f"{index}.rm",
        ]
    )


def _parse_doc(uuid: str, base: Path, meta: dict) -> DocItem:
    """Parse one document. ``base`` is where the metadata files live — the
    the backup folder itself, the extraction dir for .rmdoc."""
    content_path = _first_existing([base / f"{uuid}.content", base / ".content"])
    content = _read_json(content_path) if content_path else {}
    file_type = content.get("fileType") or "notebook"

    doc = DocItem(
        uuid=uuid,
        name=meta.get("visibleName") or "Untitled",
        parent=meta.get("parent") or "",
        deleted=bool(meta.get("deleted", False)),
        file_type=file_type,
        doc_dir=base / uuid if (base / uuid).is_dir() else base,
        last_modified=str(meta.get("lastModified") or ""),
    )

    if file_type in ("pdf", "epub"):
        doc.base_path = _first_existing([base / f"{uuid}.{file_type}", base / f".{file_type}"])
        # the tablet renders an .epub to a .pdf beside it, so both kinds
        # get a PDF underlay when that file is present
        doc.base_pdf = _first_existing([base / f"{uuid}.pdf", base / ".pdf"])

    # software 3.x stores pages as dicts under cPages.pages with per-page
    # templates; software 2.x used a flat list of page-id strings
    raw_pages = (content.get("cPages") or {}).get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raw_pages = content.get("pages")
    page_ids: list[str] = []
    templates_inline: list[str] = []
    if isinstance(raw_pages, list) and raw_pages and isinstance(raw_pages[0], dict):
        page_ids = [str(p.get("id")) for p in raw_pages]
        templates_inline = [
            str((p.get("template") or {}).get("value") or "") for p in raw_pages
        ]
    elif isinstance(raw_pages, list):
        page_ids = [str(p) for p in raw_pages]
    if not page_ids:
        if doc.base_pdf:
            # unannotated documents keep an empty pages list; show the base PDF itself
            try:
                import fitz

                with fitz.open(doc.base_pdf) as pdf:
                    page_ids = [str(i) for i in range(pdf.page_count)]
            except Exception as exc:
                log.warning("could not open base pdf %s: %s", doc.base_pdf, exc)
        else:
            # fall back to numbered page directories on disk
            page_ids = [
                d.name
                for d in sorted(doc.doc_dir.glob("[0-9]*"))
                if d.is_dir() and d.name.isdigit()
            ]

    templates: list[str] = templates_inline
    if not templates:
        pagedata = _first_existing([base / f"{uuid}.pagedata", base / "pagedata"])
        if pagedata:
            templates = pagedata.read_text(encoding="utf-8", errors="replace").splitlines()

    for i, pid in enumerate(page_ids):
        doc.pages.append(
            PageRef(
                index=i,
                rm_path=_find_page_rm(doc.doc_dir, pid, i),
                template=_normalise_template(templates[i]) if i < len(templates) else TEMPLATE_BLANK,
            )
        )
    return doc


def scan_backup(root: Path) -> list:
    """Scan a backup root; returns top-level tree items (folders + docs).

    Deleted items are skipped; pages without a .rm are kept so document
    page counts stay truthful.
    """
    root = Path(root)
    folders: dict[str, FolderItem] = {}
    docs: dict[str, DocItem] = {}

    for meta_path in sorted(root.glob("*.metadata")):
        uuid = meta_path.name[: -len(".metadata")]  # "" for bare .metadata (rmdoc)
        meta = _read_json(meta_path)
        if meta.get("deleted", False):
            continue
        item_type = meta.get("type")
        if item_type is None:
            item_type = "DocumentType" if (root / f"{uuid}.content").exists() else "CollectionType"
        if item_type == "CollectionType":
            folders[uuid] = FolderItem(uuid=uuid, name=meta.get("visibleName") or uuid or "Folder", parent=meta.get("parent") or "")
        else:
            doc = _parse_doc(uuid, root, meta)
            if doc.pages or doc.base_path:
                docs[uuid] = doc

    top: list = []

    def place(item) -> None:
        parent = item.parent
        if parent and parent in folders:
            folders[parent].children.append(item)
        else:
            top.append(item)

    for f in folders.values():
        place(f)
    for d in docs.values():
        place(d)

    def sort_level(items: list) -> None:
        items.sort(key=lambda it: (0 if it.kind == "folder" else 1, it.name.lower()))
        for it in items:
            if it.kind == "folder":
                sort_level(it.children)

    sort_level(top)
    return top


def iter_docs(items: list):
    """Flatten a scan tree into DocItems (depth-first)."""
    for item in items:
        if item.kind == "doc":
            yield item
        else:
            yield from iter_docs(item.children)


def safe_extract_names(names: Iterable[str], into: Path) -> None:
    """Raise unless every archive member would land inside ``into``.

    An archive is data from somewhere else, and a member named ``../`` or with
    an absolute path writes wherever it likes. Both extraction paths in this
    project check this: the zip of a ``.rmdoc`` someone sent, and the tar
    :mod:`remarksync.pull` streams off the tablet.

    Checked before anything is written, so a refusal leaves nothing behind.
    """
    root = Path(into).resolve()
    for name in names:
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"refusing to extract {name!r} outside {root}")


def open_rmdoc(path: Path, extract_dir: Path | None = None) -> DocItem:
    """Extract a .rmdoc archive and parse it into a single DocItem."""
    path = Path(path)
    tmp = Path(extract_dir) if extract_dir else Path(tempfile.mkdtemp(prefix="remarksync_"))
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path) as zf:
        safe_extract_names(zf.namelist(), tmp)
        zf.extractall(tmp)

    # archives may store entries at top level or under a single wrapper dir
    inner = tmp
    entries = [e for e in tmp.iterdir() if e.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and not list(tmp.glob("*.metadata")):
        inner = entries[0]

    metas = [m for m in inner.glob("*.metadata") if m.name != ".metadata"]
    meta_path = metas[0] if metas else inner / ".metadata"
    meta = _read_json(meta_path) if meta_path.exists() else {"visibleName": path.stem}
    uuid = meta_path.name[: -len(".metadata")] if metas else ""

    return _parse_doc(uuid, inner, meta)
