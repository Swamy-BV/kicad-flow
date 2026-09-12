"""Schematic MCP tools for components."""

from __future__ import annotations

from typing import Any

from ...schematic import Sheet
from .. import _meta
from .._app import mcp
from .models import (
    FieldRef,
    FieldShift,
    FieldValue,
    NewPart,
    PartFlip,
    PartMove,
    PartTurn,
)
from .session import (
    _atomic_items,
    _fail,
    _sheet,
)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_component(path: str, ref: str, unit: int = 1) -> dict[str, Any]:
    """One placed unit and its pin positions."""
    try:
        return {"ok": True, **_sheet(path).part(ref, unit=unit).as_dict()}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_components(path: str, with_pins: bool = False) -> dict[str, Any]:
    """Every part on the sheet.

    Args:
        path: The open sheet.
        with_pins: Include every pin position. Off by default -- on a full
            sheet that is most of the reply, and `get_component` gets one part.
    """
    try:
        parts = _sheet(path).parts()
    except LookupError as exc:
        return _fail(exc)
    out = []
    for p in parts:
        d = p.as_dict()
        if not with_pins:
            d["pins"] = len(p.pins)
        out.append(d)
    return {"ok": True, "count": len(out), "parts": out}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_pin(path: str, ref: str, pin: str) -> dict[str, Any]:
    """Where one pin is on the sheet -- the point to wire to.

    *pin* may be its number (``"1"``) or its name (``"VCC"``).
    """
    try:
        point = _sheet(path).pin(ref, pin)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "pin": pin, **point.as_dict()}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a part, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _sheet(path).fields(ref)}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def next_ref(path: str, prefix: str) -> dict[str, Any]:
    """The next unused reference with this prefix, e.g. ``"R"`` -> ``"R7"``.

    All the annotation this API needs: `add_component` demands a reference
    and refuses a duplicate, so a sheet cannot end up unannotated. This just
    saves you keeping a counter.
    """
    try:
        return {"ok": True, "prefix": prefix, "ref": _sheet(path).next_ref(prefix)}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_components(path: str, parts: list[NewPart]) -> dict[str, Any]:
    """Place parts on the sheet, in order.

    This applies an already-decided functional-block placement. Before calling,
    query every unique library symbol with `symbol_pins` and compose the whole
    block: signal flow, field space, wire corridors, multi-units, and explicit
    coordinates. This primitive neither plans nor repairs that composition.

    Each reply carries that part's pins at their positions ON THE SHEET, with
    rotation and mirroring already applied. Place everything first, then read
    the pins out of this reply and draw the wires with `add_wires`: a wire
    aimed at a coordinate you worked out yourself, rather than one reported
    here, looks connected and is not.

    Args:
        path: The open sheet.
        parts: The parts, in order.

    Returns:
        `parts`, one entry per placement, each with its pins.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(parts),
        "parts",
        lambda target, part: target.place(
            part.lib_id,
            part.ref,
            part.x,
            part.y,
            value=part.value,
            rotation=part.rotation,
            mirror=part.mirror,
            unit=part.unit,
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_components(
    path: str,
    moves: list[PartMove] | None = None,
    refs: list[str] | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    unit: int = 1,
) -> dict[str, Any]:
    """Move parts: each to a position of its own, or a set by one offset.

    Two ways, because a caller wants both. `moves` puts each part somewhere
    absolute. `refs` with `dx`/`dy` SHIFTS that set, which is what moving a
    block of a sheet actually is -- every other move is absolute, so without
    it a caller reads each position back, adds the offset itself, and calls
    once per part.

    Choosing the set is a separate question and stays one: `list_components`
    reports every part and where it is, you filter it however you like, and
    pass the references here. Nothing is inferred from context.

    **Wires do not follow.** A part moved out from under its wires is joined
    to nothing, and only `list_nets` says so.

    Args:
        path: The open sheet.
        moves: Absolute placements, one per part.
        refs: Parts to shift. Ignored when `moves` is given.
        dx: Offset in mm, with `refs`.
        dy: Offset in mm, with `refs`.
        unit: Unit of a multi-unit symbol, with `refs`.

    Returns:
        `moved`, each part at its new position.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    if moves is None and refs is None:
        return {
            "ok": False,
            "error": "give either moves=[...] or refs=[...] with dx/dy",
        }
    if moves is not None:
        return _atomic_items(
            sheet,
            list(moves),
            "moved",
            lambda target, move: target.move(
                move.ref, move.x, move.y, unit=move.unit
            ).as_dict(),
        )

    def shift(target: Sheet, ref: str) -> dict[str, Any]:
        was = target.part(ref, unit=unit).at
        return target.move(ref, was.x + dx, was.y + dy, unit=unit).as_dict()

    return _atomic_items(sheet, list(refs or []), "moved", shift)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def rotate_components(path: str, turns: list[PartTurn]) -> dict[str, Any]:
    """Turn parts. A rotation moves the pins, and the reply says where to.

    Args:
        path: The open sheet.
        turns: The rotations, in order.

    Returns:
        `turned`, each part with its pins at their new positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(turns),
        "turned",
        lambda target, turn: target.rotate(
            turn.ref, turn.rotation, unit=turn.unit
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def mirror_components(path: str, mirrors: list[PartFlip]) -> dict[str, Any]:
    """Mirror parts about an axis, and say where the pins ended up.

    Args:
        path: The open sheet.
        mirrors: The mirrorings, in order.

    Returns:
        `mirrored`, each part with its pins at their new positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(mirrors),
        "mirrored",
        lambda target, mirror: target.mirror(
            mirror.ref, mirror.axis, unit=mirror.unit
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_components(path: str, refs: list[str], unit: int = 1) -> dict[str, Any]:
    """Take parts off the sheet. Wires and labels stay where they are.

    Args:
        path: The open sheet.
        refs: References to remove.
        unit: Unit of a multi-unit symbol.

    Returns:
        `removed`, the references that went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def remove(target: Sheet, ref: str) -> str:
        target.remove(ref, unit=unit)
        return ref

    return _atomic_items(sheet, list(refs), "removed", remove)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def set_fields(path: str, fields: list[FieldValue]) -> dict[str, Any]:
    """Set fields on parts -- `Footprint`, `Datasheet`, a custom one.

    Args:
        path: The open sheet.
        fields: The values, in order.

    Returns:
        `fields`, each part's full field set after the write.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(fields),
        "fields",
        lambda target, field: {
            "ref": field.ref,
            "fields": target.set_field(field.ref, field.name, field.value),
        },
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_fields(path: str, moves: list[FieldShift]) -> dict[str, Any]:
    """Move a part's text relative to the part, so it stops printing on it.

    A library places these and cannot know what ends up beside them.

    Args:
        path: The open sheet.
        moves: The moves, in order.

    Returns:
        `moved`, each field at its new position.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def move(target: Sheet, item: FieldShift) -> dict[str, Any]:
        at = target.move_field(
            item.ref,
            item.name,
            item.dx,
            item.dy,
            rotation=item.rotation,
            justify=item.justify,
        )
        return {"ref": item.ref, "field": item.name, **at.as_dict()}

    return _atomic_items(sheet, list(moves), "moved", move)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_fields(path: str, fields: list[FieldRef]) -> dict[str, Any]:
    """Delete fields from parts.

    Setting a field to an empty string is a DIFFERENT thing: it stays present
    and blank, KiCad keeps writing it, and a BOM still sees the column.

    Args:
        path: The open sheet.
        fields: The fields to delete.

    Returns:
        `fields`, each part's remaining fields after the delete.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(fields),
        "fields",
        lambda target, field: {
            "ref": field.ref,
            "fields": target.remove_field(field.ref, field.name, unit=field.unit),
        },
    )
