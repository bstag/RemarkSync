"""Compile Markdown into a .rmdoc archive the tablet can open and edit.

Each markdown chunk becomes one page holding a native reMarkable text block
(written via rmscene's ``simple_text_document``). The tablet imports the
resulting .rmdoc through the USB web UI or the desktop app, with the text
fully editable on-device.

Light markdown handling only — headings/bullets/links are flattened to
text; this mirrors what rmc extracts, so md → rmdoc → md round-trips.
"""

from __future__ import annotations

import io
import json
import re
import uuid as uuidlib
import zipfile
from pathlib import Path

from rmscene import RootTextBlock, simple_text_document, write_blocks

# reMarkable text-box coordinates are centre-relative, y downwards. Device
# text pages anchor boxes near the top; simple_text_document's default
# (pos_y=234) lands mid/lower page, so compiled pages override it.
TEXT_POS_X = -468.0
TEXT_POS_Y = -780.0
TEXT_WIDTH = 936.0

# Conservative page fill: the tablet wraps text inside the 936-unit-wide text
# box; we break lines ourselves so pages stay predictable.
CHARS_PER_LINE = 62
LINES_PER_PAGE = 40

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_EMPH_RE = re.compile(r"(\*\*|__|\*|_|`)")


def markdown_to_text(md: str) -> str:
    """Flatten markdown to plain text, keeping heading/bullet markers that
    rmc-style extraction would re-produce (so content round-trips)."""
    lines_out: list[str] = []
    in_code = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            lines_out.append("    ─────" if in_code else "    ─────")
            continue
        if in_code:
            lines_out.append("    " + line)
            continue
        line = _IMAGE_RE.sub(r"[image: \1]", line)
        line = _LINK_RE.sub(r"\1 (\2)", line)
        line = _EMPH_RE.sub("", line)
        lines_out.append(line)
    return "\n".join(lines_out)


def _wrap_line(line: str, width: int = CHARS_PER_LINE) -> list[str]:
    if len(line) <= width:
        return [line]
    out: list[str] = []
    while len(line) > width:
        cut = line.rfind(" ", 0, width)
        if cut <= 0:
            cut = width
        out.append(line[:cut])
        line = line[cut:].lstrip()
    if line:
        out.append(line)
    return out


def paginate_text(text: str) -> list[str]:
    """Split wrapped text into page-sized chunks on line boundaries."""
    wrapped: list[str] = []
    for line in text.splitlines():
        wrapped.extend(_wrap_line(line))
    pages: list[str] = []
    for start in range(0, max(1, len(wrapped)), LINES_PER_PAGE):
        chunk = wrapped[start : start + LINES_PER_PAGE]
        if chunk:
            pages.append("\n".join(chunk))
    return pages or [""]


def page_rm_bytes(text: str, author_uuid) -> bytes:
    blocks = list(simple_text_document(text, author_uuid=author_uuid))
    for block in blocks:
        if isinstance(block, RootTextBlock):
            block.value.pos_x = TEXT_POS_X
            block.value.pos_y = TEXT_POS_Y
            block.value.width = TEXT_WIDTH
    buf = io.BytesIO()
    write_blocks(buf, blocks)
    return buf.getvalue()


def build_rmdoc(title: str, page_texts: list[str]) -> bytes:
    """Assemble the .rmdoc zip: bare names, numbered page dirs."""
    doc_uuid = str(uuidlib.uuid4())
    author_uuid = uuidlib.UUID(int=1)  # stable author id across pages

    content = {
        "fileType": "notebook",
        "fileName": "",
        "title": title,
        "pages": [str(i) for i in range(len(page_texts))],
        "pageTimes": {},
        "lastOpenedPage": 0,
        "links": {},
        "orientation": "portrait",
        "text": True,
        "tags": [],
    }
    metadata = {
        "visibleName": title,
        "parent": "",
        "lastModified": "1700000000000",
        "lastOpened": "1700000000000",
        "type": "DocumentType",
        "pinned": False,
        "synced": False,
        "version": 0,
        "deleted": False,
        "metadatamodified": False,
        "modified": False,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(".metadata", json.dumps(metadata))
        zf.writestr(".content", json.dumps(content))
        zf.writestr("pagedata", "".join("Blank\n" for _ in page_texts))
        for i, chunk in enumerate(page_texts):
            zf.writestr(f"{i}/{i}.rm", page_rm_bytes(chunk, author_uuid))
            zf.writestr(f"{i}/metadata.json", json.dumps({"layers": []}))
    return buf.getvalue()


def markdown_to_rmdoc(md: str, title: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    pages = paginate_text(markdown_to_text(md))
    out_path.write_bytes(build_rmdoc(title, pages))
    return out_path
