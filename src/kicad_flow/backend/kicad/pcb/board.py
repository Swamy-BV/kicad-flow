"""KiCad behind the board interface: reads and writes ``.kicad_pcb`` directly.

Nothing above :class:`~kicad_flow.pcb.api.Board` knows this file exists.

**Why the file and not pcbnew.** KiCad ships a real board API, but it is only
importable from KiCad's own interpreter, so every call would be a subprocess
round trip -- placing twenty parts would be forty of them. The schematic side
already writes S-expressions directly for want of any API at all; doing the
same here makes a placement a dictionary update instead of a process launch.
pcbnew is still used for the two things the file cannot answer for itself:
filling a pour, and anything KiCad computes rather than stores.

**The pad arithmetic is the whole point of this module**, so it is spelled out
in :func:`_pad_on_board`. Everything else is bookkeeping.

**KiCad 10 names nets rather than numbering them.** There is no net table and
no index: a pad carries ``(net "GND")``. Older boards carry ``(net 3 "GND")``
and are read too, but nothing here writes one.
"""

from __future__ import annotations

import contextlib
import copy
import itertools
import math
import os
import uuid as _uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from kicad_flow.pcb.api import Board
from kicad_flow.pcb.types import (
    BoardLimits,
    BoardRule,
    ConnectedPad,
    Connection,
    ConnectivityGroup,
    Finding,
    Footprint,
    FootprintDef,
    Graphic,
    Net,
    NetClass,
    NetClassAssignment,
    NetConnectivity,
    NetPad,
    Pad,
    PlacementEdge,
    PlacementMeasurement,
    PlacementNetLength,
    PlacementOverlap,
    PlacementProposal,
    Point,
    RouteMetric,
    Stackup,
    StackupLayer,
    Track,
    Via,
    Zone,
)

from .. import render as _render
from .._sexpr import Node, Sym, dumps, loads
from ..cli import cli as _kicad
from . import library as _fplib
from . import project as _project

_COPPER_COUNTS = (2, 4, 6, 8)


def _copper_names(count: int) -> tuple[str, ...]:
    """Copper names in physical order for a supported rigid board."""
    if count not in _COPPER_COUNTS:
        raise ValueError(
            f"layer count must be one of {list(_COPPER_COUNTS)}, not {count}"
        )
    return ("F.Cu", *(f"In{i}.Cu" for i in range(1, count - 1)), "B.Cu")
_SIDES = ("F", "B")
_GRAPHIC_NODES = {
    "line": "gr_line",
    "arc": "gr_arc",
    "circle": "gr_circle",
    "rectangle": "gr_rect",
    "polygon": "gr_poly",
}
_GRAPHIC_KINDS = {node: kind for kind, node in _GRAPHIC_NODES.items()}
_GRAPHIC_LAYERS = ("Edge.Cuts", "F.SilkS", "B.SilkS")
_STACKUP_KINDS = {
    "copper", "core", "prepreg",
    "Top Silk Screen", "Top Solder Paste", "Top Solder Mask",
    "Bottom Solder Mask", "Bottom Solder Paste", "Bottom Silk Screen",
}
_VIA_KINDS = {"through": "", "blind_buried": "blind", "microvia": "micro"}
_ISLAND_MODES = {"always": 0, "never": 1, "area": 2}
_ISLAND_MODES_BY_NUMBER = {value: key for key, value in _ISLAND_MODES.items()}


def _uid() -> str:
    """A fresh UUID, as KiCad writes them."""
    return str(_uuid.uuid4())


def _find_root(parents: list[int], index: int) -> int:
    """Find one disjoint-set root while compressing its path."""
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def _refresh_uuids(node: Node) -> None:
    """Give every object in a copied library tree its own identity.

    Library footprints carry stable UUIDs for their pads and graphics. Those
    IDs identify the library definition; multiple placed instances cannot
    reuse them. KiCad otherwise reports a real violation against an arbitrary
    sibling instance sharing the child UUID, so its reference and coordinates
    disagree with the geometry that actually failed.
    """
    for item in node.items:
        if not isinstance(item, Node):
            continue
        if item.name in {"uuid", "tstamp"}:
            item.items[1:] = [_uid()]
        else:
            _refresh_uuids(item)


def _fmt(value: float) -> str:
    """A number as KiCad writes it: no trailing zeros, no exponent."""
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _toggle_mirror(shape: Node) -> None:
    """Add or remove ``(justify mirror)`` on a text node's effects.

    Text on a back layer has to carry this flag or it reads backwards on the
    finished board -- and nothing says so: the file parses, KiCad opens it, and
    the fab plots silkscreen you cannot read. Measured on a bare board with
    four 0402 resistors placed ``side="B"``: without this, DRC reported 12
    ``nonmirrored_text_on_back_layer`` warnings, three per part; with it, none.

    Toggled rather than set, because :meth:`KiCadBoard._mirror` runs on the way
    back to the front as well.
    """
    effects = shape.get("effects")
    if effects is None:
        effects = _node("effects", [_node("justify", [Sym("mirror")])])
        shape.items.append(effects)
        return
    justify = effects.get("justify")
    if justify is None:
        effects.items.append(_node("justify", [Sym("mirror")]))
        return
    if any(str(x) == "mirror" for x in justify.items[1:]):
        justify.items = [justify.items[0]] + [
            x for x in justify.items[1:] if str(x) != "mirror"
        ]
        if len(justify.items) == 1:      # nothing left to say
            effects.items.remove(justify)
    else:
        justify.items.append(Sym("mirror"))


def _point_in_polygon(pt: tuple[float, float],
                      poly: list[tuple[float, float]]) -> bool:
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
    return abs(sum(
        point.x * poly[(i + 1) % len(poly)].y
        - poly[(i + 1) % len(poly)].x * point.y
        for i, point in enumerate(poly)
    )) / 2 if len(poly) >= 3 else 0.0


def _signed_area(poly: tuple[Point, ...] | list[Point]) -> float:
    """Signed polygon area, used to retain a clip polygon's winding."""
    return sum(
        point.x * poly[(i + 1) % len(poly)].y
        - poly[(i + 1) % len(poly)].x * point.y
        for i, point in enumerate(poly)
    ) / 2 if len(poly) >= 3 else 0.0


