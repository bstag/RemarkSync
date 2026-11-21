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

from . import rmformat
from .rmdata import DEFAULT_PAPER
# Text-box placement and page fill come from the shared text metrics, so the
# compiler, the page decoder and the painter agree on where text sits.
from .textmetrics import STYLE_CODES, TEXT_POS_X, TEXT_POS_Y, TEXT_WIDTH, block_height, page_block_room

rmformat.install()  # write the paragraph-format fields the tablet expects for subheadings

STYLE_CODE_BY_NAME = {name: code for code, name in STYLE_CODES.items()}

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
    ``#`` becomes HEADING, ``##`` SUBHEADING, deeper headings BOLD, ``-``
    items BULLET (nested: BULLET2), ``1.`` items NUMBERED
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
        # only prose carries on from the line before, and only into more prose
        continues, joinable = joinable, False
        if raw.strip().startswith("```"):
            in_code = not in_code
            paras.append(("PLAIN", "    -----"))
            continue
        if in_code:
            paras.append(("PLAIN", "    " + raw))
            continue
        if not raw.strip():
            paras.append(("PLAIN", ""))
            continue
        m = _HEADING_RE.match(raw)
        if m:
            level = len(m.group(1))
            paras.append(("HEADING" if level == 1 else "SUBHEADING" if level == 2 else "BOLD", _inline(m.group(2)).strip()))
            continue
        m = _CHECKBOX_RE.match(raw)
        if m:
            nested = len(m.group(1)) >= 2
            checked = m.group(2).lower() == "x"
            style = "CHECKBOX2" if nested else ("CHECKBOX_CHECKED" if checked else "CHECKBOX")
            paras.append((style, _inline(m.group(3)).strip()))
            continue
        m = _BULLET_RE.match(raw)
        if m:
            paras.append(("BULLET2" if len(m.group(1)) >= 2 else "BULLET", _inline(m.group(2)).strip()))
            continue
        m = _NUMBERED_RE.match(raw)
        if m:
            paras.append(("NUMBERED2" if len(m.group(1)) >= 2 else "NUMBERED", _inline(m.group(2)).strip()))
            continue
        if raw.startswith(("    ", "\t", ">", "|")):
            paras.append(("PLAIN", _inline(raw)))
            continue
        line = _inline(raw).strip()
        if continues and paras and not paras[-1][1].endswith("  "):
            paras[-1] = (paras[-1][0], paras[-1][1].rstrip() + " " + line)
        else:
            paras.append(("PLAIN", line))
        joinable = True
    return paras


def paginate(paragraphs: list[tuple[str, str]],
             paper: tuple[float, float] | None = None) -> list[list[tuple[str, str]]]:
    """Split styled paragraphs into pages of about one page each, breaking
    only between paragraphs. Each paragraph is charged the vertical room
    the tablet gives it (its wrapped lines plus paragraph spacing); a long
    paragraph is never split.

    A taller paper holds more, so the page the document is bound for decides
    where the breaks fall.

    Each page is laid out as it is filled rather than charged paragraph by
    paragraph. Charging assumes what comes next -- the old reckoning billed
    every paragraph for the space above a *body* line -- and a heading needs far
    more than that, so a page carrying one silently overran. On a 1872 page the
    error stayed hidden behind the page break; a Paper Pro's 2160 fits enough
    paragraphs for it to show, and the tablet grew the canvas to 2257 to hold
    what we had overfilled it with.
    """
    room = page_block_room((paper or DEFAULT_PAPER)[1])
    pages: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for paragraph in paragraphs:
        # a paragraph taller than a whole page still goes on one: the tablet
        # wraps it, and splitting a paragraph across pages would split the text
        if current and block_height(current + [paragraph]) > room:
            pages.append(current)
            current = []
        current.append(paragraph)
    if current:
        pages.append(current)
    return pages or [[("PLAIN", "")]]


