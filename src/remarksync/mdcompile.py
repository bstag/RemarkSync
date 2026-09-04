"""Compile Markdown into a .rmdoc archive the tablet can open and edit.

Each markdown chunk becomes one page holding a native reMarkable text block
(written via rmscene's ``simple_text_document``). The tablet imports the
resulting .rmdoc through the USB web UI or the desktop app, with the text
fully editable on-device.

Light markdown handling only — headings/bullets/links are flattened to
text; this mirrors what rmc extracts, so md → rmdoc → md round-trips.
Lines are never wrapped: the tablet wraps typed text itself, and pages
hold about one screen of text (see :mod:`textmetrics`).

The archive follows the software-3.x layout the desktop app expects
(``<uuid>.content`` with formatVersion 2 and UUID page ids, ``<uuid>.metadata``,
``<uuid>/<page-uuid>.rm``); the 2.x layout with numbered page folders is
rejected by the app since 2026.
"""

from __future__ import annotations

import io
import json
import re
import time
import uuid as uuidlib
import zipfile
from pathlib import Path

from rmscene import CrdtId, LwwValue, PageInfoBlock, RootTextBlock, SceneInfo, simple_text_document, write_blocks

from .rmdata import PAGE_HEIGHT, PAGE_WIDTH

# Text-box placement and page fill come from the shared text metrics, so the
# compiler, the page decoder and the painter agree on where text sits.
from .textmetrics import TEXT_POS_X, TEXT_POS_Y, TEXT_WIDTH, estimated_lines, lines_per_page

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_EMPH_RE = re.compile(r"(\*\*|__|\*|_|`)")
# lines that start a paragraph of their own and are never joined to the
# previous line: headings, list items, block quotes, tables, indented code
_BLOCK_START_RE = re.compile(r"^(\s*([-*+]|\d+[.)])\s+|#{1,6}\s|>|\||\s{4,}|\t)")


def markdown_to_text(md: str) -> str:
    """Flatten markdown to plain text, keeping heading/bullet markers that
    rmc-style extraction would re-produce (so content round-trips).

    Markdown sources are usually hard-wrapped; a single newline inside a
    paragraph is a soft break, so those lines are joined into one
    paragraph and the tablet wraps it. Blank lines, headings, list items,
    quotes, tables and code keep their own lines.
    """
    lines_out: list[str] = []
    in_code = False
    joinable = False  # the previous output line is prose that may continue
    for raw in md.splitlines():
        if raw.strip().startswith("```"):
            in_code = not in_code
            lines_out.append("    ─────")
            joinable = False
            continue
        if in_code:
            lines_out.append("    " + raw)
            joinable = False
            continue
        line = _IMAGE_RE.sub(r"[image: \1]", raw)
        line = _LINK_RE.sub(r"\1 (\2)", line)
        line = _EMPH_RE.sub("", line)
        if not line.strip():
            lines_out.append("")
            joinable = False
            continue
        block_start = bool(_BLOCK_START_RE.match(raw))
        if joinable and not block_start and not raw.rstrip().endswith("  "):
            lines_out[-1] = lines_out[-1].rstrip() + " " + line.strip()
        else:
            lines_out.append(line)
        # a heading, list item or quote line may itself be continued only by
        # an indented continuation, which the regex treats as a block start
        joinable = not block_start
    return "\n".join(lines_out)


def paginate_text(text: str) -> list[str]:
    """Split text into pages of about one screen each, breaking only between
    paragraphs. Lines are never wrapped here: the tablet wraps typed text
    itself inside the text box, and a hard wrap would show as short lines
    on the device. Each paragraph is charged the number of lines the tablet
    will wrap it onto."""
    budget = lines_per_page()
    pages: list[str] = []
    current: list[str] = []
    used = 0
    for paragraph in text.splitlines():
        cost = estimated_lines(paragraph)
        if current and used + cost > budget:
            pages.append("\n".join(current))
            current, used = [], 0
        current.append(paragraph)
        used += cost
    if current:
        pages.append("\n".join(current))
    return pages or [""]


