"""Compare our render against a PDF the tablet exported of the same page.

The desktop app's thumbnails settle placement but stop at 384x512, where one
pixel is 3.66 tablet pixels. The tablet's USB web interface hands out a PDF
of a whole document, and that PDF holds the ink as vector paths, so it can be
rasterised at 1404 px wide -- 1:1 with tablet pixels. That is the resolution
the sub-pixel claims need.

Fetching is read-only: a GET per document, saved outside the repo.

    .venv\\Scripts\\python -m tests.real.device_pdf fetch
    .venv\\Scripts\\python -m tests.real.device_pdf compare
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from remarksync.backup import iter_docs, scan_root
from remarksync.rmdata import PAGE_HEIGHT, PAGE_WIDTH

from .oracle import INK_LEVEL, Pairing, contact_sheet, render_paper

RAW = Path(os.environ.get("REMARKSYNC_REAL_BACKUP", r"C:\Backups\reMarkable\raw"))
CACHE = Path(os.environ.get("REMARKSYNC_DEVICE_PDF", str(Path(os.environ["TEMP"]) / "rs-device-pdf")))
DEMO = Path(__file__).resolve().parents[2] / "demo"
HOST = os.environ.get("REMARKSYNC_TABLET", "10.11.99.1")

# The pages whose ink mass disagrees with the desktop app's render.
SUSPECTS = ["Kemplay Dissertation Notebook", "Quick sheets", "Master to-do list", "2026", "Start 9/26"]


def documents() -> dict[str, object]:
    return {doc.name: doc for doc in iter_docs(scan_root(RAW))}


def fetch(names: list[str] | None = None) -> None:
    """GET each document as a PDF from the tablet. Read-only, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    docs = documents()
    for name in names or SUSPECTS:
        doc = docs.get(name)
        if doc is None:
            print(f"  {name}: not in the backup, skipped")
            continue
        dest = CACHE / f"{doc.uuid}.pdf"
        if dest.exists():
            print(f"  {name}: cached ({dest.stat().st_size} bytes)")
            continue
        url = f"http://{HOST}/download/{doc.uuid}/placeholder"
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                dest.write_bytes(response.read())
            print(f"  {name}: {dest.stat().st_size} bytes")
        except Exception as exc:
            print(f"  {name}: FAILED {type(exc).__name__}: {exc}")


def device_page(pdf: Path, index: int, size: tuple[int, int]) -> Image.Image | None:
    """One page of an exported PDF, rasterised to ``size`` in greyscale."""
    with pymupdf.open(pdf) as doc:
        if index >= doc.page_count:
            return None
        page = doc.load_page(index)
        zoom_x = size[0] / page.rect.width
        zoom_y = size[1] / page.rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom_x, zoom_y), colorspace=pymupdf.csGRAY, alpha=False)
        return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _metrics(ours: Image.Image, theirs: Image.Image) -> dict:
    a = np.asarray(ours, dtype=np.uint8)
    b = np.asarray(theirs, dtype=np.uint8)
    mask_a, mask_b = a <= INK_LEVEL, b <= INK_LEVEL
    out = {"ink_ours": float(mask_a.mean()), "ink_theirs": float(mask_b.mean())}
    out["ratio"] = out["ink_ours"] / out["ink_theirs"] if out["ink_theirs"] else float("inf")
    if mask_a.any() and mask_b.any():
        ay, ax = np.argwhere(mask_a).mean(axis=0)
        by, bx = np.argwhere(mask_b).mean(axis=0)
        out["dx"] = float(ax - bx)
        out["dy"] = float(ay - by)
        out["centroid"] = float(np.hypot(ax - bx, ay - by))
        union = np.logical_or(mask_a, mask_b).sum()
        out["iou"] = float(np.logical_and(mask_a, mask_b).sum() / union)
    return out


def compare() -> None:
    """At 1:1 with tablet pixels, do we agree with the tablet's own export?"""
    size = (int(PAGE_WIDTH), int(PAGE_HEIGHT))
    docs = documents()
    print(f"comparing at {size[0]}x{size[1]} -- one image pixel is one tablet pixel\n")
    print(f"{'page':40} {'ink%us':>7} {'ink%dev':>8} {'ratio':>6} {'dx':>7} {'dy':>7} {'IoU':>6}")
    rows = []
    for name in SUSPECTS:
        doc = docs.get(name)
        pdf = CACHE / f"{doc.uuid}.pdf" if doc else None
        if doc is None or pdf is None or not pdf.exists():
            continue
        with pymupdf.open(pdf) as opened:
            exported = opened.page_count
        if exported != len(doc.pages):
            print(f"{name[:40]:40}  page counts differ: backup {len(doc.pages)}, export {exported}")
        for page in doc.pages[: min(3, len(doc.pages))]:
            theirs = device_page(pdf, page.index, size)
            if theirs is None:
                continue
            pair = Pairing(doc, page, pdf)
            ours = render_paper(pair, size)
            m = _metrics(ours, theirs)
            rows.append((f"{name} p{page.index + 1}", m))
            print(f"{name[:34]} p{page.index + 1:<4} {m['ink_ours'] * 100:7.3f} {m['ink_theirs'] * 100:8.3f} "
                  f"{m['ratio']:6.2f} {m.get('dx', 0):7.1f} {m.get('dy', 0):7.1f} {m.get('iou', 0):6.3f}")
            if page.index == 0:
                contact_sheet([("tablet PDF export", theirs.resize((468, 624), Image.LANCZOS)),
                               ("RemarkSync", ours.resize((468, 624), Image.LANCZOS))],
                              DEMO / f"device_pdf_{doc.uuid[:8]}.png")
    if rows:
        ratios = sorted(m["ratio"] for _, m in rows)
        print(f"\nink ratio median {statistics.median(ratios):.3f}  min {ratios[0]:.3f}  max {ratios[-1]:.3f}")
        print(f"wrote sheets to {DEMO}")





