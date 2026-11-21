"""Is the desktop app's render actually an oracle for *our* copy of the page?

Two ways this harness could be measuring nothing:

1. The two stores hold the same page at different revisions, so the
   thumbnail shows content our backup does not have (or vice versa).
2. Ink survives the 3.66x downscale too faintly to clear the ink
   threshold, leaving both masks near-empty and the overlap meaningless.

Both are checked here before any assertion is built on top.
"""

from __future__ import annotations

import statistics

import numpy as np

from .paths import RAW, STORE, sha256_of
from .oracle import INK_LEVEL, MARK_LEVEL, load_thumbnail, pair_pages, render_paper



def revision_agreement() -> list:
    """Do both stores hold byte-identical .rm for the pages we compare?"""
    pairs = pair_pages(RAW, STORE)
    same = differ = missing = 0
    comparable = []
    for pair in pairs:
        twin = STORE / pair.doc.uuid / f"{pair.page.rm_path.stem}.rm"
        if not twin.exists():
            missing += 1
            continue
        if sha256_of(twin) == sha256_of(pair.page.rm_path):
            same += 1
            comparable.append(pair)
        else:
            differ += 1
    print(f"paired pages: {len(pairs)}")
    print(f"  byte-identical .rm in both stores : {same}   <- the only trustworthy oracle set")
    print(f"  different revision               : {differ}")
    print(f"  no .rm in the desktop store      : {missing}")
    return comparable


def threshold_sanity(comparable: list) -> None:
    """How much ink clears the threshold once downscaled, in each render?"""
    print(f"\n{'page':46} {'ink%us':>7} {'ink%them':>9} {'mark%us':>8} {'mark%them':>10}")
    ours_ink, theirs_ink = [], []
    for pair in comparable[:25]:
        thumb = load_thumbnail(pair.thumbnail)
        ours = render_paper(pair, thumb.size)
        a = np.asarray(ours, dtype=np.uint8)
        b = np.asarray(thumb, dtype=np.uint8)
        ink_a, ink_b = float((a <= INK_LEVEL).mean()), float((b <= INK_LEVEL).mean())
        ours_ink.append(ink_a)
        theirs_ink.append(ink_b)
        print(f"{pair.label[:46]:46} {ink_a * 100:7.3f} {ink_b * 100:9.3f} "
              f"{float((a <= MARK_LEVEL).mean()) * 100:8.3f} {float((b <= MARK_LEVEL).mean()) * 100:10.3f}")
    if ours_ink:
        print(f"\nmedian ink fraction  ours {statistics.median(ours_ink) * 100:.3f}%  "
              f"theirs {statistics.median(theirs_ink) * 100:.3f}%")
        print("If either median is near zero the mask is noise and IoU means nothing.")


def histogram(comparable: list) -> None:
    """Where does the grey actually sit? Picks the threshold honestly."""
    if not comparable:
        return
    pair = max(comparable, key=lambda p: p.thumbnail.stat().st_size)
    thumb = load_thumbnail(pair.thumbnail)
    ours = render_paper(pair, thumb.size)
    print(f"\ngrey distribution on {pair.label!r}")
    print(f"{'bucket':>12} {'ours':>8} {'theirs':>8}")
    a = np.asarray(ours, dtype=np.uint8).ravel()
    b = np.asarray(thumb, dtype=np.uint8).ravel()
    edges = [0, 64, 128, 160, 192, 216, 232, 244, 252, 256]
    for lo, hi in zip(edges, edges[1:]):
        pa = float(((a >= lo) & (a < hi)).mean()) * 100
        pb = float(((b >= lo) & (b < hi)).mean()) * 100
        print(f"{f'{lo}-{hi - 1}':>12} {pa:7.2f}% {pb:7.2f}%")


if __name__ == "__main__":
    comparable = revision_agreement()
    threshold_sanity(comparable)
    histogram(comparable)
