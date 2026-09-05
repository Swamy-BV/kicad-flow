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

import copy
import math
import uuid as _uuid
from pathlib import Path
from typing import Any

from kicad_flow.pcb.api import Board
from kicad_flow.pcb.types import (
    Connection,
    Finding,
    Footprint,
    FootprintDef,
    Graphic,
    Net,
    NetPad,
    Pad,
    Point,
    Track,
    Via,
    Zone,
)

from .. import render as _render
from .._sexpr import Node, Sym, dumps, loads
from ..cli import cli as _kicad
from . import library as _fplib

#: Copper layer names by count, in board order. KiCad numbers F.Cu 0 and B.Cu
#: 2, with inner layers between; the names are what a caller uses.
_COPPER = {
    2: ("F.Cu", "B.Cu"),
    4: ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"),
    6: ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"),
}
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


def _uid() -> str:
    """A fresh UUID, as KiCad writes them."""
    return str(_uuid.uuid4())


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
        if count not in _COPPER:
            raise ValueError(f"layer count must be one of {list(_COPPER)}, "
                             f"not {count}")
        names = _COPPER[count]
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

    def save(self) -> Path:
        """Write the board to disk and return its path."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        return self._path

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
        for fp_id in _fplib.search(query, limit=limit):
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
        courtyard = _fplib.courtyard(tree)
        return FootprintDef(
            fp_id=fp_id,
            description=_text(tree.get("descr")),
            pads=pads, courtyard=courtyard,
            bbox=_fplib.bbox(tree),
            has_pth=any(p.through_hole for p in pads),
        )

    def _load_def(self, fp_id: str) -> Node:
        """Load and cache a library footprint's tree."""
        if fp_id not in self._defs:
            self._defs[fp_id] = _fplib.load(fp_id)
        return self._defs[fp_id]

    # -- parts ------------------------------------------------------------

    def place(self, fp_id: str, ref: str, x: float, y: float, *,
              rotation: float = 0.0, side: str = "F",
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
        self._set_child(node, "layer", ["F.Cu" if side == "F" else "B.Cu"])
        self._set_child(node, "at", [x, y, rotation % 360.0])
        _turn_pads(node, rotation % 360.0)
        self._set_child(node, "uuid", [_uid()])
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
                _node("layer", ["F.SilkS"]), _node("uuid", [_uid()]),
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

    def move(self, ref: str, x: float, y: float) -> Footprint:
        """Move a placed footprint. Its pads move with it; copper does not."""
        node = self._require(ref)
        at = node.get("at")
        if at is None:
            raise LookupError(f"{ref} has no position")
        _set(at, 0, float(x))
        _set(at, 1, float(y))
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

    def _as_footprint(self, node: Node, ref: str) -> Footprint:
        """Build a :class:`Footprint` from a placed ``(footprint ...)``."""
        at_node = node.get("at")
        at = Point(_f(at_node, 0), _f(at_node, 1))
        rotation = _f(at_node, 2)
        side = "B" if _text(node.get("layer")).startswith("B.") else "F"
        value = self._prop_of(node, "Value")
        pads = tuple(self._pad_of(p, at, rotation)
                     for p in node.get_all("pad"))
        cw, ch, cx, cy = _fplib.courtyard_box(node)
        if rotation % 180 == 90:
            cw, ch = ch, cw
        return Footprint(
            ref=ref, fp_id=_text(node), value=_text(value, 1) if value else "",
            at=at, rotation=rotation, side=side, pads=pads,
            courtyard=(cw, ch),
            courtyard_offset=_pad_on_board(cx, cy, Point(0.0, 0.0),
                                           rotation),
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
        made = Track(Point(float(x1), float(y1)), Point(float(x2), float(y2)),
                     layer, float(width), net)
        self._tree.items.append(_node("segment", [
            _node("start", [made.start.x, made.start.y]),
            _node("end", [made.end.x, made.end.y]),
            _node("width", [made.width]),
            _node("layer", [layer]),
            _node("net", [net]),
            _node("uuid", [_uid()]),
        ]))
        return made

    def via(self, x: float, y: float, *, net: str = "",
            diameter: float = 0.6, drill: float = 0.3,
            layers: tuple[str, str] = ("F.Cu", "B.Cu")) -> Via:
        """Drill a plated via joining *layers* and return it."""
        for name in layers:
            self._require_layer(name)
        made = Via(Point(float(x), float(y)), float(diameter), float(drill),
                   net, layers)
        self._tree.items.append(_node("via", [
            _node("at", [made.at.x, made.at.y]),
            _node("size", [made.diameter]),
            _node("drill", [made.drill]),
            _node("layers", list(layers)),
            _node("net", [net]),
            _node("uuid", [_uid()]),
        ]))
        return made

    def zone(self, points: list[tuple[float, float]], *, layer: str,
             net: str = "", clearance: float = 0.0,
             forbids: tuple[str, ...] = ()) -> Zone:
        """Pour copper inside *points* on *layer*, or fence a region off."""
        if len(points) < 3:
            raise ValueError("a zone needs at least 3 points")
        self._require_layer(layer)
        made = Zone(net, layer, tuple(Point(float(x), float(y))
                                      for x, y in points), False, forbids)
        polygon = _node("polygon", [
            _node("pts", [_node("xy", [p.x, p.y]) for p in made.points])
        ])
        items: list[Any] = [
            _node("net", [net]),
            _node("layer", [layer]),
            _node("uuid", [_uid()]),
            _node("hatch", [Sym("edge"), 0.5]),
            _node("connect_pads", [_node("clearance", [clearance or 0.5])]),
            _node("min_thickness", [0.25]),
            _node("fill", [Sym("yes"),
                           _node("thermal_gap", [0.5]),
                           _node("thermal_bridge_width", [0.5])]),
            polygon,
        ]
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

    def remove_copper(self, *, net: str = "", layer: str = "",
                      tracks: bool = True, vias: bool = True) -> int:
        """Delete copper, filtered by net and layer, and say how much went."""
        kinds = ([("segment", "layer")] if tracks else []) + \
                ([("via", "layers")] if vias else [])
        gone = 0
        for name, layer_key in kinds:
            for node in list(self._tree.get_all(name)):
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
                _net_name(node.get("net")),
            ))
        return out

    def vias(self) -> list[Via]:
        """Every via on the board."""
        out = []
        for node in self._tree.get_all("via"):
            at = node.get("at")
            layers = node.get("layers")
            names = [str(x) for x in (layers.items[1:] if layers else [])]
            out.append(Via(
                Point(_f(at, 0), _f(at, 1)),
                _f(node.get("size"), 0), _f(node.get("drill"), 0),
                _net_name(node.get("net")),
                (names[0], names[-1]) if names else ("F.Cu", "B.Cu"),
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
            forbids = tuple(
                name for name, key in (("tracks", "tracks"), ("vias", "vias"),
                                       ("pads", "pads"), ("pours", "copperpour"),
                                       ("footprints", "footprints"))
                if keepout is not None
                and _text(keepout.get(key)) == "not_allowed"
            )
            out.append(Zone(
                _net_name(node.get("net")), _text(node.get("layer")),
                points, node.get("filled_polygon") is not None, forbids,
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

    def unrouted(self) -> list[Connection]:
        """Every pair of pads on a net with no copper between them.

        Connectivity is walked here rather than asked of KiCad: pads, track
        ends and vias join when they share a point, and **a filled plane joins
        everything of its own net that it covers**. That last one is not a
        refinement -- without it a board whose power and ground are planes
        reports every pad on them as unrouted, which on a 160-LED board was
        25,760 connections that were all in fact copper.
        """
        out: list[Connection] = []
        # One pass for every pad position, rather than a lookup per pair: the
        # pairs are quadratic in the pads on a net and a plane net has
        # hundreds of them.
        where = {(f.ref, p.number): p.at
                 for f in self.footprints() for p in f.pads}
        for net in self.nets():
            if len(net.pads) < 2:
                continue
            groups = self._groups_of(net.name)
            index: dict[tuple[float, float], int] = {}
            for n, group in enumerate(groups):
                for point in group:
                    index[point] = n
            seen: set[tuple[str, str]] = set()
            for i, a in enumerate(net.pads):
                for b in net.pads[i + 1:]:
                    pa = where[(a.ref, a.pad)]
                    pb = where[(b.ref, b.pad)]
                    ga = index.get((round(pa.x, 3), round(pa.y, 3)))
                    gb = index.get((round(pb.x, 3), round(pb.y, 3)))
                    if ga is not None and ga == gb:
                        continue
                    pair = (f"{a.ref}.{a.pad}", f"{b.ref}.{b.pad}")
                    if pair in seen:
                        continue
                    seen.add(pair)
                    out.append(Connection(
                        net.name, a, b,
                        math.dist((pa.x, pa.y), (pb.x, pb.y))))
        return out

    def _groups_of(self, net: str) -> list[set[tuple[float, float]]]:
        """Points on *net* joined by copper, as connected groups."""
        edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
        # A filled plane is copper. Everything of its own net inside its
        # outline is joined to everything else, with no track between them.
        for zone in self.zones():
            if zone.net != net or not zone.filled or zone.forbids:
                continue
            poly = [(p.x, p.y) for p in zone.points]
            inside: list[tuple[float, float]] = []
            for part in self.footprints():
                for pd in part.pads:
                    if pd.net == net and _point_in_polygon(
                            (pd.at.x, pd.at.y), poly):
                        inside.append((round(pd.at.x, 3), round(pd.at.y, 3)))
            for v in self.vias():
                if v.net == net and _point_in_polygon((v.at.x, v.at.y), poly):
                    inside.append((round(v.at.x, 3), round(v.at.y, 3)))
            for point in inside[1:]:
                edges.append((inside[0], point))
        # Pads of one net that share a point are one point. A USB-C receptacle
        # duplicates VBUS and GND across both rows at identical coordinates,
        # and without this they are reported as needing 0.0 mm of track.
        seen_at: dict[tuple[float, float], tuple[float, float]] = {}
        for part in self.footprints():
            for pd in part.pads:
                if pd.net != net:
                    continue
                key = (round(pd.at.x, 3), round(pd.at.y, 3))
                if key in seen_at:
                    edges.append((seen_at[key], key))
                seen_at[key] = key
        for t in self.tracks():
            if t.net == net:
                edges.append(((round(t.start.x, 3), round(t.start.y, 3)),
                              (round(t.end.x, 3), round(t.end.y, 3))))
        for v in self.vias():
            if v.net == net:
                key = (round(v.at.x, 3), round(v.at.y, 3))
                edges.append((key, key))
        parent: dict[tuple[float, float], tuple[float, float]] = {}

        def find(k: tuple[float, float]) -> tuple[float, float]:
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for a, b in edges:
            parent[find(a)] = find(b)
        groups: dict[tuple[float, float], set[tuple[float, float]]] = {}
        for k in list(parent):
            groups.setdefault(find(k), set()).add(k)
        return list(groups.values())

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
                out.append(Finding(
                    severity=str(violation.get("severity", "error")),
                    kind=str(violation.get("type", kind)),
                    message=str(violation.get("description", "")),
                    ref=ref, pad=number, at=at, other_ref=other,
                ))
        return out

    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What is at a point: pads, track ends, vias and zones."""
        def near(px: float, py: float) -> bool:
            return abs(px - x) <= radius and abs(py - y) <= radius

        pads = [{"ref": f.ref, "pad": p.number, "net": p.net,
                 "layers": list(p.layers)}
                for f in self.footprints() for p in f.pads
                if near(p.at.x, p.at.y)]
        ends = [{"layer": t.layer, "net": t.net} for t in self.tracks()
                if near(t.start.x, t.start.y) or near(t.end.x, t.end.y)]
        vias = [v.as_dict() for v in self.vias() if near(v.at.x, v.at.y)]
        return {"x": round(x, 3), "y": round(y, 3), "pads": pads,
                "track_ends": ends, "vias": vias,
                "connected": len(pads) + len(ends) + len(vias) > 1}

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
