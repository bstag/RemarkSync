"""Compile Markdown, put it on the tablet, and ask the tablet what it made of it.

The other two oracles check reading: the desktop app's renders and the tablet's
PDF export both tell us whether we understood a page the device wrote. This one
checks writing. A compiled ``.rmdoc`` goes up over the USB web interface, and the
tablet's own export of it comes back, which answers three questions nothing local
can:

* does the archive import at all;
* does the text arrive as text -- the export carries real words, with ligatures,
  rather than an image;
* does the device break lines where :mod:`remarksync.textmetrics` predicts?

The last is the interesting one. The compiler never wraps: it hands whole
paragraphs over and the device breaks them, so its export is a direct test of
our glyph-advance model.

    python -m tests.real.compile_roundtrip build     # compile the fixture
    python -m tests.real.compile_roundtrip upload    # POST it to the tablet
    python -m tests.real.compile_roundtrip check     # fetch the export and diff

``upload`` writes to the tablet. It creates a new notebook and changes nothing
else, and the notebook can be deleted on the device afterwards.

Measured 2026-09-18, software 3.27 on a Paper Pro: imported (HTTP 201), came
back as 3 pages of real text at the 1403x1873 paper the archive declares, our
pagination preserved, and 32 of 34 lines broken exactly where we predicted. The
8 px wrap inset (textmetrics.TEXT_WRAP_INSET) took that to 34 of 34, and the
comparison is now made glyph by glyph rather than word by word, so a ligature
the export cannot name no longer reads as agreement it has not earned.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .exported import compare, drawn_lines
from .paths import work_dir
from .tablet import document_id, export_pdf, upload as post_archive

WORK = work_dir("REMARKSYNC_ROUNDTRIP", "rs-roundtrip")
TITLE = "RemarkSync compile test"
SOURCE = Path(__file__).with_name("compile_fixture.md")
ARCHIVE = WORK / "remarksync-compile-test.rmdoc"


def build() -> None:
    from remarksync.mdcompile import markdown_to_rmdoc

    WORK.mkdir(parents=True, exist_ok=True)
    out = markdown_to_rmdoc(SOURCE.read_text(encoding="utf-8"), TITLE, ARCHIVE)
    print(f"wrote {out} ({out.stat().st_size} bytes)")

    from remarksync.backup import open_rmdoc
    from remarksync.rmdata import PAGE_HEIGHT, decode_page

    doc = open_rmdoc(out, extract_dir=WORK / "extract")
    print(f"{doc.name!r}: {len(doc.pages)} pages")
    for page in doc.pages:
        model = decode_page(page.rm_path)
        block = model.texts[0]
        bottom = block.pos_y + block.height
        print(f"   page {page.index + 1}: paper={model.paper_size} lines={len(block.lines)} "
              f"text bottom {bottom:.0f} of {PAGE_HEIGHT:.0f} "
              f"{'(overflows!)' if bottom > PAGE_HEIGHT else ''}")


def upload() -> None:
    if not ARCHIVE.exists():
        print("no archive yet; run 'build' first")
        return
    print(post_archive(ARCHIVE))


def check() -> None:
    """Fetch the tablet's export of what we uploaded and compare line breaks."""
    import pymupdf

    from remarksync.backup import open_rmdoc
    from remarksync.layout import PDF_SCALE
    from remarksync.rmdata import decode_page
    from remarksync.textmetrics import style

    uuid = document_id(TITLE)
    if uuid is None:
        print(f"the tablet has no document called {TITLE!r}; run 'upload' first")
        return
    pdf_path = export_pdf(uuid, WORK)
    print(f"the tablet's export: {pdf_path.stat().st_size} bytes")

    doc = open_rmdoc(ARCHIVE, extract_dir=WORK / "extract")
    agreed = compared = 0
    with pymupdf.open(pdf_path) as pdf:
        if pdf.page_count != len(doc.pages):
            print(f"!! we made {len(doc.pages)} pages, the tablet shows {pdf.page_count}")
        for page_ref in doc.pages:
            if page_ref.index >= pdf.page_count:
                break
            model = decode_page(page_ref.rm_path)
            ours = [(style(ln.style), ln.text) for ln in model.texts[0].lines if ln.text.strip()]
            page = pdf.load_page(page_ref.index)
            theirs = drawn_lines(pdf, page_ref.index)
            print(f"\npage {page_ref.index + 1}: {page.rect.width * PDF_SCALE:.0f}x"
                  f"{page.rect.height * PDF_SCALE:.0f} px")
            a, c = compare(ours, theirs, f"page {page_ref.index + 1}")
            agreed += a
            compared += c
    print(f"\nlines broken where we predicted: {agreed} of {compared}")


MODES = ("build", "upload", "check")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
