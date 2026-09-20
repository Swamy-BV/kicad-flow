"""Non-electrical schematic drawing primitives."""

from __future__ import annotations

import math
from itertools import pairwise

from kicad_flow.schematic.api import snap
from kicad_flow.schematic.types import Point, SheetGraphic

from .._sexpr import Node, Sym
from ._nodes import _f, _node, _set, _text
from ._state import SheetState

_STROKES = {"dash", "dash_dot", "dash_dot_dot", "dot", "default", "solid"}


def _is_rectangle(points: tuple[Point, ...]) -> bool:
    """Whether a closed polyline is an axis-aligned four-corner rectangle."""
    if len(points) != 5 or points[0] != points[-1]:
        return False
    corners = points[:-1]
    if len(set(corners)) != 4:
        return False
    if len({point.x for point in corners}) != 2:
        return False
    if len({point.y for point in corners}) != 2:
        return False
    return all(
        a.x == b.x or a.y == b.y
        for a, b in pairwise(points)
    )


def _from_node(node: Node) -> SheetGraphic:
    """Describe one stored graphical polyline without exposing syntax."""
    pts = node.get("pts")
    points = tuple(
        Point(_f(point, 0), _f(point, 1))
        for point in (pts.get_all("xy") if pts is not None else [])
    )
    kind = "rectangle" if _is_rectangle(points) else "polyline"
    authored = (points[0], points[2]) if kind == "rectangle" else points
    stroke = node.get("stroke")
    return SheetGraphic(
        uuid=_text(node.get("uuid")),
        kind=kind,
        points=authored,
        width=_f(stroke.get("width"), 0) if stroke is not None else 0.0,
        stroke=(
            _text(stroke.get("type"), 0, "default")
            if stroke is not None
            else "default"
        ),
    )


def graphic(
    self: SheetState,
    kind: str,
    points: list[tuple[float, float]],
    *,
    width: float = 0.0,
    stroke: str = "default",
) -> SheetGraphic:
    """Draw one caller-defined polyline or rectangle and return it."""
    if kind not in {"polyline", "rectangle"}:
        raise ValueError("schematic graphic kind must be 'polyline' or 'rectangle'")
    if stroke not in _STROKES:
        raise ValueError(f"stroke must be one of {sorted(_STROKES)}, not {stroke!r}")
    if not math.isfinite(width) or width < 0:
        raise ValueError("graphic width must be a finite non-negative number")
    if kind == "rectangle" and len(points) != 2:
        raise ValueError("a rectangle needs exactly two opposite corners")
    if kind == "polyline" and len(points) < 2:
        raise ValueError("a polyline needs at least two points")
    made = tuple(Point(snap(float(x)), snap(float(y))) for x, y in points)
    if not all(math.isfinite(value) for point in made for value in (point.x, point.y)):
        raise ValueError("graphic coordinates must be finite")
    if kind == "rectangle":
        first, opposite = made
        if first.x == opposite.x or first.y == opposite.y:
            raise ValueError("a rectangle needs non-zero width and height")
        stored: tuple[Point, ...] = (
            first,
            Point(opposite.x, first.y),
            opposite,
            Point(first.x, opposite.y),
            first,
        )
    else:
        if any(a == b for a, b in pairwise(made)):
            raise ValueError("a polyline cannot contain a zero-length segment")
        stored = made
    uid = self._uid_for(
        f"graphic:{kind}:"
        + ":".join(f"{point.x},{point.y}" for point in made)
        + f":{width}:{stroke}"
    )
    self._tree.items.append(
        _node(
            "polyline",
            [
                _node("pts", [_node("xy", [point.x, point.y]) for point in stored]),
                _node(
                    "stroke",
                    [
                        _node("width", [width]),
                        _node("type", [Sym(stroke)]),
                        _node("color", [0, 0, 0, 0]),
                    ],
                ),
                _node("uuid", [uid]),
            ],
        )
    )
    return SheetGraphic(uid, kind, made, float(width), stroke)


def graphics(self: SheetState) -> list[SheetGraphic]:
    """Every root-level graphical polyline, in file order."""
    return [
        _from_node(item)
        for item in self._tree.items
        if isinstance(item, Node) and item.name == "polyline"
    ]


def _graphic_node(self: SheetState, uuid: str) -> Node:
    """The root graphical polyline carrying *uuid*, or a useful refusal."""
    for item in self._tree.items:
        if (
            isinstance(item, Node)
            and item.name == "polyline"
            and _text(item.get("uuid")) == uuid
        ):
            return item
    raise LookupError(f"no schematic graphic with uuid {uuid!r}")


def move_graphic(
    self: SheetState, uuid: str, dx: float, dy: float
) -> SheetGraphic:
    """Shift one graphical primitive by an explicit offset."""
    node = _graphic_node(self, uuid)
    pts = node.get("pts")
    if pts is None:
        raise LookupError(f"schematic graphic {uuid!r} has no points")
    for point in pts.get_all("xy"):
        _set(point, 0, snap(_f(point, 0) + dx))
        _set(point, 1, snap(_f(point, 1) + dy))
    return _from_node(node)


def remove_graphic(self: SheetState, uuid: str) -> None:
    """Remove one graphical primitive by UUID."""
    self._tree.items.remove(_graphic_node(self, uuid))


__all__ = ["graphic", "graphics", "move_graphic", "remove_graphic"]
