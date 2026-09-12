"""PCB MCP tools for graphics."""

from __future__ import annotations

from typing import Any

from ...pcb.api import Board
from .. import _meta
from .._app import mcp
from .models import (
    ArcGraphic,
    CircleGraphic,
    GraphicMove,
    GraphicSpec,
    LineGraphic,
    NewBoardText,
    RectangleGraphic,
)
from .session import (
    _ERRORS,
    _atomic_items,
    _board,
    _fail,
)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_graphics(path: str, graphics: list[GraphicSpec]) -> dict[str, Any]:
    """Draw board outlines and front/back silkscreen art in order.

    Lines, arcs, circles, rectangles and polygons are geometric primitives,
    not shape generators: the caller supplies every coordinate. Join line and
    arc endpoints on ``Edge.Cuts`` to make a complex contour. Closed circles,
    rectangles and polygons make contours by themselves. KiCad decides which
    nested closed contours are cutouts; the API does not guess.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, shape: GraphicSpec) -> dict[str, Any]:
        if isinstance(shape, LineGraphic):
            points = [(shape.x1, shape.y1), (shape.x2, shape.y2)]
            fill = False
        elif isinstance(shape, ArcGraphic):
            points = [(shape.x1, shape.y1), (shape.xm, shape.ym), (shape.x2, shape.y2)]
            fill = False
        elif isinstance(shape, CircleGraphic):
            points = [(shape.x, shape.y), (shape.x + shape.radius, shape.y)]
            fill = shape.fill
        elif isinstance(shape, RectangleGraphic):
            points = [(shape.x1, shape.y1), (shape.x2, shape.y2)]
            fill = shape.fill
        else:
            points = [(point[0], point[1]) for point in shape.points]
            fill = shape.fill
        return target.graphic(
            shape.kind,
            points,
            layer=shape.layer,
            width=shape.width,
            fill=fill,
        ).as_dict()

    result = _atomic_items(board, graphics, "graphics", each)
    if result.get("ok"):
        result["size"] = list(board.size)
    return result


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_graphics(path: str, layer: str = "") -> dict[str, Any]:
    """List outline and silkscreen shapes, optionally on one layer."""
    try:
        found = _board(path).graphics(layer)
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "graphics": [shape.as_dict() for shape in found],
    }


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_graphics(path: str, moves: list[GraphicMove]) -> dict[str, Any]:
    """Shift graphical primitives by UUID; nothing else follows them."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    return _atomic_items(
        board,
        moves,
        "moved",
        lambda target, move: target.move_graphic(move.uuid, move.dx, move.dy).as_dict(),
    )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_graphics(path: str, uuids: list[str]) -> dict[str, Any]:
    """Remove graphical primitives by UUID in order."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, uuid: str) -> str:
        target.remove_graphic(uuid)
        return uuid

    return _atomic_items(board, uuids, "removed", each)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_board_texts(path: str, texts: list[NewBoardText]) -> dict[str, Any]:
    """Put texts on board layers -- legends, fab notes, part markings.

    Back-side silkscreen wants ``mirror=true`` or it reads reversed.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, note: NewBoardText) -> dict[str, Any]:
        at = target.text(
            note.x,
            note.y,
            note.text,
            layer=note.layer,
            size=note.size,
            rotation=note.rotation,
            mirror=note.mirror,
        )
        return {"text": note.text, "layer": note.layer, **at.as_dict()}

    return _atomic_items(board, texts, "texts", each)
