"""Contract values observed from native schematic objects, without file encoding."""

from __future__ import annotations

from typing import Any

from kicad_flow.schematic.types import (
    Label,
    Part,
    Pin,
    Point,
    SheetGraphic,
    SheetRef,
    SheetText,
)

from ._geometry import _pin_on_sheet


def point(value: dict[str, Any]) -> Point:
    """Read a native integer position in millimetres."""
    return Point(float(value.get("x_nm", 0)) / 1e6, float(value.get("y_nm", 0)) / 1e6)


def pin(value: dict[str, Any]) -> Pin:
    """Read a flattened native pin observation."""
    return Pin(
        value["number"],
        value["name"],
        Point(value["x"], value["y"]),
        value["orientation"],
        value["kind"],
        value["length"],
    )


def part(value: dict[str, Any]) -> Part:
    """Resolve the actual native definition's selected-unit pins on the sheet."""
    definition = value["definition"]
    identifier = definition["id"]
    transform = value.get("transform", {})
    rotation = float(transform.get("orientation", "SSO_0").removeprefix("SSO_"))
    mirror = (
        "x" if transform.get("mirror_x") else "y" if transform.get("mirror_y") else ""
    )
    at = point(value.get("position", {}))
    unit = value.get("unit", {}).get("unit", 1)
    pins = []
    for child in definition.get("items", []):
        raw = child.get("item", {})
        if not raw.get("@type", "").endswith(".SchematicPin"):
            continue
        if child.get("unit", {}).get("unit", 0) not in (0, unit):
            continue
        if child.get("body_style", {}).get("style", 1) not in (
            0,
            value.get("body_style", {}).get("style", 1),
        ):
            continue
        local = point(raw.get("position", {}))
        angle = {"SPO_RIGHT": 0, "SPO_UP": 90, "SPO_LEFT": 180, "SPO_DOWN": 270}[
            raw["orientation"]
        ]
        here, direction = _pin_on_sheet(local.x, -local.y, angle, at, rotation, mirror)
        kind = (
            raw.get("electrical_type", "EPT_UNSPECIFIED").removeprefix("EPT_").lower()
        )
        kind = {
            "power_input": "power_in",
            "power_output": "power_out",
            "tristate": "tri_state",
        }.get(kind, kind)
        name = raw.get("name", "")
        pins.append(
            Pin(
                raw.get("number", ""),
                "" if name == "~" else name,
                here,
                direction,
                kind,
                float(raw.get("length", {}).get("value_nm", 0)) / 1e6,
            )
        )
    return Part(
        value["reference_field"]["text"].get("text", ""),
        identifier["library_nickname"] + ":" + identifier["entry_name"],
        value["value_field"]["text"].get("text", ""),
        at,
        rotation,
        mirror,
        unit,
        tuple(sorted(pins, key=lambda p: p.number)),
        value["id"]["value"],
    )


def result(operation: str, reply: dict[str, Any]) -> Any:
    """Adapt native observations to the unchanged primitive return types."""
    item = reply.get("item", {})
    if operation in (
        "place",
        "move",
        "rotate",
        "mirror",
        "power",
        "power_flag",
        "part",
    ):
        return part(item)
    if operation == "parts":
        return [part(v) for v in reply["items"]]
    if operation in ("wire",):
        return [Point(*p) for p in reply["points"]]
    if operation in ("junction", "no_connect", "move_field"):
        return Point(*reply["point"])
    if operation in ("add_sheet", "move_sheet"):
        return SheetRef(
            reply["name"],
            reply["filename"],
            Point(*reply["point"]),
            tuple(reply["size"]),
            reply["uuid"],
            reply["instance_path"],
            tuple(pin(p) for p in reply["pins"]),
        )
    if operation in ("label", "move_label_by_id", "rotate_label_by_id"):
        return Label(
            reply["uuid"],
            reply["text"],
            reply["kind"],
            Point(*reply["point"]),
            reply["rotation"],
            reply["justify"],
        )
    if operation in ("text", "update_text"):
        return SheetText(
            reply["uuid"],
            reply["text"],
            Point(*reply["point"]),
            reply["size"],
            reply["rotation"],
            reply["bold"],
            reply["justify"],
        )
    if operation in ("graphic", "move_graphic"):
        return SheetGraphic(
            reply["uuid"],
            reply["kind"],
            tuple(Point(*p) for p in reply["points"]),
            reply["width"],
            reply["stroke"],
        )
    return reply.get("value")
