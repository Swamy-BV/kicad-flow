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
    RouteMetric,
    Via,
)

from ._geometry import _find_root
from ._runner import run_pcbnew

if TYPE_CHECKING:
    from .board import KiCadBoard


_CONNECTED_PADS_SCRIPT = """
import json
import sys
import pcbnew

with open(sys.argv[1], encoding='utf-8') as source:
    job = json.load(source)
board = pcbnew.LoadBoard(job['path'])
board.BuildConnectivity()
connectivity = board.GetConnectivity()
groups = []
group_indexes = {}
pads = {}
for footprint in board.GetFootprints():
    for pad in footprint.Pads():
        if not pad.GetNetname():
            continue
        members = tuple(sorted(item.m_Uuid.AsString()
                               for item in connectivity.GetConnectedItems(pad)))
        if not members:
            members = (pad.m_Uuid.AsString(),)
        key = (pad.GetNetname(), members)
        if key not in group_indexes:
            group_indexes[key] = len(groups)
            groups.append({'net': pad.GetNetname(), 'uuids': members})
        pads[pad.m_Uuid.AsString()] = group_indexes[key]
print(json.dumps({'groups': groups, 'pads': pads}))
"""


def nets(self: KiCadBoard) -> list[Net]:
    """What the board is MEANT to connect, from its own pads."""
    found: dict[str, list[NetPad]] = {}
    for part in self.footprints():
        for pad in part.pads:
            if pad.net:
                found.setdefault(pad.net, []).append(NetPad(part.ref, pad.number))
    return [Net(name, tuple(pads)) for name, pads in sorted(found.items())]


def connectivity(self: KiCadBoard, nets: tuple[str, ...] = ()) -> list[NetConnectivity]:
    """Return KiCad's actual pad-bearing connected copper groups."""
    wanted = set(nets)
    footprints = self.footprints()
    native = run_pcbnew(_CONNECTED_PADS_SCRIPT, {"path": str(self.path)})
    pad_groups: dict[str, int] = native["pads"]
    native_groups: list[dict[str, object]] = native["groups"]
    layers_by_uuid: dict[str, tuple[str, ...]] = {
        pad.uuid: self._pad_copper_layers(pad)
        for part in footprints for pad in part.pads
    }
    layers_by_uuid.update(
        {track.uuid: (track.layer,) for track in self.tracks()}
    )
    layers_by_uuid.update(
        {via.uuid: self._via_copper_layers(via) for via in self.vias()}
    )
    layers_by_uuid.update(
        {zone.uuid: (zone.layer,) for zone in self.zones() if zone.filled}
    )
    pads_by_group: dict[int, list[ConnectedPad]] = {}
    for part in footprints:
        for pad in part.pads:
            if not pad.net or (wanted and pad.net not in wanted):
                continue
            if pad.uuid not in pad_groups:
                raise ValueError(f"native connectivity omitted {part.ref}.{pad.number}")
            pads_by_group.setdefault(pad_groups[pad.uuid], []).append(
                ConnectedPad(part.ref, pad.number, pad.at,
                             self._pad_copper_layers(pad))
            )
    out: list[NetConnectivity] = []
    for net in self.nets():
        if wanted and net.name not in wanted:
            continue
        ordered = sorted(
            ((index, pads) for index, pads in pads_by_group.items()
             if native_groups[index]["net"] == net.name),
            key=lambda item: min((pad.ref, pad.pad) for pad in item[1]),
        )
        groups: list[ConnectivityGroup] = []
        for public_index, (source_index, pads) in enumerate(ordered):
            members = native_groups[source_index]["uuids"]
            assert isinstance(members, list)
            groups.append(
                ConnectivityGroup(
                    index=public_index,
                    pads=tuple(sorted(pads, key=lambda pad: (pad.ref, pad.pad))),
                    layers=tuple(sorted({layer for uuid in members
                                         for layer in layers_by_uuid.get(uuid, ())})),
                    copper_nodes=len(members),
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
