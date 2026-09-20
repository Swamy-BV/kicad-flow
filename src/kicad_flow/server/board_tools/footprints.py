"""PCB MCP tools for footprints."""

from __future__ import annotations

from typing import Any

from ...pcb.api import Board
from ...pcb.types import (
    PlacementProposal,
)
from .. import _meta
from .._app import mcp
from ..limits import BatchItems
from .models import (
    FootprintFieldShift,
    FootprintFieldValue,
    FootprintFlip,
    FootprintMove,
    FootprintTurn,
    PadNet,
    PlacementCandidate,
)
from .session import (
    _ERRORS,
    _atomic_items,
    _blank,
    _board,
    _fail,
)

# No manual footprint-creation MCP tool: the schematic owns footprint IDs.
# update_board_from_schematic creates them; the tools below only arrange or
# inspect footprints that already exist on the board.


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def find_footprint(
    query: str, limit: int = 20, project_dir: str = ""
) -> dict[str, Any]:
    """Search the footprint libraries for a land pattern.

    Matched against ``Library:Footprint`` ids -- search by package or family,
    ``"0603"``, ``"LQFP-64"``, ``"PinHeader_1x06"``.
    """
    try:
        found = _blank(project_dir).find_footprints(query, limit=limit)
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "footprints": [
            {
                "fp_id": f.fp_id,
                "courtyard": list(f.courtyard),
                "pads": len(f.pads),
                "has_pth": f.has_pth,
            }
            for f in found
        ],
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def footprint_pads(fp_id: str, project_dir: str = "") -> dict[str, Any]:
    """A library footprint's pads and size, before it is placed anywhere.

    Use the COURTYARD to decide how much room to leave -- not the bounding
    box, which includes silkscreen, and not the pad extent, which excludes the
    body. Its centre and polygon are local to the footprint origin here. For
    positions to actually ROUTE to, place it and read the board-coordinate
    pads and courtyard from `get_footprint` after schematic export.
    fabrication_polygon separately bounds fabrication graphics, excluding all
    text. It is a drawn envelope, not a verified body or connector mating datum.
    Missing/unsupported graphics return an empty polygon, never a guessed body.
    """
    try:
        found = _blank(project_dir).footprint_def(fp_id)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **found.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprints(
    path: str,
    moves: BatchItems[FootprintMove] | None = None,
    refs: BatchItems[str] | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
) -> dict[str, Any]:
    """Move absolute poses, or shift an explicit footprint set by one offset.

    Absolute moves can name either the native origin or courtyard centre.
    `refs` with `dx`/`dy` is the compact functional-cluster operation. Copper
    never follows a moved footprint.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    if moves is not None and refs is not None:
        return {
            "ok": False,
            "error": "give moves=[...] or refs=[...] with dx/dy, not both",
        }
    if moves is None and refs is None:
        return {
            "ok": False,
            "error": "give either moves=[...] or refs=[...] with dx/dy",
        }
    if moves is not None:
        return _atomic_items(
            board,
            moves,
            "moved",
            lambda target, item: target.move(
                item.ref, item.x, item.y, anchor=item.anchor
            ).as_dict(),
        )

    def shift(target: Board, ref: str) -> dict[str, Any]:
        was = target.footprint(ref).at
        return target.move(ref, was.x + dx, was.y + dy).as_dict()

    return _atomic_items(board, refs or [], "moved", shift)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def rotate_footprints(path: str, turns: BatchItems[FootprintTurn]) -> dict[str, Any]:
    """Turn footprints to absolute rotations. Any angle is valid."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    return _atomic_items(
        board,
        turns,
        "turned",
        lambda target, turn: target.rotate(turn.ref, turn.rotation).as_dict(),
    )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def flip_footprints(path: str, flips: BatchItems[FootprintFlip]) -> dict[str, Any]:
    """Put footprints on requested ``"F"`` or ``"B"`` sides.

    Flipping MIRRORS it: the pads run the other way. Anything you routed to
    the old pad positions now goes nowhere -- read them back.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    return _atomic_items(
        board,
        flips,
        "flipped",
        lambda target, flip: target.flip(flip.ref, flip.side).as_dict(),
    )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def remove_footprints(path: str, refs: BatchItems[str]) -> dict[str, Any]:
    """Take footprints off the board in order."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, ref: str) -> str:
        target.remove(ref)
        return ref

    return _atomic_items(board, refs, "removed", each)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint(path: str, ref: str) -> dict[str, Any]:
    """One placed footprint and its pad positions."""
    try:
        return {"ok": True, **_board(path).footprint(ref).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_footprints(path: str, with_pads: bool = False) -> dict[str, Any]:
    """Every footprint on the board.

    Args:
        path: The open board.
        with_pads: Include every pad position. Off by default -- on a full
            board that is most of the reply.
    """
    try:
        parts = _board(path).footprints()
    except _ERRORS as exc:
        return _fail(exc)
    out = []
    for p in parts:
        d = p.as_dict()
        if not with_pads:
            d["pads"] = len(p.pads)
        out.append(d)
    return {"ok": True, "count": len(out), "footprints": out}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def measure_placement(
    path: str,
    placements: BatchItems[PlacementCandidate] | None = None,
    edge_clearance: float = 0.0,
    net_limit: int = 20,
    edge_exempt_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Measure current or tentative placement without changing the board.

    `placements` contains only caller decisions. Omit it to inspect the board
    as written, or propose new poses to compare before calling
    `move_footprints`. The result reports same-side courtyard intersections,
    edge clearance, per-face courtyard area and Euclidean minimum connection
    length as separate facts; it deliberately does not combine them into a
    quality score or choose a placement. The combined courtyard ratio is
    accounting across both faces, not a capacity limit; use the front, back
    and maximum-side ratios when judging density. `net_limit` controls how
    many of the longest per-net estimates are returned; totals always cover
    every net.

    Edge checks use the courtyard, not labels or the fabrication envelope.
    For a deliberately edge-mounted connector, edge_exempt_refs can waive
    that named part's courtyard-to-board requirement. Its otherwise failing
    measurements remain in edge_exceptions. Exemptions do not waive courtyard
    overlaps, copper/board-edge DRC, or mechanical verification. They are
    per inspection, not persisted board rules; unknown references are rejected.
    """
    if net_limit < 0:
        return _fail(ValueError("net_limit must be zero or greater"))
    proposals = tuple(
        PlacementProposal(
            ref=item.ref,
            x=item.x,
            y=item.y,
            anchor=item.anchor,
            rotation=item.rotation,
            side=item.side,
        )
        for item in placements or []
    )
    try:
        measured = _board(path).measure_placement(
            proposals,
            edge_clearance=edge_clearance,
            edge_exempt_refs=tuple(edge_exempt_refs or []),
        )
    except _ERRORS as exc:
        return _fail(exc)
    result = measured.as_dict()
    net_lengths = result["net_lengths"]
    result["net_count"] = len(net_lengths)
    result["net_lengths"] = net_lengths[:net_limit]
    return {"ok": True, **result}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_pad(path: str, ref: str, pad: str) -> dict[str, Any]:
    """Where one pad is on the board -- the point to route to.

    This is the call that makes the rest usable: it applies the footprint's
    rotation and side so you never have to. A track drawn to where a pad
    would have been unrotated looks connected and is not.
    """
    try:
        point = _board(path).pad(ref, pad)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "pad": pad, **point.as_dict()}


# Intentionally not registered as MCP: pad membership belongs to the schematic.
# Kept only for direct Python compatibility; agents use schematic sync.
def set_pad_nets(path: str, pads: list[PadNet]) -> dict[str, Any]:
    """Put pads on nets in order.

    A library footprint carries no nets -- it is a land pattern, not a
    circuit. Without them the board is geometry: nothing is unrouted because
    nothing is connected, a plane joins nothing, and DRC calls every track a
    short.

    Which pad is on which net is a fact the SCHEMATIC holds. This function is
    intentionally unavailable through MCP; use `update_board_from_schematic`.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, pad: PadNet) -> dict[str, str]:
        net = target.set_net(pad.ref, pad.pad, pad.net)
        return {"ref": pad.ref, "pad": pad.pad, "net": net}

    return _atomic_items(board, pads, "pads", each)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a footprint, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _board(path).fields(ref)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_footprint_fields(
    path: str, fields: BatchItems[FootprintFieldValue]
) -> dict[str, Any]:
    """Set board-local fields; schematic-owned identity and value stay fixed."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, field: FootprintFieldValue) -> dict[str, Any]:
        if field.name in {"Reference", "Value", "Footprint"}:
            raise ValueError(
                f"{field.name} belongs to the schematic; use "
                "update_board_from_schematic"
            )
        values = target.set_field(field.ref, field.name, field.value)
        return {"ref": field.ref, "fields": values}

    return _atomic_items(board, fields, "fields", each)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprint_fields(
    path: str, moves: BatchItems[FootprintFieldShift]
) -> dict[str, Any]:
    """Move footprint fields relative to their footprints.

    The library places these and cannot know what ends up beside them. On a
    dense board they land on their own part, a neighbour, or a pad -- and
    turning a part turns its designator with it, so a row of parts at
    different angles gets a row of differently-slanted labels over the top of
    them.

    On fine-pitch passives the reference is wider than the part it names;
    `hide=true` takes it off the silkscreen without losing it.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, move: FootprintFieldShift) -> dict[str, Any]:
        at = target.move_field(
            move.ref,
            move.name,
            move.dx,
            move.dy,
            rotation=move.rotation,
            layer=move.layer,
            hide=move.hide,
        )
        return {"ref": move.ref, "field": move.name, **at.as_dict()}

    return _atomic_items(board, moves, "moved", each)