def _polygon_intersection(subject: tuple[Point, ...],
                          clip: tuple[Point, ...]) -> list[Point]:
    """Intersect a polygon with a convex clip polygon."""
    output = list(subject)
    winding = 1.0 if _signed_area(clip) >= 0 else -1.0

    def inside(point: Point, a: Point, b: Point) -> bool:
        cross = (b.x - a.x) * (point.y - a.y) - \
            (b.y - a.y) * (point.x - a.x)
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
    t = max(0.0, min(1.0, ((point.x - start.x) * dx
                           + (point.y - start.y) * dy) / length2))
    return math.dist(
        (point.x, point.y), (start.x + t * dx, start.y + t * dy)
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Whether two closed segments intersect, including endpoint contact."""
    def turn(p: Point, q: Point, r: Point) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    ta, tb, tc, td = turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b)
    if ((ta > 1e-9 and tb < -1e-9) or (ta < -1e-9 and tb > 1e-9)) and \
            ((tc > 1e-9 and td < -1e-9) or
             (tc < -1e-9 and td > 1e-9)):
        return True
    return any(abs(value) <= 1e-9 and _point_on_segment(point, start, end)
               for value, point, start, end in (
                   (ta, c, a, b), (tb, d, a, b),
                   (tc, a, c, d), (td, b, c, d)))


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """Shortest Euclidean distance between two closed segments."""
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(_point_segment_distance(a, c, d),
               _point_segment_distance(b, c, d),
               _point_segment_distance(c, a, b),
               _point_segment_distance(d, a, b))


def _minimum_tree_length(points: list[Point]) -> float:
    """Euclidean minimum-spanning-tree length across *points*."""
    edges = sorted(
        (math.dist((a.x, a.y), (b.x, b.y)), i, j)
        for i, a in enumerate(points)
        for j, b in enumerate(points[i + 1:], start=i + 1)
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
    return (min(start.x, end.x) - 1e-6 <= point.x <=
            max(start.x, end.x) + 1e-6 and
            min(start.y, end.y) - 1e-6 <= point.y <=
            max(start.y, end.y) + 1e-6)


def _circle_through(a: Point, b: Point,
                    c: Point) -> tuple[Point, float] | None:
    """Centre and radius of the circle through three points, if defined."""
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y)
             + c.x * (a.y - b.y))
    if abs(d) < 1e-12:
        return None
    aa = a.x * a.x + a.y * a.y
    bb = b.x * b.x + b.y * b.y
    cc = c.x * c.x + c.y * c.y
    x = (aa * (b.y - c.y) + bb * (c.y - a.y)
         + cc * (a.y - b.y)) / d
    y = (aa * (c.x - b.x) + bb * (a.x - c.x)
         + cc * (b.x - a.x)) / d
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
    angles = [math.atan2(p.y - centre.y, p.x - centre.x) % (2 * math.pi)
              for p in points]
    start, mid, end = angles
    ccw_span = (end - start) % (2 * math.pi)
    ccw = (mid - start) % (2 * math.pi) <= ccw_span

    def on_arc(angle: float) -> bool:
        if ccw:
            return (angle - start) % (2 * math.pi) <= ccw_span + 1e-12
        return (start - angle) % (2 * math.pi) <= \
            (start - end) % (2 * math.pi) + 1e-12

    out = list(points)
    for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
        if on_arc(angle):
            out.append(Point(centre.x + radius * math.cos(angle),
                             centre.y + radius * math.sin(angle)))
    return out


def _node(name: str, atoms: list[Any] | None = None) -> Node:
    """``(name atom ...)``, with the quoting KiCad expects."""
    items: list[Node | Sym | str] = [Sym(name)]
    for atom in atoms or []:
        if isinstance(atom, bool):
            items.append(Sym("yes" if atom else "no"))
        elif isinstance(atom, (int, float)):
            items.append(Sym(_fmt(float(atom))))
        elif isinstance(atom, Node):
            items.append(atom)
        else:
            items.append(atom)
    return Node(items)


def _atom(node: Node | None, index: int) -> str | None:
    """The *index*-th atom AFTER the node name, as text."""
    if node is None or len(node.items) <= index + 1:
        return None
    return str(node.items[index + 1])


def _f(node: Node | None, index: int, default: float = 0.0) -> float:
    """Float value at *index*, or *default*."""
    raw = _atom(node, index)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _text(node: Node | None, index: int = 0, default: str = "") -> str:
    """String value at *index*, or *default*."""
    raw = _atom(node, index)
    return default if raw is None else raw


def _set(node: Node, index: int, value: Any) -> None:
    """Write the *index*-th atom after the node name."""
    while len(node.items) <= index + 1:
        node.items.append(Sym("0"))
    node.items[index + 1] = (
        Sym(_fmt(float(value))) if isinstance(value, (int, float)) else value
    )


def _net_name(node: Node | None) -> str:
    """The net a ``(net ...)`` names, in either the new or the old form.

    KiCad 10 writes ``(net "GND")``. Before that it was ``(net 3 "GND")``,
    an index and a name, and a board written by an older KiCad still reads
    that way -- so take the LAST atom, which is the name either way.
    """
    if node is None or len(node.items) < 2:
        return ""
    return str(node.items[-1])


def _turn_pads(node: Node, by: float) -> None:
    """Add *by* degrees to every pad's own ``at`` angle.

    KiCad rotates a pad's POSITION from the footprint's angle but takes the
    pad's SHAPE orientation from the pad's own ``(at x y angle)``. Leave the
    angle off and a turned part keeps axis-aligned pads: measured on a
    SOT-23-6 at 90 degrees, its own pads overlapped at 0.0000 mm and DRC
    returned 4 clearance and 4 solder-mask errors, all of the part against
    itself. Writing the angle cleared all eight.

    Added rather than set, because a footprint may already give a pad an angle
    of its own, and that is relative to the footprint.
    """
    if not by % 360.0:
        return
    for pad in node.get_all("pad"):
        at = pad.get("at")
        if at is None:
            continue
        if len(at.items) > 3:
            _set(at, 2, (_f(at, 2) + by) % 360.0)
        else:
            at.items.append(Sym(_fmt(by % 360.0)))


def _pad_on_board(dx: float, dy: float, at: Point,
                  rotation: float) -> Point:
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


def _courtyard_geometry(node: Node, at: Point, rotation: float
                        ) -> tuple[Point, Point, tuple[Point, ...],
                                   tuple[float, float]]:
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


def _origin_for_anchor(node: Node, x: float, y: float, rotation: float,
                       anchor: str) -> Point:
    """Convert an explicit origin/centre anchor to a footprint origin."""
    if anchor == "origin":
        return Point(float(x), float(y))
    if anchor != "courtyard_center":
        raise ValueError(
            "anchor must be 'origin' or 'courtyard_center', "
            f"not {anchor!r}"
        )
    _, _, cx, cy = _fplib.courtyard_box(node)
    offset = _pad_on_board(cx, cy, Point(0.0, 0.0), rotation)
    return Point(round(float(x) - offset.x, 6),
                 round(float(y) - offset.y, 6))


class KiCadBoard(Board):
    """A ``.kicad_pcb`` file, edited through the primitive API."""

    def __init__(self, path: Path, tree: Node) -> None:
        """Wrap an already-parsed board. Use :func:`create` or :func:`load`."""
        self._path = Path(path)
        self._tree = tree
        self._defs: dict[str, Node] = {}

    # -- the board --------------------------------------------------------

    @property
    def path(self) -> Path:
        """Where this board will be written."""
        return self._path

    @property
    def size(self) -> tuple[float, float]:
        """The outline's ``(width, height)``, or ``(0, 0)`` if undrawn."""
        points: list[Point] = []
        for graphic in self.graphics("Edge.Cuts"):
            if graphic.kind == "circle":
                centre, rim = graphic.points
                radius = math.hypot(rim.x - centre.x, rim.y - centre.y)
                points.extend([
                    Point(centre.x - radius, centre.y - radius),
                    Point(centre.x + radius, centre.y + radius),
                ])
            elif graphic.kind == "arc":
                points.extend(_arc_extrema(graphic.points))
            else:
                points.extend(graphic.points)
        if not points:
            return (0.0, 0.0)
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))

    @property
    def layers(self) -> tuple[str, ...]:
        """Every copper layer, front to back."""
        table = self._tree.get("layers")
        out = []
        for entry in (table.items[1:] if table is not None else []):
            if isinstance(entry, Node) and _text(entry, 1) == "signal":
                out.append(_text(entry, 0))
        return tuple(out)

    def set_layers(self, count: int) -> tuple[str, ...]:
        """Set the copper layer count and return the new layers."""
        names = _copper_names(count)
        table = self._tree.get("layers")
        if table is None:
            raise LookupError("board has no layer table")
        # Numbering is KiCad's: F.Cu is 0, B.Cu is 2, inner layers run from 4.
        ids = {"F.Cu": 0, "B.Cu": 2}
        for index, name in enumerate(names[1:-1], start=1):
            ids[name] = 2 + 2 * index
        keep = [e for e in table.items[1:]
                if not (isinstance(e, Node) and _text(e, 1) == "signal")]
        table.items = [table.items[0]] + [
            _node(str(ids[n]), [n, Sym("signal")]) for n in names
        ] + keep
        return names

    def save(self, *, validate: bool = False) -> Path:
        """Atomically write the board and optionally prove KiCad can load it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self._path.with_name(
            f".{self._path.stem}.writing{self._path.suffix}"
        )
        try:
            scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            if validate:
                from ..cli import cli

                cli.drc(scratch)
            os.replace(scratch, self._path)      # atomic on the same volume
        except Exception:
            scratch.unlink(missing_ok=True)
            raise
        return self._path

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Rollback the in-memory board if a composed primitive write fails."""
        tree = copy.deepcopy(self._tree)
        definitions = copy.deepcopy(self._defs)
        try:
            yield
        except Exception:
            self._tree = tree
            self._defs = definitions
            raise

    def set_stackup(self, stackup: Stackup) -> Stackup:
        """Replace only the board's stackup, preserving all other setup."""
        if not stackup.layers:
            raise ValueError("stackup needs at least one layer")
        if stackup.edge_connector not in ("", "yes", "bevelled"):
            raise ValueError("edge_connector must be empty, 'yes' or 'bevelled'")
        copper = tuple(layer.name for layer in stackup.layers
                       if layer.kind.lower() == "copper")
        if copper != self.layers:
            raise ValueError(
                f"stackup copper layers must be {list(self.layers)}, "
                f"in that order; got {list(copper)}"
            )
        for layer in stackup.layers:
            if not layer.name.strip() or not layer.kind.strip():
                raise ValueError("stackup layer name and kind cannot be empty")
            if layer.kind not in _STACKUP_KINDS:
                raise ValueError(f"stackup layer {layer.name!r} has unknown "
                                 f"kind {layer.kind!r}")
            if (layer.kind in ("copper", "core", "prepreg")
                    and layer.thickness is None):
                raise ValueError(f"physical stackup layer {layer.name!r} "
                                 "needs a thickness")
            if layer.thickness is not None and layer.thickness <= 0:
                raise ValueError(f"stackup layer {layer.name!r} thickness "
                                 "must be positive")
            if layer.epsilon_r is not None and layer.epsilon_r <= 0:
                raise ValueError(f"stackup layer {layer.name!r} epsilon_r "
                                 "must be positive")
            if layer.loss_tangent is not None and layer.loss_tangent < 0:
                raise ValueError(f"stackup layer {layer.name!r} loss_tangent "
                                 "cannot be negative")
        physical_kinds = [layer.kind for layer in stackup.layers
                          if layer.kind in ("copper", "core", "prepreg")]
        if (not physical_kinds or physical_kinds[0] != "copper"
                or physical_kinds[-1] != "copper"):
            raise ValueError("physical stackup must start and end with copper")
        if any(a == b == "copper"
               for a, b in itertools.pairwise(physical_kinds)):
            raise ValueError("each pair of copper layers needs at least one "
                             "core/prepreg dielectric between them")

        children: list[Node | Sym | str] = [Sym("stackup")]
        for layer in stackup.layers:
            values: list[Node | Sym | str] = [Sym("layer"), layer.name]
            values.append(_node("type", [layer.kind]))
            if layer.thickness is not None:
                values.append(_node("thickness", [layer.thickness]))
            if layer.material:
                values.append(_node("material", [layer.material]))
            if layer.epsilon_r is not None:
                values.append(_node("epsilon_r", [layer.epsilon_r]))
            if layer.loss_tangent is not None:
                values.append(_node("loss_tangent", [layer.loss_tangent]))
            if layer.color:
                values.append(_node("color", [layer.color]))
            children.append(Node(values))
        if stackup.copper_finish:
            children.append(_node("copper_finish", [stackup.copper_finish]))
        children.append(_node("dielectric_constraints", [
            stackup.dielectric_constraints
        ]))
        if stackup.edge_connector:
            children.append(_node("edge_connector", [
                Sym(stackup.edge_connector)
            ]))
        children.append(_node("castellated_pads", [stackup.castellated_pads]))
        children.append(_node("edge_plating", [stackup.edge_plating]))
        made = Node(children)

        setup = self._tree.get("setup")
        if setup is None:
            raise LookupError("board has no setup block")
        old = setup.get("stackup")
        if old is None:
            setup.items.insert(1, made)
        else:
            setup.items[setup.items.index(old)] = made

        physical = sum(layer.thickness or 0.0 for layer in stackup.layers
                       if layer.kind.lower() in ("copper", "core", "prepreg"))
        general = self._tree.get("general")
        thickness = general.get("thickness") if general is not None else None
        if thickness is not None and physical > 0:
            _set(thickness, 0, physical)
        return self.stackup()

    def stackup(self) -> Stackup:
        """Read the physical construction without interpreting its choices."""
        setup = self._tree.get("setup")
        node = setup.get("stackup") if setup is not None else None
        if node is None:
            return Stackup(())
        layers = []
        for item in node.get_all("layer"):
            layers.append(StackupLayer(
                name=_text(item), kind=_text(item.get("type")),
                thickness=(None if item.get("thickness") is None
                           else _f(item.get("thickness"), 0)),
                material=_text(item.get("material")),
                epsilon_r=(None if item.get("epsilon_r") is None
                           else _f(item.get("epsilon_r"), 0)),
                loss_tangent=(None if item.get("loss_tangent") is None
                              else _f(item.get("loss_tangent"), 0)),
                color=_text(item.get("color")),
            ))
        return Stackup(
            layers=tuple(layers),
            copper_finish=_text(node.get("copper_finish")),
            dielectric_constraints=(
                _text(node.get("dielectric_constraints")) == "yes"),
            edge_connector=_text(node.get("edge_connector")),
            castellated_pads=_text(node.get("castellated_pads")) == "yes",
            edge_plating=_text(node.get("edge_plating")) == "yes",
        )

    @property
    def thickness(self) -> float:
        """The finished thickness recorded in the board general block."""
        general = self._tree.get("general")
        node = general.get("thickness") if general is not None else None
        return _f(node, 0) if node is not None else 0.0

    def set_limits(self, limits: BoardLimits) -> BoardLimits:
        """Update board-wide manufacturing limits without changing geometry."""
        if any(value < 0 for value in limits.as_dict().values()):
            raise ValueError("board limits cannot be negative")
        _project.set_limits(self._path, limits)
        if limits.min_solder_mask_bridge is not None:
            setup = self._tree.get("setup")
            if setup is None:
                raise LookupError("board has no setup block")
            node = setup.get("solder_mask_min_width")
            if node is None:
                setup.items.insert(1, _node(
                    "solder_mask_min_width", [limits.min_solder_mask_bridge]
                ))
            else:
                _set(node, 0, limits.min_solder_mask_bridge)
        return self.limits()

    def limits(self) -> BoardLimits:
        """Read project-wide limits and the mask bridge stored on the board."""
        values: dict[str, Any] = vars(_project.limits(self._path)).copy()
        setup = self._tree.get("setup")
        node = setup.get("solder_mask_min_width") if setup is not None else None
        values["min_solder_mask_bridge"] = (
            _f(node, 0) if node is not None else None
        )
        return BoardLimits(**values)

    def set_net_classes(self, classes: tuple[NetClass, ...]) -> list[NetClass]:
        """Create or update netclasses in the sibling project."""
        return _project.set_net_classes(self._path, classes)

    def net_classes(self) -> list[NetClass]:
        """Read every project netclass."""
        return _project.net_classes(self._path)

    def assign_net_classes(
            self, assignments: tuple[NetClassAssignment, ...]
    ) -> list[NetClassAssignment]:
        """Replace memberships for the nets mentioned by the caller."""
        return _project.assign(self._path, assignments)

    def net_class_assignments(self) -> list[NetClassAssignment]:
        """Read explicit net-to-netclass memberships."""
        return _project.assignments(self._path)

    def set_rules(self, rules: tuple[BoardRule, ...]) -> list[BoardRule]:
        """Create or update named custom design rules."""
        return _project.set_rules(self._path, rules)

    def rules(self) -> list[BoardRule]:
        """Read numeric custom design rules."""
        return _project.rules(self._path)

    def graphic(self, kind: str, points: list[tuple[float, float]], *,
                layer: str, width: float = 0.1,
                fill: bool = False) -> Graphic:
        """Draw one outline or silkscreen primitive and return it."""
        if kind not in _GRAPHIC_NODES:
            raise ValueError(f"kind must be one of {sorted(_GRAPHIC_NODES)}, "
                             f"not {kind!r}")
        if layer not in _GRAPHIC_LAYERS:
            raise ValueError(f"graphic layer must be one of "
                             f"{list(_GRAPHIC_LAYERS)}, not {layer!r}")
        expected = {"line": 2, "arc": 3, "circle": 2, "rectangle": 2}
        if kind == "polygon":
            if len(points) < 3:
                raise ValueError("a polygon needs at least 3 points")
        elif len(points) != expected[kind]:
            raise ValueError(f"a {kind} needs {expected[kind]} points")
        made = tuple(Point(float(x), float(y)) for x, y in points)
        if width < 0:
            raise ValueError("graphic width cannot be negative")
        if kind == "arc" and _circle_through(*made) is None:
            raise ValueError("an arc's start, mid and end cannot be collinear")
        if kind == "circle" and made[0] == made[1]:
            raise ValueError("a circle needs a non-zero radius")
        if fill and kind not in ("circle", "rectangle", "polygon"):
            raise ValueError(f"a {kind} cannot be filled")
        if fill and layer == "Edge.Cuts":
            raise ValueError("Edge.Cuts shapes cannot be filled")

        uid = _uid()
        stroke = _node("stroke", [_node("width", [width]),
                                  _node("type", [Sym("solid")])])
        common = [stroke]
        if kind in ("circle", "rectangle", "polygon"):
            common.append(_node("fill", [Sym("yes" if fill else "no")]))
        common.extend([_node("layer", [layer]), _node("uuid", [uid])])
        if kind == "line":
            geometry = [_node("start", [made[0].x, made[0].y]),
                        _node("end", [made[1].x, made[1].y])]
        elif kind == "arc":
            geometry = [_node("start", [made[0].x, made[0].y]),
                        _node("mid", [made[1].x, made[1].y]),
                        _node("end", [made[2].x, made[2].y])]
        elif kind == "circle":
            geometry = [_node("center", [made[0].x, made[0].y]),
                        _node("end", [made[1].x, made[1].y])]
        elif kind == "rectangle":
            geometry = [_node("start", [made[0].x, made[0].y]),
                        _node("end", [made[1].x, made[1].y])]
        else:
            geometry = [_node("pts", [
                _node("xy", [point.x, point.y]) for point in made])]
        self._tree.items.append(_node(_GRAPHIC_NODES[kind], geometry + common))
        return Graphic(uid, kind, layer, made, float(width), fill)

    def graphics(self, layer: str = "") -> list[Graphic]:
        """Every outline and silkscreen primitive, in file order."""
        if layer and layer not in _GRAPHIC_LAYERS:
            raise ValueError(f"graphic layer must be one of "
                             f"{list(_GRAPHIC_LAYERS)}, not {layer!r}")
        out: list[Graphic] = []
        for item in self._tree.items:
            if not isinstance(item, Node) or item.name not in _GRAPHIC_KINDS:
                continue
            found = self._graphic_from_node(item)
            if found.layer in _GRAPHIC_LAYERS and \
                    (not layer or found.layer == layer):
                out.append(found)
        return out

    def outline_polygon(self, *, inset: float, max_error: float) -> tuple[Point, ...]:
        """Return KiCad's resolved outside contour as bounded-error points."""
        if inset < 0:
            raise ValueError("outline inset cannot be negative")
        if max_error <= 0:
            raise ValueError("outline max_error must be positive")

        from ._runner import run_pcbnew

        scratch = self._path.with_name(
            f".{self._path.stem}.outline-{_uid()}{self._path.suffix}"
        )
        try:
            scratch.parent.mkdir(parents=True, exist_ok=True)
            scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            result = run_pcbnew(_OUTLINE_POLYGON, {
                "board_path": str(scratch),
                "inset": inset,
                "max_error": max_error,
            })
        finally:
            for artifact in (
                scratch,
                scratch.with_suffix(".kicad_pro"),
                scratch.with_suffix(".kicad_prl"),
                scratch.with_suffix(".kicad_dru"),
                Path(f"{scratch}-bak"),
            ):
                artifact.unlink(missing_ok=True)
        raw = result.get("points")
        if not isinstance(raw, list) or len(raw) < 3:
            raise ValueError("board outline did not produce a usable polygon")
        return tuple(Point(float(item[0]), float(item[1])) for item in raw)

    def move_graphic(self, uuid: str, dx: float, dy: float) -> Graphic:
        """Shift one graphic primitive by an offset."""
        node = self._graphic_node(uuid)
        for name in ("start", "mid", "end", "center"):
            point = node.get(name)
            if point is not None:
                point.items = [point.items[0], Sym(_fmt(_f(point, 0) + dx)),
                               Sym(_fmt(_f(point, 1) + dy))]
        pts = node.get("pts")
        if pts is not None:
            for point in pts.get_all("xy"):
                point.items = [point.items[0], Sym(_fmt(_f(point, 0) + dx)),
                               Sym(_fmt(_f(point, 1) + dy))]
        return self._graphic_from_node(node)

    def remove_graphic(self, uuid: str) -> None:
        """Remove one graphic primitive by UUID."""
        self._tree.items.remove(self._graphic_node(uuid))

    def _graphic_node(self, uuid: str) -> Node:
        """The top-level graphic carrying *uuid*."""
        for item in self._tree.items:
            if isinstance(item, Node) and item.name in _GRAPHIC_KINDS and \
                    _text(item.get("uuid")) == uuid:
                return item
        raise LookupError(f"no graphic with uuid {uuid!r}")

    def _graphic_from_node(self, node: Node) -> Graphic:
        """Read one KiCad graphical node into the board contract."""
        kind = _GRAPHIC_KINDS[node.name]
        names = {"line": ("start", "end"),
                 "arc": ("start", "mid", "end"),
                 "circle": ("center", "end"),
                 "rectangle": ("start", "end")}
        if kind == "polygon":
            pts = node.get("pts")
            point_nodes = pts.get_all("xy") if pts is not None else []
        else:
            point_nodes = []
            for name in names[kind]:
                point = node.get(name)
                if point is not None:
                    point_nodes.append(point)
        points = tuple(Point(_f(point, 0), _f(point, 1))
                       for point in point_nodes)
        stroke = node.get("stroke")
        fill = node.get("fill")
        return Graphic(
            _text(node.get("uuid")), kind, _text(node.get("layer")), points,
            _f(stroke.get("width"), 0) if stroke is not None else 0.0,
            _text(fill) in ("yes", "solid") if fill is not None else False,
        )

    # -- the library ------------------------------------------------------

    def find_footprints(self, query: str, limit: int = 20) -> list[FootprintDef]:
        """Library footprints whose ``Library:Footprint`` id contains *query*."""
        out: list[FootprintDef] = []
        for fp_id in _fplib.search(query, limit=limit,
                                   project_dir=self._path.parent):
            try:
                out.append(self.footprint_def(fp_id))
            except (LookupError, ValueError):
                continue
        return out

    def footprint_def(self, fp_id: str) -> FootprintDef:
        """One library footprint, with its pads at the footprint origin."""
        tree = self._load_def(fp_id)
        pads = tuple(self._pad_of(node, Point(0.0, 0.0), 0.0)
                     for node in tree.get_all("pad"))
        width, height, cx, cy = _fplib.courtyard_box(tree)
        courtyard_polygon = tuple(Point(x, y) for x, y in (
            (cx - width / 2, cy - height / 2),
            (cx + width / 2, cy - height / 2),
            (cx + width / 2, cy + height / 2),
            (cx - width / 2, cy + height / 2),
        ))
        return FootprintDef(
            fp_id=fp_id,
            description=_text(tree.get("descr")),
            pads=pads, courtyard=(width, height),
            courtyard_center=Point(cx, cy),
            courtyard_polygon=courtyard_polygon,
            bbox=_fplib.bbox(tree),
            has_pth=any(p.through_hole for p in pads),
        )

    def _load_def(self, fp_id: str) -> Node:
        """Load and cache a library footprint's tree."""
        if fp_id not in self._defs:
            self._defs[fp_id] = _fplib.load(fp_id, self._path.parent)
        return self._defs[fp_id]

    # -- parts ------------------------------------------------------------

    def place(self, fp_id: str, ref: str, x: float, y: float, *,
              anchor: str = "origin", rotation: float = 0.0, side: str = "F",
              value: str = "") -> Footprint:
        """Put *fp_id* on the board at ``(x, y)`` as *ref*."""
        if side not in _SIDES:
            raise ValueError(f"side must be 'F' or 'B', not {side!r}")
        if self._find(ref) is not None:
            raise ValueError(f"{ref} is already on the board")
        node = copy.deepcopy(self._load_def(fp_id))
        # A library footprint node is `(footprint "Name" ...)`; on a board it
        # is `(footprint "Lib:Name" ...)` and carries a position and a side.
        node.items[1] = fp_id
        if side == "B":
            self._mirror(node)
        origin = _origin_for_anchor(node, x, y, rotation, anchor)
        self._set_child(node, "layer", ["F.Cu" if side == "F" else "B.Cu"])
        self._set_child(
            node, "at", [origin.x, origin.y, rotation % 360.0]
        )
        _turn_pads(node, rotation % 360.0)
        _refresh_uuids(node)
        if node.get("uuid") is None:
            node.items.append(_node("uuid", [_uid()]))
        self._set_property(node, "Reference", ref)
        self._set_property(node, "Value", value or fp_id.split(":")[-1])
        for pad in node.get_all("pad"):
            if pad.get("uuid") is None:
                pad.items.append(_node("uuid", [_uid()]))
        self._tree.items.append(node)
        return self.footprint(ref)

    @staticmethod
    def _mirror(node: Node) -> None:
        """Flip a footprint's stored geometry to the other side of the board.

        This is what the file records, and why :func:`_pad_on_board` applies
        no side of its own: a back-side footprint carries coordinates that are
        ALREADY mirrored. Negate X on everything it draws, negate the angles,
        and move every layer to its opposite -- a pad left on ``F.Cu`` under a
        part on the back is a pad on the wrong side, which routes cleanly and
        connects nothing.
        """
        for kind in ("pad", "fp_line", "fp_rect", "fp_poly", "fp_circle",
                     "fp_arc", "fp_text", "property"):
            for shape in node.get_all(kind):
                for corner in ("at", "start", "end", "center", "mid"):
                    point = shape.get(corner)
                    if point is not None and len(point.items) >= 2:
                        _set(point, 0, -_f(point, 0))
                        if len(point.items) >= 4:
                            _set(point, 2, (-_f(point, 2)) % 360.0)
                pts = shape.get("pts")
                for xy in (pts.get_all("xy") if pts is not None else []):
                    _set(xy, 0, -_f(xy, 0))
                for holder in (shape.get("layers"), shape.get("layer")):
                    if holder is None:
                        continue
                    holder.items = [holder.items[0]] + [
                        _flip_layer(str(x)) for x in holder.items[1:]
                    ]
                if kind in ("fp_text", "property"):
                    _toggle_mirror(shape)

    @staticmethod
    def _set_child(node: Node, name: str, atoms: list[Any]) -> None:
        """Replace or append a single child node."""
        existing = node.get(name)
        if existing is not None:
            node.items.remove(existing)
        node.items.insert(2, _node(name, atoms))

    @staticmethod
    def _prop_of(node: Node, name: str) -> Node | None:
        """The ``(property "<name>" ...)`` of a footprint, if it has one."""
        for prop in node.get_all("property"):
            if _text(prop) == name:
                return prop
        return None

    def _set_property(self, node: Node, name: str, value: str) -> None:
        """Set a footprint property, adding it if absent."""
        prop = self._prop_of(node, name)
        if prop is None:
            node.items.append(_node("property", [
                name, value, _node("at", [0, 0, 0]),
                # A new custom property is BOM/fabrication metadata, not
                # artwork.  Reference and Value already exist and retain the
                # library's visibility; callers can explicitly expose a
                # custom property with move_field(hide=False, layer=...).
                _node("layer", ["F.Fab"]), _node("hide", [Sym("yes")]),
                _node("uuid", [_uid()]),
            ]))
        else:
            _set(prop, 1, value)

    def _find(self, ref: str) -> Node | None:
        """The ``(footprint ...)`` node placed as *ref*, if any."""
        for node in self._tree.get_all("footprint"):
            prop = self._prop_of(node, "Reference")
            if prop is not None and _text(prop, 1) == ref:
                return node
        return None

    def _require(self, ref: str) -> Node:
        """The node for *ref*, or a :class:`LookupError`."""
        node = self._find(ref)
        if node is None:
            raise LookupError(f"{ref} is not on the board")
        return node

    def move(self, ref: str, x: float, y: float, *,
             anchor: str = "origin") -> Footprint:
        """Move a footprint by origin or courtyard centre; copper stays."""
        node = self._require(ref)
        at = node.get("at")
        if at is None:
            raise LookupError(f"{ref} has no position")
        origin = _origin_for_anchor(node, x, y, _f(at, 2), anchor)
        _set(at, 0, origin.x)
        _set(at, 1, origin.y)
        return self.footprint(ref)

    def rotate(self, ref: str, rotation: float) -> Footprint:
        """Set a placed footprint's rotation in degrees."""
        node = self._require(ref)
        at = node.get("at")
        if at is None:
            raise LookupError(f"{ref} has no position")
        was = _f(at, 2) if len(at.items) > 3 else 0.0
        _set(at, 2, rotation % 360.0)
        _turn_pads(node, (rotation - was) % 360.0)
        return self.footprint(ref)

    def flip(self, ref: str, side: str) -> Footprint:
        """Put a footprint on ``"F"`` or ``"B"``."""
        if side not in _SIDES:
            raise ValueError(f"side must be 'F' or 'B', not {side!r}")
        node = self._require(ref)
        now = "B" if _text(node.get("layer")).startswith("B.") else "F"
        if now != side:
            self._mirror(node)
            self._set_child(node, "layer", ["F.Cu" if side == "F" else "B.Cu"])
        return self.footprint(ref)

    def remove(self, ref: str) -> None:
        """Take a footprint off the board."""
        self._tree.items.remove(self._require(ref))

    def footprints(self) -> list[Footprint]:
        """Every placed footprint, in reference order."""
        out = []
        for node in self._tree.get_all("footprint"):
            prop = self._prop_of(node, "Reference")
            if prop is not None:
                out.append(self._as_footprint(node, _text(prop, 1)))
        return sorted(out, key=lambda f: f.ref)

    def footprint(self, ref: str) -> Footprint:
        """One placed footprint, with its pads at board positions."""
        return self._as_footprint(self._require(ref), ref)

    def measure_placement(
        self,
        proposals: tuple[PlacementProposal, ...] = (),
        *,
        edge_clearance: float = 0.0,
    ) -> PlacementMeasurement:
        """Measure current or proposed poses without modifying this board."""
        if edge_clearance < 0:
            raise ValueError("edge_clearance cannot be negative")
        target = self
        if proposals:
            target = KiCadBoard(self._path, copy.deepcopy(self._tree))
            target._defs = self._defs
            seen: set[str] = set()
            for proposal in proposals:
                if proposal.ref in seen:
                    raise ValueError(
                        f"duplicate placement proposal for {proposal.ref}"
                    )
                seen.add(proposal.ref)
                if proposal.side is not None:
                    target.flip(proposal.ref, proposal.side)
                if proposal.rotation is not None:
                    target.rotate(proposal.ref, proposal.rotation)
                target.move(
                    proposal.ref, proposal.x, proposal.y,
                    anchor=proposal.anchor,
                )
        return target._measure_current_placement(edge_clearance)

    def _measure_current_placement(
        self, edge_clearance: float
    ) -> PlacementMeasurement:
        """Measure the current tree; caller choices have already been applied."""
        footprints = self.footprints()
        outline = self.outline_polygon(inset=0.0, max_error=0.02)
        outline_list = list(outline)
        board_area = _polygon_area(outline_list)
        courtyard_area = sum(_polygon_area(part.courtyard_polygon)
                              for part in footprints)
        front_courtyard_area = sum(
            _polygon_area(part.courtyard_polygon)
            for part in footprints if part.side == "F"
        )
        back_courtyard_area = sum(
            _polygon_area(part.courtyard_polygon)
            for part in footprints if part.side == "B"
        )

        overlaps: list[PlacementOverlap] = []
        for i, first in enumerate(footprints):
            for second in footprints[i + 1:]:
                if first.side != second.side:
                    continue
                area = _polygon_area(_polygon_intersection(
                    first.courtyard_polygon, second.courtyard_polygon
                ))
                if area > 1e-6:
                    overlaps.append(PlacementOverlap(
                        first.ref, second.ref, first.side, area
                    ))

        edge_violations: list[PlacementEdge] = []
        clearances: list[float] = []
        outline_pairs = [
            (point, outline[(i + 1) % len(outline)])
            for i, point in enumerate(outline)
        ]
        for part in footprints:
            outside = any(
                not self._inside_or_on_outline(point, outline_list)
                for point in part.courtyard_polygon
            )
            edges = [
                (point, part.courtyard_polygon[(i + 1)
                                                % len(part.courtyard_polygon)])
                for i, point in enumerate(part.courtyard_polygon)
            ]
            clearance = min(
                _segment_distance(a, b, c, d)
                for a, b in edges for c, d in outline_pairs
            )
            clearances.append(clearance)
            if outside or clearance + 1e-9 < edge_clearance:
                edge_violations.append(PlacementEdge(
                    part.ref, clearance, outside
                ))

        net_lengths = self._placement_net_lengths(footprints)
        ratio = courtyard_area / board_area if board_area else 0.0
        front_ratio = front_courtyard_area / board_area if board_area else 0.0
        back_ratio = back_courtyard_area / board_area if board_area else 0.0
        return PlacementMeasurement(
            footprint_count=len(footprints),
            board_area=board_area,
            courtyard_area=courtyard_area,
            courtyard_area_ratio=ratio,
            front_courtyard_area=front_courtyard_area,
            back_courtyard_area=back_courtyard_area,
            front_courtyard_area_ratio=front_ratio,
            back_courtyard_area_ratio=back_ratio,
            maximum_side_area_ratio=max(front_ratio, back_ratio),
            minimum_edge_clearance=min(clearances) if clearances else None,
            required_edge_clearance=edge_clearance,
            overlaps=tuple(overlaps),
            edge_violations=tuple(edge_violations),
            connection_length=sum(item.length for item in net_lengths),
            net_lengths=tuple(sorted(
                net_lengths, key=lambda item: (-item.length, item.net)
            )),
        )

    @staticmethod
    def _inside_or_on_outline(point: Point, outline: list[Point]) -> bool:
        """Whether a point is inside or on the sampled board outline."""
        if any(_point_on_segment(point, edge, outline[(i + 1) % len(outline)])
               for i, edge in enumerate(outline)):
            return True
        return _point_in_polygon(
            (point.x, point.y), [(item.x, item.y) for item in outline]
        )

    def _placement_net_lengths(
        self, footprints: list[Footprint]
    ) -> list[PlacementNetLength]:
        """Euclidean minimum-spanning-tree length for every multi-pad net."""
        pads = {(part.ref, pad.number): pad.at
                for part in footprints for pad in part.pads}
        out: list[PlacementNetLength] = []
        for net in self.nets():
            points = [pads[(pad.ref, pad.pad)] for pad in net.pads]
            if len(points) < 2:
                continue
            out.append(PlacementNetLength(
                net.name, len(points), _minimum_tree_length(points)
            ))
        return out

    def _as_footprint(self, node: Node, ref: str) -> Footprint:
        """Build a :class:`Footprint` from a placed ``(footprint ...)``."""
        at_node = node.get("at")
        at = Point(_f(at_node, 0), _f(at_node, 1))
        rotation = _f(at_node, 2)
        side = "B" if _text(node.get("layer")).startswith("B.") else "F"
        value = self._prop_of(node, "Value")
        pads = tuple(self._pad_of(p, at, rotation)
                     for p in node.get_all("pad"))
        centre, offset, polygon, courtyard = _courtyard_geometry(
            node, at, rotation
        )
        return Footprint(
            ref=ref, fp_id=_text(node), value=_text(value, 1) if value else "",
            at=at, rotation=rotation, side=side, pads=pads,
            courtyard=courtyard,
            courtyard_offset=offset,
            courtyard_center=centre,
            courtyard_polygon=polygon,
            uuid=_text(node.get("uuid")),
        )

    def _pad_of(self, node: Node, at: Point, rotation: float) -> Pad:
        """Build a :class:`Pad` from a ``(pad ...)`` node."""
        pat = node.get("at")
        size = node.get("size")
        layers = node.get("layers")
        kind = _text(node, 1, "smd")
        drill = node.get("drill")
        return Pad(
            number=_text(node),
            at=_pad_on_board(_f(pat, 0), _f(pat, 1), at, rotation),
            size=(_f(size, 0), _f(size, 1)),
            layers=tuple(str(x) for x in (layers.items[1:] if layers else [])),
            net=_net_name(node.get("net")),
            drill=_f(drill, 0) if drill is not None else 0.0,
            kind="pth" if kind == "thru_hole" else (
                "npth" if kind == "np_thru_hole" else "smd"),
        )

    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a footprint, by name."""
        node = self._require(ref)
        return {_text(p, 0): _text(p, 1) for p in node.get_all("property")}

    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a footprint's fields and return all of them."""
        self._set_property(self._require(ref), name, value)
        return self.fields(ref)

    def move_field(self, ref: str, name: str, dx: float, dy: float, *,
                   rotation: float | None = None, layer: str = "",
                   hide: bool | None = None) -> Point:
        """Move a footprint's field, relative to the footprint's position."""
        node = self._require(ref)
        prop = self._prop_of(node, name)
        if prop is None:
            have = ", ".join(self.fields(ref))
            raise LookupError(f"{ref} has no field {name!r}; it has {have}")
        at = node.get("at")
        pat = prop.get("at")
        if at is None or pat is None:
            raise LookupError(f"{ref}.{name} has no position")
        # KiCad stores a footprint field's position as an offset from the
        # footprint, so this is what a caller means by "move it 2 mm left" and
        # the field keeps following its part.
        _set(pat, 0, round(float(dx), 6))
        _set(pat, 1, round(float(dy), 6))
        if rotation is not None:
            _set(pat, 2, float(rotation) % 360.0)
        if layer:
            self._set_child(prop, "layer", [layer])
        if hide is not None:
            existing = prop.get("hide")
            if existing is not None:
                prop.items.remove(existing)
            prop.items.append(_node("hide", [Sym("yes" if hide else "no")]))
        return _pad_on_board(float(dx), float(dy),
                             Point(_f(at, 0), _f(at, 1)), _f(at, 2))

    def set_net(self, ref: str, pad: str, net: str) -> str:
        """Put EVERY pad of *ref* numbered *pad* on *net*, and return the net.

        A pad number is not unique. A USB-C receptacle carries four shield lugs
        all numbered ``SH``; an SOT-223 numbers its tab ``2`` along with a lead.
        They are one electrical pin and the schematic names them once, so
        setting the first and leaving the rest floating is silently wrong --
        the unnetted copper reads as an island and DRC calls it unconnected.
        """
        node = self._require(ref)
        hit = 0
        for candidate in node.get_all("pad"):
            if _text(candidate) == pad:
                existing = candidate.get("net")
                if existing is not None:
                    candidate.items.remove(existing)
                candidate.items.append(_node("net", [net]))
                hit += 1
        if hit:
            return net
        have = ", ".join(_text(p) for p in node.get_all("pad"))
        raise LookupError(f"{ref} has no pad {pad!r}; it has {have}")

    def pad(self, ref: str, pad: str) -> Point:
        """Where *ref*'s *pad* is on the board -- the point to route to."""
        part = self.footprint(ref)
        found = part.pad(pad)
        if found is None:
            have = ", ".join(p.number for p in part.pads)
            raise LookupError(f"{ref} has no pad {pad!r}; it has {have}")
        return found.at

    # -- copper -----------------------------------------------------------

    def track(self, x1: float, y1: float, x2: float, y2: float, *,
              layer: str, width: float, net: str = "") -> Track:
        """Lay one straight copper segment and return it."""
        self._require_layer(layer)
        values = (float(x1), float(y1), float(x2), float(y2), float(width))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("track coordinates and width must be finite")
        if width <= 0:
            raise ValueError("track width must be positive")
        if x1 == x2 and y1 == y2:
            raise ValueError("a track must have two different endpoints")
        uid = _uid()
        made = Track(Point(float(x1), float(y1)), Point(float(x2), float(y2)),
                     layer, float(width), net, uid)
        self._tree.items.append(_node("segment", [
            _node("start", [made.start.x, made.start.y]),
            _node("end", [made.end.x, made.end.y]),
            _node("width", [made.width]),
            _node("layer", [layer]),
            _node("net", [net]),
            _node("uuid", [uid]),
        ]))
        return made

    def via(self, x: float, y: float, *, net: str = "",
            diameter: float = 0.6, drill: float = 0.3,
            layers: tuple[str, str] = ("F.Cu", "B.Cu"),
            kind: str = "through") -> Via:
        """Drill an explicitly typed plated via joining *layers*."""
        if kind not in _VIA_KINDS:
            raise ValueError(f"via kind must be one of {sorted(_VIA_KINDS)}")
        if len(layers) != 2 or layers[0] == layers[1]:
            raise ValueError("via layers must name two different copper layers")
        for name in layers:
            self._require_layer(name)
        first, last = (self.layers.index(name) for name in layers)
        if kind == "through" and {first, last} != {0, len(self.layers) - 1}:
            raise ValueError("a through via must span F.Cu to B.Cu")
        if kind == "microvia" and abs(first - last) != 1:
            raise ValueError("a microvia must span adjacent copper layers")
        values = (float(x), float(y), float(diameter), float(drill))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("via coordinates and dimensions must be finite")
        if diameter <= 0 or drill <= 0:
            raise ValueError("via diameter and drill must be positive")
        if drill >= diameter:
            raise ValueError("via drill must be smaller than its diameter")
        uid = _uid()
        made = Via(Point(float(x), float(y)), float(diameter), float(drill),
                   net, layers, kind, uid)
        items: list[Any] = [
            _node("at", [made.at.x, made.at.y]),
            _node("size", [made.diameter]),
            _node("drill", [made.drill]),
            _node("layers", list(layers)),
            _node("net", [net]),
            _node("uuid", [uid]),
        ]
        native_kind = _VIA_KINDS[kind]
        if native_kind:
            # KiCad stores the type as a bare token: ``(via blind ...)``.
            items.insert(0, Sym(native_kind))
        self._tree.items.append(_node("via", items))
        return made

    def zone(self, points: list[tuple[float, float]], *, layer: str,
             net: str = "", clearance: float = 0.5,
             pad_connection: str = "thermal",
             min_thickness: float = 0.25, thermal_gap: float = 0.5,
             thermal_spoke_width: float = 0.5, priority: int = 0,
             island_removal: str = "always", min_island_area: float = 0.0,
             forbids: tuple[str, ...] = ()) -> Zone:
        """Pour copper inside *points* on *layer*, or fence a region off."""
        if len(points) < 3:
            raise ValueError("a zone needs at least 3 points")
        self._require_layer(layer)
        if pad_connection not in {"thermal", "solid", "none"}:
            raise ValueError(
                "pad_connection must be 'thermal', 'solid', or 'none'"
            )
        if island_removal not in _ISLAND_MODES:
            raise ValueError(
                f"island_removal must be one of {sorted(_ISLAND_MODES)}"
            )
        numeric = (clearance, min_thickness, thermal_gap,
                   thermal_spoke_width, min_island_area)
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("zone dimensions must be finite")
        if clearance < 0 or min_island_area < 0:
            raise ValueError(
                "zone clearance and minimum island area cannot be negative"
            )
        if min_thickness <= 0 or thermal_gap <= 0 or thermal_spoke_width <= 0:
            raise ValueError("zone thickness and thermal dimensions must be positive")
        if priority < 0:
            raise ValueError("zone priority cannot be negative")
        uid = _uid()
        made = Zone(
            net=net,
            layer=layer,
            points=tuple(Point(float(x), float(y)) for x, y in points),
            filled=False,
            pad_connection=pad_connection,
            clearance=float(clearance),
            min_thickness=float(min_thickness),
            thermal_gap=float(thermal_gap),
            thermal_spoke_width=float(thermal_spoke_width),
            priority=int(priority),
            island_removal=island_removal,
            min_island_area=float(min_island_area),
            forbids=forbids,
            uuid=uid,
        )
        polygon = _node("polygon", [
            _node("pts", [_node("xy", [p.x, p.y]) for p in made.points])
        ])
        items: list[Any] = [
            _node("net", [net]),
            _node("layer", [layer]),
            _node("uuid", [uid]),
            _node("hatch", [Sym("edge"), 0.5]),
            _node(
                "connect_pads",
                ([] if pad_connection == "thermal" else [
                    Sym("yes" if pad_connection == "solid" else "no")
                ]) + [_node("clearance", [clearance])],
            ),
            _node("min_thickness", [min_thickness]),
            _node("fill", [Sym("yes"),
                           _node("thermal_gap", [thermal_gap]),
                           _node("thermal_bridge_width", [thermal_spoke_width]),
                           _node("island_removal_mode", [
                               _ISLAND_MODES[island_removal]
                           ]),
                           *([_node("island_area_min", [min_island_area])]
                             if island_removal == "area" else [])]),
            polygon,
        ]
        if priority:
            items.insert(3, _node("priority", [priority]))
        if forbids:
            allowed = {"tracks", "vias", "pads", "pours", "footprints"}
            bad = set(forbids) - allowed
            if bad:
                raise ValueError(f"forbids must be from {sorted(allowed)}, "
                                 f"not {sorted(bad)}")
            items.insert(3, _node("keepout", [
                _node("tracks", [Sym("not_allowed" if "tracks" in forbids
                                     else "allowed")]),
                _node("vias", [Sym("not_allowed" if "vias" in forbids
                                   else "allowed")]),
                _node("pads", [Sym("not_allowed" if "pads" in forbids
                                   else "allowed")]),
                _node("copperpour", [Sym("not_allowed" if "pours" in forbids
                                         else "allowed")]),
                _node("footprints", [Sym("not_allowed" if "footprints" in forbids
                                         else "allowed")]),
            ]))
        self._tree.items.append(_node("zone", items))
        return made

    def refill(self) -> int:
        """Recompute every pour against the copper as it now stands.

        The one thing the file cannot do for itself. Filling a zone means
        running KiCad's own filler over the copper as it stands, so this is
        the single place the board side enters pcbnew -- through KiCad's
        bundled Python, because the module is not importable from ours.
        """
        from ._runner import run_pcbnew

        self.save()
        result = run_pcbnew(_REFILL, {"board_path": str(self._path)})
        self._tree = loads(self._path.read_text(encoding="utf-8"))
        # `zones`, not `filled`: pcbnew reports how many it refilled and the
        # first version of this read a key that was never there, so a board
        # with two pours reported none and looked unfilled.
        count = result.get("zones", 0)
        return int(count) if isinstance(count, int) else 0

    def text(self, x: float, y: float, text: str, *, layer: str,
             size: float = 1.0, rotation: float = 0.0,
             mirror: bool = False) -> Point:
        """Put text on a layer -- a legend, a fab note, a designator."""
        at = Point(float(x), float(y))
        effects: list[Any] = [
            _node("font", [_node("size", [size, size]),
                           _node("thickness", [size * 0.15])])
        ]
        if mirror:
            effects.append(_node("justify", [Sym("mirror")]))
        self._tree.items.append(_node("gr_text", [
            text,
            _node("at", [at.x, at.y, rotation % 360.0]),
            _node("layer", [layer]),
            _node("uuid", [_uid()]),
            _node("effects", effects),
        ]))
        return at

    def remove_copper(self, *, uuid: str = "", net: str = "", layer: str = "",
                      tracks: bool = True, vias: bool = True,
                      zones: bool = False, all: bool = False) -> int:
        """Delete one UUID, or copper filtered by net and layer."""
        if not uuid and not net and not layer and not all:
            raise ValueError(
                "select uuid, net or layer; use all=True to remove all selected kinds"
            )
        if all and (uuid or net or layer):
            raise ValueError("all=True cannot be combined with selectors")
        if uuid and (net or layer):
            raise ValueError("uuid selection cannot be combined with net or layer")
        kinds = (
            [("segment", "layer"), ("via", "layers"), ("zone", "layer")]
            if uuid else
            ([("segment", "layer")] if tracks else [])
            + ([("via", "layers")] if vias else [])
            + ([("zone", "layer")] if zones else [])
        )
        gone = 0
        for name, layer_key in kinds:
            for node in list(self._tree.get_all(name)):
                if uuid and _text(node.get("uuid")) != uuid:
                    continue
                if net and _net_name(node.get("net")) != net:
                    continue
                if layer:
                    holder = node.get(layer_key)
                    names = ([_text(holder)] if layer_key == "layer"
                             else [str(x) for x in
                                   (holder.items[1:] if holder else [])])
                    if layer not in names:
                        continue
                self._tree.items.remove(node)
                gone += 1
        return gone

    def _require_layer(self, name: str) -> None:
        """Refuse a layer the board does not have."""
        if name not in self.layers:
            raise ValueError(
                f"no layer {name!r} on this board; it has {list(self.layers)}")

    # -- reading back -----------------------------------------------------

    def tracks(self) -> list[Track]:
        """Every copper segment on the board."""
        out = []
        for node in self._tree.get_all("segment"):
            start, end = node.get("start"), node.get("end")
            out.append(Track(
                Point(_f(start, 0), _f(start, 1)),
                Point(_f(end, 0), _f(end, 1)),
                _text(node.get("layer")), _f(node.get("width"), 0),
                _net_name(node.get("net")), _text(node.get("uuid")),
            ))
        return out

    def vias(self) -> list[Via]:
        """Every via on the board."""
        out = []
        for node in self._tree.get_all("via"):
            at = node.get("at")
            layers = node.get("layers")
            names = [str(x) for x in (layers.items[1:] if layers else [])]
            native_kind = (
                str(node.items[1])
                if len(node.items) > 1
                and not isinstance(node.items[1], Node)
                and str(node.items[1]) in {"blind", "micro"}
                else ""
            )
            kind = {"blind": "blind_buried", "micro": "microvia"}.get(
                native_kind, "through"
            )
            out.append(Via(
                Point(_f(at, 0), _f(at, 1)),
                _f(node.get("size"), 0), _f(node.get("drill"), 0),
                _net_name(node.get("net")),
                (names[0], names[-1]) if names else ("F.Cu", "B.Cu"),
                kind,
                _text(node.get("uuid")),
            ))
        return out

    def zones(self) -> list[Zone]:
        """Every pour and keep-out."""
        out = []
        for node in self._tree.get_all("zone"):
            polygon = node.get("polygon")
            pts = polygon.get("pts") if polygon is not None else None
            points = tuple(Point(_f(xy, 0), _f(xy, 1))
                           for xy in (pts.get_all("xy") if pts else []))
            keepout = node.get("keepout")
            connect = node.get("connect_pads")
            fill = node.get("fill")
            island_number = int(_f(fill.get("island_removal_mode"), 0)) \
                if fill is not None and fill.get("island_removal_mode") else 0
            forbids = tuple(
                name for name, key in (("tracks", "tracks"), ("vias", "vias"),
                                       ("pads", "pads"), ("pours", "copperpour"),
                                       ("footprints", "footprints"))
                if keepout is not None
                and _text(keepout.get(key)) == "not_allowed"
            )
            out.append(Zone(
                net=_net_name(node.get("net")),
                layer=_text(node.get("layer")),
                points=points,
                filled=node.get("filled_polygon") is not None,
                pad_connection={"yes": "solid", "no": "none"}.get(
                    _text(connect), "thermal"
                ),
                clearance=(
                    _f(connect.get("clearance"), 0)
                    if connect is not None and connect.get("clearance") else 0.0
                ),
                min_thickness=_f(node.get("min_thickness"), 0),
                thermal_gap=(
                    _f(fill.get("thermal_gap"), 0)
                    if fill is not None and fill.get("thermal_gap") else 0.0
                ),
                thermal_spoke_width=(
                    _f(fill.get("thermal_bridge_width"), 0)
                    if fill is not None and fill.get("thermal_bridge_width") else 0.0
                ),
                priority=(
                    int(_f(node.get("priority"), 0))
                    if node.get("priority") is not None else 0
                ),
                island_removal=_ISLAND_MODES_BY_NUMBER.get(
                    island_number, "always"
                ),
                min_island_area=(
                    _f(fill.get("island_area_min"), 0)
                    if fill is not None and fill.get("island_area_min") else 0.0
                ),
                forbids=forbids,
                uuid=_text(node.get("uuid")),
            ))
        return out

    def nets(self) -> list[Net]:
        """What the board is MEANT to connect, from its own pads."""
        found: dict[str, list[NetPad]] = {}
        for part in self.footprints():
            for pad in part.pads:
                if pad.net:
                    found.setdefault(pad.net, []).append(
                        NetPad(part.ref, pad.number))
        return [Net(name, tuple(pads))
                for name, pads in sorted(found.items())]

    def connectivity(self, nets: tuple[str, ...] = ()) -> list[NetConnectivity]:
        """Return pad-bearing connected copper groups without selecting routes."""
        wanted = set(nets)
        where = {(part.ref, pad.number): pad
                 for part in self.footprints() for pad in part.pads}
        out: list[NetConnectivity] = []
        for net in self.nets():
            if wanted and net.name not in wanted:
                continue
            copper_groups = self._groups_of(net.name)
            node_group = {
                node: index
                for index, group in enumerate(copper_groups)
                for node in group
            }
            grouped_pads: dict[int, list[ConnectedPad]] = {}
            for item in net.pads:
                pad = where[(item.ref, item.pad)]
                indexes = {
                    node_group[node]
                    for layer in self._pad_copper_layers(pad)
                    if (node := (
                        round(pad.at.x, 3), round(pad.at.y, 3), layer
                    )) in node_group
                }
                # _groups_of always creates nodes for net pads. Keep a guarded
                # fallback so malformed imported boards remain inspectable.
                index = min(indexes) if indexes else len(copper_groups)
                grouped_pads.setdefault(index, []).append(ConnectedPad(
                    item.ref, item.pad, pad.at, self._pad_copper_layers(pad)
                ))

            ordered = sorted(
                grouped_pads.items(),
                key=lambda item: min(
                    (pad.ref, pad.pad) for pad in item[1]
                ),
            )
            groups: list[ConnectivityGroup] = []
            for public_index, (source_index, pads) in enumerate(ordered):
                nodes = (
                    copper_groups[source_index]
                    if source_index < len(copper_groups) else set()
                )
                groups.append(ConnectivityGroup(
                    index=public_index,
                    pads=tuple(sorted(pads, key=lambda pad: (pad.ref, pad.pad))),
                    layers=tuple(sorted({node[2] for node in nodes})),
                    copper_nodes=len(nodes),
                ))
            out.append(NetConnectivity(net.name, tuple(groups)))
        return out

    def route_metrics(self, nets: tuple[str, ...] = ()) -> list[RouteMetric]:
        """Measure authored copper on each intended net."""
        connectivity = {item.net: item for item in self.connectivity(nets)}
        wanted = set(nets)
        net_names = [net.name for net in self.nets()
                     if not wanted or net.name in wanted]
        pad_counts = {net.name: len(net.pads) for net in self.nets()}
        tracks = self.tracks()
        vias = self.vias()
        out: list[RouteMetric] = []
        for name in net_names:
            net_tracks = [track for track in tracks if track.net == name]
            by_layer: dict[str, float] = {}
            for track in net_tracks:
                length = math.dist(
                    (track.start.x, track.start.y),
                    (track.end.x, track.end.y),
                )
                by_layer[track.layer] = by_layer.get(track.layer, 0.0) + length
            widths = [track.width for track in net_tracks]
            out.append(RouteMetric(
                net=name,
                pad_count=pad_counts[name],
                track_count=len(net_tracks),
                track_length=sum(by_layer.values()),
                length_by_layer=tuple(sorted(by_layer.items())),
                via_count=sum(via.net == name for via in vias),
                minimum_width=min(widths) if widths else None,
                connected_groups=len(connectivity[name].groups),
            ))
        return out

    def unrouted(self) -> list[Connection]:
        """A minimum set of pad-group separations, nearest endpoints first.

        Connected-component membership is factual. The nearest pad pair is a
        distance measurement returned to identify each separation; it is not
        a proposed track or a routing choice.
        """
        out: list[Connection] = []
        for net in self.connectivity():
            if len(net.groups) < 2:
                continue
            edges: list[tuple[float, int, int, ConnectedPad, ConnectedPad]] = []
            for index, first in enumerate(net.groups):
                for second in net.groups[index + 1:]:
                    candidates = [
                        (math.dist((a.at.x, a.at.y), (b.at.x, b.at.y)), a, b)
                        for a in first.pads for b in second.pads
                    ]
                    distance, a, b = min(
                        candidates,
                        key=lambda item: (
                            item[0], item[1].ref, item[1].pad,
                            item[2].ref, item[2].pad,
                        ),
                    )
                    edges.append((distance, first.index, second.index, a, b))

            parent = list(range(len(net.groups)))

            for distance, first_index, second_index, a, b in sorted(
                edges,
                key=lambda item: (
                    item[0], item[3].ref, item[3].pad,
                    item[4].ref, item[4].pad,
                ),
            ):
                left = _find_root(parent, first_index)
                right = _find_root(parent, second_index)
                if left == right:
                    continue
                parent[left] = right
                out.append(Connection(
                    net.net, NetPad(a.ref, a.pad), NetPad(b.ref, b.pad), distance
                ))
        return out

    def _pad_copper_layers(self, pad: Pad) -> tuple[str, ...]:
        """Copper layers a pad actually reaches."""
        if pad.kind == "pth" or "*.Cu" in pad.layers:
            return self.layers
        return tuple(layer for layer in pad.layers if layer in self.layers)

    def _via_copper_layers(self, via: Via) -> tuple[str, ...]:
        """Copper layers inside a via's declared span."""
        try:
            first = self.layers.index(via.layers[0])
            last = self.layers.index(via.layers[-1])
        except ValueError:
            return ()
        low, high = sorted((first, last))
        return self.layers[low:high + 1]

    def _groups_of(self, net: str) -> list[set[tuple[float, float, str]]]:
        """Layer-aware copper nodes on *net*, grouped by connectivity."""
        node = tuple[float, float, str]
        edges: list[tuple[node, node]] = []
        nodes: set[node] = set()

        def key(point: Point, layer: str) -> node:
            return (round(point.x, 3), round(point.y, 3), layer)

        # A plated pad joins its layers internally. Coincident pads join only
        # on a copper layer both can actually reach.
        seen_at: dict[node, node] = {}
        for part in self.footprints():
            for pad in part.pads:
                if pad.net != net:
                    continue
                pad_nodes = [key(pad.at, layer)
                             for layer in self._pad_copper_layers(pad)]
                nodes.update(pad_nodes)
                for other in pad_nodes[1:]:
                    edges.append((pad_nodes[0], other))
                for pad_node in pad_nodes:
                    if pad_node in seen_at:
                        edges.append((seen_at[pad_node], pad_node))
                    seen_at[pad_node] = pad_node

        tracks = [track for track in self.tracks() if track.net == net]
        for track in tracks:
            start = key(track.start, track.layer)
            end = key(track.end, track.layer)
            nodes.update((start, end))
            edges.append((start, end))
        # Same-layer copper joins at crossings and T intersections even when
        # neither caller supplied the intersection as an endpoint.
        for index, first in enumerate(tracks):
            for second in tracks[index + 1:]:
                if first.layer == second.layer and _segments_intersect(
                        first.start, first.end, second.start, second.end):
                    edges.append((key(first.start, first.layer),
                                  key(second.start, second.layer)))

        for via in self.vias():
            if via.net != net:
                continue
            via_nodes = [key(via.at, layer)
                         for layer in self._via_copper_layers(via)]
            nodes.update(via_nodes)
            for other in via_nodes[1:]:
                edges.append((via_nodes[0], other))

        # A filled plane joins conductive objects on ITS layer only. An SMD
        # pad on F.Cu does not reach an In1.Cu plane without a via.
        for zone in self.zones():
            if zone.net != net or not zone.filled or zone.forbids:
                continue
            poly = [(point.x, point.y) for point in zone.points]
            inside = [item for item in nodes if item[2] == zone.layer
                      and _point_in_polygon((item[0], item[1]), poly)]
            for item in inside[1:]:
                edges.append((inside[0], item))

        parent: dict[node, node] = {item: item for item in nodes}

        def find(k: node) -> node:
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for a, b in edges:
            parent[find(a)] = find(b)
        groups: dict[node, set[node]] = {}
        for k in list(parent):
            groups.setdefault(find(k), set()).add(k)
        return list(groups.values())

    def _routing_findings(self) -> list[Finding]:
        """Factual copper defects KiCad's DRC does not currently report.

        This deliberately does not judge corner angles. Choosing 45-degree,
        orthogonal or curved routing is caller policy; a zero-length segment,
        an exact duplicate and an endpoint touching no same-net copper object
        are properties of the board as written.
        """
        tracks = self.tracks()
        vias = self.vias()
        pads = [(part.ref, pad) for part in self.footprints()
                for pad in part.pads]
        zones = [zone for zone in self.zones()
                 if zone.filled and not zone.forbids]
        out: list[Finding] = []

        def key(point: Point) -> tuple[float, float]:
            # Pad positions are deliberately reported to 0.001 mm and callers
            # route to those reported facts. A rotated library pad may sit at
            # 12.1875 internally while its returned routing point is 12.188.
            return round(point.x, 3), round(point.y, 3)

        def pad_on_layer(track: Track, pad: Pad) -> bool:
            return (track.layer in pad.layers or
                    (pad.kind == "pth" and track.layer in self.layers))

        def via_on_layer(track: Track, via: Via) -> bool:
            try:
                layer = self.layers.index(track.layer)
                first = self.layers.index(via.layers[0])
                last = self.layers.index(via.layers[-1])
            except ValueError:
                return False
            low, high = sorted((first, last))
            return low <= layer <= high

        def anchored(track: Track, point: Point, own: int) -> bool:
            point_key = key(point)
            if any(pad.net == track.net and pad_on_layer(track, pad)
                   and key(pad.at) == point_key for _ref, pad in pads):
                return True
            if any(via.net == track.net and via_on_layer(track, via)
                   and key(via.at) == point_key for via in vias):
                return True
            if any(other.net == track.net and other.layer == track.layer
                   and _point_on_segment(point, other.start, other.end)
                   for index, other in enumerate(tracks) if index != own):
                return True
            return any(zone.net == track.net and zone.layer == track.layer
                       and _point_in_polygon(point_key,
                                             [(p.x, p.y) for p in zone.points])
                       for zone in zones)

        seen: dict[tuple[str, str, tuple[float, float],
                         tuple[float, float]], Track] = {}
        for index, track in enumerate(tracks):
            start, end = key(track.start), key(track.end)
            if start == end:
                out.append(Finding(
                    severity="error", kind="zero_length_track",
                    message="Track has identical start and end points",
                    layer=track.layer, at=track.start, uuid=track.uuid,
                ))
                continue
            ordered = tuple(sorted((start, end)))
            duplicate_key = (track.net, track.layer, ordered[0], ordered[1])
            original = seen.get(duplicate_key)
            if original is not None:
                out.append(Finding(
                    severity="warning", kind="duplicate_track",
                    message="Track exactly duplicates another segment",
                    layer=track.layer, at=track.start, uuid=track.uuid,
                    other_uuid=original.uuid,
                ))
            else:
                seen[duplicate_key] = track
            for point in (track.start, track.end):
                if not anchored(track, point, index):
                    out.append(Finding(
                        severity="warning", kind="dangling_track_end",
                        message=("Track endpoint does not meet a same-net pad, "
                                 "via, filled zone or track"),
                        layer=track.layer, at=point, uuid=track.uuid,
                    ))
        return out

    def check(self) -> list[Finding]:
        """Every violation, mapped from a position back to a part and pad."""
        self.save()
        data = _kicad.drc(self._path)
        where: dict[tuple[float, float], tuple[str, str]] = {}
        for part in self.footprints():
            for p in part.pads:
                where[(round(p.at.x, 2), round(p.at.y, 2))] = (
                    part.ref, p.number)
        out: list[Finding] = []
        for kind in ("violations", "unconnected_items", "schematic_parity"):
            for violation in data.get(kind, []):
                items = violation.get("items") or [{}]
                refs = []
                for item in items:
                    pos = item.get("pos") or {}
                    at = Point(round(float(pos.get("x", 0.0)), 3),
                               round(float(pos.get("y", 0.0)), 3))
                    refs.append((where.get((round(at.x, 2), round(at.y, 2)),
                                           ("", "")), at))
                (ref, number), at = refs[0]
                other = refs[1][0][0] if len(refs) > 1 else ""
                first_uuid = str(items[0].get("uuid") or "")
                other_uuid = (
                    str(items[1].get("uuid") or "")
                    if len(items) > 1 else ""
                )
                out.append(Finding(
                    severity=str(violation.get("severity", "error")),
                    kind=str(violation.get("type", kind)),
                    message=str(violation.get("description", "")),
                    ref=ref, pad=number, at=at, other_ref=other,
                    uuid=first_uuid, other_uuid=other_uuid,
                ))
        out.extend(self._routing_findings())
        return out

    def check_proposed(self, tracks: tuple[Track, ...] = (),
                       vias: tuple[Via, ...] = (),
                       zones: tuple[Zone, ...] = ()) -> list[Finding]:
        """Check caller-supplied copper on a temporary board copy."""
        scratch = self._path.with_name(
            f".{self._path.stem}.route-check-{_uid()}{self._path.suffix}"
        )
        candidate = KiCadBoard(scratch, copy.deepcopy(self._tree))
        inputs: dict[str, tuple[str, int]] = {}
        try:
            # A filled zone is cached copper, not merely a declaration. Loading
            # a scratch board containing that stale fill plus newly proposed
            # copper lets pcbnew build connectivity before the filler runs and
            # can permanently reassign a proposed via to the plane net. Keep
            # the zone rules, but discard cached geometry before adding copper.
            for zone_node in candidate._tree.get_all("zone"):
                zone_node.items[:] = [
                    item
                    for item in zone_node.items
                    if not (
                        isinstance(item, Node)
                        and item.name in {"filled_polygon", "fill_segments"}
                    )
                ]
            for index, track_item in enumerate(tracks):
                made_track = candidate.track(
                    track_item.start.x, track_item.start.y,
                    track_item.end.x, track_item.end.y,
                    layer=track_item.layer, width=track_item.width,
                    net=track_item.net,
                )
                inputs[made_track.uuid] = ("tracks", index)
            for index, via_item in enumerate(vias):
                made_via = candidate.via(
                    via_item.at.x, via_item.at.y, net=via_item.net,
                    diameter=via_item.diameter, drill=via_item.drill,
                    layers=via_item.layers, kind=via_item.kind,
                )
                inputs[made_via.uuid] = ("vias", index)
            for index, zone_item in enumerate(zones):
                made_zone = candidate.zone(
                    [(point.x, point.y) for point in zone_item.points],
                    layer=zone_item.layer,
                    net=zone_item.net,
                    clearance=zone_item.clearance,
                    pad_connection=zone_item.pad_connection,
                    min_thickness=zone_item.min_thickness,
                    thermal_gap=zone_item.thermal_gap,
                    thermal_spoke_width=zone_item.thermal_spoke_width,
                    priority=zone_item.priority,
                    island_removal=zone_item.island_removal,
                    min_island_area=zone_item.min_island_area,
                    forbids=zone_item.forbids,
                )
                inputs[made_zone.uuid] = ("zones", index)
            if candidate.zones():
                candidate.refill()
            found = candidate.check()
            attributed: list[Finding] = []
            for finding in found:
                first = inputs.get(finding.uuid)
                second = inputs.get(finding.other_uuid)
                attributed.append(replace(
                    finding,
                    input_kind=first[0] if first else "",
                    input_index=first[1] if first else None,
                    other_input_kind=second[0] if second else "",
                    other_input_index=second[1] if second else None,
                ))
            return attributed
        finally:
            for artifact in (
                scratch,
                scratch.with_suffix(".kicad_pro"),
                scratch.with_suffix(".kicad_prl"),
                scratch.with_suffix(".kicad_dru"),
                Path(f"{scratch}-bak"),
            ):
                artifact.unlink(missing_ok=True)

    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What geometrically touches a point, including track interiors."""
        if radius < 0 or not all(math.isfinite(value)
                                 for value in (x, y, radius)):
            raise ValueError("point coordinates must be finite and radius non-negative")

        def segment_distance(track: Track) -> float:
            dx = track.end.x - track.start.x
            dy = track.end.y - track.start.y
            length_squared = dx * dx + dy * dy
            if not length_squared:
                return math.dist((x, y), (track.start.x, track.start.y))
            position = max(0.0, min(1.0, (
                (x - track.start.x) * dx + (y - track.start.y) * dy
            ) / length_squared))
            return math.dist(
                (x, y),
                (track.start.x + position * dx,
                 track.start.y + position * dy),
            )

        pads = [
            {"ref": footprint.ref, **pad.as_dict()}
            for footprint in self.footprints() for pad in footprint.pads
            if abs(pad.at.x - x) <= pad.size[0] / 2 + radius
            and abs(pad.at.y - y) <= pad.size[1] / 2 + radius
        ]
        tracks = [
            track.as_dict() for track in self.tracks()
            if segment_distance(track) <= track.width / 2 + radius
        ]
        track_ends = [
            {"layer": track.layer, "net": track.net, "uuid": track.uuid}
            for track in self.tracks()
            if math.dist((x, y), (track.start.x, track.start.y)) <= radius
            or math.dist((x, y), (track.end.x, track.end.y)) <= radius
        ]
        vias = [
            via.as_dict() for via in self.vias()
            if math.dist((x, y), (via.at.x, via.at.y))
            <= via.diameter / 2 + radius
        ]
        zones = [
            {"uuid": zone.uuid, "net": zone.net, "layer": zone.layer,
             "filled": zone.filled}
            for zone in self.zones()
            if _point_in_polygon((x, y), [(p.x, p.y) for p in zone.points])
        ]
        count = len(pads) + len(tracks) + len(vias) + len(zones)
        return {"x": round(x, 3), "y": round(y, 3), "radius": radius,
                "pads": pads, "tracks": tracks, "vias": vias, "zones": zones,
                "track_ends": track_ends,
                "connected": count > 1}

    def region(self, x1: float, y1: float, x2: float, y2: float, *,
               layers: tuple[str, ...] = ()) -> dict[str, object]:
        """Return objects whose conservative bounds intersect a rectangle."""
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            raise ValueError("region coordinates must be finite")
        left, right = sorted((float(x1), float(x2)))
        top, bottom = sorted((float(y1), float(y2)))
        wanted = set(layers)
        unknown = wanted - set(self.layers) - set(_GRAPHIC_LAYERS)
        if unknown:
            raise ValueError(
                f"unknown region layers {sorted(unknown)}; board copper is "
                f"{list(self.layers)} and graphics are {list(_GRAPHIC_LAYERS)}"
            )

        def intersects(bounds: tuple[float, float, float, float]) -> bool:
            bx1, by1, bx2, by2 = bounds
            return not (bx2 < left or bx1 > right or by2 < top or by1 > bottom)

        footprints = []
        pads = []
        for footprint in self.footprints():
            if not wanted or f"{footprint.side}.Cu" in wanted:
                polygon = footprint.courtyard_polygon
                bounds = (
                    min(point.x for point in polygon),
                    min(point.y for point in polygon),
                    max(point.x for point in polygon),
                    max(point.y for point in polygon),
                )
                if intersects(bounds):
                    footprints.append({
                        "ref": footprint.ref,
                        "side": footprint.side,
                        "courtyard_polygon": [
                            point.as_dict() for point in polygon
                        ],
                    })
            for pad in footprint.pads:
                pad_layers = set(self._pad_copper_layers(pad))
                if wanted and not wanted.intersection(pad_layers):
                    continue
                half = max(pad.size) / 2
                if intersects((pad.at.x - half, pad.at.y - half,
                               pad.at.x + half, pad.at.y + half)):
                    pads.append({"ref": footprint.ref, **pad.as_dict()})

        tracks = []
        for track in self.tracks():
            if wanted and track.layer not in wanted:
                continue
            half = track.width / 2
            if intersects((min(track.start.x, track.end.x) - half,
                           min(track.start.y, track.end.y) - half,
                           max(track.start.x, track.end.x) + half,
                           max(track.start.y, track.end.y) + half)):
                tracks.append(track.as_dict())

        vias = []
        for via in self.vias():
            if wanted and not wanted.intersection(self._via_copper_layers(via)):
                continue
            half = via.diameter / 2
            if intersects((via.at.x - half, via.at.y - half,
                           via.at.x + half, via.at.y + half)):
                vias.append(via.as_dict())

        zones = []
        for zone in self.zones():
            if wanted and zone.layer not in wanted:
                continue
            xs = [point.x for point in zone.points]
            ys = [point.y for point in zone.points]
            if xs and intersects((min(xs), min(ys), max(xs), max(ys))):
                zones.append(zone.as_dict())

        graphics = []
        for graphic in self.graphics():
            if wanted and graphic.layer not in wanted:
                continue
            points = (
                _arc_extrema(graphic.points)
                if graphic.kind == "arc" else list(graphic.points)
            )
            if graphic.kind == "circle":
                centre, rim = graphic.points
                circle_radius = math.dist(
                    (centre.x, centre.y), (rim.x, rim.y)
                )
                bounds = (centre.x - circle_radius, centre.y - circle_radius,
                          centre.x + circle_radius, centre.y + circle_radius)
            else:
                bounds = (min(point.x for point in points),
                          min(point.y for point in points),
                          max(point.x for point in points),
                          max(point.y for point in points))
            if intersects(bounds):
                graphics.append(graphic.as_dict())

        return {
            "bounds": {"x1": left, "y1": top, "x2": right, "y2": bottom},
            "layers": list(layers),
            "selection": "bounds_intersect",
            "footprints": footprints,
            "pads": pads,
            "tracks": tracks,
            "vias": vias,
            "zones": zones,
            "graphics": graphics,
        }

    def render(self, output_file: str | Path, *, side: str = "top",
               width: int = 1200, height: int = 1200,
               quality: str = "basic", background: str = "opaque",
               zoom: float = 1.0, rotate: str = "",
               perspective: bool = False, floor: bool = False,
               pan: str = "", pivot: str = "") -> Path:
        """Render the board in 3D and return the image."""
        self.save()
        return _render.render_board(
            self._path, output_file, side=side, width=width, height=height,
            quality=quality, background=background, zoom=zoom,
            rotate=rotate or None, perspective=perspective, floor=floor,
            pan=pan or None, pivot=pivot or None)

    def render_layout(self, output_file: str | Path, *, side: str = "top",
                      dpi: int = 200, copper: bool = True,
                      silkscreen: bool = True,
                      courtyard: bool = True) -> Path:
        """Render a fast orthographic placement view."""
        # The monitor may have the editable board open for its own preview.
        # Render an in-memory snapshot beside it so `${KIPRJMOD}` still resolves
        # while no explicit preview needs to replace or lock the design file.
        snapshot = self._path.with_name(
            f".{self._path.stem}.layout-{_uid()}{self._path.suffix}"
        )
        try:
            snapshot.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            return _render.render_board_layout(
                snapshot, output_file, side=side, dpi=dpi, copper=copper,
                silkscreen=silkscreen, courtyard=courtyard,
            )
        finally:
            snapshot.unlink(missing_ok=True)


#: Load, fill and save. `save_board` is the runner's own preamble and is what
#: actually runs ``ZONE_FILLER``; this script only says which board.
_REFILL = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
board = pcbnew.LoadBoard(job["board_path"])
zones = list(board.Zones())
save_board(board, job["board_path"])
print(json.dumps({"ok": True, "zones": len(zones)}))
"""


