"""Read the tablet's three column widths, and check our wrapping at each.

The device offers three column widths in its text UI and records the chosen one
as ``RootText.settings.textWidth``: 740, 936 or 1152. A worksheet typed at all
three (2026-09-18) completed ``TEXT_WIDTHS`` and showed that text stops a fixed
8 px short of the declared width at every setting. Its last disagreement was a
ligature the device would not split; with that in the model, all 20 of its lines
break where we say.

A page per width, carrying the same words, settles both. The tablet's own PDF
export says where it broke each line; ``textmetrics`` says where we would.

    python -m tests.real.text_width read     # what widths the pages declare
    python -m tests.real.text_width wrap     # our line breaks vs the device's

Both read only. ``wrap`` fetches the document's PDF export over the USB web
interface, which is a GET.

To make the fixture, see FIXTURE_NOTE below.
"""

from __future__ import annotations

import os
import sys

from remarksync.backup import iter_docs, scan_backup
from remarksync.rmdata import decode_page
from remarksync.textmetrics import TEXT_WIDTHS, lay_out_lines, style

from .exported import compare, drawn_lines
from .paths import RAW, work_dir
from .tablet import export_pdf

WORK = work_dir("REMARKSYNC_ROUNDTRIP", "rs-roundtrip")
# The worksheet that was actually typed, on 2026-09-18. Override it if you make
# another; the fixture note below says what has to be on it.
DOC_NAME = os.environ.get("REMARKSYNC_WIDTH_DOC", "Test bob")

FIXTURE_NOTE = """\
On the tablet, make a notebook of three typed pages and name it here (see
DOC_NAME). Set the column width control to a different one of its three
settings on each page, and type the same two sentences on all three:

    The first flight of five finches flew off in fine drifting fog.
    A quiet brown pony walked past the open gate and waited by the wall.

The first sentence is deliberately full of f-pairs, because the device sets
several of them as single ligature glyphs and will not break a line inside one
(see textmetrics.LIGATURES_SERIF). The second is plain, as a control.

Then pull the backup so the pages are here to read:

    .venv\\Scripts\\python -m remarksync pull C:\\Backups\\reMarkable\\raw
"""


def _pages():
    for doc in iter_docs(scan_backup(RAW)):
        if doc.name == DOC_NAME:
            return doc
    return None


def read() -> None:
    doc = _pages()
    if doc is None:
        print(f"no document called {DOC_NAME!r} in {RAW}\n")
        print(FIXTURE_NOTE)
        return
    print(f"{doc.name!r}: {len(doc.pages)} pages\n")
    print(f"{'page':>5} {'paper':>12} {'textWidth':>10} {'pos_x':>8} {'margin':>7} {'lines':>6} {'chars':>6}")
    widths = []
    for page in doc.pages:
        if page.rm_path is None:
            continue
        model = decode_page(page.rm_path)
        if not model.texts:
            print(f"{page.index + 1:5}  (no typed text on this page)")
            continue
        block = model.texts[0]
        sheet = (model.paper_size or (1404.0, 1872.0))[0]
        widths.append(block.width)
        print(f"{page.index + 1:5} {f'{sheet:.0f}':>12} {block.width:10.1f} {block.pos_x:8.1f} "
              f"{(sheet - block.width) / 2:7.1f} {len(block.lines):6} {len(block.text):6}")

    fresh = sorted({w for w in widths} - set(TEXT_WIDTHS))
    print(f"\nwidths on these pages : {sorted(set(widths))}")
    print(f"already known         : {list(TEXT_WIDTHS)}")
    print(f"new                   : {fresh if fresh else 'none'}")
    if len(set(widths)) == 3:
        print("\nAll three settings captured; TEXT_WIDTHS can be completed.")


def wrap() -> None:
    """Compare our line breaks against the tablet's, at each width."""
    import pymupdf

    doc = _pages()
    if doc is None:
        print(f"no document called {DOC_NAME!r}; see 'read' for how to make it")
        return

    # the download endpoint takes a uuid, and the backup already knows it, so
    # there is no need for the root listing -- which only covers the top level
    # and so would miss a document filed in a folder
    pdf_path = export_pdf(doc.uuid, WORK)

    total = agreed = 0
    with pymupdf.open(pdf_path) as pdf:
        for page in doc.pages:
            if page.rm_path is None or page.index >= pdf.page_count:
                continue
            model = decode_page(page.rm_path)
            if not model.texts:
                continue
            block = model.texts[0]
            # lay the same paragraphs out at the width the file declares
            ours = [(style(ln.style), ln.text)
                    for ln in lay_out_lines(block.paragraphs, block.width) if ln.text.strip()]
            print(f"\npage {page.index + 1}, width {block.width:.0f}:")
            a, c = compare(ours, drawn_lines(pdf, page.index), f"page {page.index + 1}")
            agreed += a
            total += c
    print(f"\nlines broken where we predicted: {agreed} of {total}")


MODES = ("read", "wrap")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
