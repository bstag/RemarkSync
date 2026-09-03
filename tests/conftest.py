"""Synthetic reMarkable backup fixtures for tests.

We author .rm files with rmscene's own writer (same path used by the
markdown compiler), so fixtures are valid v6 files by construction.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rmscene import (CrdtId, SceneGlyphItemBlock, SceneLineItemBlock,
                     simple_text_document, write_blocks)
from rmscene.crdt_sequence import END_MARKER, CrdtSequenceItem
from rmscene import scene_items as si


def page_rm(text: str | None = None, strokes: list[si.Line] | None = None,
            glyphs: list[si.GlyphRange] | None = None) -> bytes:
    """Serialize a .rm page with optional root text, stroke lines and
    snap text highlights."""
    blocks = list(simple_text_document(text or ""))
    prev_id = None
    for i, line in enumerate(strokes or []):
        item_id = CrdtId(1, 100 + i)
        left = prev_id if prev_id is not None else END_MARKER
        blocks.append(
            SceneLineItemBlock(
                parent_id=CrdtId(0, 1),
                item=CrdtSequenceItem(
                    item_id=item_id, left_id=left, right_id=END_MARKER, deleted_length=0, value=line
                ),
            )
        )
        prev_id = item_id
    for i, glyph in enumerate(glyphs or []):
        blocks.append(
            SceneGlyphItemBlock(
                parent_id=CrdtId(0, 1),
                item=CrdtSequenceItem(
                    item_id=CrdtId(1, 300 + i), left_id=END_MARKER, right_id=END_MARKER,
                    deleted_length=0, value=glyph,
                ),
            )
        )
    buf = io.BytesIO()
    write_blocks(buf, blocks)
    return buf.getvalue()


def page_rm_v5(lines: list[tuple[int, int, float, list[tuple[float, float]]]]) -> bytes:
    """Serialize a 'version=5' .lines page (software 2.x format).

    Each entry is (pen, colour, width, points). Mirrors the real-device
    layout including the per-line id records that firmware >= 2.13 added
    before some lines, to exercise the tolerant scanner.
    """
    import struct as _struct

    out = bytearray()
    out += b"reMarkable .lines file, version=5          "
    out += _struct.pack("<i", 1)
    out += _struct.pack("<i", 1)  # one layer
    out += _struct.pack("<i", len(lines))  # lines in layer
    for n, (pen, colour, width, points) in enumerate(lines):
        if n % 2 == 1:
            out += _struct.pack("<i", 6 + n)  # extended-format id record
        out += _struct.pack("<i", pen)
        out += _struct.pack("<d", width)
        out += _struct.pack("<i", colour)
        out += _struct.pack("<i", len(points))
        for x, y in points:
            out += _struct.pack("<6f", x, y, 0.0, 0.0, 30.0, 0.0)
    return bytes(out)


def line(color, tool, points, thickness_scale=1.0) -> si.Line:
    return si.Line(
        color=color,
        tool=tool,
        thickness_scale=thickness_scale,
        starting_length=0.0,
        move_id=None,
        points=[si.Point(x=x, y=y, speed=0, direction=0, width=4, pressure=0) for x, y in points],
    )


def write_metadata(root: Path, uuid: str, meta: dict) -> None:
    (root / f"{uuid}.metadata").write_text(json.dumps(meta), encoding="utf-8")


def write_content(root: Path, uuid: str, content: dict) -> None:
    (root / f"{uuid}.content").write_text(json.dumps(content), encoding="utf-8")


FOLDER_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
INK_UUID = "bbbbbbbb-1111-2222-3333-444444444444"
PDF_UUID = "cccccccc-1111-2222-3333-444444444444"
DIARY_UUID = "dddddddd-1111-2222-3333-444444444444"
DELETED_UUID = "eeeeeeee-1111-2222-3333-444444444444"


JOURNAL_UUID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
JOURNAL_PAGE = "c48a9c11-c2ba-4027-a867-0fc7bcb5476a"


def write_cpages_doc(root: Path, uuid: str, name: str, parent: str, page_uuids: list[str]) -> None:
    """Write a software 3.x style doc: cPages dict content + flat page .rm
    files in the doc directory (no numbered or uuid-named page dirs)."""
    write_metadata(root, uuid, {"visibleName": name, "parent": parent, "type": "DocumentType"})
    content = {
        "cPages": {
            "pages": [
                {
                    "id": pid,
                    "idx": {"timestamp": "1:%d" % (i + 1), "value": "ba" if i == 0 else "ba%d" % i},
                    "modifed": "1788178455556",
                    "template": {"timestamp": "1:1", "value": "P Lines medium"},
                }
                for i, pid in enumerate(page_uuids)
            ]
        },
        "fileType": "notebook",
    }
    (root / f"{uuid}.content").write_text(json.dumps(content), encoding="utf-8")
    d = root / uuid
    d.mkdir(exist_ok=True)
    for i, pid in enumerate(page_uuids):
        (d / f"{pid}.rm").write_bytes(
            page_rm(text=f"journal page {i} entry about coffee") if i % 2 == 0 else
            page_rm(strokes=[line(si.PenColor.BLUE, si.Pen.BALLPOINT_1, [(100, 200), (300, 260)])])
        )


@pytest.fixture
def backup_root(tmp_path: Path) -> Path:
    """A small synthetic xochitl backup: one folder with a notebook and an
    annotated PDF, one root-level text diary, one deleted doc."""
    # folder
    write_metadata(tmp_path, FOLDER_UUID,
                   {"visibleName": "Research", "parent": "", "type": "CollectionType"})

    # notebook with ink + typed text (software 3.x numbered-page layout)
    write_metadata(tmp_path, INK_UUID,
                   {"visibleName": "Ink Notes", "parent": FOLDER_UUID, "type": "DocumentType",
                    "lastModified": "1700000000000"})
    write_content(tmp_path, INK_UUID,
                  {"fileType": "notebook", "pages": ["0", "1"], "orientation": "portrait"})
    (tmp_path / f"{INK_UUID}.pagedata").write_text("Blank\nDots\n", encoding="utf-8")
    d = tmp_path / INK_UUID
    (d / "0").mkdir(parents=True)
    (d / "1").mkdir()
    (d / "0" / "0.rm").write_bytes(
        page_rm(
            text="typed note about quantum computing",
            strokes=[
                line(si.PenColor.BLACK, si.Pen.BALLPOINT_1, [(100, 200), (200, 220), (300, 260)]),
                line(si.PenColor.YELLOW, si.Pen.HIGHLIGHTER_1, [(100, 300), (600, 300)], thickness_scale=2.0),
                line(si.PenColor.GRAY, si.Pen.ERASER, [(0, 0), (100, 100)]),  # must be ignored
            ],
        )
    )
    (d / "1" / "1.rm").write_bytes(
        page_rm(strokes=[line(si.PenColor.RED, si.Pen.FINELINER_1, [(50, 50), (150, 150)])])
    )

    # annotated PDF: base pdf with 2 pages + one page of annotation ink
    import fitz

    pdf = fitz.open()
    for i in range(2):
        page = pdf.new_page()
        page.insert_text((72, 96), f"base pdf page {i} with searchable words")
    pdf.save(tmp_path / f"{PDF_UUID}.pdf")
    pdf.close()
    write_metadata(tmp_path, PDF_UUID,
                   {"visibleName": "Paper PDF", "parent": FOLDER_UUID, "type": "DocumentType"})
    write_content(tmp_path, PDF_UUID,
                  {"fileType": "pdf", "pages": ["0", "1"], "orientation": "portrait"})
    (tmp_path / f"{PDF_UUID}.pagedata").write_text("Blank\nBlank\n", encoding="utf-8")
    dp = tmp_path / PDF_UUID
    (dp / "0").mkdir(parents=True)
    (dp / "0" / "0.rm").write_bytes(
        page_rm(strokes=[line(si.PenColor.BLUE, si.Pen.BALLPOINT_1, [(80, 90), (400, 95)])])
    )

    # root-level typed diary
    write_metadata(tmp_path, DIARY_UUID,
                   {"visibleName": "Diary 2026", "parent": "", "type": "DocumentType"})
    write_content(tmp_path, DIARY_UUID,
                  {"fileType": "notebook", "pages": ["0"], "orientation": "portrait"})
    (tmp_path / f"{DIARY_UUID}.pagedata").write_text("Lines\n", encoding="utf-8")
    dd = tmp_path / DIARY_UUID
    (dd / "0").mkdir(parents=True)
    (dd / "0" / "0.rm").write_bytes(page_rm(text="dear diary today I welded a flux capacitor"))

    # deleted document: must be skipped by the scanner
    write_metadata(tmp_path, DELETED_UUID,
                   {"visibleName": "Gone", "parent": "", "type": "DocumentType", "deleted": True})
    write_content(tmp_path, DELETED_UUID, {"fileType": "notebook", "pages": ["0"]})

    # software 3.x journal-style doc inside a folder (cPages content,
    # flat uuid-named .rm files, no pagedata)
    write_cpages_doc(tmp_path, JOURNAL_UUID, "2026", FOLDER_UUID, [JOURNAL_PAGE])

    return tmp_path
