"""Read primitive contract values from official native IPC object replies."""

from __future__ import annotations

import math
from typing import Any

from kicad_flow.pcb.types import (
    BoardText,
    Footprint,
    Graphic,
    Pad,
    Point,
    Track,
    Via,
    Zone,
)

from ._geometry import _arc_extrema


def mm(value: dict[str, Any], key: str = "value_nm") -> float:
    """Read native integer distances without applying a design grid."""
    return float(value.get(key, 0)) / 1e6


def point(value: dict[str, Any]) -> Point:
    """Read actual board coordinates from a native Vector2."""
    return Point(round(mm(value, "x_nm"), 6), round(mm(value, "y_nm"), 6))


def layer(value: str) -> str:
    """Translate the official enum spelling to the canonical layer name."""
    return value.removeprefix("BL_").replace("_", ".")


def identity(value: dict[str, Any]) -> str:
    """Read the UUID returned by KiCad."""
    return str(value.get("id", {}).get("value", ""))


def graphic(value: dict[str, Any]) -> Graphic:
    """Read native graphic geometry and its explicit drawing attributes."""
    shape = value["shape"]
    kind = next(
        k for k in ("segment", "arc", "circle", "rectangle", "polygon") if k in shape
    )
    geometry = shape[kind]
    pts: tuple[Point, ...]
    if kind == "segment":
        pts = (point(geometry.get("start", {})), point(geometry.get("end", {})))
    elif kind == "arc":
        pts = tuple(point(geometry.get(k, {})) for k in ("start", "mid", "end"))
    elif kind == "circle":
        centre = point(geometry.get("center", {}))
        pts = (centre, point(geometry.get("radius_point", {})))
    elif kind == "rectangle":
        pts = tuple(point(geometry.get(k, {})) for k in ("top_left", "bottom_right"))
    else:
        polygons = geometry.get("polygons", [])
        if len(polygons) != 1 or polygons[0].get("holes"):
            raise ValueError(
                "primitive polygon reply must have one contour and no holes"
            )
        pts = tuple(point(n["point"]) for n in polygons[0]["outline"]["nodes"])
    attributes = shape.get("attributes", {})
    return Graphic(
        identity(value),
        "line" if kind == "segment" else kind,
        layer(value["layer"]),
        pts,
        mm(attributes.get("stroke", {}).get("width", {})),
        attributes.get("fill", {}).get("fill_type") == "GFT_FILLED",
    )


def text(value: dict[str, Any]) -> BoardText:
    """Read actual native text dimensions and alignment."""
    data = value.get("text", {})
    attr = data.get("attributes", {})
    size = attr.get("size", {})
    return BoardText(
        identity(value),
        data.get("text", ""),
        point(data.get("position", {})),
        layer(value["layer"]),
        mm(size, "x_nm"),
        mm(size, "y_nm"),
        mm(attr.get("stroke_width", {})),
        attr.get("angle", {}).get("value_degrees", 0) % 360,
        attr.get("mirrored", False),
        attr.get("horizontal_alignment", "HA_CENTER").removeprefix("HA_").lower(),
        attr.get("vertical_alignment", "VA_CENTER").removeprefix("VA_").lower(),
    )


def track(value: dict[str, Any]) -> Track:
    """Read a created native track rather than echoing input coordinates."""
    return Track(
        point(value.get("start", {})),
        point(value.get("end", {})),
        layer(value["layer"]),
        mm(value.get("width", {})),
        value.get("net", {}).get("name", ""),
        identity(value),
    )


def via(value: dict[str, Any]) -> Via:
    """Read a created native via and its real drill stack."""
    stack = value["pad_stack"]
    drill = stack["drill"]
    return Via(
        at=point(value.get("position", {})),
        diameter=mm(stack["copper_layers"][0]["size"], "x_nm"),
        drill=mm(drill.get("diameter", {}), "x_nm"),
        layers=(layer(drill["start_layer"]), layer(drill["end_layer"])),
        net=value.get("net", {}).get("name", ""),
        uuid=identity(value),
        kind={
            "VT_THROUGH": "through",
            "VT_BLIND_BURIED": "blind_buried",
            "VT_MICRO": "microvia",
        }[value["type"]],
    )


def zone(value: dict[str, Any]) -> Zone:
    """Read one native zone or explicit keepout area."""
    copper = value.get("copper_settings", {})
    connection = copper.get("connection", {})
    rules = value.get("rule_area_settings", {})
    forbids = tuple(
        name
        for name, key in (
            ("tracks", "keepout_tracks"),
            ("vias", "keepout_vias"),
            ("pads", "keepout_pads"),
            ("pours", "keepout_copper"),
            ("footprints", "keepout_footprints"),
        )
        if rules.get(key)
    )
    return Zone(
        points=tuple(
            point(n["point"])
            for n in value["outline"]["polygons"][0]["outline"]["nodes"]
        ),
        layer=layer(value["layers"][0]),
        net=copper.get("net", {}).get("name", ""),
        uuid=identity(value),
        clearance=mm(copper.get("clearance", {})),
        pad_connection={
            "ZCS_THERMAL": "thermal",
            "ZCS_FULL": "solid",
            "ZCS_NONE": "none",
        }.get(connection.get("zone_connection"), "thermal"),
        min_thickness=mm(copper.get("min_thickness", {})),
        thermal_gap=mm(connection.get("thermal_spokes", {}).get("gap", {})),
        thermal_spoke_width=mm(connection.get("thermal_spokes", {}).get("width", {})),
        priority=value.get("priority", 0),
        island_removal={
            "IRM_ALWAYS": "always",
            "IRM_NEVER": "never",
            "IRM_AREA": "area",
        }.get(copper.get("island_mode"), "always"),
        min_island_area=float(copper.get("min_island_area", 0)) / 1e12,
        filled=value.get("filled", False),
        forbids=forbids,
    )