#: Resolve line/arc/circle Edge.Cuts with KiCad itself, offset the outside
#: contour, then return the tessellated points the zone file format can store.
#: This belongs in the backend: the public contract knows only that a board has
#: an outline, never how a particular application represents one.
_OUTLINE_POLYGON = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
board = pcbnew.LoadBoard(job["board_path"])
error = pcbnew.FromMM(float(job["max_error"]))
board.GetDesignSettings().m_MaxError = error
outlines = pcbnew.SHAPE_POLY_SET()
valid = board.GetBoardPolygonOutlines(
    outlines, False, None, True, False
)
if not valid:
    raise ValueError("Edge.Cuts is not a valid closed board outline")
if outlines.OutlineCount() != 1:
    raise ValueError(
        "board-outline zones require exactly one outside contour; found "
        + str(outlines.OutlineCount())
    )
inset = pcbnew.FromMM(float(job["inset"]))
if inset:
    outlines.Deflate(
        inset, pcbnew.CORNER_STRATEGY_ALLOW_ACUTE_CORNERS, error
    )
if outlines.OutlineCount() != 1:
    raise ValueError("board outline collapsed or split under the requested inset")
chain = outlines.COutline(0)
points = [[mm(chain.CPoint(i).x), mm(chain.CPoint(i).y)]
          for i in range(chain.PointCount())]