def erasers() -> None:
    """Which v5 pen ids are the eraser?

    The tablet's own export shows clean handwriting where we paint blobs, so
    some pen id is an eraser we are drawing as ink. ERASER_TOOLS only ever
    matched v6 names; v5 strokes are tagged V5_PEN_<id> and never match.

    Rather than guess from a spec that does not fit this file variant, drop
    each candidate and see which choice brings our ink mass to the device's.
    """
    from remarksync.layout import lay_out
    from remarksync.rmdata import decode_page

    size = (int(PAGE_WIDTH), int(PAGE_HEIGHT))
    docs = documents()

    def render_without(pair, dropped: set[str]) -> Image.Image:
        model = decode_page(pair.page.rm_path)
        model.strokes = [s for s in model.strokes if s.tool not in dropped]
        frame = lay_out(model, template=pair.page.template, page_index=pair.page.index)
        return render_paper(pair, size, frame=frame)

    candidates = [set(), {"V5_PEN_2"}, {"V5_PEN_5"}, {"V5_PEN_2", "V5_PEN_5"}, {"V5_PEN_0"}]
    print(f"{'page':34} " + " ".join(f"{'drop ' + (','.join(sorted(c)) or 'nothing'):>22}" for c in candidates))
    for name in SUSPECTS:
        doc = docs.get(name)
        pdf = CACHE / f"{doc.uuid}.pdf" if doc else None
        if doc is None or pdf is None or not pdf.exists():
            continue
        for page in doc.pages[:2]:
            if page.rm_path is None:
                continue
            with open(page.rm_path, "rb") as fh:
                if fh.read(33) != b"reMarkable .lines file, version=5":
                    continue
            theirs = device_page(pdf, page.index, size)
            if theirs is None:
                continue
            pair = Pairing(doc, page, pdf)
            cells = []
            for dropped in candidates:
                m = _metrics(render_without(pair, dropped), theirs)
                cells.append(f"x{m['ratio']:5.2f} IoU {m.get('iou', 0):.3f}")
            print(f"{(name[:28] + ' p' + str(page.index + 1)):34} " + " ".join(f"{c:>22}" for c in cells))
    print("\nThe column whose ratio is nearest 1.00 with the highest IoU names the eraser.")






def freshness() -> None:
    """Is the backup still current with the tablet?

    Tier 2 renders the backup's ``.rm`` and compares it to a PDF the tablet
    exported just now. If a document has been drawn on since the backup was
    taken, those are two different pages and any disagreement is meaningless.
    Tier 1 has the same hazard and answers it by hashing both stores; here the
    tablet's own document list carries a modification time to compare.
    """
    import datetime as dt
    import urllib.request

    try:
        request = urllib.request.Request(f"http://{HOST}/documents/", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            listing = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        print(f"cannot read the tablet's document list: {type(exc).__name__}: {exc}")
        return

    on_tablet = {}
    for entry in listing:
        stamp = entry.get("ModifiedClient") or ""
        try:
            when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        on_tablet[entry.get("VisibleName")] = when

    print(f"tablet lists {len(on_tablet)} items at this level\n")
    print(f"{'document':34} {'backup lastModified':>22} {'tablet ModifiedClient':>22} {'':>6}")
    stale = []
    for name, doc in documents().items():
        when = on_tablet.get(name)
        if when is None:
            continue  # nested documents are not in the root listing
        if not doc.last_modified:
            print(f"{name[:34]:34} {'(none)':>22} {when.isoformat():>22} {'?':>6}")
            continue
        local = dt.datetime.fromtimestamp(int(doc.last_modified) / 1000, dt.timezone.utc)
        drift = abs((local - when).total_seconds())
        verdict = "ok" if drift <= 2 else "STALE"
        if verdict == "STALE":
            stale.append((name, drift))
        print(f"{name[:34]:34} {local.isoformat(timespec='seconds'):>22} "
              f"{when.isoformat(timespec='seconds'):>22} {verdict:>6}")

    if stale:
        print("\nthe backup is behind the tablet for:")
        for name, drift in sorted(stale, key=lambda s: -s[1]):
            print(f"   {name}  ({drift / 3600:.1f} h behind)")
        print("\nPages from these documents cannot be compared until the backup is pulled again.")
    else:
        print("\nevery listed document matches; tier 2 comparisons are sound")



# Dispatch by name so that adding a mode above needs no edit down here.
MODES = ("fetch", "compare", "erasers", "freshness")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; try one of {', '.join(MODES)}")
    globals()[mode]()
