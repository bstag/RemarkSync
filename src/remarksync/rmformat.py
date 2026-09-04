"""Paragraph-format fields of the .rm text block that rmscene 0.8 skips.

Each paragraph style entry in a text block is a sub-block holding the
style code (field 1, one byte) and, on software 3.x, sometimes two more
fields: field 2 (one byte) and field 3 (four bytes). rmscene reads the
code and drops the rest, which loses one distinction the tablet makes:
the toolbar's *Subheading* is style code 3 with field 2 = 2 and field 3 =
3, while *Bold* is the bare code 3. Nested list styles carry field 2 = 3
and a field-3 value of their own (bullet 1, numbered 33, checkbox 785 on
the page they were measured from).

This module reads those fields and writes them back, reporting the
subheading as its own style code (:data:`SUBHEADING_CODE`) so the rest of
RemarkSync can treat it as a paragraph style like any other. Call
:func:`install` once; it swaps rmscene's two format functions for these.
"""

from __future__ import annotations

from rmscene import CrdtId, LwwValue
from rmscene import scene_items as si
from rmscene import scene_stream
from rmscene.tagged_block_reader import TaggedBlockReader
from rmscene.tagged_block_writer import TaggedBlockWriter

# A code of our own for "style 3 with variant 2"; the file never contains it.
SUBHEADING_CODE = 103
SUBHEADING_VARIANT = 2
SUBHEADING_EXTRA = 3
# Nested list styles as the tablet writes them: (variant, extra) per code.
NESTED_FIELDS: dict[int, tuple[int, int]] = {5: (3, 1), 11: (3, 33), 8: (3, 785)}


def _style(code: int) -> si.ParagraphStyle:
    try:
        return si.ParagraphStyle(code)
    except ValueError:
        return si.ParagraphStyle.PLAIN


def text_format_from_stream(stream: TaggedBlockReader) -> tuple[CrdtId, LwwValue[si.ParagraphStyle]]:
    char_id = stream.data.read_crdt_id()
    timestamp = stream.read_id(1)
    with stream.read_subblock(2) as info:
        code = stream.read_byte(1)
        fields = _tagged_fields(stream, info.offset + info.size)
    variant = fields.get(2)
    if code == 3 and variant == SUBHEADING_VARIANT:
        code = SUBHEADING_CODE
    return char_id, LwwValue(timestamp, _style(code))


def _tagged_fields(stream: TaggedBlockReader, end: int) -> dict[int, int]:
    """Remaining (index -> value) fields of the current sub-block. Read by
    hand: rmscene's optional readers consume the tag byte on a mismatch."""
    fields: dict[int, int] = {}
    data = stream.data
    while data.tell() < end:
        tag = data.read_uint8()
        index, kind = tag >> 4, tag & 0xF
        if kind == 0x1:
            fields[index] = data.read_uint8()
        elif kind == 0x4:
            fields[index] = data.read_uint32()
        elif kind == 0x8:
            data.read_bytes(8)
        elif kind == 0xC:
            data.read_bytes(data.read_uint32())
        elif kind == 0xF:
            data.read_crdt_id()
        else:
            break  # unknown tag type: leave the rest to rmscene's skip
    return fields


def text_format_to_stream(char_id: CrdtId, value: LwwValue[si.ParagraphStyle], writer: TaggedBlockWriter) -> None:
    code = int(value.value)
    writer.data.write_crdt_id(char_id)
    writer.write_id(1, value.timestamp)
    with writer.write_subblock(2):
        if code == SUBHEADING_CODE:
            writer.write_byte(1, 3)
            writer.write_byte(2, SUBHEADING_VARIANT)
            writer.write_int(3, SUBHEADING_EXTRA)
        elif code in NESTED_FIELDS:
            variant, extra = NESTED_FIELDS[code]
            writer.write_byte(1, code)
            writer.write_byte(2, variant)
            writer.write_int(3, extra)
        else:
            writer.write_byte(1, code)


def install() -> None:
    """Make rmscene read and write the extra format fields."""
    scene_stream.text_format_from_stream = text_format_from_stream
    scene_stream.text_format_to_stream = text_format_to_stream
