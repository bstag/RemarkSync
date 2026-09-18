"""Read the tablet's three column widths, and check our wrapping at each.

The device offers three column widths in its text UI and records the chosen one
as ``RootText.settings.textWidth``. Two turn up in the reference backup, 1152
and 936; the third has never been seen, so ``TEXT_WIDTHS`` is incomplete
and the wrapping model has only ever been checked at 1152.

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
import urllib.request
from pathlib import Path

from remarksync.backup import iter_docs, scan_backup
from remarksync.rmdata import decode_page
from remarksync.textmetrics import TEXT_WIDTHS, lay_out_lines

from .compile_roundtrip import normalise

RAW = Path(os.environ.get("REMARKSYNC_REAL_BACKUP", r"C:\Backups\reMarkable\raw"))
HOST = os.environ.get("REMARKSYNC_TABLET", "10.11.99.1")
WORK = Path(os.environ.get("REMARKSYNC_ROUNDTRIP", str(Path(os.environ["TEMP"]) / "rs-roundtrip")))
DOC_NAME = os.environ.get("REMARKSYNC_WIDTH_DOC", "Width test")

FIXTURE_NOTE = """\
On the tablet, make a notebook called 'Width test' with three typed pages.
Set the column width control to a different one of its three settings on each
page, and type the same two sentences on all three:

    The first flight of five finches flew off in fine drifting fog.
    A quiet brown pony walked past the open gate and waited by the wall.

The first sentence is deliberately full of fi and fl pairs, because the device
sets those as single ligature glyphs and we measure them as two -- which is the
leading suspect for the one line in thirty-four that the compiler round-trip
broke differently. The second is plain, as a control.

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
    uuid = doc.uuid
    WORK.mkdir(parents=True, exist_ok=True)
    pdf_path = WORK / f"{uuid}.pdf"
    with urllib.request.urlopen(f"http://{HOST}/download/{uuid}/placeholder", timeout=180) as response:
        pdf_path.write_bytes(response.read())

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
            ours = [ln.text for ln in lay_out_lines(block.paragraphs, block.width) if ln.text.strip()]

            rendered = pdf.load_page(page.index)
            rows: dict[int, list] = {}
            for x0, _y0, _x1, y1, word, *_ in rendered.get_text("words"):
                rows.setdefault(round(y1), []).append((x0, word))
            theirs = [" ".join(w for _, w in sorted(v)) for _, v in sorted(rows.items())]

            print(f"\npage {page.index + 1}, width {block.width:.0f}: "
                  f"we predict {len(ours)} lines, the tablet drew {len(theirs)}")
            for i in range(min(len(ours), len(theirs))):
                total += 1
                if normalise(ours[i]) == normalise(theirs[i]):
                    agreed += 1
                else:
                    print(f"   !! ours   {normalise(ours[i])[:74]!r}")
                    print(f"   !! tablet {normalise(theirs[i])[:74]!r}")
    print(f"\nlines broken where we predicted: {agreed} of {total}")


MODES = ("read", "wrap")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
