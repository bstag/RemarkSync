"""Edit a notebook's typed text and page list, producing a new archive.

A :class:`Draft` is a notebook's files held in memory plus the edits asked of
it. Nothing is written until :meth:`Draft.build`, which returns the bytes of an
archive; the document it was loaded from is never modified.

Two kinds of edit, chosen because both can be made without re-encoding ink:

* **Pages** -- reorder, delete, duplicate. Only the page list in ``.content``
  changes. A deleted page is marked deleted the way the tablet marks one and
  its files stay in the archive.
* **Typed text** -- a page's text block is replaced. The new block is spliced
  into the page file in place of the old one and every other byte of the page
  is left exactly as the tablet wrote it. That matters: on real pages rmscene
  reproduces ink blocks byte for byte but not text blocks, so a page is never
  passed through its writer whole.

A page whose text cannot be replaced safely says why in ``DraftPage.locked``:
ink pinned to a line of text would lose its anchor, and a page rmscene cannot
fully read cannot be checked for that.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid as uuidlib
import zipfile
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from rmscene import CrdtId, read_blocks, write_blocks
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import PageInfoBlock, RootTextBlock, SceneInfo, TreeNodeBlock, UnreadableBlock
from rmscene.text import TextDocument

from . import rmformat
from .backup import DocItem, active_cpages
from .mdcompile import STYLE_CODE_BY_NAME, page_rm_bytes, paragraph_styles
from .rmdata import HEADER_SIZE, V5_HEADER, _paragraphs

rmformat.install()

_HEADER = HEADER_SIZE  # the v6 magic; blocks follow, each a uint32 length and a 4-byte header
_ROOT_TEXT = 0x07
# The author RemarkSync signs page-list edits with, so they are told apart
# from the tablet's own and never reuse one of its timestamps.
AUTHOR = "7e6d6172-6b73-5379-9e63-000000000001"


class EditError(ValueError):
    """The document or the requested edit is outside what can be done safely."""


# ------------------------------------------------------------- text <-> lines

_MARK_BY_STYLE = {
    "CHECKBOX2": "  - [ ] ", "CHECKBOX_CHECKED": "- [x] ", "CHECKBOX": "- [ ] ",
    "BULLET2": "  - ", "BULLET": "- ", "NUMBERED2": "   1. ", "NUMBERED": "1. ",
    "BOLD": "### ", "SUBHEADING": "## ", "HEADING": "# ",
}
_PARSE = [  # tried in order: the longer marker first, so "- [ ] " is not read as a bullet
    ("CHECKBOX2", re.compile(r"^\s{2,}- \[[ xX]\] (.*)$")), ("CHECKBOX_CHECKED", re.compile(r"^- \[[xX]\] (.*)$")),
    ("CHECKBOX", re.compile(r"^- \[ \] (.*)$")), ("BULLET2", re.compile(r"^\s{2,}- (.*)$")),
    ("BULLET", re.compile(r"^- (.*)$")), ("NUMBERED2", re.compile(r"^\s{2,}\d+[.)] (.*)$")),
    ("NUMBERED", re.compile(r"^\d+[.)] (.*)$")), ("BOLD", re.compile(r"^### (.*)$")),
    ("SUBHEADING", re.compile(r"^## (.*)$")), ("HEADING", re.compile(r"^# (.*)$")),
]


def paragraphs_to_text(paragraphs: list[tuple[str, str]]) -> str:
    """One line per paragraph, its style shown as a Markdown-like marker.

    Unlike the compiler's Markdown this is exact: no joining of lines and no
    inline markup, so text survives a round trip untouched. A plain line that
    would read as a marker is escaped with a backslash.
    """
    lines = []
    for style, text in paragraphs:
        mark = _MARK_BY_STYLE.get(style, "")
        if not mark and (text.startswith("\\") or any(rx.match(text) for _, rx in _PARSE)):
            text = "\\" + text
        lines.append(mark + text)
    return "\n".join(lines)


def text_to_paragraphs(text: str) -> list[tuple[str, str]]:
    paragraphs = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("\\"):
            paragraphs.append(("PLAIN", line[1:]))
            continue
        for style, rx in _PARSE:
            if m := rx.match(line):
                paragraphs.append((style, m.group(1)))
                break
        else:
            paragraphs.append(("PLAIN", line))
    return paragraphs


# ------------------------------------------------------------------ page file


def _spans(data: bytes) -> list[tuple[int, int, int]]:
    """(start, end, block type) of every block, read from the length prefixes."""
    spans, pos = [], _HEADER
    while pos < len(data):
        if pos + 8 > len(data):
            raise EditError("page file ends inside a block")
        end = pos + 8 + int.from_bytes(data[pos:pos + 4], "little")
        if end > len(data):
            raise EditError("page file ends inside a block")
        spans.append((pos, end, data[pos + 7]))
        pos = end
    return spans


def _block_bytes(block) -> bytes:
    out = io.BytesIO()
    write_blocks(out, [block])
    return out.getvalue()[_HEADER:]


def _highest_counter(value, best: int = 0) -> int:
    """The largest CRDT counter anywhere in a block, so new ids come after it."""
    if isinstance(value, CrdtId):
        return max(best, value.part2)
    if is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            best = _highest_counter(getattr(value, f.name), best)
    elif isinstance(value, dict):
        for k, v in value.items():
            best = _highest_counter(v, _highest_counter(k, best))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            best = _highest_counter(v, best)
    elif isinstance(value, CrdtSequence):
        for item in value.sequence_items():
            best = _highest_counter(item, best)
    return best


@dataclass
class _Scene:
    data: bytes
    blocks: list
    spans: list[tuple[int, int, int]]
    text_at: int  # index of the root text block


def _read_scene(data: bytes) -> _Scene:
    """Parse a page for text editing, or raise EditError saying why not."""
    if data.startswith(V5_HEADER):
        raise EditError("this page is in the older (software 2.x) format")
    try:
        blocks = list(read_blocks(io.BytesIO(data)))
        spans = _spans(data)
    except EditError:
        raise
    except Exception as exc:
        raise EditError(f"this page could not be read ({type(exc).__name__})") from exc
    if len(blocks) != len(spans):
        raise EditError("this page could not be read block by block")
    if any(isinstance(b, UnreadableBlock) for b in blocks):
        raise EditError("this page holds data newer than RemarkSync can read, so it is left untouched")
    texts = [i for i, b in enumerate(blocks) if isinstance(b, RootTextBlock)]
    if len(texts) != 1 or spans[texts[0]][2] != _ROOT_TEXT:
        raise EditError("this page has no typed text block")
    if any(isinstance(b, TreeNodeBlock) and b.group.anchor_id is not None for b in blocks):
        raise EditError("handwriting on this page is pinned to its text; changing the text would detach it")
    return _Scene(data, blocks, spans, texts[0])


def page_paragraphs(data: bytes) -> list[tuple[str, str]]:
    """The typed text of a page as (style, text) paragraphs."""
    scene = _read_scene(data)
    return _paragraphs(TextDocument.from_scene_item(scene.blocks[scene.text_at].value))


def replace_page_text(data: bytes, paragraphs: list[tuple[str, str]]) -> bytes:
    """A page file with its typed text replaced and every other byte kept.

    The new text is one fresh run of characters whose ids come after every id
    already on the page, placed and sized like the block it replaces.
    """
    scene = _read_scene(data)
    old: RootTextBlock = scene.blocks[scene.text_at]
    text = "\n".join(t for _, t in paragraphs)
    first = max(_highest_counter(b) for b in scene.blocks) + 2
    styles = paragraph_styles(paragraphs, first)
    new = RootTextBlock(block_id=old.block_id, value=si.Text(
        items=CrdtSequence([CrdtSequenceItem(item_id=CrdtId(1, first), left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
                                             deleted_length=0, value=text)]),
        styles=styles, pos_x=old.value.pos_x, pos_y=old.value.pos_y, width=old.value.width))
    replaced = {scene.text_at: _block_bytes(new)}
    for i, block in enumerate(scene.blocks):
        # keep the page's own character/line counts honest, but only where the
        # block is one rmscene writes back exactly as it was
        start, end, _ = scene.spans[i]
        if isinstance(block, PageInfoBlock) and _block_bytes(block) == data[start:end]:
            block.text_chars_count = len(text) + 1
            block.text_lines_count = text.count("\n") + 1
            replaced[i] = _block_bytes(block)
    out = bytearray(data[:_HEADER])
    for i, (start, end, _) in enumerate(scene.spans):
        out += replaced.get(i, data[start:end])
    result = bytes(out)
    if page_paragraphs(result) != [(s if s in STYLE_CODE_BY_NAME else "PLAIN", t) for s, t in paragraphs]:
        raise EditError("the rewritten page did not read back as typed; nothing was changed")
    return result


def _paper(data: bytes) -> tuple[float, float] | None:
    try:
        for block in read_blocks(io.BytesIO(data)):
            if isinstance(block, SceneInfo) and block.paper_size:
                return tuple(block.paper_size)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------- draft


@dataclass
class DraftPage:
    page_id: str  # id in the built archive
    source: str | None  # the original page this one's file comes from; None for a new blank page
    original: str | None  # text as loaded (marker lines); None when the page has none to edit
    text: str | None  # text now
    locked: str | None  # why the text cannot be edited, or None

    @property
    def changed(self) -> bool:
        return self.text != self.original


def _stamps(value):
    """Every "author:counter" timestamp string anywhere in a page list."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "timestamp" and isinstance(inner, str):
                yield inner
            else:
                yield from _stamps(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _stamps(inner)


def _order_key(index: int) -> str:
    """Page order keys only have to sort; three letters is room for 17,576 pages."""
    return "b" + "".join(chr(97 + (index // 26 ** p) % 26) for p in (2, 1, 0))


class Draft:
    """A notebook loaded for editing. ``files`` maps archive member names to bytes."""

    def __init__(self, identifier: str, files: dict[str, bytes]):
        try:
            if str(uuidlib.UUID(identifier)) != identifier:
                raise ValueError
            self.content = json.loads(files[identifier + ".content"])
            self.metadata = json.loads(files[identifier + ".metadata"])
        except (KeyError, ValueError) as exc:
            raise EditError("this document has no readable identity, metadata or page list") from exc
        self.identifier, self.files = identifier, dict(files)
        self.name = self.metadata.get("visibleName") or "Untitled"
        pages = (self.content.get("cPages") or {}).get("pages")
        if (self.content.get("fileType") or "notebook") != "notebook":
            raise EditError("only notebooks can be edited; an annotated PDF or EPUB keeps its pages tied to its base document")
        if not isinstance(pages, list) or not pages or not all(isinstance(p, dict) and p.get("id") for p in pages):
            raise EditError("this notebook uses an older page list that RemarkSync does not edit")
        self._original = [str(p["id"]) for p in active_cpages(self.content)]
        self.pages = [self._load(pid) for pid in self._original]

    @classmethod
    def from_document(cls, doc: DocItem) -> "Draft":
        """Read a document's files from where it lies: a library or an opened archive."""
        if not doc.uuid or doc.doc_dir is None:
            raise EditError("this document has no identity to edit under")
        base = doc.files_root
        files = {p.name: p.read_bytes() for p in base.glob(doc.uuid + ".*") if p.is_file()}
        folder = base / doc.uuid
        if folder.is_dir():
            files.update({p.relative_to(base).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()})
        return cls(doc.uuid, files)

    @classmethod
    def from_archive(cls, path: Path) -> "Draft":
        with zipfile.ZipFile(path) as bundle:
            files = {i.filename: bundle.read(i) for i in bundle.infolist() if not i.is_dir()}
        contents = [n for n in files if n.endswith(".content") and "/" not in n]
        if len(contents) != 1:
            raise EditError("the archive does not hold exactly one document")
        identifier = contents[0][:-len(".content")]
        return cls(identifier, {n: d for n, d in files.items() if n.startswith((identifier + ".", identifier + "/"))})

    def _rm(self, page_id: str) -> str:
        return f"{self.identifier}/{page_id}.rm"

    def _load(self, page_id: str) -> DraftPage:
        data = self.files.get(self._rm(page_id))
        if data is None:
            nested = f"{self.identifier}/{page_id}/{page_id}.rm"
            if nested in self.files:
                return DraftPage(page_id, page_id, None, None, "this page is stored in an older layout")
            return DraftPage(page_id, page_id, "", "", None)  # a page nothing was ever put on
        try:
            text = paragraphs_to_text(page_paragraphs(data))
        except EditError as exc:
            return DraftPage(page_id, page_id, None, None, str(exc))
        return DraftPage(page_id, page_id, text, text, None)

    # ------------------------------------------------------------------ edits

    @property
    def base_files(self) -> dict[str, str]:
        """SHA-256 of each file this draft was loaded from: what the cloud must
        still hold for a replacement to be safe."""
        return {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}

    @property
    def changed(self) -> bool:
        return [p.page_id for p in self.pages] != self._original or any(p.changed for p in self.pages)

    def set_text(self, index: int, text: str) -> None:
        page = self.pages[index]
        if page.locked:
            raise EditError(page.locked)
        # compare as paragraphs so a different line ending is not an edit
        page.text = page.original if text_to_paragraphs(text) == text_to_paragraphs(page.original or "") else text

    def move(self, index: int, to: int) -> None:
        self.pages.insert(to, self.pages.pop(index))

    def delete(self, index: int) -> None:
        if len(self.pages) == 1:
            raise EditError("a notebook keeps at least one page")
        del self.pages[index]

    def duplicate(self, index: int) -> None:
        page = self.pages[index]
        self.pages.insert(index + 1, DraftPage(str(uuidlib.uuid4()), page.source, page.original, page.text, page.locked))

    def add_page(self, index: int) -> None:
        """A new blank page for typed text, after ``index``."""
        self.pages.insert(index + 1, DraftPage(str(uuidlib.uuid4()), None, None, "", None))

    # ------------------------------------------------------------------ build

    def build(self, *, new_identity: bool = False, name: str | None = None) -> bytes:
        """The edited document as archive bytes.

        ``new_identity`` gives the result its own document id and ``name``, for
        a copy that sits beside the original; without it the archive keeps the
        original's id, which is what replacing the original needs.
        """
        files = dict(self.files)
        content = json.loads(files[self.identifier + ".content"])
        pages = content["cPages"]["pages"]
        by_id = {str(p["id"]): p for p in pages}
        paper = next((size for size in (_paper(d) for n, d in self.files.items() if n.endswith(".rm")) if size), None)

        for page in self.pages:
            source = self.files.get(self._rm(page.source)) if page.source else None
            if page.changed or source is None:
                paragraphs = text_to_paragraphs(page.text or "")
                if source is not None:
                    files[self._rm(page.page_id)] = replace_page_text(source, paragraphs)
                elif page.source is None or page.text:
                    files[self._rm(page.page_id)] = page_rm_bytes(paragraphs, uuidlib.UUID(int=1), paper)
            elif page.page_id != page.source:
                files[self._rm(page.page_id)] = source

        order = [p.page_id for p in self.pages]
        if order != self._original:
            cpages = content["cPages"]
            authors = cpages.setdefault("uuids", [])
            mine = next((a["second"] for a in authors if a.get("first") == AUTHOR), None)
            if mine is None:
                mine = max([a.get("second", 0) for a in authors] + [0]) + 1
                authors.append({"first": AUTHOR, "second": mine})
            counter = 0
            for text in _stamps(cpages):
                if m := re.fullmatch(r"(\d+):(\d+)", text):
                    if int(m.group(1)) == mine:
                        counter = max(counter, int(m.group(2)))

            def stamp(value):
                nonlocal counter
                counter += 1
                return {"timestamp": f"{mine}:{counter}", "value": value}

            for pid in self._original:
                if pid not in order:
                    by_id[pid]["deleted"] = stamp(1)
            for index, page in enumerate(self.pages):
                entry = by_id.get(page.page_id)
                if entry is None:
                    template = (by_id.get(page.source) or {}).get("template", {}).get("value", "Blank")
                    entry = {"id": page.page_id, "template": stamp(template)}
                    pages.append(entry)
                entry["idx"] = stamp(_order_key(index))
            opened = cpages.get("lastOpened") or {}
            if opened.get("value") not in order:
                cpages["lastOpened"] = stamp(order[0])
            content["pageCount"] = len(order)
            files[self.identifier + ".content"] = json.dumps(content, indent=4).encode("utf-8")

        identifier = self.identifier
        if new_identity:
            identifier = str(uuidlib.uuid4())
            metadata = dict(self.metadata, visibleName=name or self.name, parent="")
            files[self.identifier + ".metadata"] = json.dumps(metadata, indent=4).encode("utf-8")
            files = {identifier + n[len(self.identifier):]: d for n, d in files.items()}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
            for member in sorted(files):
                bundle.writestr(member, files[member])
        return buf.getvalue()
