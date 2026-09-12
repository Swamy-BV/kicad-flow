"""Board coordinate transforms, bounds and intersection calculations."""

from __future__ import annotations

import math

from kicad_flow.pcb.types import (
    Point,
)

from .._sexpr import Node
from . import library as _fplib
from ._nodes import (
    _f,
    _text,
)


def _find_root(parents: list[int], index: int) -> int:
    """Find one disjoint-set root while compressing its path."""
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def _point_in_polygon(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon (points on the edge may go either way)."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def _polygon_area(poly: tuple[Point, ...] | list[Point]) -> float:
    """Absolute area of one closed-by-convention polygon."""
    return (
        abs(
            sum(
                point.x * poly[(i + 1) % len(poly)].y
                - poly[(i + 1) % len(poly)].x * point.y
                for i, point in enumerate(poly)
            )
        )
        / 2
        if len(poly) >= 3
        else 0.0
    )


def _signed_area(poly: tuple[Point, ...] | list[Point]) -> float:
    """Signed polygon area, used to retain a clip polygon's winding."""
    return (
        sum(
            point.x * poly[(i + 1) % len(poly)].y
            - poly[(i + 1) % len(poly)].x * point.y
            for i, point in enumerate(poly)
        )
        / 2
        if len(poly) >= 3
        else 0.0
    )


def _polygon_intersection(
    subject: tuple[Point, ...], clip: tuple[Point, ...]
) -> list[Point]:
    """Intersect a polygon with a convex clip polygon."""
    output = list(subject)
    winding = 1.0 if _signed_area(clip) >= 0 else -1.0

    def inside(point: Point, a: Point, b: Point) -> bool:
        cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x)
        return winding * cross >= -1e-9

    def crossing(start: Point, end: Point, a: Point, b: Point) -> Point:
        dx, dy = end.x - start.x, end.y - start.y
        ex, ey = b.x - a.x, b.y - a.y
        denominator = dx * ey - dy * ex
        if abs(denominator) < 1e-12:
            return end
        t = ((a.x - start.x) * ey - (a.y - start.y) * ex) / denominator
        return Point(start.x + t * dx, start.y + t * dy)

    for i, a in enumerate(clip):
        b = clip[(i + 1) % len(clip)]
        source, output = output, []
        if not source:
            break
        previous = source[-1]
        for current in source:
            if inside(current, a, b):
                if not inside(previous, a, b):
                    output.append(crossing(previous, current, a, b))
                output.append(current)
            elif inside(previous, a, b):
                output.append(crossing(previous, current, a, b))
            previous = current
    return output


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    """Shortest Euclidean distance from a point to a segment."""
    dx, dy = end.x - start.x, end.y - start.y
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist((point.x, point.y), (start.x, start.y))
    t = max(
        0.0, min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2)
    )
    return math.dist((point.x, point.y), (start.x + t * dx, start.y + t * dy))


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Whether two closed segments intersect, including endpoint contact."""

    def turn(p: Point, q: Point, r: Point) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    ta, tb, tc, td = turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b)
    if ((ta > 1e-9 and tb < -1e-9) or (ta < -1e-9 and tb > 1e-9)) and (
        (tc > 1e-9 and td < -1e-9) or (tc < -1e-9 and td > 1e-9)
    ):
        return True
    return any(
        abs(value) <= 1e-9 and _point_on_segment(point, start, end)
        for value, point, start, end in (
            (ta, c, a, b),
            (tb, d, a, b),
            (tc, a, c, d),
            (td, b, c, d),
        )
    )


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """Shortest Euclidean distance between two closed segments."""
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _point_segment_distance(a, c, d),
        _point_segment_distance(b, c, d),
        _point_segment_distance(c, a, b),
        _point_segment_distance(d, a, b),
    )


def _minimum_tree_length(points: list[Point]) -> float:
    """Euclidean minimum-spanning-tree length across *points*."""
    edges = sorted(
        (math.dist((a.x, a.y), (b.x, b.y)), i, j)
        for i, a in enumerate(points)
        for j, b in enumerate(points[i + 1 :], start=i + 1)
    )
    parent = list(range(len(points)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    length = 0.0
    for distance, first, second in edges:
        a, b = root(first), root(second)
        if a == b:
            continue
        parent[a] = b
        length += distance
    return length


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    """Whether *point* lies on a segment, within file-coordinate precision."""
    dx = end.x - start.x
    dy = end.y - start.y
    px = point.x - start.x
    py = point.y - start.y
    scale = max(1.0, abs(dx), abs(dy))
    if abs(dx * py - dy * px) > 1e-6 * scale:
        return False
    return (
        min(start.x, end.x) - 1e-6 <= point.x <= max(start.x, end.x) + 1e-6
        and min(start.y, end.y) - 1e-6 <= point.y <= max(start.y, end.y) + 1e-6
    )


def _circle_through(a: Point, b: Point, c: Point) -> tuple[Point, float] | None:
    """Centre and radius of the circle through three points, if defined."""
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < 1e-12:
        return None
    aa = a.x * a.x + a.y * a.y
    bb = b.x * b.x + b.y * b.y
    cc = c.x * c.x + c.y * c.y
    x = (aa * (b.y - c.y) + bb * (c.y - a.y) + cc * (a.y - b.y)) / d
    y = (aa * (c.x - b.x) + bb * (a.x - c.x) + cc * (b.x - a.x)) / d
    centre = Point(x, y)
    return centre, math.hypot(a.x - x, a.y - y)


def _arc_extrema(points: tuple[Point, ...]) -> list[Point]:
    """The cardinal extrema lying on a start/mid/end circular arc."""
    if len(points) != 3:
        return list(points)
    circle = _circle_through(*points)
    if circle is None:
        return list(points)
    centre, radius = circle
    angles = [
        math.atan2(p.y - centre.y, p.x - centre.x) % (2 * math.pi) for p in points
    ]
    start, mid, end = angles
    ccw_span = (end - start) % (2 * math.pi)
    ccw = (mid - start) % (2 * math.pi) <= ccw_span

    def on_arc(angle: float) -> bool:
        if ccw:
            return (angle - start) % (2 * math.pi) <= ccw_span + 1e-12
        return (start - angle) % (2 * math.pi) <= (start - end) % (2 * math.pi) + 1e-12

    out = list(points)
    for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
        if on_arc(angle):
            out.append(
                Point(
                    centre.x + radius * math.cos(angle),
                    centre.y + radius * math.sin(angle),
                )
            )
    return out


def _pad_on_board(dx: float, dy: float, at: Point, rotation: float) -> Point:
    """Where a pad lands once its footprint is placed and turned.

    This is the arithmetic every caller would otherwise repeat and quietly get
    wrong, and it was got wrong here first. Two things:

    1. **Rotation is counter-clockwise on screen** with Y running down, so it
       is ``x' = dx*cos + dy*sin``, ``y' = -dx*sin + dy*cos``. The other sign
       convention agrees at 0 degrees and at nothing else, which is exactly
       how it survived being written.
    2. **The side is NOT applied here.** A footprint on the back has its
       stored pad coordinates already mirrored -- that is what flipping one
       does to the file -- so mirroring again on read would undo it. The
       mirror lives in :meth:`KiCadBoard._mirror`, once, at placement.

    Measured against pcbnew's own pad positions for a part at 0, 45, 90 and
    270 degrees on both sides. All six agree; the first version agreed with
    one of them.
    """
    theta = math.radians(rotation % 360.0)
    cos, sin = math.cos(theta), math.sin(theta)
    rx = dx * cos + dy * sin
    ry = -dx * sin + dy * cos
    return Point(round(at.x + rx, 6), round(at.y + ry, 6))


def _courtyard_geometry(
    node: Node, at: Point, rotation: float
) -> tuple[Point, Point, tuple[Point, ...], tuple[float, float]]:
    """Placed courtyard centre, offset, polygon and axis-aligned size.

    KiCad courtyards can contain arcs and circles. The library reader already
    reduces those primitives to a conservative local bounding box; rotating
    all four corners here retains that safe envelope at every angle instead of
    pretending only quarter turns change it.
    """
    width, height, cx, cy = _fplib.courtyard_box(node)
    offset = _pad_on_board(cx, cy, Point(0.0, 0.0), rotation)
    centre = Point(round(at.x + offset.x, 6), round(at.y + offset.y, 6))
    polygon = tuple(
        _pad_on_board(x, y, at, rotation)
        for x, y in (
            (cx - width / 2, cy - height / 2),
            (cx + width / 2, cy - height / 2),
            (cx + width / 2, cy + height / 2),
            (cx - width / 2, cy + height / 2),
        )
    )
    xs, ys = [point.x for point in polygon], [point.y for point in polygon]
    size = (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))
    return centre, offset, polygon, size


def _fabrication_geometry(
    node: Node, at: Point, rotation: float
) -> tuple[tuple[Point, ...], str]:
    """Envelope of Fab graphics, with no text, pad or courtyard fallback.

    This is the library's drawn envelope, not an inferred connector mating
    face or a guarantee of physical dimensions. Stroke widths are excluded.
    """
    xs: list[float] = []
    ys: list[float] = []
    for shape in node.items:
        if not isinstance(shape, Node) or not shape.name.startswith("fp_"):
            continue
        if shape.name in {"fp_text", "fp_text_box"}:
            continue
        if not _text(shape.get("layer")).endswith(".Fab"):
            continue
        if shape.name == "fp_arc":
            corners = [shape.get(key) for key in ("start", "mid", "end")]
            if any(corner is None for corner in corners):
                return (), "unsupported"
            points = _arc_extrema(tuple(Point(_f(p, 0), _f(p, 1)) for p in corners))
            xs.extend(p.x for p in points)
            ys.extend(p.y for p in points)
        elif shape.name in {"fp_line", "fp_rect", "fp_poly", "fp_circle"}:
            sx, sy = _fplib._extent(shape, shape.name)
            xs.extend(sx)
            ys.extend(sy)
        else:
            return (), "unsupported"
    if not xs:
        return (), "missing"
    return tuple(
        _pad_on_board(x, y, at, rotation)
        for x, y in (
            (min(xs), min(ys)),
            (max(xs), min(ys)),
            (max(xs), max(ys)),
            (min(xs), max(ys)),
        )
    ), "available"


def _origin_for_anchor(
    node: Node, x: float, y: float, rotation: float, anchor: str
) -> Point:
    """Convert an explicit origin/centre anchor to a footprint origin."""
    if anchor == "origin":
        return Point(float(x), float(y))
    if anchor != "courtyard_center":
        raise ValueError(
            f"anchor must be 'origin' or 'courtyard_center', not {anchor!r}"
        )
    _, _, cx, cy = _fplib.courtyard_box(node)
    offset = _pad_on_board(cx, cy, Point(0.0, 0.0), rotation)
    return Point(round(float(x) - offset.x, 6), round(float(y) - offset.y, 6))