print(json.dumps({"ok": True, "points": points}))
"""


def _flip_layer(name: str) -> str:
    """The same layer on the other side of the board."""
    if name.startswith("F."):
        return "B." + name[2:]
    if name.startswith("B."):
        return "F." + name[2:]
    return name


def create(path: str | Path, *, layers: int = 2,
           thickness: float = 1.6) -> Board:
    """Make a new, empty board and return it open for editing.

    Typed as the abstract :class:`~kicad_flow.pcb.api.Board`, not as the
    concrete class, so a caller who takes their type from the return value
    binds to the interface rather than to this backend.
    """
    tree = loads(_TEMPLATE)
    board = KiCadBoard(Path(path), tree)
    board.set_layers(layers)
    general = tree.get("general")
    if general is not None:
        thick = general.get("thickness")
        if thick is not None:
            _set(thick, 0, thickness)
    return board


def load(path: str | Path) -> Board:
    """Open an existing board."""
    file = Path(path)
    return KiCadBoard(file, loads(file.read_text(encoding="utf-8")))


#: The smallest board KiCad will open: a version, a layer table and a setup
#: block. Everything else -- footprints, copper, pours -- is appended.
_TEMPLATE = """(kicad_pcb
\t(version 20260206)
\t(generator "kicad_flow")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(pcbplotparams
\t\t\t(layerselection 0x00000000_00000000_55555555_5755f5ff)
\t\t\t(plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
\t\t\t(disableapertmacros no)
\t\t\t(usegerberextensions no)
\t\t\t(usegerberattributes yes)
\t\t\t(usegerberadvancedattributes yes)
\t\t\t(creategerberjobfile yes)
\t\t\t(dashed_line_dash_ratio 12.000000)
\t\t\t(dashed_line_gap_ratio 3.000000)
\t\t\t(svgprecision 4)
\t\t\t(plotframeref no)
\t\t\t(mode 1)
\t\t\t(useauxorigin no)
\t\t\t(dxfpolygonmode yes)
\t\t\t(dxfimperialunits yes)
\t\t\t(dxfusepcbnewfont yes)
\t\t\t(psnegative no)
\t\t\t(psa4output no)
\t\t\t(plot_black_and_white yes)
\t\t\t(sketchpadsonfab no)
\t\t\t(plotpadnumbers no)
\t\t\t(hidednponfab no)
\t\t\t(sketchdnponfab yes)
\t\t\t(crossoutdnponfab yes)
\t\t\t(subtractmaskfromsilk no)
\t\t\t(outputformat 1)
\t\t\t(mirror no)
\t\t\t(drillshape 1)
\t\t\t(scaleselection 1)
\t\t\t(outputdirectory "")
\t\t)
\t)
\t(embedded_fonts no)
)
"""

__all__ = ["KiCadBoard", "create", "load"]
