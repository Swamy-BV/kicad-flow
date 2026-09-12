"""Board net membership, copper connectivity and route measurements."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from kicad_flow.pcb.types import (
    ConnectedPad,
    Connection,
    ConnectivityGroup,
    Net,
    NetConnectivity,
    NetPad,
    Pad,
    Point,
    RouteMetric,
    Via,
)

from ._geometry import (
    _find_root,
    _point_in_polygon,
    _segments_intersect,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def nets(self: KiCadBoard) -> list[Net]:
    """What the board is MEANT to connect, from its own pads."""
    found: dict[str, list[NetPad]] = {}
    for part in self.footprints():
        for pad in part.pads:
            if pad.net:
                found.setdefault(pad.net, []).append(NetPad(part.ref, pad.number))
    return [Net(name, tuple(pads)) for name, pads in sorted(found.items())]


def connectivity(self: KiCadBoard, nets: tuple[str, ...] = ()) -> list[NetConnectivity]:
    """Return pad-bearing connected copper groups without selecting routes."""
    wanted = set(nets)
    where = {
        (part.ref, pad.number): pad for part in self.footprints() for pad in part.pads
    }
    out: list[NetConnectivity] = []
    for net in self.nets():
        if wanted and net.name not in wanted:
            continue
        copper_groups = self._groups_of(net.name)
        node_group = {
            node: index for index, group in enumerate(copper_groups) for node in group
        }
        grouped_pads: dict[int, list[ConnectedPad]] = {}
        for item in net.pads:
            pad = where[(item.ref, item.pad)]
            indexes = {
                node_group[node]
                for layer in self._pad_copper_layers(pad)
                if (node := (round(pad.at.x, 3), round(pad.at.y, 3), layer))
                in node_group
            }
            # _groups_of always creates nodes for net pads. Keep a guarded
            # fallback so malformed imported boards remain inspectable.
            index = min(indexes) if indexes else len(copper_groups)
            grouped_pads.setdefault(index, []).append(
                ConnectedPad(item.ref, item.pad, pad.at, self._pad_copper_layers(pad))
            )

        ordered = sorted(
            grouped_pads.items(),
            key=lambda item: min((pad.ref, pad.pad) for pad in item[1]),
        )
        groups: list[ConnectivityGroup] = []
        for public_index, (source_index, pads) in enumerate(ordered):
            nodes = (
                copper_groups[source_index]
                if source_index < len(copper_groups)
                else set()
            )
            groups.append(
                ConnectivityGroup(
                    index=public_index,
                    pads=tuple(sorted(pads, key=lambda pad: (pad.ref, pad.pad))),
                    layers=tuple(sorted({node[2] for node in nodes})),
                    copper_nodes=len(nodes),
                )
            )
        out.append(NetConnectivity(net.name, tuple(groups)))
    return out


def route_metrics(self: KiCadBoard, nets: tuple[str, ...] = ()) -> list[RouteMetric]:
    """Measure authored copper on each intended net."""
    connectivity = {item.net: item for item in self.connectivity(nets)}
    wanted = set(nets)
    net_names = [net.name for net in self.nets() if not wanted or net.name in wanted]
    pad_counts = {net.name: len(net.pads) for net in self.nets()}
    tracks = self.tracks()
    vias = self.vias()
    out: list[RouteMetric] = []
    for name in net_names:
        net_tracks = [track for track in tracks if track.net == name]
        by_layer: dict[str, float] = {}
        for track in net_tracks:
            length = math.dist(
                (track.start.x, track.start.y),
                (track.end.x, track.end.y),
            )
            by_layer[track.layer] = by_layer.get(track.layer, 0.0) + length
        widths = [track.width for track in net_tracks]
        out.append(
            RouteMetric(
                net=name,
                pad_count=pad_counts[name],
                track_count=len(net_tracks),
                track_length=sum(by_layer.values()),
                length_by_layer=tuple(sorted(by_layer.items())),
                via_count=sum(via.net == name for via in vias),
                minimum_width=min(widths) if widths else None,
                connected_groups=len(connectivity[name].groups),
            )
        )
    return out


def unrouted(self: KiCadBoard) -> list[Connection]:
    """A minimum set of pad-group separations, nearest endpoints first.

    Connected-component membership is factual. The nearest pad pair is a
    distance measurement returned to identify each separation; it is not
    a proposed track or a routing choice.
    """
    out: list[Connection] = []
    for net in self.connectivity():
        if len(net.groups) < 2:
            continue
        edges: list[tuple[float, int, int, ConnectedPad, ConnectedPad]] = []
        for index, first in enumerate(net.groups):
            for second in net.groups[index + 1 :]:
                candidates = [
                    (math.dist((a.at.x, a.at.y), (b.at.x, b.at.y)), a, b)
                    for a in first.pads
                    for b in second.pads
                ]
                distance, a, b = min(
                    candidates,
                    key=lambda item: (
                        item[0],
                        item[1].ref,
                        item[1].pad,
                        item[2].ref,
                        item[2].pad,
                    ),
                )
                edges.append((distance, first.index, second.index, a, b))

        parent = list(range(len(net.groups)))

        for distance, first_index, second_index, a, b in sorted(
            edges,
            key=lambda item: (
                item[0],
                item[3].ref,
                item[3].pad,
                item[4].ref,
                item[4].pad,
            ),
        ):
            left = _find_root(parent, first_index)
            right = _find_root(parent, second_index)
            if left == right:
                continue
            parent[left] = right
            out.append(
                Connection(
                    net.net, NetPad(a.ref, a.pad), NetPad(b.ref, b.pad), distance
                )
            )
    return out


def _pad_copper_layers(self: KiCadBoard, pad: Pad) -> tuple[str, ...]:
    """Copper layers a pad actually reaches."""
    if pad.kind == "pth" or "*.Cu" in pad.layers:
        return self.layers
    return tuple(layer for layer in pad.layers if layer in self.layers)


def _via_copper_layers(self: KiCadBoard, via: Via) -> tuple[str, ...]:
    """Copper layers inside a via's declared span."""
    try:
        first = self.layers.index(via.layers[0])
        last = self.layers.index(via.layers[-1])
    except ValueError:
        return ()
    low, high = sorted((first, last))
    return self.layers[low : high + 1]


