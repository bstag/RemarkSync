"""Readable typed-note layout aligned with a base PDF's body-text columns."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import replace
from functools import cache

import pymupdf as fitz

from .layout import PDF_SCALE
from .textmetrics import DESCENT_RATIO, TEXT_LEFT_PAD, TEXT_WRAP_INSET, lay_out_lines, style


@cache
def _font_named(fontname):
    # one object per face: building a Font is not free, and the exporter asks
    # for one for every run of text it measures or writes
    return fitz.Font(fontname=fontname)


def pdf_font(st):
    """The built-in PDF face standing in for a paragraph style's device font."""
    return _font_named("tiro" if st.serif else ("hebo" if st.weight >= 500 else "helv"))


def body_columns(page):
    """Estimate dominant body columns; keep separate columns separate.

    Font sizes are weighted by character count so headings/footer text do not
    usually determine the measure. Left-edge groups collect ragged lines and
    nearby list indents. Return displayed PDF coordinates, as the viewer does.
    """
    sizes = defaultdict(int)
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            for span in spans:
                sizes[round(span["size"] * 2) / 2] += len(span["text"].strip())
            if spans:
                lines.append(spans)
    if not sizes or page.rotation in (90, 270):
        return [], "No usable horizontal PDF body-text column; notes use page margins."
    dominant = max(sizes, key=sizes.get)
    measures = []
    for spans in lines:
        body = [s for s in spans if abs(s["size"] - dominant) <= max(0.5, dominant * 0.04)]
        if not body:
            continue
        rect = fitz.Rect(body[0]["bbox"])
        for span in body[1:]:
            rect |= fitz.Rect(span["bbox"])
        rect = (rect * page.rotation_matrix) & page.rect
        if rect.width >= max(48, dominant * 5) and rect.height > 0:
            measures.append((rect, sum(len(s["text"].strip()) for s in body)))
    groups = []
    for rect, weight in sorted(measures, key=lambda item: item[0].x0):
        group = next((g for g in groups if abs(g["left"] - rect.x0) <= 14), None)
        if group is None:
            groups.append({"left": rect.x0, "rect": fitz.Rect(rect), "weight": weight})
        else:
            group["rect"] |= rect
            group["weight"] += weight
    if not groups:
        return [], "No usable horizontal PDF body-text column; notes use page margins."
    # Reject tiny captions competing with the main body, while retaining a
    # genuine shorter second column instead of spanning the gutter.
    threshold = max(g["weight"] for g in groups) * 0.2
    return [g["rect"] for g in groups if g["weight"] >= threshold], None


def _graphemes(word):
    units = []
    for char in word:
        if units and (unicodedata.combining(char) or char in ("\ufe0f", "\u200d") or units[-1].endswith("\u200d")):
            units[-1] += char
        else:
            units.append(char)
    return units


def _wrap(text, st, width):
    font = pdf_font(st)
    def measure(value):
        return font.text_length(value, fontsize=st.size)
    limit = max(st.size, width - st.indent)
    result = []
    for explicit_line in text.split("\n"):
        current = ""
        for index, word in enumerate(explicit_line.split(" ")):
            candidate = current + (" " if index else "") + word
            if current and measure(candidate) > limit:
                result.append(current)
                current = word
            else:
                current = candidate
            if measure(current) > limit:
                part = ""
                for unit in _graphemes(current):
                    if part and measure(part + unit) > limit:
                        result.append(part)
                        part = unit
                    else:
                        part += unit
                current = part
        result.append(current)
    return result


def fit_notes_to_columns(model, paper, columns):
    """Copy only visible typed notes; leave raw models and ink untouched.

    Keep each block's vertical start and paragraph hierarchy. Rewrap with the
    *export font's* metrics, so the visible letters fit the inferred measure.
    """
    fitted, decisions = [], []
    for index, block in enumerate(model.texts):
        if not block.layer_visible or not block.text.strip():
            fitted.append(block)
            continue
        original_x = (block.pos_x + TEXT_LEFT_PAD - paper.x0) / PDF_SCALE
        column = min(columns, key=lambda c: abs(c.x0 - original_x))
        width = column.width * PDF_SCALE
        # the tablet's vertical rhythm, with the lines broken by the export font
        lines = lay_out_lines(block.paragraphs or [("PLAIN", block.text)], width, wrap_fn=_wrap)
        height = max((ln.baseline + style(ln.style).size * DESCENT_RATIO for ln in lines), default=block.height)
        fitted.append(replace(block, pos_x=paper.x0 + column.x0 * PDF_SCALE - TEXT_LEFT_PAD,
                              width=width + TEXT_WRAP_INSET, height=height, lines=lines))
        decisions.append({"block": index, "column_points": [column.x0, column.x1],
                          "original_width_pixels": block.width, "rendered_width_pixels": width})
    return replace(model, texts=fitted), decisions
