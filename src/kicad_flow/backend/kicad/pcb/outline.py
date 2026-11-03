"""Factual contour reconstruction from native-observed outline primitives."""

from __future__ import annotations

import math

from kicad_flow.pcb.types import Graphic, Point

from ._geometry import _circle_through, _point_in_polygon, _signed_area


def polygon(
    graphics: list[Graphic], inset: float, max_error: float
) -> tuple[Point, ...]:
    """Resolve one outside contour with explicit curve tolerance and inset."""
    if (
        not math.isfinite(inset)
        or inset < 0
        or not math.isfinite(max_error)
        or max_error <= 0
    ):
        raise ValueError("inset must be non-negative; max_error must be positive")
    chains: list[list[Point]] = []
    for shape in graphics:
        points = list(shape.points)
        if shape.kind == "rectangle":
            a, b = points
            points = [a, Point(b.x, a.y), b, Point(a.x, b.y), a]
        elif shape.kind in ("circle", "arc"):
            if shape.kind == "circle":
                centre, rim = points
                radius = math.dist((centre.x, centre.y), (rim.x, rim.y))
                start = math.atan2(rim.y - centre.y, rim.x - centre.x)
                sweep = 2 * math.pi
            else:
                values = _circle_through(*points)
                if values is None:
                    raise ValueError("outline arc points are collinear")
                centre, radius = values
                cx, cy = centre.x, centre.y
                start, mid, end = [math.atan2(p.y - cy, p.x - cx) for p in points]
                sweep = (end - start) % (2 * math.pi)
                if (mid - start) % (2 * math.pi) > sweep:
                    sweep -= 2 * math.pi
            step = 2 * math.acos(max(-1.0, 1 - min(max_error / 2, radius) / radius))
            count = max(3, math.ceil(abs(sweep) / step))
            sampled = [
                Point(
                    centre.x + radius * math.cos(start + sweep * i / count),
                    centre.y + radius * math.sin(start + sweep * i / count),
                )
                for i in range(count + 1)
            ]
            if shape.kind == "arc":
                sampled[0], sampled[-1] = points[0], points[-1]
            else:
                sampled[-1] = sampled[0]
            points = sampled
        elif shape.kind == "polygon":
            points.append(points[0])
        chains.append(points)
    rings = []

    def same(a: Point, b: Point) -> bool:
        return math.hypot(a.x - b.x, a.y - b.y) <= 1e-6

    while chains:
        chain = chains.pop(0)
        while not same(chain[0], chain[-1]):
            matches = [
                (i, reverse)
                for i, other in enumerate(chains)
                for reverse in (False, True)
                if same(chain[-1], other[-1] if reverse else other[0])
            ]
            if len(matches) != 1:
                raise ValueError("Edge.Cuts is open or has an ambiguous branch")
            index, reverse = matches[0]
            following = chains.pop(index)
            chain.extend((list(reversed(following)) if reverse else following)[1:])
        if len(chain) < 4:
            raise ValueError("outline contour has fewer than three vertices")
        rings.append(chain[:-1])
    if not rings:
        raise ValueError("board has no closed Edge.Cuts outline")
    rings.sort(key=lambda ring: abs(_signed_area(ring)), reverse=True)
    outside = rings[0]
    if any(
        not _point_in_polygon((r[0].x, r[0].y), [(p.x, p.y) for p in outside])
        for r in rings[1:]
    ):
        raise ValueError("board has more than one outside contour")
    if inset:
        # A convex contour has a unique intersection of its inward edge
        # half-planes. Do not invent geometry for a concave offset that can split.
        winding = 1 if _signed_area(outside) > 0 else -1

        def cross(a: Point, b: Point, c: Point) -> float:
            return (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)

        if any(
            winding * cross(outside[i - 1], p, outside[(i + 1) % len(outside)]) < -1e-9
            for i, p in enumerate(outside)
        ):
            raise ValueError(
                "concave outline inset is unavailable through this IPC backend"
            )
        shifted = []
        for i, a in enumerate(outside):
            b = outside[(i + 1) % len(outside)]
            dx, dy = b.x - a.x, b.y - a.y
            length = math.hypot(dx, dy)
            shifted.append(
                (
                    Point(
                        a.x - winding * dy / length * inset,
                        a.y + winding * dx / length * inset,
                    ),
                    Point(dx, dy),
                )
            )
        result = []
        for i, (p, d) in enumerate(shifted):
            q, e = shifted[i - 1]
            denominator = d.x * e.y - d.y * e.x
            if abs(denominator) < 1e-12:
                continue
            t = ((q.x - p.x) * e.y - (q.y - p.y) * e.x) / denominator
            result.append(Point(p.x + t * d.x, p.y + t * d.y))
        if len(result) < 3 or _signed_area(result) * winding <= 0:
            raise ValueError("outline collapsed under the requested inset")
        outside = result
    return tuple(Point(round(p.x, 6), round(p.y, 6)) for p in outside)
