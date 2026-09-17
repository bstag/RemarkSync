"""Measure our rendering against the desktop app's, before asserting anything.

Run this to see the numbers; any threshold in the tests beside it comes from
what this prints, rather than from a guess.

    .venv\\Scripts\\python -m tests.real.characterize parity
    .venv\\Scripts\\python -m tests.real.characterize translucency

Two lessons already baked in here, both learned from the first run:

* Only pages whose ``.rm`` is byte-identical in both stores are an oracle.
  The desktop app and the backup can hold different revisions.
* IoU is useless for thin ink at this scale. A 2 px stroke downscaled 3.66x
  lands on half a pixel, so a sub-pixel offset drops overlap to nearly zero
  while the render is in fact correct. Ink *mass* and centroid are stable;
  overlap is not.
"""

from __future__ import annotations

import hashlib
import os
import statistics
import sys
from pathlib import Path

import numpy as np

from remarksync.rmdata import V5_HEADER, decode_page

from .oracle import (Pairing, build_frame, compare, contact_sheet, load_thumbnail, pair_pages, paper_box,
                     render_paper)

RAW = Path(os.environ.get("REMARKSYNC_REAL_BACKUP", r"C:\Backups\reMarkable\raw"))
STORE = Path(os.environ.get("REMARKSYNC_DESKTOP_STORE", str(Path(os.environ["APPDATA"]) / "remarkable" / "desktop")))
DEMO = Path(__file__).resolve().parents[2] / "demo"

# The width above which the v5 decoder currently calls a stroke translucent.
V5_TRANSLUCENT_THRESHOLD = 8.0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trustworthy_pairs() -> list[Pairing]:
    """Paired pages whose .rm is byte-identical in both stores."""
    out = []
    for pair in pair_pages(RAW, STORE):
        twin = STORE / pair.doc.uuid / f"{pair.page.rm_path.stem}.rm"
        if twin.exists() and _sha(twin) == _sha(pair.page.rm_path):
            out.append(pair)
    return out


def is_v5(pair: Pairing) -> bool:
    with open(pair.page.rm_path, "rb") as fh:
        return fh.read(len(V5_HEADER)) == V5_HEADER


def is_comparable(model) -> bool:
    """Only pages made of ink alone can be compared offscreen.

    Qt ships no fonts, so we paint no typed text while the device paints it
    all. A page that mixes ink with typed text therefore looks like we drew
    too little, when the ink itself may be perfect. Excluding any page that
    carries typed characters keeps that artefact out of the numbers.
    """
    return bool(model.strokes) and not any(t.text.strip() for t in model.texts)


def ink_pairs() -> list[Pairing]:
    """Trustworthy pages whose content is ink and nothing else."""
    return [p for p in trustworthy_pairs() if is_comparable(decode_page(p.page.rm_path))]


def parity() -> None:
    """Every trustworthy inked page: how close is our render to the device's?"""
    pairs = ink_pairs()
    print(f"trustworthy inked pages: {len(pairs)} "
          f"(of {len(trustworthy_pairs())} trustworthy; the rest are typed text)\n")
    print(f"{'page':46} {'ver':>4} {'ink%us':>7} {'ink%them':>9} {'ratio':>6} {'centroid dpx':>13}")
    rows = []
    for pair in pairs:
        thumb = load_thumbnail(pair.thumbnail)
        m = compare(render_paper(pair, thumb.size), thumb)
        version = "v5" if is_v5(pair) else "v6"
        rows.append((pair, m, version))
        print(f"{pair.label[:46]:46} {version:>4} {m.ink_ours * 100:7.3f} {m.ink_theirs * 100:9.3f} "
              f"{m.ink_ratio:6.2f} {m.centroid_delta_px:13.1f}")

    for version in ("v5", "v6"):
        subset = [(p, m) for p, m, v in rows if v == version and m.ink_theirs]
        if not subset:
            continue
        ratios = sorted(m.ink_ratio for _, m in subset)
        deltas = sorted(m.centroid_delta_px for _, m in subset if m.ink_ours)
        print(f"\n{version}: {len(subset)} pages")
        print(f"   ink ratio  median {statistics.median(ratios):.3f}  min {ratios[0]:.3f}  max {ratios[-1]:.3f}")
        if deltas:
            print(f"   centroid   median {statistics.median(deltas):.1f} px  "
                  f"p90 {deltas[int(len(deltas) * 0.9)]:.1f} px  max {deltas[-1]:.1f} px")

    print("\nfurthest ten by centroid:")
    for pair, m, version in sorted(rows, key=lambda r: -r[1].centroid_delta_px)[:10]:
        print(f"  {m.centroid_delta_px:8.1f} px  ink x{m.ink_ratio:5.2f}  {version}  {pair.label}")