def paragraph_styles(paragraphs: list[tuple[str, str]], first_char: int) -> dict:
    """The style entries of a text block whose characters are one run of
    consecutive ids starting at ``CrdtId(1, first_char)``.

    Keyed the way the tablet keys them: the root id for the first paragraph,
    the id of the newline ending the previous paragraph for the rest. Plain
    paragraphs after the first need no entry. Each entry is stamped with an id
    of its own, after the last character's.
    """
    styles = {}
    stamp = first_char + sum(len(t) + 1 for _, t in paragraphs)  # one past the text and its end
    offset = 0
    for k, (style, t) in enumerate(paragraphs):
        code = STYLE_CODE_BY_NAME.get(style, 1)
        if code != 1 or k == 0:
            key = CrdtId(0, 0) if k == 0 else CrdtId(1, first_char + offset - 1)
            # unknown codes survive via rmdata's _missing_ shim
            styles[key] = LwwValue(timestamp=CrdtId(1, stamp), value=si.ParagraphStyle(code))
            stamp += 1
        offset += len(t) + 1
    return styles


def page_rm_bytes(paragraphs, author_uuid, paper: tuple[float, float] | None = None) -> bytes:
    """A software-3.x page holding one text block with per-paragraph
    styles: rmscene's simple text document (one text item whose characters
    get consecutive ids from (1, 16)), the SceneInfo block every device
    page carries, the text block where the device puts it, and a style entry
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
            block.value.styles = paragraph_styles(paragraphs, first_char)
        out.append(block)
        if isinstance(block, PageInfoBlock):
            out.append(
                SceneInfo(
                    current_layer=LwwValue(timestamp=CrdtId(0, 14), value=CrdtId(0, 11)),
                    background_visible=LwwValue(timestamp=CrdtId(0, 15), value=True),
                    root_document_visible=LwwValue(timestamp=CrdtId(0, 16), value=True),
                    # whole pixels: the block writes the pair as uint32
                    paper_size=tuple(int(n) for n in (paper or DEFAULT_PAPER)),
                )
            )
    buf = io.BytesIO()
    write_blocks(buf, out)
    return buf.getvalue()


def _page_order_key(index: int) -> str:
    """The tablet orders pages by a lexicographic key ("baa", "bab", ...).

    The first 676 pages get "b" and two letters. Two letters cannot count
    further, so the pages after them get "c" and three, then "d" and four: a
    longer key under a later first letter, which still sorts after every key
    before it. (The two-letter form used to run on past "bzz" into punctuation
    and then out of ASCII.)
    """
    letters, lead = 2, ord("b")
    while index >= 26 ** letters:
        index -= 26 ** letters
        letters, lead = letters + 1, lead + 1
    return chr(lead) + "".join(chr(97 + (index // 26 ** p) % 26) for p in reversed(range(letters)))


def build_rmdoc(title: str, page_texts: list, paper: tuple[float, float] | None = None) -> bytes:
    """Assemble the .rmdoc zip the way software 3.x lays a document out:
    ``<uuid>.content`` (formatVersion 2, pages under cPages with UUID ids),
    ``<uuid>.metadata``, and one ``<uuid>/<page-uuid>.rm`` per page."""
    width, height = paper or DEFAULT_PAPER
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
        "customZoomCenterY": height // 2,
        "customZoomOrientation": "portrait",
        "customZoomPageHeight": height,
        "customZoomPageWidth": width,
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
            zf.writestr(f"{doc_uuid}/{pid}.rm", page_rm_bytes(chunk, author_uuid, paper))
    return buf.getvalue()


def markdown_to_rmdoc(md: str, title: str, out_path: Path,
                      paper: tuple[float, float] | None = None) -> Path:
    """Compile Markdown into an archive for a page of ``paper``, defaulting to
    the 1404x1872 every reMarkable understands.

    Handing it the device's own paper is worth doing: a Paper Pro page is 2160
    tall where the default is 1872, so a document bound for one otherwise leaves
    a sixth of every page empty and runs to more pages than it needs.
    """
    out_path = Path(out_path)
    pages = paginate(markdown_to_paragraphs(md), paper)
    out_path.write_bytes(build_rmdoc(title, pages, paper))
    return out_path
