"""Schematic MCP tools for hierarchy."""

from __future__ import annotations

from typing import Any

from ...schematic import Sheet
from .. import _meta
from .._app import mcp
from .models import (
    NewSheetBox,
    SheetMove,
    SheetNote,
)
from .session import (
    _atomic_items,
    _fail,
    _sheet,
)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_sheets(path: str, sheets: list[NewSheetBox]) -> dict[str, Any]:
    """Put child-sheet boxes on this one, and say where their ports landed.

    A design of more than one page is two halves that meet BY NAME: a port on
    the box here, and a hierarchical label of the same name inside the child.
    Nothing checks the pairing while you draw; `check_sheet` on the ROOT does.
    Power needs no ports -- it is global.

    Then create each child with `new_sheet`, passing back the `instance_path`
    returned here, or the child's parts will not join the design's nets.

    Args:
        path: The open parent sheet.
        sheets: The boxes, in order.

    Returns:
        `sheets`, each with its `instance_path` and port positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(sheets),
        "sheets",
        lambda target, child: target.add_sheet(
            child.name,
            child.filename,
            child.x,
            child.y,
            width=child.width,
            height=child.height,
            ports=tuple((port.name, port.kind) for port in child.ports),
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_texts(path: str, notes: list[SheetNote]) -> dict[str, Any]:
    """Write notes on the sheet -- plain text that connects nothing.

    THIS IS NOT `add_labels`. A label names a net and joins everything it
    touches; a note is ignored by ERC and never appears in `list_nets`. Put
    the things a reader needs and the netlist must not have here: a revision
    block, a derivation, "all VBAT caps 50 V", why a resistor is 13k7.

    The board has `add_board_texts`; this is the schematic's equivalent.

    Args:
        path: The open sheet.
        notes: The notes, in order.

    Returns:
        `notes`, each with the point it was snapped to.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def add(target: Sheet, note: SheetNote) -> dict[str, Any]:
        at = target.text(
            note.x,
            note.y,
            note.text,
            size=note.size,
            rotation=note.rotation,
            bold=note.bold,
            justify=note.justify,
        )
        return {"text": note.text, "size": note.size, **at.as_dict()}

    return _atomic_items(sheet, list(notes), "notes", add)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_sheets(path: str, moves: list[SheetMove]) -> dict[str, Any]:
    """Move child-sheet boxes, and say where their ports ended up.

    The box moves and its ports move with it. The child FILE and its
    `instance_path` do not change, so nothing downstream needs rebuilding --
    which is the point: re-creating a root to move a box regenerates its UUID
    and orphans every child.

    Args:
        path: The open parent sheet.
        moves: The boxes to move, by name.

    Returns:
        `sheets`, each box with its new position and port coordinates.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(moves),
        "sheets",
        lambda target, move: target.move_sheet(move.name, move.x, move.y).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_sheets(path: str, names: list[str]) -> dict[str, Any]:
    """Take child-sheet boxes off this sheet, by name.

    The child FILES are left alone. This removes the boxes that refer to them,
    so the design stops walking into those pages.

    Args:
        path: The open parent sheet.
        names: Sheet names, as shown above each box.

    Returns:
        `removed`, the names that went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def remove(target: Sheet, name: str) -> str:
        target.remove_sheet(name)
        return name

    return _atomic_items(sheet, list(names), "removed", remove)
