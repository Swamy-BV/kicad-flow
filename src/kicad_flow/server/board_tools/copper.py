"""PCB MCP tools for copper."""

from __future__ import annotations

from typing import Any

from ...pcb.api import Board
from ...pcb.types import (
    Point,
    Zone,
)
from .. import _meta
from .._app import mcp
from .models import (
    NewTrack,
    NewVia,
    NewZone,
)
from .session import (
    _ERRORS,
    _atomic_items,
    _board,
    _fail,
)


def _zone_value(board: Board, item: NewZone) -> Zone:
    """Resolve one zone request to the format-neutral primitive value."""
    if item.boundary == "board_outline":
        assert item.inset is not None
        points = board.outline_polygon(inset=item.inset, max_error=item.max_error)
    else:
        points = tuple(Point(point[0], point[1]) for point in item.points)
    return Zone(
        net=item.net,
        layer=item.layer,
        points=points,
        pad_connection=item.pad_connection,
        clearance=item.clearance,
        min_thickness=item.min_thickness,
        thermal_gap=item.thermal_gap,
        thermal_spoke_width=item.thermal_spoke_width,
        priority=item.priority,
        island_removal=item.island_removal,
        min_island_area=item.min_island_area,
        forbids=tuple(item.forbids),
    )


def _provider_via_refusal(path: str, vias: list[NewVia]) -> dict[str, Any] | None:
    """Return a precise active-profile refusal for unsupported via kinds."""
    from .._fabrication import read_profile

    profile = read_profile(path)
    if profile is None:
        return None
    allowed = profile.get("via_kinds")
    if not isinstance(allowed, list):
        return None
    for index, via in enumerate(vias):
        if via.kind not in allowed:
            return {
                "ok": False,
                "error": (
                    f"ValueError: active fabrication profile permits via kinds "
                    f"{allowed}, not {via.kind!r}"
                ),
                "index": index,
                "applied_count": 0,
                "vias": [],
            }
    return None


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_tracks(path: str, tracks: list[NewTrack]) -> dict[str, Any]:
    """Lay straight copper segments in order.

    A corner is two list items and a layer change is a via. That is deliberate:
    where a track turns and where it changes layer are routing decisions.

    Prefer 45-degree PCB corners and keep every endpoint on a same-net pad,
    via, filled zone or track. The call preflights the complete list before
    writing and returns a stable UUID for each segment. It does not reroute.

    Copper on the wrong layer connects nothing, so *layer* is required --
    `new_board` reports which exist.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    return _atomic_items(
        board,
        tracks,
        "tracks",
        lambda target, track: target.track(
            track.x1,
            track.y1,
            track.x2,
            track.y2,
            layer=track.layer,
            width=track.width,
            net=track.net,
        ).as_dict(),
    )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_vias(path: str, vias: list[NewVia]) -> dict[str, Any]:
    """Drill caller-typed vias across explicit copper-layer spans."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    try:
        refusal = _provider_via_refusal(path, vias)
    except _ERRORS as exc:
        return _fail(exc)
    if refusal is not None:
        return refusal
    return _atomic_items(
        board,
        vias,
        "vias",
        lambda target, via: target.via(
            via.x,
            via.y,
            net=via.net,
            diameter=via.diameter,
            drill=via.drill,
            layers=via.layers,
            kind=via.kind,
        ).as_dict(),
    )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_zones(path: str, zones: list[NewZone]) -> dict[str, Any]:
    """Add copper pours or keep-out polygons in order.

    A zone can use caller-supplied polygon *points*, or an explicit
    ``boundary="board_outline"`` with an inward *inset*. Curved Edge.Cuts are
    converted to the polygon KiCad zones actually store, within *max_error*;
    the generated points are returned for inspection.

    With *forbids* it is a KEEP-OUT instead -- a region refusing any of
    ``tracks``, ``vias``, ``pads``, ``pours``, ``footprints``.

    A pour is not filled until `refill_zones`. An unfilled zone is an outline
    that connects nothing and renders as almost nothing.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)

    def each(target: Board, zone: NewZone) -> dict[str, Any]:
        value = _zone_value(target, zone)
        return target.zone(
            [(point.x, point.y) for point in value.points],
            layer=value.layer,
            net=value.net,
            clearance=value.clearance,
            pad_connection=value.pad_connection,
            min_thickness=value.min_thickness,
            thermal_gap=value.thermal_gap,
            thermal_spoke_width=value.thermal_spoke_width,
            priority=value.priority,
            island_removal=value.island_removal,
            min_island_area=value.min_island_area,
            forbids=value.forbids,
        ).as_dict()

    return _atomic_items(board, zones, "zones", each)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def refill_zones(path: str) -> dict[str, Any]:
    """Recompute every pour against the copper as it now stands.

    Tracks laid after a pour do not update it, so a board looks poured while
    the fill still hugs routing that has moved.
    """
    try:
        return {"ok": True, "filled": _board(path).refill()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_copper(
    path: str,
    net: str = "",
    layer: str = "",
    tracks: bool = True,
    vias: bool = True,
    zones: bool = False,
    uuid: str = "",
    all: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete one copper UUID, or copper filtered by net and layer.

    Use a UUID returned by `add_tracks`, `add_vias`, `add_zones` or
    `list_copper` to repair one item. UUID cannot be combined with net/layer.
    Empty selectors are rejected unless `all=true`; `dry_run=true` reports the
    count and identities without changing the board.
    """
    if not uuid and not net and not layer and not all:
        return _fail(
            ValueError(
                "select uuid, net or layer; use all=true to remove all selected kinds"
            )
        )
    if all and (uuid or net or layer):
        return _fail(ValueError("all=true cannot be combined with selectors"))
    try:
        board = _board(path)
        selected = (
            ((True, board.tracks()), (True, board.vias()), (True, board.zones()))
            if uuid
            else (
                (tracks, board.tracks()),
                (vias, board.vias()),
                (zones, board.zones()),
            )
        )
        before = [
            item.as_dict()
            for enabled, items in selected
            if enabled
            for item in items
            if (not uuid or item.uuid == uuid)
            and (not net or item.net == net)
            and (
                not layer
                or (
                    getattr(item, "layer", "") == layer
                    or layer in getattr(item, "layers", ())
                )
            )
        ]
        gone = (
            len(before)
            if dry_run
            else board.remove_copper(
                uuid=uuid,
                net=net,
                layer=layer,
                tracks=tracks,
                vias=vias,
                zones=zones,
                all=all,
            )
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "dry_run": dry_run, "removed": gone, "items": before}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_copper(path: str) -> dict[str, Any]:
    """Every track, via and zone; tracks and vias include repairable UUIDs."""
    try:
        board = _board(path)
        return {
            "ok": True,
            "tracks": [t.as_dict() for t in board.tracks()],
            "vias": [v.as_dict() for v in board.vias()],
            "zones": [z.as_dict() for z in board.zones()],
        }
    except _ERRORS as exc:
        return _fail(exc)