def footprint(value: dict[str, Any]) -> Footprint:
    """Read native placed pad coordinates and conservative graphic envelopes."""
    origin = point(value.get("position", {}))
    angle = float(value.get("orientation", {}).get("value_degrees", 0))
    side = "B" if value["layer"] == "BL_B_Cu" else "F"
    definition = value["definition"]
    parts = definition.get("items", [])
    pads = []
    for item in parts:
        if not item.get("@type", "").endswith(".Pad"):
            continue
        stack = item["pad_stack"]
        copper = stack["copper_layers"][0]
        shape = copper.get("shape", "PSS_UNKNOWN").removeprefix("PSS_").lower()
        if shape == "rectangle":
            shape = "rect"
        pad_kind = {"PT_PTH": "pth", "PT_NPTH": "npth"}.get(item["type"], "smd")
        pads.append(
            Pad(
                number=item.get("number", ""),
                at=point(item.get("position", {})),
                size=(mm(copper["size"], "x_nm"), mm(copper["size"], "y_nm")),
                layers=tuple(layer(name) for name in stack.get("layers", [])),
                net=item.get("net", {}).get("name", ""),
                drill=mm(stack.get("drill", {}).get("diameter", {}), "x_nm"),
                kind=pad_kind,
                shape=shape,
                rotation=stack.get("angle", {}).get("value_degrees", 0) % 360,
                corner_ratio=copper.get("corner_rounding_ratio", 0),
                uuid=identity(item),
                geometry_supported=shape in ("circle", "rect", "oval", "roundrect")
                and not copper.get("offset"),
            )
        )
    # Native rollback can reorder footprint children. Stable identities and
    # numbers keep an unchanged observation deterministic across that restore.
    pads.sort(key=lambda pad: (pad.number, pad.uuid))
    radians = math.radians(angle)
    c, s = math.cos(radians), math.sin(radians)

    def envelope(suffix: str) -> tuple[Point, ...]:
        local: list[tuple[float, float]] = []
        for item in parts:
            if not item.get("layer", "").endswith(suffix) or "shape" not in item:
                continue
            if any(key in item["shape"] for key in ("bezier", "polyline")):
                return ()
            shape = graphic(item)
            points = list(shape.points)
            if shape.kind == "rectangle":
                a, b = shape.points
                points += [Point(a.x, b.y), Point(b.x, a.y)]
            transformed = []
            for p in points:
                dx, dy = p.x - origin.x, p.y - origin.y
                transformed.append(Point(dx * c - dy * s, dx * s + dy * c))
            if shape.kind == "arc":
                transformed = list(_arc_extrema(tuple(transformed)))
            elif shape.kind == "circle":
                centre = transformed[0]
                radius = math.hypot(
                    points[1].x - points[0].x, points[1].y - points[0].y
                )
                transformed = [
                    Point(centre.x - radius, centre.y - radius),
                    Point(centre.x + radius, centre.y + radius),
                ]
            local.extend((p.x, p.y) for p in transformed)
        if not local and suffix == "_CrtYd":
            for pad in pads:
                # Preserve the previous conservative pad-envelope fallback.
                dx, dy = pad.at.x - origin.x, pad.at.y - origin.y
                x, y = dx * c - dy * s, dx * s + dy * c
                local += [
                    (x - pad.size[0] / 2, y - pad.size[1] / 2),
                    (x + pad.size[0] / 2, y + pad.size[1] / 2),
                ]
        if not local:
            return ()
        x1, x2 = min(x for x, _ in local), max(x for x, _ in local)
        y1, y2 = min(y for _, y in local), max(y for _, y in local)
        return tuple(
            Point(
                round(origin.x + x * c + y * s, 6), round(origin.y - x * s + y * c, 6)
            )
            for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        )

    courtyard = envelope("_CrtYd")
    fabrication = envelope("_Fab")
    if not courtyard:
        courtyard = (origin, origin, origin, origin)
    centre = Point(sum(p.x for p in courtyard) / 4, sum(p.y for p in courtyard) / 4)
    lib_id = definition["id"]
    return Footprint(
        ref=value["reference_field"]["text"]["text"]["text"],
        fp_id=lib_id.get("library_nickname", "") + ":" + lib_id["entry_name"],
        value=value["value_field"]["text"]["text"].get("text", ""),
        at=origin,
        rotation=(angle - (180 if side == "B" else 0)) % 360,
        side=side,
        pads=tuple(pads),
        courtyard=(
            round(max(p.x for p in courtyard) - min(p.x for p in courtyard), 3),
            round(max(p.y for p in courtyard) - min(p.y for p in courtyard), 3),
        ),
        courtyard_offset=Point(centre.x - origin.x, centre.y - origin.y),
        courtyard_center=centre,
        courtyard_polygon=courtyard,
        uuid=identity(value),
        fabrication_polygon=fabrication,
        fabrication_status="available" if fabrication else "missing",
    )