def translucency() -> None:
    """A/B the proposed v5 change against the oracle, on v5 pages only.

    Variant A is today's behaviour: strokes wider than 8 px are translucent.
    Variant B forces them opaque, which is what mapping the pen id would
    have done. Measured inside the thick strokes' own bounding box, because
    that is the only place the two variants differ.
    """
    candidates = []
    for pair in trustworthy_pairs():
        if not is_v5(pair):
            continue
        frame = build_frame(pair)
        thick = [s for s in frame.page.strokes if s.width > V5_TRANSLUCENT_THRESHOLD]
        if thick:
            candidates.append((pair, frame, thick))

    print(f"trustworthy v5 pages with wide strokes: {len(candidates)}\n")
    if not candidates:
        print("none — this corpus cannot settle the v5 question by picture")
        return

    print(f"{'page':40} {'n':>3} {'grey: theirs':>12} {'A translucent':>14} {'B opaque':>9}  closer")
    wins = {"A": 0, "B": 0}
    for pair, frame, thick in sorted(candidates, key=lambda c: -len(c[2])):
        thumb = load_thumbnail(pair.thumbnail)
        a = render_paper(pair, thumb.size, frame=frame)
        b = render_paper(pair, thumb.size, force_opaque_above=V5_TRANSLUCENT_THRESHOLD)

        points = [p for s in thick for p in s.points]
        x0, y0, x1, y1 = paper_box(frame, points, thumb.size)
        crop = (slice(y0, y1), slice(x0, x1))
        grey_them = float(np.asarray(thumb, dtype=np.uint8)[crop].mean())
        grey_a = float(np.asarray(a, dtype=np.uint8)[crop].mean())
        grey_b = float(np.asarray(b, dtype=np.uint8)[crop].mean())

        closer = "A" if abs(grey_a - grey_them) <= abs(grey_b - grey_them) else "B"
        wins[closer] += 1
        print(f"{pair.label[:40]:40} {len(thick):3} {grey_them:12.1f} {grey_a:14.1f} {grey_b:9.1f}  {closer}")

        if len(thick) == max(len(c[2]) for c in candidates):
            out = contact_sheet(
                [("desktop app", thumb.crop((x0, y0, x1, y1))),
                 ("ours: translucent (today)", a.crop((x0, y0, x1, y1))),
                 ("ours: forced opaque", b.crop((x0, y0, x1, y1)))],
                DEMO / "v5_translucency_ab.png",
            )
            full = contact_sheet(
                [("desktop app", thumb), ("ours: translucent (today)", a), ("ours: forced opaque", b)],
                DEMO / "v5_translucency_ab_full.png",
            )
            print(f"    wrote {out}\n    wrote {full}")

    print(f"\ncloser to the device: A (translucent, today) {wins['A']}  |  B (forced opaque) {wins['B']}")
    print("Lower grey = darker. Opaque ink should read darker than translucent.")


def sheets() -> None:
    """Side-by-side pictures: the v6 pipeline agreeing, and v5 not."""
    best_v6 = worst_v5 = None
    for pair in ink_pairs():
        thumb = load_thumbnail(pair.thumbnail)
        m = compare(render_paper(pair, thumb.size), thumb)
        if not m.ink_theirs:
            continue
        if is_v5(pair):
            if worst_v5 is None or m.ink_ratio < worst_v5[1].ink_ratio:
                worst_v5 = (pair, m)
        else:
            score = abs(1 - m.ink_ratio) + m.centroid_delta_px / 100
            if best_v6 is None or score < best_v6[2]:
                best_v6 = (pair, m, score)

    if best_v6:
        pair, m, _ = best_v6
        thumb = load_thumbnail(pair.thumbnail)
        out = contact_sheet([("desktop app", thumb), ("RemarkSync", render_paper(pair, thumb.size))],
                            DEMO / "parity_v6_agrees.png")
        print(f"v6 agreement: {pair.label}  ink x{m.ink_ratio:.3f}  centroid {m.centroid_delta_px:.1f} px")
        print(f"  wrote {out}")

    if worst_v5:
        pair, m = worst_v5
        thumb = load_thumbnail(pair.thumbnail)
        out = contact_sheet([("desktop app", thumb), ("RemarkSync (v5)", render_paper(pair, thumb.size))],
                            DEMO / "parity_v5_thin.png")
        print(f"v5 worst:     {pair.label}  ink x{m.ink_ratio:.3f}  centroid {m.centroid_delta_px:.1f} px")
        print(f"  wrote {out}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parity"
    {"parity": parity, "translucency": translucency, "sheets": sheets}[mode]()
