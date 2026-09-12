"""Board outline, graphic primitives and text editing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from kicad_flow.pcb.types import (
    Graphic,
    Point,
)

from .._sexpr import Node, Sym, dumps
from ._constants import (
    _GRAPHIC_KINDS,
    _GRAPHIC_LAYERS,
    _GRAPHIC_NODES,
    _OUTLINE_POLYGON,
)
from ._geometry import (
    _circle_through,
)
from ._nodes import (
    _f,
    _fmt,
    _node,
    _text,
    _uid,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def graphic(
    self: KiCadBoard,
    kind: str,
    points: list[tuple[float, float]],
    *,
    layer: str,
    width: float = 0.1,
    fill: bool = False,
) -> Graphic:
    """Draw one outline or silkscreen primitive and return it."""
    if kind not in _GRAPHIC_NODES:
        raise ValueError(f"kind must be one of {sorted(_GRAPHIC_NODES)}, not {kind!r}")
    if layer not in _GRAPHIC_LAYERS:
        raise ValueError(
            f"graphic layer must be one of {list(_GRAPHIC_LAYERS)}, not {layer!r}"
        )
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
    stroke = _node("stroke", [_node("width", [width]), _node("type", [Sym("solid")])])
    common = [stroke]
    if kind in ("circle", "rectangle", "polygon"):
        common.append(_node("fill", [Sym("yes" if fill else "no")]))
    common.extend([_node("layer", [layer]), _node("uuid", [uid])])
    if kind == "line":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    elif kind == "arc":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("mid", [made[1].x, made[1].y]),
            _node("end", [made[2].x, made[2].y]),
        ]
    elif kind == "circle":
        geometry = [
            _node("center", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    elif kind == "rectangle":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    else:
        geometry = [_node("pts", [_node("xy", [point.x, point.y]) for point in made])]
    self._tree.items.append(_node(_GRAPHIC_NODES[kind], geometry + common))
    return Graphic(uid, kind, layer, made, float(width), fill)


def graphics(self: KiCadBoard, layer: str = "") -> list[Graphic]:
    """Every outline and silkscreen primitive, in file order."""
    if layer and layer not in _GRAPHIC_LAYERS:
        raise ValueError(
            f"graphic layer must be one of {list(_GRAPHIC_LAYERS)}, not {layer!r}"
        )
    out: list[Graphic] = []
    for item in self._tree.items:
        if not isinstance(item, Node) or item.name not in _GRAPHIC_KINDS:
            continue
        found = self._graphic_from_node(item)
        if found.layer in _GRAPHIC_LAYERS and (not layer or found.layer == layer):
            out.append(found)
    return out


def outline_polygon(
    self: KiCadBoard, *, inset: float, max_error: float
) -> tuple[Point, ...]:
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
        result = run_pcbnew(
            _OUTLINE_POLYGON,
            {
                "board_path": str(scratch),
                "inset": inset,
                "max_error": max_error,
            },
        )
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


def move_graphic(self: KiCadBoard, uuid: str, dx: float, dy: float) -> Graphic:
    """Shift one graphic primitive by an offset."""
    node = self._graphic_node(uuid)
    for name in ("start", "mid", "end", "center"):
        point = node.get(name)
        if point is not None:
            point.items = [
                point.items[0],
                Sym(_fmt(_f(point, 0) + dx)),
                Sym(_fmt(_f(point, 1) + dy)),
            ]
    pts = node.get("pts")
    if pts is not None:
        for point in pts.get_all("xy"):
            point.items = [
                point.items[0],
                Sym(_fmt(_f(point, 0) + dx)),
                Sym(_fmt(_f(point, 1) + dy)),
            ]
    return self._graphic_from_node(node)


def remove_graphic(self: KiCadBoard, uuid: str) -> None:
    """Remove one graphic primitive by UUID."""
    self._tree.items.remove(self._graphic_node(uuid))


def _graphic_node(self: KiCadBoard, uuid: str) -> Node:
    """The top-level graphic carrying *uuid*."""
    for item in self._tree.items:
        if (
            isinstance(item, Node)
            and item.name in _GRAPHIC_KINDS
            and _text(item.get("uuid")) == uuid
        ):
            return item
    raise LookupError(f"no graphic with uuid {uuid!r}")


def _graphic_from_node(self: KiCadBoard, node: Node) -> Graphic:
    """Read one KiCad graphical node into the board contract."""
    kind = _GRAPHIC_KINDS[node.name]
    names = {
        "line": ("start", "end"),
        "arc": ("start", "mid", "end"),
        "circle": ("center", "end"),
        "rectangle": ("start", "end"),
    }
    if kind == "polygon":
        pts = node.get("pts")
        point_nodes = pts.get_all("xy") if pts is not None else []
    else:
        point_nodes = []
        for name in names[kind]:
            point = node.get(name)
            if point is not None:
                point_nodes.append(point)
    points = tuple(Point(_f(point, 0), _f(point, 1)) for point in point_nodes)
    stroke = node.get("stroke")
    fill = node.get("fill")
    return Graphic(
        _text(node.get("uuid")),
        kind,
        _text(node.get("layer")),
        points,
        _f(stroke.get("width"), 0) if stroke is not None else 0.0,
        _text(fill) in ("yes", "solid") if fill is not None else False,
    )


def text(
    self: KiCadBoard,
    x: float,
    y: float,
    text: str,
    *,
    layer: str,
    size: float = 1.0,
    rotation: float = 0.0,
    mirror: bool = False,
) -> Point:
    """Put text on a layer -- a legend, a fab note, a designator."""
    at = Point(float(x), float(y))
    effects: list[Any] = [
        _node("font", [_node("size", [size, size]), _node("thickness", [size * 0.15])])
    ]
    if mirror:
        effects.append(_node("justify", [Sym("mirror")]))
    self._tree.items.append(
        _node(
            "gr_text",
            [
                text,
                _node("at", [at.x, at.y, rotation % 360.0]),
                _node("layer", [layer]),
                _node("uuid", [_uid()]),
                _node("effects", effects),
            ],
        )
    )
    return at
