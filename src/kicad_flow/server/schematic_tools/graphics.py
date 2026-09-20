"""MCP tools for non-electrical schematic drawing primitives."""

from __future__ import annotations

from typing import Any

from ...schematic import Sheet
from .. import _meta
from .._app import mcp
from .models import (
    SchematicGraphicMove,
    SchematicGraphicSpec,
    SchematicPolyline,
    SchematicRectangle,
    SheetTextUpdate,
)
from .session import _atomic_items, _fail, _sheet


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_schematic_graphics(
    path: str, graphics: list[SchematicGraphicSpec]
) -> dict[str, Any]:
    """Draw non-electrical polylines and rectangles in order.

    These shapes only organize the page visually; they never create nets or
    assign semantic groups. The caller supplies every vertex or both opposite
    rectangle corners. Add headings separately with `add_texts` so shape and
    text remain independently editable primitives.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def each(target: Sheet, shape: SchematicGraphicSpec) -> dict[str, Any]:
        if isinstance(shape, SchematicRectangle):
            points = [(shape.x1, shape.y1), (shape.x2, shape.y2)]
        elif isinstance(shape, SchematicPolyline):
            points = list(shape.points)
        else:  # pragma: no cover - the discriminated schema prevents this
            raise ValueError(f"unsupported schematic graphic {shape!r}")
        return target.graphic(
            shape.kind,
            points,
            width=shape.width,
            stroke=shape.stroke,
        ).as_dict()

    return _atomic_items(sheet, list(graphics), "graphics", each)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_schematic_graphics(path: str) -> dict[str, Any]:
    """List non-electrical schematic graphics with their stable UUIDs."""
    try:
        found = _sheet(path).graphics()
    except (LookupError, OSError, ValueError) as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "graphics": [graphic.as_dict() for graphic in found],
    }


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_schematic_graphics(
    path: str, moves: list[SchematicGraphicMove]
) -> dict[str, Any]:
    """Shift graphical primitives by UUID; page contents do not follow."""
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(moves),
        "graphics",
        lambda target, move: target.move_graphic(
            move.uuid, move.dx, move.dy
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_schematic_graphics(path: str, uuids: list[str]) -> dict[str, Any]:
    """Remove graphical primitives by stable UUID, atomically and in order."""
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def each(target: Sheet, uuid: str) -> str:
        target.remove_graphic(uuid)
        return uuid

    return _atomic_items(sheet, list(uuids), "removed", each)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_texts(path: str) -> dict[str, Any]:
    """List literal schematic notes with stable UUIDs and authored properties."""
    try:
        found = _sheet(path).texts()
    except (LookupError, OSError, ValueError) as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "notes": [note.as_dict() for note in found],
    }


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def update_texts(path: str, updates: list[SheetTextUpdate]) -> dict[str, Any]:
    """Update exact schematic note UUIDs; omitted properties are preserved."""
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(updates),
        "notes",
        lambda target, update: target.update_text(
            update.uuid,
            x=update.x,
            y=update.y,
            text=update.text,
            size=update.size,
            rotation=update.rotation,
            bold=update.bold,
            justify=update.justify,
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_texts(path: str, uuids: list[str]) -> dict[str, Any]:
    """Remove literal schematic notes by stable UUID, atomically and in order."""
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def each(target: Sheet, uuid: str) -> str:
        target.remove_text(uuid)
        return uuid

    return _atomic_items(sheet, list(uuids), "removed", each)


__all__ = [
    "add_schematic_graphics",
    "list_schematic_graphics",
    "list_texts",
    "move_schematic_graphics",
    "remove_schematic_graphics",
    "remove_texts",
    "update_texts",
]
