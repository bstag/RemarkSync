"""Throwaway: is the column inset absolute or proportional?

The advances document bounds the usable width at 1152 to within 0.8 px. The
three-width worksheet was typed at 740, 936 and 1152, so bounding it at each
says whether the inset is a fixed number of pixels or a fraction of the column.

For every line the device drew: its width is within the limit, and adding the
next character of the paragraph would have exceeded it. With the advance table
now complete, that next character's width is known.
"""

import sys
from pathlib import Path

import pymupdf

from remarksync.backup import iter_docs, scan_backup
from remarksync.layout import PDF_SCALE
from remarksync.rmdata import decode_page
from remarksync.textmetrics import style, text_width

sys.stdout.reconfigure(encoding="utf-8")

RAW = Path(r"C:\Backups\reMarkable\raw")
PDF = next(Path(r"C:\Users\jason\AppData\Local\Temp\rs-roundtrip").glob("a598661f*.pdf"), None)
if PDF is None:
    raise SystemExit("no export of 'Test bob' cached; run text_width wrap first")

doc = next(d for d in iter_docs(scan_backup(RAW)) if d.name == "Test bob")
print(f"{'width':>7} {'lines':>6} {'widest drawn':>13} {'limit under':>12} {'inset':>7} {'as %':>6}")
with pymupdf.open(PDF) as pdf:
    for page in doc.pages:
        if page.rm_path is None or page.index >= pdf.page_count:
            continue
        model = decode_page(page.rm_path)
        if not model.texts:
            continue
        block = model.texts[0]
        s = style(block.paragraphs[0][0])
        whole = "".join(text for _, text in block.paragraphs)

        rows: dict[float, list] = {}
        for x0, _y0, x1, y1, word, *_ in pdf.load_page(page.index).get_text("words"):
            rows.setdefault(round(y1, 1), []).append((x0, x1, word))
        drawn = []
        consumed = 0
        for _, boxes in sorted(rows.items()):
            boxes.sort()
            line = "".join(w for _, _, w in boxes)
            width = (max(b[1] for b in boxes) - min(b[0] for b in boxes)) * PDF_SCALE
            nxt = whole[consumed + len(line): consumed + len(line) + 1]
            consumed += len(line)
            drawn.append((width, nxt))

        lower = max(w for w, _ in drawn)
        # the tightest "one more character would not have fitted"
        upper = min((w + text_width(c, s) for w, c in drawn[:-1] if c), default=float("inf"))
        inset = block.width - (lower + upper) / 2
        print(f"{block.width:7.0f} {len(drawn):6} {lower:13.1f} {upper:12.1f} "
              f"{inset:7.1f} {inset / block.width * 100:5.1f}%")
