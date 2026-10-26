"""Overlap of convex polygons.

Used to score a mask against the hand-drawn overlay. Coordinates are a plain
cartesian plane; the rack photo uses pixels with the origin at the top-left.
Polygons are implicitly closed (do not repeat the first point).

The clip is Sutherland–Hodgman, which is correct for convex polygons. A
concave or self-crossing ring is rejected rather than given a silent score.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "point_in_convex_polygon",
    "polygon_area",
    "polygon_iou",
    "polygon_is_convex",
]

_EPS = 1e-9


def polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    """Absolute shoelace area of a polygon."""

    return abs(_signed_area(_points(polygon)))


def polygon_is_convex(polygon: Sequence[Sequence[float]]) -> bool:
    """True when every turn goes the same way and the ring has area."""

    pts = _points(polygon)
    if len(pts) < 3:
        return False
    sign = 0
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        cx, cy = pts[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) <= _EPS:
            continue
        turn = 1 if cross > 0 else -1
        if sign == 0:
            sign = turn
        elif turn != sign:
            return False
    return sign != 0


def point_in_convex_polygon(
    point: Sequence[float], polygon: Sequence[Sequence[float]]
) -> bool:
    """True if ``point`` is inside or on the boundary of a convex polygon."""

    if len(point) != 2:
        raise ValueError("point must be [x, y]")
    if not polygon_is_convex(polygon):
        raise ValueError("polygon is not convex")
    x = float(point[0])
    y = float(point[1])
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("point must be finite")
    pts = _points(polygon)
    sign = 0
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if abs(cross) <= _EPS:
            continue
        turn = 1 if cross > 0 else -1
        if sign == 0:
            sign = turn
        elif turn != sign:
            return False
    return True


def polygon_iou(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> float:
    """Intersection area divided by union area. Both rings must be convex.

    Identical polygons score 1. Polygons that only share an edge score 0.
    """

    a = _ccw(_points(left))
    b = _ccw(_points(right))
    if not polygon_is_convex(a) or not polygon_is_convex(b):
        raise ValueError("polygon is not convex")
    area_a = abs(_signed_area(a))
    area_b = abs(_signed_area(b))
    if area_a <= _EPS or area_b <= _EPS:
        raise ValueError("polygon has no area")
    overlap = abs(_signed_area(_clip(a, b)))
    if overlap <= _EPS:
        return 0.0
    if abs(area_a - area_b) <= _EPS and overlap >= min(area_a, area_b) - _EPS:
        return 1.0
    union = area_a + area_b - overlap
    if union <= _EPS:
        raise ValueError("polygon has no area")
    return overlap / union


def _points(polygon: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for point in polygon:
        if len(point) != 2:
            raise ValueError("polygon point must be [x, y]")
        x = float(point[0])
        y = float(point[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("polygon point must be finite")
        pts.append((x, y))
    return pts


def _signed_area(pts: Sequence[tuple[float, float]]) -> float:
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _ccw(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if _signed_area(pts) < 0:
        return list(reversed(pts))
    return list(pts)


def _left_of(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    ex, ey = end[0] - start[0], end[1] - start[1]
    px, py = point[0] - start[0], point[1] - start[1]
    return ex * py - ey * px >= -_EPS


def _edge_hit(
    p: tuple[float, float],
    s: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    """Where segment ``p``→``s`` crosses the infinite line through ``a``→``b``."""

    rx, ry = s[0] - p[0], s[1] - p[1]
    qx, qy = b[0] - a[0], b[1] - a[1]
    denom = rx * qy - ry * qx
    if abs(denom) <= _EPS:
        return s
    t = ((a[0] - p[0]) * qy - (a[1] - p[1]) * qx) / denom
    return (p[0] + t * rx, p[1] + t * ry)


def _clip_edge(
    subject: list[tuple[float, float]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    if not subject:
        return []
    output: list[tuple[float, float]] = []
    prev = subject[-1]
    for cur in subject:
        cur_in = _left_of(cur, start, end)
        prev_in = _left_of(prev, start, end)
        if cur_in:
            if not prev_in:
                output.append(_edge_hit(prev, cur, start, end))
            output.append(cur)
        elif prev_in:
            output.append(_edge_hit(prev, cur, start, end))
        prev = cur
    return _dedup(output)


def _clip(
    subject: list[tuple[float, float]], clip: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    output = subject
    n = len(clip)
    for i in range(n):
        output = _clip_edge(output, clip[i], clip[(i + 1) % n])
        if not output:
            return []
    return output


def _dedup(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not pts:
        return []
    kept = [pts[0]]
    for point in pts[1:]:
        if abs(point[0] - kept[-1][0]) > _EPS or abs(point[1] - kept[-1][1]) > _EPS:
            kept.append(point)
    if (
        len(kept) > 1
        and abs(kept[0][0] - kept[-1][0]) <= _EPS
        and abs(kept[0][1] - kept[-1][1]) <= _EPS
    ):
        kept.pop()
    return kept
