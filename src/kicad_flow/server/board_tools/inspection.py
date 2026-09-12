"""PCB MCP tools for inspection."""

from __future__ import annotations

from typing import Any

from .. import _meta
from .._app import mcp
from .models import (
    NetPairSpec,
)
from .session import (
    _ERRORS,
    _board,
    _fail,
    _key,
)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_nets(
    path: str, net: str = "", include_rules: bool = False
) -> dict[str, Any]:
    """What the board is MEANT to connect, from its own pads.

    Intent, not fact. Whether copper actually joins these pads is
    `unrouted_connections`, and the two disagreeing is the normal state of a
    board mid-layout.

    net selects one exact name. include_rules adds KiCad-resolved netclass
    dimensions and the source class of each inherited value. It reports pads
    without net assignments and explicit class coverage so callers can review
    policy before routing. Default is valid; inspect it instead of guessing.
    Read custom constraints separately and check candidates with check_board:
    class width preferences are not necessarily enforced width limits.
    """
    try:
        board = _board(path)
        nets = board.nets()
        if net:
            nets = [item for item in nets if item.name == net]
            if not nets:
                raise LookupError(f"board has no net {net!r}")
        policy: dict[str, Any] = {}
        if include_rules:
            if len(nets) > 200:
                raise ValueError("more than 200 nets; select one net for rule readback")
            assigned = {item.net for item in board.net_class_assignments()}
            policy = {
                "routing_policy": board.net_policy(tuple(item.name for item in nets)),
                "unassigned_pad_count": sum(
                    not pad.net
                    for footprint in board.footprints()
                    for pad in footprint.pads
                    if pad.kind != "npth"
                ),
                "without_explicit_class": [
                    item.name for item in nets if item.name not in assigned
                ],
                "assignment_note": "no explicit class may still match a class pattern",
            }
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(nets),
        "nets": [n.as_dict() for n in nets],
        **policy,
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def unrouted_connections(path: str, limit: int = 40) -> dict[str, Any]:
    """Disconnected pad-bearing copper groups, without choosing routes.

    `nets` is the complete factual group membership. `connections` contains a
    minimum number of nearest-pad measurements spanning those groups, useful
    as a compact progress count but not as proposed tracks. `pair_count`
    preserves the old all-pairs magnitude without returning quadratic output.
    """
    try:
        board = _board(path)
        found = sorted(board.unrouted(), key=lambda c: c.distance)
        connectivity = [item for item in board.connectivity() if len(item.groups) > 1]
    except _ERRORS as exc:
        return _fail(exc)
    pair_count = 0
    for item in connectivity:
        sizes = [len(group.pads) for group in item.groups]
        pair_count += sum(
            left * right
            for index, left in enumerate(sizes)
            for right in sizes[index + 1 :]
        )
    return {
        "ok": True,
        "complete": not found,
        "count": len(found),
        "pair_count": pair_count,
        "connections": [c.as_dict() for c in found[:limit]],
        "nets": [item.as_dict() for item in connectivity],
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def measure_routes(
    path: str, nets: list[str] | None = None, pairs: list[NetPairSpec] | None = None
) -> dict[str, Any]:
    """Measure authored copper by explicitly named net.

    Length, layer use, widths, via counts and connected-group counts are raw
    facts. Optional pairs only compare the two supplied total lengths; no net
    naming convention or differential-pair membership is inferred.
    """
    requested = list(nets or [])
    for pair in pairs or []:
        requested.extend((pair.first, pair.second))
    requested = list(dict.fromkeys(requested))
    try:
        board = _board(path)
        available = {net.name for net in board.nets()}
        missing = [name for name in requested if name not in available]
        if missing:
            raise LookupError(f"board has no nets {missing}")
        measured = board.route_metrics(tuple(requested))
    except _ERRORS as exc:
        return _fail(exc)
    by_name = {item.net: item for item in measured}
    comparisons = []
    for pair in pairs or []:
        first = by_name[pair.first].track_length
        second = by_name[pair.second].track_length
        comparisons.append(
            {
                "first": pair.first,
                "second": pair.second,
                "first_length": round(first, 3),
                "second_length": round(second, 3),
                "skew": round(abs(first - second), 3),
            }
        )
    return {
        "ok": True,
        "count": len(measured),
        "nets": [item.as_dict() for item in measured],
        "pairs": comparisons,
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def what_is_on_board(
    path: str, x: float, y: float, radius: float = 0.01
) -> dict[str, Any]:
    """What touches a point: pads, track interiors, vias and zones.

    This is a geometric observation. It does not infer which object the caller
    intended to touch.
    """
    try:
        return {"ok": True, **_board(path).at(x, y, radius)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def query_board_region(
    path: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layers: list[str] | None = None,
    since: str = "",
    max_objects: int = 500,
    max_bytes: int = 100000,
    include_fills: bool = False,
) -> dict[str, Any]:
    """Objects whose conservative bounds intersect one explicit rectangle.

    Returns footprints, pads, tracks, vias, zones and graphics. `layers` is an
    exact filter; omission inspects every layer. The result reports geometry
    only and does not select a corridor or propose a route.

    Edge.Cuts is always included where it intersects the region. Pad shape,
    rotation and copper layers are explicit. Unsupported pad geometry is
    flagged and included conservatively, never silently treated as free space.
    include_fills adds stored zone-fill contours; they can be stale until refill.
    Bounds are not clearance envelopes; check_board validates candidate routes.

    Reuse since with the same path, rectangle, layers and fill option to get
    changed items plus removed IDs per collection. Replace on mode=full.
    Limits refuse oversized replies instead of returning incomplete obstacles.
    """
    import json

    from ..observations import board_history

    try:
        if not 1 <= max_objects <= 10000 or not 1000 <= max_bytes <= 2000000:
            raise ValueError("max_objects must be 1..10000; max_bytes 1000..2000000")
        data = _board(path).region(
            x1,
            y1,
            x2,
            y2,
            layers=tuple(sorted(set(layers or []))),
            include_fills=include_fills,
        )
        scope = json.dumps([_key(path), data["bounds"], data["layers"], include_fills])
        return {
            "ok": True,
            **board_history.observe(scope, data, since, max_objects, max_bytes),
        }
    except _ERRORS as exc:
        return _fail(exc)
