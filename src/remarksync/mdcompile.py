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
from rmscene import scene_items as si

from .rmdata import PAGE_HEIGHT, PAGE_WIDTH
from .textmetrics import STYLE_CODES

STYLE_CODE_BY_NAME = {name: code for code, name in STYLE_CODES.items()}

# Text-box placement and page fill come from the shared text metrics, so the
# compiler, the page decoder and the painter agree on where text sits.
from .textmetrics import TEXT_POS_X, TEXT_POS_Y, TEXT_WIDTH, page_text_budget, paragraph_advance

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_EMPH_RE = re.compile(r"(\*\*|__|\*|_|`)")


# Markdown block markers and the paragraph style they become.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^(\s*)[-*+]\s+\[( |x|X)\]\s+(.*)$")


def _inline(text: str) -> str:
    text = _IMAGE_RE.sub(r"[image: \1]", text)
    text = _LINK_RE.sub(r"\1 (\2)", text)
    return _EMPH_RE.sub("", text)


def markdown_to_paragraphs(md: str) -> list[tuple[str, str]]:
    """Markdown as (style, text) paragraphs the way the tablet types them:
    ``#`` headings become HEADING, deeper headings the tablet's subheading
    (BOLD), ``-`` items BULLET (nested: BULLET2), ``1.`` items NUMBERED
    (nested: NUMBERED2), ``- [ ]`` and ``- [x]`` the checkbox styles, code
    is kept indented, and everything else is PLAIN.

    Markdown sources are usually hard-wrapped; a single newline inside a
    paragraph is a soft break, so those lines are joined and the tablet
    wraps them. A blank line becomes an empty paragraph, as on the device.
    """
    paras: list[tuple[str, str]] = []
    in_code = False
    joinable = False  # the previous paragraph is prose that may continue
    for raw in md.splitlines():
        if raw.strip().startswith("```"):
            in_code = not in_code
            paras.append(("PLAIN", "    \u2500\u2500\u2500\u2500\u2500"))
            joinable = False
            continue
        if in_code:
            paras.append(("PLAIN", "    " + raw))
            joinable = False
            continue
        if not raw.strip():
            paras.append(("PLAIN", ""))
            joinable = False
            continue
        m = _HEADING_RE.match(raw)
        if m:
            paras.append(("HEADING" if len(m.group(1)) == 1 else "BOLD", _inline(m.group(2)).strip()))
            joinable = False
            continue
        m = _CHECKBOX_RE.match(raw)
        if m:
            nested = len(m.group(1)) >= 2
            checked = m.group(2).lower() == "x"
            style = "CHECKBOX2" if nested else ("CHECKBOX_CHECKED" if checked else "CHECKBOX")
            paras.append((style, _inline(m.group(3)).strip()))
            joinable = False
            continue
        m = _BULLET_RE.match(raw)
        if m:
            paras.append(("BULLET2" if len(m.group(1)) >= 2 else "BULLET", _inline(m.group(2)).strip()))
            joinable = False
            continue
        m = _NUMBERED_RE.match(raw)
        if m:
            paras.append(("NUMBERED2" if len(m.group(1)) >= 2 else "NUMBERED", _inline(m.group(2)).strip()))
            joinable = False
            continue
        if raw.startswith(("    ", "\t", ">", "|")):
            paras.append(("PLAIN", _inline(raw)))
            joinable = False
            continue
        line = _inline(raw).strip()
        if joinable and paras and not paras[-1][1].endswith("  "):
            paras[-1] = (paras[-1][0], paras[-1][1].rstrip() + " " + line)
        else:
            paras.append(("PLAIN", line))
        joinable = True
    return paras


_MARKERS = {
    "HEADING": "# ", "BOLD": "## ", "BULLET": "- ", "BULLET2": "  - ", "NUMBERED": "1. ", "NUMBERED2": "   a. ",
    "CHECKBOX": "- [ ] ", "CHECKBOX_CHECKED": "- [x] ", "CHECKBOX2": "  - [ ] ",
}


def markdown_to_text(md: str) -> str:
    """Flatten markdown to plain text with the block markers rmc-style
    extraction would re-produce, so content round-trips as text."""
    return "\n".join(_MARKERS.get(style, "") + text if text else "" for style, text in markdown_to_paragraphs(md))


def paginate(paragraphs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split styled paragraphs into pages of about one screen each, breaking
    only between paragraphs. Each paragraph is charged the vertical room
    the tablet gives it (its wrapped lines plus paragraph spacing); a long
    paragraph is never split."""
    budget = page_text_budget()
    pages: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0.0
    for style, text in paragraphs:
        cost = paragraph_advance(style, text)
        if current and used + cost > budget:
            pages.append(current)
            current, used = [], 0.0
        current.append((style, text))
        used += cost
    if current:
        pages.append(current)
    return pages or [[("PLAIN", "")]]


def paginate_text(text: str) -> list[str]:
    """Plain-text pagination (every line a PLAIN paragraph)."""
    pages = paginate([("PLAIN", line) for line in text.splitlines()] or [("PLAIN", "")])
    return ["\n".join(t for _, t in page) for page in pages]


def page_rm_bytes(paragraphs, author_uuid) -> bytes:
    """A software-3.x page holding one text block with per-paragraph
    styles: rmscene's simple text document (one text item whose characters
    get consecutive ids from (1, 16)), the SceneInfo block every device
    page carries, the text box where the device puts it, and a style entry
    per paragraph keyed the way the tablet keys them: the root id for the
    first paragraph, the id of the newline ending the previous paragraph
    for the rest."""
    if isinstance(paragraphs, str):
        paragraphs = [("PLAIN", line) for line in paragraphs.split("\n")]
    text = "\n".join(t for _, t in paragraphs)
    blocks = list(simple_text_document(text, author_uuid=author_uuid))
    out: list = []
    first_char = 16  # simple_text_document's single text item starts at CrdtId(1, 16)
    for block in blocks:
        if isinstance(block, RootTextBlock):
            block.value.pos_x = TEXT_POS_X
            block.value.pos_y = TEXT_POS_Y
            block.value.width = TEXT_WIDTH
            styles = {}
            stamp = first_char + len(text) + 1
            offset = 0
            for k, (style, t) in enumerate(paragraphs):
                key = CrdtId(0, 0) if k == 0 else CrdtId(1, first_char + offset - 1)
                code = STYLE_CODE_BY_NAME.get(style, 1)
                if code != 1 or k == 0:
                    styles[key] = LwwValue(timestamp=CrdtId(1, stamp), value=si.ParagraphStyle(code))
                    stamp += 1
                offset += len(t) + 1
            block.value.styles = styles
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


def build_rmdoc(title: str, page_texts: list) -> bytes:
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
    pages = paginate(markdown_to_paragraphs(md))
    out_path.write_bytes(build_rmdoc(title, pages))
    return out_path
