"""Cut a page's ink into text lines and render them: the input a
line-based handwriting recogniser wants.

We hold the strokes, not a picture, which makes line finding easy and
exact: the vertical centres of strokes cluster tightly on each written
line, so a smoothed histogram of centres has one peak per line and the
valleys between peaks are the cut points. Ascenders and descenders of
neighbouring lines overlap in extent but not in centre. No Qt here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .rmdata import PageModel, Stroke


@dataclass
class InkLine:
    """One written line: its strokes and their bounding box, page coordinates."""

    strokes: list[Stroke]
    top: float
    bottom: float
    left: float
    right: float

    @property
    def height(self) -> float:
        return self.bottom - self.top


def cut_lines(model: PageModel, bin_px: float = 4.0, smooth_px: float = 24.0) -> list[InkLine]:
    """Group a page's pen strokes into written lines, top to bottom."""
    strokes = [s for s in model.strokes if not s.highlighter and len(s.points) >= 2]
    if not strokes:
        return []
    centres = []
    for s in strokes:
        ys = [y for _, y in s.points]
        centres.append((min(ys) + max(ys)) / 2)
    lo, hi = min(centres), max(centres)
    nbins = int((hi - lo) / bin_px) + 1
    hist = [0.0] * nbins
    for c, s in zip(centres, strokes):
        hist[int((c - lo) / bin_px)] += max(1.0, len(s.points) ** 0.5)  # long strokes weigh more than dots
    radius = int(smooth_px / bin_px)
    smooth = [sum(hist[max(0, i - radius) : i + radius + 1]) for i in range(nbins)]
    floor = max(smooth) * 0.05
    peaks = [i for i in range(nbins) if smooth[i] > floor and smooth[i] >= max(smooth[max(0, i - radius) : i + radius + 1])]
    kept: list[int] = []
    for pk in peaks:  # one peak per line: drop a peak within half a line of the previous
        if kept and (pk - kept[-1]) * bin_px < smooth_px:
            continue
        kept.append(pk)
    boundaries = [lo + min(range(a, b + 1), key=lambda i: smooth[i]) * bin_px for a, b in zip(kept, kept[1:])]
    lines = [InkLine([], math.inf, -math.inf, math.inf, -math.inf) for _ in kept]
    for c, s in zip(centres, strokes):
        ln = lines[sum(1 for bnd in boundaries if c > bnd)]
        ln.strokes.append(s)
        xs = [x for x, _ in s.points]
        ys = [y for _, y in s.points]
        ln.left, ln.right = min(ln.left, min(xs)), max(ln.right, max(xs))
        ln.top, ln.bottom = min(ln.top, min(ys)), max(ln.bottom, max(ys))
    return [ln for ln in lines if ln.strokes]


def render_line(line: InkLine, height: int = 128, pad: int = 12):
    """The line as a black-on-white PIL image scaled to ``height`` px."""
    from PIL import Image, ImageDraw

    scale = (height - 2 * pad) / max(1.0, line.height)
    width = int((line.right - line.left) * scale) + 2 * pad
    img = Image.new("L", (max(width, 32), height), 255)
    draw = ImageDraw.Draw(img)
    for s in line.strokes:
        pts = [((x - line.left) * scale + pad, (y - line.top) * scale + pad) for x, y in s.points]
        if len(pts) >= 2:
            draw.line(pts, fill=0, width=max(1, int(s.width * scale)), joint="curve")
    return img
