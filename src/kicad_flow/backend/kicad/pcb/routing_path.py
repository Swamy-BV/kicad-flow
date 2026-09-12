"""Resolve a unique authored centerline path between explicit terminals."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from kicad_flow.pcb.routing import RoutePath, RouteTerminal
from kicad_flow.pcb.types import Point

from ._geometry import _point_on_segment, _segments_intersect
from ._nodes import _net_name

if TYPE_CHECKING:
    from .board import KiCadBoard

NodeKey = tuple[float, float, str]


@dataclass(frozen=True)
class Edge:
    """A centerline span or an explicitly traversed via barrel."""

    a: NodeKey
    b: NodeKey
    uuid: str
    width: float
    length: float | None
    kind: str = "track"


def _key(x: float, y: float, layer: str) -> NodeKey:
    return round(x, 6), round(y, 6), layer


def _terminal(board: KiCadBoard, terminal: RouteTerminal, net: str) -> NodeKey:
    pads = [p for p in board.footprint(terminal.ref).pads if p.number == terminal.pad]
    if len(pads) != 1:
        raise ValueError(f"{terminal.ref}.{terminal.pad}: expected one physical pad")
    pad = pads[0]
    if pad.net != net or not net:
        raise ValueError(f"{terminal.ref}.{terminal.pad} is not on net {net!r}")
    if pad.kind == "npth" or terminal.layer not in board._pad_copper_layers(pad):
        raise ValueError(
            f"{terminal.ref}.{terminal.pad} does not reach {terminal.layer}"
        )
    return _key(pad.at.x, pad.at.y, terminal.layer)


def _depths(board: KiCadBoard) -> dict[str, float]:
    """Copper-center depths only when the saved stackup specifies every layer."""
    setup = board._tree.get("setup")
    saved = setup.get("stackup") if setup is not None else None
    # The contract reader does not expose imported dielectric sublayers.
    if saved is not None and any(
        str(atom) == "addsublayer"
        for layer in saved.get_all("layer")
        for atom in layer.items
    ):
        return {}
    layers = [
        item
        for item in board.stackup().layers
        if item.kind.lower() in {"copper", "core", "prepreg"}
    ]
    if [item.name for item in layers if item.kind.lower() == "copper"] != list(
        board.layers
    ):
        return {}
    kinds = [item.kind.lower() for item in layers]
    if not kinds or kinds[0] != "copper" or kinds[-1] != "copper":
        return {}
    if any(a == b == "copper" for a, b in pairwise(kinds)):
        return {}
    depth = 0.0
    result = {}
    for item in layers:
        if (
            item.thickness is None
            or not math.isfinite(item.thickness)
            or item.thickness <= 0
        ):
            return {}
        if item.kind.lower() == "copper":
            result[item.name] = depth + item.thickness / 2
        depth += item.thickness
    if not math.isclose(depth, board.thickness, abs_tol=1e-6):
        return {}
    return result


def resolve(board: KiCadBoard, request: RoutePath) -> dict[str, Any]:
    """Return a path or explicit unresolved/ambiguous topology observations.

    Contacts are at centerlines. Pad-edge contacts and plane traversal are not
    reconstructed; native DRC/connectivity remains a separate required check.
    """
    start = _terminal(board, request.start, request.net)
    end = _terminal(board, request.end, request.net)
    if start == end:
        raise ValueError("route terminals must be distinct")
    tracks = [t for t in board.tracks() if t.net == request.net]
    vias = [v for v in board.vias() if v.net == request.net]
    pads = [p for fp in board.footprints() for p in fp.pads if p.net == request.net]
    if len(tracks) + len(vias) + len(pads) > 500:
        raise ValueError(f"{request.net}: path inspection supports at most 500 objects")
    out: dict[str, Any] = {
        "net": request.net,
        "status": "unresolved",
        "segments": [],
        "transitions": [],
        "track_length": None,
        "via_length": None,
        "path_length": None,
        "issues": [],
        "topology_basis": "unique centerline path; native connectivity unverified",
    }
    issues: list[dict[str, Any]] = out["issues"]
    if any(_net_name(n.get("net")) == request.net for n in board._tree.get_all("arc")):
        issues.append({"kind": "unsupported_copper_arc"})
    if any(z.net == request.net and not z.forbids for z in board.zones()):
        issues.append({"kind": "unsupported_zone_path"})
    if any(not p.geometry_supported for p in pads):
        issues.append({"kind": "unsupported_pad_geometry"})
    if issues:
        return out

    points = {start, end}
    for t in tracks:
        points.update(
            (_key(t.start.x, t.start.y, t.layer), _key(t.end.x, t.end.y, t.layer))
        )
    for pad in pads:
        points.update(
            _key(pad.at.x, pad.at.y, layer) for layer in board._pad_copper_layers(pad)
        )
    for via in vias:
        points.update(
            _key(via.at.x, via.at.y, layer) for layer in board._via_copper_layers(via)
        )

    edges: list[Edge] = []
    graph: dict[NodeKey, list[int]] = defaultdict(list)

    def add(edge: Edge) -> None:
        index = len(edges)
        edges.append(edge)
        graph[edge.a].append(index)
        graph[edge.b].append(index)

    for index, t in enumerate(tracks):
        if t.start == t.end:
            issues.append({"kind": "zero_length_track", "uuid": t.uuid})
            continue
        for other_track in tracks[index + 1 :]:
            if t.layer == other_track.layer and _segments_intersect(
                t.start, t.end, other_track.start, other_track.end
            ):
                shared = any(
                    _point_on_segment(p, t.start, t.end)
                    and _point_on_segment(p, other_track.start, other_track.end)
                    for p in (t.start, t.end, other_track.start, other_track.end)
                )
                if not shared:
                    issues.append(
                        {
                            "kind": "interior_crossing",
                            "uuids": [t.uuid, other_track.uuid],
                        }
                    )
        on = sorted(
            (
                p
                for p in points
                if p[2] == t.layer
                and _point_on_segment(Point(p[0], p[1]), t.start, t.end)
            ),
            key=lambda p: math.dist(p[:2], (t.start.x, t.start.y)),
        )
        for a, b in pairwise(on):
            add(Edge(a, b, t.uuid, t.width, math.dist(a[:2], b[:2])))
    depths = _depths(board)
    for via in vias:
        active = [
            _key(via.at.x, via.at.y, layer) for layer in board._via_copper_layers(via)
        ]
        active = [p for p in active if p in graph or p in (start, end)]
        for a, b in pairwise(active):
            length = abs(depths[a[2]] - depths[b[2]]) if depths else None
            add(Edge(a, b, via.uuid, via.diameter, length, "via"))
    if issues:
        return out

    reached = {start}
    pending = [start]
    while pending:
        point = pending.pop()
        for index in graph[point]:
            e = edges[index]
            other = e.b if e.a == point else e.a
            if other not in reached:
                reached.add(other)
                pending.append(other)
    if end not in reached:
        issues.append({"kind": "no_centerline_path", "at": list(end)})
        return out
    branches = [p for p in reached if len(graph[p]) != (1 if p in (start, end) else 2)]
    if branches:
        out["status"] = "ambiguous"
        issues.extend(
            {"kind": "branch_or_loop", "at": list(p), "degree": len(graph[p])}
            for p in sorted(branches)
        )
        return out

    current, previous = start, -1
    used: set[int] = set()
    lengths: list[float | None] = []
    while current != end:
        index = next(i for i in graph[current] if i != previous)
        used.add(index)
        edge = edges[index]
        other = edge.b if edge.a == current else edge.a
        row = {
            "uuid": edge.uuid,
            "start": list(current[:2]),
            "end": list(other[:2]),
            "length": edge.length,
        }
        if edge.kind == "track":
            row.update(layer=current[2], width=edge.width)
            out["segments"].append(row)
        else:
            row.update(from_layer=current[2], to_layer=other[2])
            out["transitions"].append(row)
            lengths.append(edge.length)
        current, previous = other, index
    out["status"] = "resolved_centerline"
    out["off_path_track_ids"] = sorted(
        {e.uuid for i, e in enumerate(edges) if i not in used and e.kind == "track"}
    )
    out["track_length"] = sum(row["length"] for row in out["segments"])
    out["via_length"] = None if None in lengths else sum(v or 0 for v in lengths)
    if out["via_length"] is not None:
        out["path_length"] = out["track_length"] + out["via_length"]
    else:
        issues.append({"kind": "missing_stackup_depths"})
    return out
