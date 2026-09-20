"""PCB MCP tools for graphics."""

from __future__ import annotations

from typing import Any

from ...pcb.api import Board
from .. import _meta
from .._app import mcp
from ..limits import BatchItems
from .models import (
    ArcGraphic,
    BoardTextUpdate,
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
def add_graphics(path: str, graphics: BatchItems[GraphicSpec]) -> dict[str, Any]:
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
def move_graphics(path: str, moves: BatchItems[GraphicMove]) -> dict[str, Any]:
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
def remove_graphics(path: str, uuids: BatchItems[str]) -> dict[str, Any]:
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
def add_board_texts(path: str, texts: BatchItems[NewBoardText]) -> dict[str, Any]:
    """Put texts on board layers -- legends, fab notes, part markings.

    Back-side silkscreen wants ``mirror=true`` or it reads reversed.
    Use one text item containing actual newlines for an aligned multiline block.
    justify aligns each line left/center/right; vertical_justify anchors the
    whole block top/center/bottom. Both default to center. Alignment follows
    the text's rotation/mirroring, not the screen axes. Use separate items at
    explicit coordinates for columns or custom line spacing; padding with
    spaces is not a reliable column layout. Literal backslash-n is not a newline.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, note: NewBoardText) -> dict[str, Any]:
        made = target.text(
            note.x,
            note.y,
            note.text,
            layer=note.layer,
            width=note.width,
            height=note.height,
            thickness=note.thickness,
            rotation=note.rotation,
            mirror=note.mirror,
            justify=note.justify,
            vertical_justify=note.vertical_justify,
        )
        return made.as_dict()

    result = _atomic_items(board, texts, "texts", each)
    if result.get("ok"):
        _attach_text_bounds(board, result["texts"])
    return result


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_texts(path: str, layer: str = "") -> dict[str, Any]:
    """List editable literal board text and KiCad-measured rendered bounds."""
    try:
        board = _board(path)
        found = [item.as_dict() for item in board.texts(layer)]
        _attach_text_bounds(board, found)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found), "texts": found}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def update_board_texts(
    path: str, updates: BatchItems[BoardTextUpdate]
) -> dict[str, Any]:
    """Update exact text UUIDs atomically; omitted properties are preserved."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, update: BoardTextUpdate) -> dict[str, Any]:
        return target.update_text(
            update.uuid,
            x=update.x,
            y=update.y,
            text=update.text,
            layer=update.layer,
            width=update.width,
            height=update.height,
            thickness=update.thickness,
            rotation=update.rotation,
            mirror=update.mirror,
            justify=update.justify,
            vertical_justify=update.vertical_justify,
        ).as_dict()

    result = _atomic_items(board, updates, "texts", each)
    if result.get("ok"):
        _attach_text_bounds(board, result["texts"])
    return result


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_board_texts(path: str, uuids: BatchItems[str]) -> dict[str, Any]:
    """Remove literal board text by stable UUID, atomically and in order."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, uuid: str) -> str:
        target.remove_text(uuid)
        return uuid

    return _atomic_items(board, uuids, "removed", each)


def _attach_text_bounds(board: Board, items: list[dict[str, Any]]) -> None:
    """Attach one native measurement pass to already serialized text items."""
    if not items:
        return
    uuids = tuple(str(item["uuid"]) for item in items)
    measured = {item.uuid: item.as_dict() for item in board.text_bounds(uuids)}
    for item in items:
        bounds = measured[str(item["uuid"])]
        bounds.pop("uuid", None)
        item["bounds"] = bounds
