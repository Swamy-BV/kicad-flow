"""Schematic MCP tools for connections."""

from __future__ import annotations

from typing import Any

from ...schematic import Sheet
from .. import _meta
from .._app import mcp
from .models import (
    LabelShift,
    LabelTarget,
    LabelTurn,
    NewFlag,
    NewLabel,
    NewPower,
    Segment,
    Spot,
    WireEnds,
    WireShift,
)
from .session import (
    _atomic_items,
    _counted,
    _fail,
    _sheet,
)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_wires(path: str) -> dict[str, Any]:
    """Every wire segment on the sheet."""
    try:
        segments = _sheet(path).wires()
    except LookupError as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(segments),
        "wires": [{"start": a.as_dict(), "end": b.as_dict()} for a, b in segments],
    }


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_labels(path: str) -> dict[str, Any]:
    """Every label, including its stable UUID for move/remove operations."""
    try:
        labels = _sheet(path).labels()
    except LookupError as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(labels),
        "labels": [label.as_dict() for label in labels],
    }


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_wires(path: str, wires: list[Segment]) -> dict[str, Any]:
    """Draw wires: the default connection method for nearby components.

    A wire connects by TOUCHING a pin, so both ends are snapped to the grid
    and must land exactly on the pin coordinates `add_components` reported.
    Two wires that cross do not connect unless a junction says they do.
    Prefer these visible wires within a functional block; use labels only for
    distant, cross-sheet, or otherwise unreadable connections.

    Args:
        path: The open sheet.
        wires: The segments, in order.

    Returns:
        `wires`, one `{start, end}` per segment as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)

    def wire(target: Sheet, segment: Segment) -> dict[str, Any]:
        a, b = target.wire(segment.x1, segment.y1, segment.x2, segment.y2)
        return {"start": a.as_dict(), "end": b.as_dict()}

    return _atomic_items(sheet, list(wires), "wires", wire)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_labels(path: str, labels: list[NewLabel]) -> dict[str, Any]:
    """Name necessary distant or cross-sheet nets, not ordinary local wiring.

    Do not use labels named GND or +3V3; place those rails with `add_power` and
    wire to the returned symbol pins. Use a global label only for a signal that
    is intentionally shared across the design. Two labels with the same text
    are one net.

    Prefer direct wires between nearby components. For a necessary local label,
    draw a short wire stub away from the component and place the label at its
    free end; anchoring local text directly on a pin often draws it over the
    body. Set its justification explicitly for the side of the component.

    Args:
        path: The open sheet.
        labels: The labels, in order.

    Returns:
        `labels`, one entry per label with where it landed.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(labels),
        "labels",
        lambda target, label: target.label(
            label.x,
            label.y,
            label.text,
            kind=label.kind,
            rotation=label.rotation,
            justify=label.justify,
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_junctions(path: str, points: list[Spot]) -> dict[str, Any]:
    """Join crossing wires. Without one, wires that cross are separate nets.

    Args:
        path: The open sheet.
        points: Where to put them.

    Returns:
        `points`, each as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(points),
        "points",
        lambda target, point: target.junction(point.x, point.y).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_no_connects(path: str, points: list[Spot]) -> dict[str, Any]:
    """Mark pins deliberately unconnected, so ERC stops reporting them.

    Args:
        path: The open sheet.
        points: Pin positions, from `add_components` or `get_pin`.

    Returns:
        `points`, each as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(points),
        "points",
        lambda target, point: target.no_connect(point.x, point.y).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power(path: str, symbols: list[NewPower]) -> dict[str, Any]:
    """Place power symbols, especially for every local GND and +3V3 cluster.

    Use this instead of labels named GND or +3V3. Place a symbol near the
    component or readable local power cluster, read the single pin returned,
    then connect component pins to it with `add_wires`. Grounds normally point
    down and positive rails up. A rail joins BY NAME across every sheet.

    Singular where the rest are plural, because `add_powers` is not
    English. It takes a list like every other write here.

    Args:
        path: The open sheet.
        symbols: The symbols, in order.

    Returns:
        `symbols`, each with its single pin -- the point to wire to.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(symbols),
        "symbols",
        lambda target, symbol: target.power(
            symbol.x, symbol.y, symbol.net, rotation=symbol.rotation
        ).as_dict(),
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power_flags(path: str, flags: list[NewFlag]) -> dict[str, Any]:
    """Place PWR_FLAGs, which tell ERC a rail is driven.

    A rail of only power INPUTS reads as undriven however many symbols sit on
    it; one flag per rail is what settles that.

    Args:
        path: The open sheet.
        flags: The flags, in order.

    Returns:
        `flags`, each with its single pin.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _atomic_items(
        sheet,
        list(flags),
        "flags",
        lambda target, flag: target.power_flag(
            flag.x, flag.y, rotation=flag.rotation
        ).as_dict(),
    )


def _remove_label(sheet: Sheet, target: LabelTarget) -> int:
    """Remove one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.remove_label_by_id(target.uuid)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.remove_label(target.x, target.y)


def _move_label(sheet: Sheet, target: LabelShift) -> int:
    """Move one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.move_label_by_id(target.uuid, target.dx, target.dy)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.move_label(target.x, target.y, target.dx, target.dy)


def _rotate_label(sheet: Sheet, target: LabelTurn) -> int:
    """Rotate one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.rotate_label_by_id(target.uuid, target.rotation)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.rotate_label(target.x, target.y, target.rotation)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_wires(path: str, wires: list[WireEnds]) -> dict[str, Any]:
    """Delete wires running between the given pairs of points.

    Either direction matches -- a segment does not know which end was drawn
    first. `list_wires` reports the coordinates to pass here.

    Args:
        path: The open sheet.
        wires: The segments to delete.

    Returns:
        `removed`, how many segments actually went. Zero means nothing was at
        those coordinates.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(
        sheet,
        list(wires),
        lambda s, w: s.remove_wire(w.x1, w.y1, w.x2, w.y2),
        "removed",
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_wires(path: str, wires: list[WireShift]) -> dict[str, Any]:
    """Shift wires by an offset. Both ends move, so length and angle survive.

    A wire moved off a pin is no longer joined to it and nothing on the sheet
    says so -- `list_nets` is what says so.

    Args:
        path: The open sheet.
        wires: The segments to shift, and by how much.

    Returns:
        `moved`, how many segments were found and shifted.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(
        sheet,
        list(wires),
        lambda s, w: s.move_wire(w.x1, w.y1, w.x2, w.y2, w.dx, w.dy),
        "moved",
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_labels(path: str, points: list[LabelTarget]) -> dict[str, Any]:
    """Delete labels by UUID, or legacy snapped position.

    Args:
        path: The open sheet.
        points: UUID targets from `add_labels`/`list_labels`, or positions.

    Returns:
        `removed`, how many labels actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(points), _remove_label, "removed")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_labels(path: str, moves: list[LabelShift]) -> dict[str, Any]:
    """Shift labels by an offset.

    A label names the net it TOUCHES. Move one off its wire and it names
    nothing, quietly -- read `list_nets` back afterwards.

    Args:
        path: The open sheet.
        moves: Each label's UUID or position, and how far to move it.

    Returns:
        `moved`, how many labels were found and shifted.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(moves), _move_label, "moved")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def rotate_labels(path: str, turns: list[LabelTurn]) -> dict[str, Any]:
    """Turn labels at these points.

    Which way a GLOBAL label points is its justification, not its rotation --
    see `add_labels`.

    Args:
        path: The open sheet.
        turns: Each label's UUID or position, and the requested angle.

    Returns:
        `turned`, how many labels were found and turned.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(turns), _rotate_label, "turned")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_junctions(path: str, points: list[Spot]) -> dict[str, Any]:
    """Delete junctions at these points.

    Removing one separates wires that cross there into different nets, so read
    `list_nets` back afterwards.

    Args:
        path: The open sheet.
        points: Where the junctions are.

    Returns:
        `removed`, how many junctions actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(
        sheet, list(points), lambda s, p: s.remove_junction(p.x, p.y), "removed"
    )


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_no_connects(path: str, points: list[Spot]) -> dict[str, Any]:
    """Delete no-connect marks at these points.

    A no-connect SUPPRESSES an ERC error. Taking one off lets a real fault be
    reported again, which is usually the reason to.

    Args:
        path: The open sheet.
        points: Where the marks are.

    Returns:
        `removed`, how many marks actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(
        sheet, list(points), lambda s, p: s.remove_no_connect(p.x, p.y), "removed"
    )
