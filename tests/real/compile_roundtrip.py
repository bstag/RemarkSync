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
pagination preserved, and 32 of 34 lines broken exactly where we predicted.
"""

from __future__ import annotations

import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid as uuidlib
from pathlib import Path

WORK = Path(os.environ.get("REMARKSYNC_ROUNDTRIP", str(Path(os.environ["TEMP"]) / "rs-roundtrip")))
HOST = os.environ.get("REMARKSYNC_TABLET", "10.11.99.1")
TITLE = "RemarkSync compile test"
SOURCE = Path(__file__).with_name("compile_fixture.md")
ARCHIVE = WORK / "remarksync-compile-test.rmdoc"

# The list markers the device draws into the same row as the text, and the
# ligatures its PDF carries as one glyph. Neither is a wrapping difference.
MARKERS = ("\u2022 ", "\u25e6 ", "1. ", "a. ", "\u2610 ", "\u2611 ")
LIGATURES = (("\ufb01", "fi"), ("\ufb02", "fl"), ("\ufb00", "ff"))


def normalise(line: str) -> str:
    for lig, plain in LIGATURES:
        line = line.replace(lig, plain)
    for marker in MARKERS:
        if line.startswith(marker):
            line = line[len(marker):]
    return line.strip()


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
    """POST the archive the way the web interface does: one multipart part
    named 'file'. Its own code accepts pdf, rmdoc and epub."""
    if not ARCHIVE.exists():
        print("no archive yet; run 'build' first")
        return
    boundary = f"----remarksync{uuidlib.uuid4().hex}"
    content_type = mimetypes.guess_type(ARCHIVE.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{ARCHIVE.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        ARCHIVE.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"http://{HOST}/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Origin": f"http://{HOST}", "Referer": f"http://{HOST}/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            print(f"HTTP {response.status}: {response.read(200).decode('utf-8', 'replace')}")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read(400).decode('utf-8', 'replace')}")


def _find_uploaded() -> str | None:
    import json

    request = urllib.request.Request(f"http://{HOST}/documents/", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        for entry in json.loads(response.read()):
            if entry.get("VisibleName") == TITLE:
                return entry.get("ID")
    return None


def check() -> None:
    """Fetch the tablet's export of what we uploaded and compare line breaks."""
    import pymupdf

    from remarksync.backup import open_rmdoc
    from remarksync.layout import PDF_SCALE
    from remarksync.rmdata import decode_page

    uuid = _find_uploaded()
    if uuid is None:
        print(f"the tablet has no document called {TITLE!r}; run 'upload' first")
        return
    pdf_path = WORK / f"{uuid}.pdf"
    with urllib.request.urlopen(f"http://{HOST}/download/{uuid}/placeholder", timeout=180) as response:
        pdf_path.write_bytes(response.read())
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
            ours = [ln.text for ln in model.texts[0].lines if ln.text.strip()]
            page = pdf.load_page(page_ref.index)
            rows: dict[int, list] = {}
            for x0, _y0, _x1, y1, word, *_ in page.get_text("words"):
                rows.setdefault(round(y1), []).append((x0, word))
            theirs = [" ".join(w for _, w in sorted(v)) for _, v in sorted(rows.items())]
            print(f"\npage {page_ref.index + 1}: {page.rect.width * PDF_SCALE:.0f}x"
                  f"{page.rect.height * PDF_SCALE:.0f} px, we predict {len(ours)} lines, "
                  f"the tablet drew {len(theirs)}")
            for i in range(min(len(ours), len(theirs))):
                compared += 1
                if normalise(ours[i]) == normalise(theirs[i]):
                    agreed += 1
                else:
                    print(f"   !! ours   {normalise(ours[i])[:74]!r}")
                    print(f"   !! tablet {normalise(theirs[i])[:74]!r}")
    print(f"\nlines broken where we predicted: {agreed} of {compared}")


MODES = ("build", "upload", "check")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