def _groups_of(self: KiCadBoard, net: str) -> list[set[tuple[float, float, str]]]:
    """Layer-aware copper nodes on *net*, grouped by connectivity."""
    node = tuple[float, float, str]
    edges: list[tuple[node, node]] = []
    nodes: set[node] = set()

    def key(point: Point, layer: str) -> node:
        return (round(point.x, 3), round(point.y, 3), layer)

    # A plated pad joins its layers internally. Coincident pads join only
    # on a copper layer both can actually reach.
    seen_at: dict[node, node] = {}
    for part in self.footprints():
        for pad in part.pads:
            if pad.net != net:
                continue
            pad_nodes = [key(pad.at, layer) for layer in self._pad_copper_layers(pad)]
            nodes.update(pad_nodes)
            for other in pad_nodes[1:]:
                edges.append((pad_nodes[0], other))
            for pad_node in pad_nodes:
                if pad_node in seen_at:
                    edges.append((seen_at[pad_node], pad_node))
                seen_at[pad_node] = pad_node

    tracks = [track for track in self.tracks() if track.net == net]
    for track in tracks:
        start = key(track.start, track.layer)
        end = key(track.end, track.layer)
        nodes.update((start, end))
        edges.append((start, end))
    # Same-layer copper joins at crossings and T intersections even when
    # neither caller supplied the intersection as an endpoint.
    for index, first in enumerate(tracks):
        for second in tracks[index + 1 :]:
            if first.layer == second.layer and _segments_intersect(
                first.start, first.end, second.start, second.end
            ):
                edges.append(
                    (key(first.start, first.layer), key(second.start, second.layer))
                )

    for via in self.vias():
        if via.net != net:
            continue
        via_nodes = [key(via.at, layer) for layer in self._via_copper_layers(via)]
        nodes.update(via_nodes)
        for other in via_nodes[1:]:
            edges.append((via_nodes[0], other))

    # A filled plane joins conductive objects on ITS layer only. An SMD
    # pad on F.Cu does not reach an In1.Cu plane without a via.
    for zone in self.zones():
        if zone.net != net or not zone.filled or zone.forbids:
            continue
        poly = [(point.x, point.y) for point in zone.points]
        inside = [
            item
            for item in nodes
            if item[2] == zone.layer and _point_in_polygon((item[0], item[1]), poly)
        ]
        for item in inside[1:]:
            edges.append((inside[0], item))

    parent: dict[node, node] = {item: item for item in nodes}

    def find(k: node) -> node:
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[node, set[node]] = {}
    for k in list(parent):
        groups.setdefault(find(k), set()).add(k)
    return list(groups.values())
