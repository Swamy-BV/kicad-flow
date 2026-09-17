"""Small schematic sources for board-only MCP inspection examples."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


async def export_footprints(
    call: Callable[..., Awaitable[dict[str, Any]]],
    board_path: str,
    placements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Give a board geometry fixture a schematic source before export."""
    sheet = str(Path(board_path).with_suffix(".placement.kicad_sch"))
    definitions = {
        item["fp_id"]: await call("footprint_pads", fp_id=item["fp_id"])
        for item in placements
    }
    parts = []
    for index, item in enumerate(placements):
        numbers = {pad["number"] for pad in definitions[item["fp_id"]]["pads"]
                   if pad["number"]}
        if numbers == {str(number) for number in range(1, len(numbers) + 1)}:
            symbol = ("Connector:TestPoint" if len(numbers) == 1 else
                      f"Connector_Generic:Conn_01x{len(numbers):02d}")
        else:
            symbol = "Mechanical:MountingHole"
        parts.append({
            "lib_id": symbol, "ref": item["ref"],
            "x": 50.8 + 25.4 * (index % 6),
            "y": 50.8 + 25.4 * (index // 6),
            "value": item.get("value", ""),
        })
    await call("new_sheet", path=sheet, paper="A3")
    placed = (await call("add_components", path=sheet, parts=parts))["parts"]
    await call("set_fields", path=sheet, fields=[
        {"ref": item["ref"], "name": "Footprint", "value": item["fp_id"]}
        for item in placements
    ])
    await call("add_no_connects", path=sheet, points=[
        {"x": pin["x"], "y": pin["y"]}
        for part in placed for pin in part["pins"]
    ])
    await call("save_sheet", path=sheet)
    result = await call(
        "update_board_from_schematic", schematic_path=sheet,
        board_path=board_path,
        placements=[{key: value for key, value in item.items()
                     if key in ("ref", "x", "y", "rotation", "side", "anchor")}
                    for item in placements],
    )
    if len(result["placed"]) != len(placements):
        raise RuntimeError("fixture schematic export missed a footprint")
    refs = {item["ref"] for item in placements}
    footprints = (await call("list_footprints", path=board_path,
                             with_pads=True))["footprints"]
    selected = [item for item in footprints if item["ref"] in refs]
    return {"ok": True, "count": len(selected), "footprints": selected}


async def schematic_nets(
    call: Callable[..., Awaitable[dict[str, Any]]],
    board_path: str,
    assignments: list[dict[str, str]],
    *,
    two_pin_refs: set[str] | None = None,
) -> dict[str, Any]:
    """Build a tiny schematic and transfer its stated nets to placed pads."""
    sheet = str(Path(board_path).with_suffix(".fixture.kicad_sch"))
    refs = sorted({item["ref"] for item in assignments})
    footprints = {
        ref: await call("get_footprint", path=board_path, ref=ref)
        for ref in refs
    }
    two_pin = two_pin_refs or set()
    await call("new_sheet", path=sheet)
    parts = (await call("add_components", path=sheet, parts=[
        {
            "lib_id": "Device:R" if ref in two_pin else "Connector:TestPoint",
            "ref": ref,
            "x": 50.8 + 25.4 * (index % 6),
            "y": 50.8 + 25.4 * (index // 6),
        }
        for index, ref in enumerate(refs)
    ]))["parts"]
    await call("set_fields", path=sheet, fields=[
        {"ref": ref, "name": name, "value": value}
        for ref, item in footprints.items()
        for name, value in (
            ("Footprint", item["fp_id"]), ("Value", item["value"]),
        )
    ])
    pins = {
        (part["ref"], pin["number"]): pin
        for part in parts for pin in part["pins"]
    }
    mapped = {(item["ref"], item["pad"]): item["net"] for item in assignments}
    unknown = set(mapped) - set(pins)
    if unknown:
        raise ValueError(f"fixture assignments lack schematic pins: {unknown}")
    await call("add_labels", path=sheet, labels=[
        {"x": pins[key]["x"], "y": pins[key]["y"],
         "text": net, "kind": "global"}
        for key, net in mapped.items()
    ])
    await call("add_no_connects", path=sheet, points=[
        {"x": pin["x"], "y": pin["y"]}
        for key, pin in pins.items() if key not in mapped
    ])
    await call("save_sheet", path=sheet)
    return await call(
        "update_board_from_schematic", schematic_path=sheet,
        board_path=board_path,
    )
