"""Local board geometry observations and point queries."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from kicad_flow.pcb.types import (
    Track,
)

from .._sexpr import Node
from ._constants import (
    _GRAPHIC_LAYERS,
)
from ._geometry import (
    _arc_extrema,
    _point_in_polygon,
)
from ._nodes import (
    _f,
    _text,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def at(self: KiCadBoard, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
    """What geometrically touches a point, including track interiors."""
    if radius < 0 or not all(math.isfinite(value) for value in (x, y, radius)):
        raise ValueError("point coordinates must be finite and radius non-negative")

    def segment_distance(track: Track) -> float:
        dx = track.end.x - track.start.x
        dy = track.end.y - track.start.y
        length_squared = dx * dx + dy * dy
        if not length_squared:
            return math.dist((x, y), (track.start.x, track.start.y))
        position = max(
            0.0,
            min(
                1.0,
                ((x - track.start.x) * dx + (y - track.start.y) * dy) / length_squared,
            ),
        )
        return math.dist(
            (x, y),
            (track.start.x + position * dx, track.start.y + position * dy),
        )

    pads = [
        {"ref": footprint.ref, **pad.as_dict()}
        for footprint in self.footprints()
        for pad in footprint.pads
        if abs(pad.at.x - x) <= pad.size[0] / 2 + radius
        and abs(pad.at.y - y) <= pad.size[1] / 2 + radius
    ]
    tracks = [
        track.as_dict()
        for track in self.tracks()
        if segment_distance(track) <= track.width / 2 + radius
    ]
    track_ends = [
        {"layer": track.layer, "net": track.net, "uuid": track.uuid}
        for track in self.tracks()
        if math.dist((x, y), (track.start.x, track.start.y)) <= radius
        or math.dist((x, y), (track.end.x, track.end.y)) <= radius
    ]
    vias = [
        via.as_dict()
        for via in self.vias()
        if math.dist((x, y), (via.at.x, via.at.y)) <= via.diameter / 2 + radius
    ]
    zones = [
        {"uuid": zone.uuid, "net": zone.net, "layer": zone.layer, "filled": zone.filled}
        for zone in self.zones()
        if _point_in_polygon((x, y), [(p.x, p.y) for p in zone.points])
    ]
    count = len(pads) + len(tracks) + len(vias) + len(zones)
    return {
        "x": round(x, 3),
        "y": round(y, 3),
        "radius": radius,
        "pads": pads,
        "tracks": tracks,
        "vias": vias,
        "zones": zones,
        "track_ends": track_ends,
        "connected": count > 1,
    }


def region(
    self: KiCadBoard,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    layers: tuple[str, ...] = (),
    include_fills: bool = False,
) -> dict[str, object]:
    """Return objects whose conservative bounds intersect a rectangle."""
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        raise ValueError("region coordinates must be finite")
    left, right = sorted((float(x1), float(x2)))
    top, bottom = sorted((float(y1), float(y2)))
    wanted = set(layers)
    unknown = wanted - set(self.layers) - set(_GRAPHIC_LAYERS)
    if unknown:
        raise ValueError(
            f"unknown region layers {sorted(unknown)}; board copper is "
            f"{list(self.layers)} and graphics are {list(_GRAPHIC_LAYERS)}"
        )

    def intersects(bounds: tuple[float, float, float, float]) -> bool:
        bx1, by1, bx2, by2 = bounds
        return not (bx2 < left or bx1 > right or by2 < top or by1 > bottom)

    footprints = []
    pads = []
    for footprint in self.footprints():
        if not wanted or f"{footprint.side}.Cu" in wanted:
            polygon = footprint.courtyard_polygon
            bounds = (
                min(point.x for point in polygon),
                min(point.y for point in polygon),
                max(point.x for point in polygon),
                max(point.y for point in polygon),
            )
            if intersects(bounds):
                footprints.append(
                    {
                        "ref": footprint.ref,
                        "uuid": footprint.uuid,
                        "side": footprint.side,
                        "courtyard_polygon": [point.as_dict() for point in polygon],
                        "fabrication_polygon": [
                            p.as_dict() for p in footprint.fabrication_polygon
                        ],
                        "fabrication_status": footprint.fabrication_status,
                    }
                )
        for pad in footprint.pads:
            pad_layers = set(self._pad_copper_layers(pad))
            if wanted and not wanted.intersection(pad_layers):
                continue
            # A rotated square reaches beyond max(width, height)/2.
            # Keep the enclosing rectangle conservative for curved pads.
            angle = math.radians(pad.rotation)
            c, s = abs(math.cos(angle)), abs(math.sin(angle))
            hx = (pad.size[0] * c + pad.size[1] * s) / 2
            hy = (pad.size[0] * s + pad.size[1] * c) / 2
            bounds = (pad.at.x - hx, pad.at.y - hy, pad.at.x + hx, pad.at.y + hy)
            supported = pad.geometry_supported
            # Custom copper may extend outside its anchor rectangle. Do
            # not silently exclude it from a local observation.
            if not supported or intersects(bounds):
                pads.append(
                    {
                        "ref": footprint.ref,
                        **pad.as_dict(),
                        "copper_layers": sorted(pad_layers),
                        "bounds": list(bounds) if supported else None,
                        "geometry_supported": supported,
                    }
                )

    tracks = []
    for track in self.tracks():
        if wanted and track.layer not in wanted:
            continue
        half = track.width / 2
        if intersects(
            (
                min(track.start.x, track.end.x) - half,
                min(track.start.y, track.end.y) - half,
                max(track.start.x, track.end.x) + half,
                max(track.start.y, track.end.y) + half,
            )
        ):
            tracks.append(track.as_dict())

    vias = []
    for via in self.vias():
        if wanted and not wanted.intersection(self._via_copper_layers(via)):
            continue
        half = via.diameter / 2
        if intersects(
            (via.at.x - half, via.at.y - half, via.at.x + half, via.at.y + half)
        ):
            vias.append(via.as_dict())

    zones = []
    for zone in self.zones():
        if wanted and zone.layer not in wanted:
            continue
        xs = [point.x for point in zone.points]
        ys = [point.y for point in zone.points]
        if xs and intersects((min(xs), min(ys), max(xs), max(ys))):
            record = zone.as_dict()
            if include_fills:
                node = next(
                    (
                        n
                        for n in self._tree.get_all("zone")
                        if _text(n.get("uuid")) == zone.uuid
                    ),
                    None,
                )
                record["fill_contours"] = [
                    {
                        "layer": _text(fill.get("layer")),
                        "points": [[_f(p, 0), _f(p, 1)] for p in pts.get_all("xy")],
                    }
                    for fill in (node.get_all("filled_polygon") if node else [])
                    if (pts := fill.get("pts")) is not None
                    and (not wanted or _text(fill.get("layer")) in wanted)
                ]
            zones.append(record)

    graphics = []
    for graphic in self.graphics():
        if wanted and graphic.layer not in wanted and graphic.layer != "Edge.Cuts":
            continue
        points = (
            _arc_extrema(graphic.points)
            if graphic.kind == "arc"
            else list(graphic.points)
        )
        if graphic.kind == "circle":
            centre, rim = graphic.points
            circle_radius = math.dist((centre.x, centre.y), (rim.x, rim.y))
            bounds = (
                centre.x - circle_radius,
                centre.y - circle_radius,
                centre.x + circle_radius,
                centre.y + circle_radius,
            )
        else:
            bounds = (
                min(point.x for point in points),
                min(point.y for point in points),
                max(point.x for point in points),
                max(point.y for point in points),
            )
        if intersects(bounds):
            graphics.append(graphic.as_dict())

    return {
        "bounds": {"x1": left, "y1": top, "x2": right, "y2": bottom},
        "layers": list(layers),
        "selection": "bounds_intersect",
        "footprints": footprints,
        "pads": pads,
        "tracks": tracks,
        "vias": vias,
        "zones": zones,
        "graphics": graphics,
        "units": "mm",
        "y_direction": "down",
        "coordinates": "board; never mirrored for back-side observations",
        "clearance_expanded": False,
        "unsupported_kinds": sorted(
            {"pad_geometry" for pad in pads if not pad["geometry_supported"]}
            | {
                node.name
                for node in self._tree.items
                if isinstance(node, Node)
                and node.name in {"arc", "gr_curve", "gr_polyline"}
            }
            | {
                "copper_graphics"
                for node in self._tree.items
                if isinstance(node, Node)
                and node.name.startswith("gr_")
                and _text(node.get("layer")).endswith(".Cu")
            }
            | {
                "footprint_copper_or_edges"
                for fp in self._tree.get_all("footprint")
                for node in fp.items
                if isinstance(node, Node)
                and (
                    node.name == "zone"
                    or (
                        node.name.startswith("fp_")
                        and (
                            _text(node.get("layer")).endswith(".Cu")
                            or _text(node.get("layer")) == "Edge.Cuts"
                        )
                    )
                )
            }
            | {
                "multilayer_zone"
                for node in self._tree.get_all("zone")
                if node.get("layers") is not None
            }
        ),
        "zone_geometry": (
            "stored fill contours; refill after copper edits"
            if include_fills
            else "declared boundaries only"
        ),
    }
