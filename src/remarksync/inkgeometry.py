"""Pure geometry shared by the viewer and preservation exporter."""

from __future__ import annotations

import math

from .rmdata import Stroke


def decimated(stroke: Stroke) -> tuple[list[tuple[float, float]], list[float]]:
    pts, ws = stroke.points, stroke.widths
    if len(pts) < 3:
        return pts, ws
    spacing = max(1.5, stroke.width / 4)
    keep_pts, keep_ws = [pts[0]], [ws[0]]
    lx, ly = pts[0]
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        if abs(x - lx) + abs(y - ly) >= spacing:
            keep_pts.append((x, y))
            keep_ws.append(ws[i])
            lx, ly = x, y
    return keep_pts + [pts[-1]], keep_ws + [ws[-1]]


def stroke_polygons(stroke: Stroke) -> list[list[tuple[float, float]]]:
    """Tapered segments, joint wedges and round caps with consistent winding.

    Fill the compound path once with a nonzero winding rule. This keeps a
    translucent stroke from darkening wherever its own segments overlap.
    """
    pts, ws = decimated(stroke)
    if not pts:
        return []
    radii = [max(0.2, w / 2) for w in ws]
    polygons = []
    normals = []

    def add(polygon):
        area = sum(ax * by - bx * ay for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]))
        polygons.append(polygon[::-1] if area < 0 else polygon)

    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            normals.append(None)
            continue
        nx, ny = -dy / length, dx / length
        normals.append((nx, ny))
        r0, r1 = radii[i - 1], radii[i]
        add([(x0 + nx * r0, y0 + ny * r0), (x1 + nx * r1, y1 + ny * r1),
             (x1 - nx * r1, y1 - ny * r1), (x0 - nx * r0, y0 - ny * r0)])
    for i in range(1, len(pts) - 1):
        a, b = normals[i - 1], normals[i]
        if a is None or b is None or a[0] * b[0] + a[1] * b[1] > 0.985:
            continue
        x, y = pts[i]
        r = radii[i]
        for sign in (1.0, -1.0):
            add([(x, y), (x + sign * a[0] * r, y + sign * a[1] * r),
                 (x + sign * b[0] * r, y + sign * b[1] * r)])
    for i in (0, len(pts) - 1):
        (x, y), r = pts[i], radii[i]
        add([(x + r * math.cos(t * math.pi / 8), y + r * math.sin(t * math.pi / 8)) for t in range(16)])
    return polygons
