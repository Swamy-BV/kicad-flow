"""Native DRC, authored-copper checks and isolated routing proposals."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from kicad_flow.pcb.types import (
    Finding,
    Pad,
    Point,
    Track,
    Via,
    Zone,
)

from .._sexpr import Node
from ..cli import cli as _kicad
from ._geometry import (
    _point_in_polygon,
    _point_on_segment,
)
from ._nodes import (
    _uid,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def _routing_findings(self: KiCadBoard) -> list[Finding]:
    """Factual copper defects KiCad's DRC does not currently report.

    This deliberately does not judge corner angles. Choosing 45-degree,
    orthogonal or curved routing is caller policy; a zero-length segment,
    an exact duplicate and an endpoint touching no same-net copper object
    are properties of the board as written.
    """
    tracks = self.tracks()
    vias = self.vias()
    pads = [(part.ref, pad) for part in self.footprints() for pad in part.pads]
    zones = [zone for zone in self.zones() if zone.filled and not zone.forbids]
    out: list[Finding] = []

    def key(point: Point) -> tuple[float, float]:
        # Pad positions are deliberately reported to 0.001 mm and callers
        # route to those reported facts. A rotated library pad may sit at
        # 12.1875 internally while its returned routing point is 12.188.
        return round(point.x, 3), round(point.y, 3)

    def pad_on_layer(track: Track, pad: Pad) -> bool:
        return track.layer in pad.layers or (
            pad.kind == "pth" and track.layer in self.layers
        )

    def via_on_layer(track: Track, via: Via) -> bool:
        try:
            layer = self.layers.index(track.layer)
            first = self.layers.index(via.layers[0])
            last = self.layers.index(via.layers[-1])
        except ValueError:
            return False
        low, high = sorted((first, last))
        return low <= layer <= high

    def anchored(track: Track, point: Point, own: int) -> bool:
        point_key = key(point)
        if any(
            pad.net == track.net
            and pad_on_layer(track, pad)
            and key(pad.at) == point_key
            for _ref, pad in pads
        ):
            return True
        if any(
            via.net == track.net
            and via_on_layer(track, via)
            and key(via.at) == point_key
            for via in vias
        ):
            return True
        if any(
            other.net == track.net
            and other.layer == track.layer
            and _point_on_segment(point, other.start, other.end)
            for index, other in enumerate(tracks)
            if index != own
        ):
            return True
        return any(
            zone.net == track.net
            and zone.layer == track.layer
            and _point_in_polygon(point_key, [(p.x, p.y) for p in zone.points])
            for zone in zones
        )

    seen: dict[tuple[str, str, tuple[float, float], tuple[float, float]], Track] = {}
    for index, track in enumerate(tracks):
        start, end = key(track.start), key(track.end)
        if start == end:
            out.append(
                Finding(
                    severity="error",
                    kind="zero_length_track",
                    message="Track has identical start and end points",
                    layer=track.layer,
                    at=track.start,
                    uuid=track.uuid,
                )
            )
            continue
        ordered = tuple(sorted((start, end)))
        duplicate_key = (track.net, track.layer, ordered[0], ordered[1])
        original = seen.get(duplicate_key)
        if original is not None:
            out.append(
                Finding(
                    severity="warning",
                    kind="duplicate_track",
                    message="Track exactly duplicates another segment",
                    layer=track.layer,
                    at=track.start,
                    uuid=track.uuid,
                    other_uuid=original.uuid,
                )
            )
        else:
            seen[duplicate_key] = track
        for point in (track.start, track.end):
            if not anchored(track, point, index):
                out.append(
                    Finding(
                        severity="warning",
                        kind="dangling_track_end",
                        message=(
                            "Track endpoint does not meet a same-net pad, "
                            "via, filled zone or track"
                        ),
                        layer=track.layer,
                        at=point,
                        uuid=track.uuid,
                    )
                )
    return out


def check(self: KiCadBoard) -> list[Finding]:
    """Every violation, mapped from a position back to a part and pad."""
    self.save()
    data = _kicad.drc(self._path)
    where: dict[tuple[float, float], tuple[str, str]] = {}
    for part in self.footprints():
        for p in part.pads:
            where[(round(p.at.x, 2), round(p.at.y, 2))] = (part.ref, p.number)
    out: list[Finding] = []
    for kind in ("violations", "unconnected_items", "schematic_parity"):
        for violation in data.get(kind, []):
            items = violation.get("items") or [{}]
            refs = []
            for item in items:
                pos = item.get("pos") or {}
                at = Point(
                    round(float(pos.get("x", 0.0)), 3),
                    round(float(pos.get("y", 0.0)), 3),
                )
                refs.append((where.get((round(at.x, 2), round(at.y, 2)), ("", "")), at))
            (ref, number), at = refs[0]
            other = refs[1][0][0] if len(refs) > 1 else ""
            first_uuid = str(items[0].get("uuid") or "")
            other_uuid = str(items[1].get("uuid") or "") if len(items) > 1 else ""
            out.append(
                Finding(
                    severity=str(violation.get("severity", "error")),
                    kind=str(violation.get("type", kind)),
                    message=str(violation.get("description", "")),
                    ref=ref,
                    pad=number,
                    at=at,
                    other_ref=other,
                    uuid=first_uuid,
                    other_uuid=other_uuid,
                )
            )
    out.extend(self._routing_findings())
    return out


def check_proposed(
    self: KiCadBoard,
    tracks: tuple[Track, ...] = (),
    vias: tuple[Via, ...] = (),
    zones: tuple[Zone, ...] = (),
) -> list[Finding]:
    """Check caller-supplied copper on a temporary board copy."""
    from .board import KiCadBoard

    scratch = self._path.with_name(
        f".{self._path.stem}.route-check-{_uid()}{self._path.suffix}"
    )
    candidate = KiCadBoard(scratch, copy.deepcopy(self._tree))
    inputs: dict[str, tuple[str, int]] = {}
    try:
        # A filled zone is cached copper, not merely a declaration. Loading
        # a scratch board containing that stale fill plus newly proposed
        # copper lets pcbnew build connectivity before the filler runs and
        # can permanently reassign a proposed via to the plane net. Keep
        # the zone rules, but discard cached geometry before adding copper.
        for zone_node in candidate._tree.get_all("zone"):
            zone_node.items[:] = [
                item
                for item in zone_node.items
                if not (
                    isinstance(item, Node)
                    and item.name in {"filled_polygon", "fill_segments"}
                )
            ]
        for index, track_item in enumerate(tracks):
            made_track = candidate.track(
                track_item.start.x,
                track_item.start.y,
                track_item.end.x,
                track_item.end.y,
                layer=track_item.layer,
                width=track_item.width,
                net=track_item.net,
            )
            inputs[made_track.uuid] = ("tracks", index)
        for index, via_item in enumerate(vias):
            made_via = candidate.via(
                via_item.at.x,
                via_item.at.y,
                net=via_item.net,
                diameter=via_item.diameter,
                drill=via_item.drill,
                layers=via_item.layers,
                kind=via_item.kind,
            )
            inputs[made_via.uuid] = ("vias", index)
        for index, zone_item in enumerate(zones):
            made_zone = candidate.zone(
                [(point.x, point.y) for point in zone_item.points],
                layer=zone_item.layer,
                net=zone_item.net,
                clearance=zone_item.clearance,
                pad_connection=zone_item.pad_connection,
                min_thickness=zone_item.min_thickness,
                thermal_gap=zone_item.thermal_gap,
                thermal_spoke_width=zone_item.thermal_spoke_width,
                priority=zone_item.priority,
                island_removal=zone_item.island_removal,
                min_island_area=zone_item.min_island_area,
                forbids=zone_item.forbids,
            )
            inputs[made_zone.uuid] = ("zones", index)
        if candidate.zones():
            candidate.refill()
        found = candidate.check()
        attributed: list[Finding] = []
        for finding in found:
            first = inputs.get(finding.uuid)
            second = inputs.get(finding.other_uuid)
            attributed.append(
                replace(
                    finding,
                    input_kind=first[0] if first else "",
                    input_index=first[1] if first else None,
                    other_input_kind=second[0] if second else "",
                    other_input_index=second[1] if second else None,
                )
            )
        return attributed
    finally:
        for artifact in (
            scratch,
            scratch.with_suffix(".kicad_pro"),
            scratch.with_suffix(".kicad_prl"),
            scratch.with_suffix(".kicad_dru"),
            Path(f"{scratch}-bak"),
        ):
            artifact.unlink(missing_ok=True)