def page_rm_bytes(text: str, author_uuid) -> bytes:
    """A software-3.x page holding one text block: rmscene's simple text
    document, with the SceneInfo block every device-written page carries
    and the text box where the device puts it."""
    blocks = list(simple_text_document(text, author_uuid=author_uuid))
    out: list = []
    for block in blocks:
        if isinstance(block, RootTextBlock):
            block.value.pos_x = TEXT_POS_X
            block.value.pos_y = TEXT_POS_Y
            block.value.width = TEXT_WIDTH
        out.append(block)
        if isinstance(block, PageInfoBlock):
            out.append(
                SceneInfo(
                    current_layer=LwwValue(timestamp=CrdtId(0, 14), value=CrdtId(0, 11)),
                    background_visible=LwwValue(timestamp=CrdtId(0, 15), value=True),
                    root_document_visible=LwwValue(timestamp=CrdtId(0, 16), value=True),
                    paper_size=(PAGE_WIDTH, PAGE_HEIGHT),
                )
            )
    buf = io.BytesIO()
    write_blocks(buf, out)
    return buf.getvalue()


def _page_order_key(index: int) -> str:
    """The tablet orders pages by a lexicographic key ("ba", "bb", ...)."""
    return f"b{chr(97 + index // 26)}{chr(97 + index % 26)}"


def build_rmdoc(title: str, page_texts: list[str]) -> bytes:
    """Assemble the .rmdoc zip the way software 3.x lays a document out:
    ``<uuid>.content`` (formatVersion 2, pages under cPages with UUID ids),
    ``<uuid>.metadata``, and one ``<uuid>/<page-uuid>.rm`` per page."""
    doc_uuid = str(uuidlib.uuid4())
    author_uuid = uuidlib.UUID(int=1)  # stable author id across pages
    page_ids = [str(uuidlib.uuid4()) for _ in page_texts]
    now_ms = str(int(time.time() * 1000))

    content = {
        "cPages": {
            "lastOpened": {"timestamp": "1:1", "value": page_ids[0]},
            "original": {"timestamp": "0:0", "value": -1},
            "pages": [
                {
                    "id": pid,
                    "idx": {"timestamp": "1:2", "value": _page_order_key(i)},
                    "template": {"timestamp": "1:1", "value": "Blank"},
                }
                for i, pid in enumerate(page_ids)
            ],
            "uuids": [{"first": str(author_uuid), "second": 1}],
        },
        "coverPageNumber": -1,
        "customZoomCenterX": 0,
        "customZoomCenterY": PAGE_HEIGHT // 2,
        "customZoomOrientation": "portrait",
        "customZoomPageHeight": PAGE_HEIGHT,
        "customZoomPageWidth": PAGE_WIDTH,
        "customZoomScale": 1,
        "documentMetadata": {},
        "extraMetadata": {},
        "fileType": "notebook",
        "fontName": "",
        "formatVersion": 2,
        "lineHeight": -1,
        "orientation": "portrait",
        "pageCount": len(page_ids),
        "pageTags": [],
        "tags": [],
        "textAlignment": "justify",
        "textScale": 1,
        "zoomMode": "bestFit",
    }
    metadata = {
        "createdTime": now_ms,
        "lastModified": now_ms,
        "lastOpened": now_ms,
        "lastOpenedPage": 0,
        "new": False,
        "parent": "",
        "pinned": False,
        "source": "",
        "type": "DocumentType",
        "visibleName": title,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{doc_uuid}.content", json.dumps(content, indent=4))
        zf.writestr(f"{doc_uuid}.metadata", json.dumps(metadata, indent=4))
        for pid, chunk in zip(page_ids, page_texts):
            zf.writestr(f"{doc_uuid}/{pid}.rm", page_rm_bytes(chunk, author_uuid))
    return buf.getvalue()


def markdown_to_rmdoc(md: str, title: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    pages = paginate_text(markdown_to_text(md))
    out_path.write_bytes(build_rmdoc(title, pages))
    return out_path
