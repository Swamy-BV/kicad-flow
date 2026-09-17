"""Small schematic sources for PCB inspection examples."""

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
    sheet = str(Path(board_path).with_suffix(".kicad_sch"))
    existing = (await call("list_footprints", path=board_path))["footprints"]
    definitions = {
        item["fp_id"]: await call("footprint_pads", fp_id=item["fp_id"])
        for item in placements
    }
    parts = []
    for index, item in enumerate(placements, start=len(existing)):
        numbers = {pad["number"] for pad in definitions[item["fp_id"]]["pads"]
                   if pad["number"]}
        symbol = item.get("lib_id")
        if not symbol:
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
    if not existing:
        await call("new_sheet", path=sheet, paper="A3")
    elif not Path(sheet).is_file():
        raise RuntimeError("placed footprints lack their source schematic")
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
) -> dict[str, Any]:
    """Wire the same schematic that supplied the board's footprints."""
    sheet = str(Path(board_path).with_suffix(".kicad_sch"))
    refs = sorted({item["ref"] for item in assignments})
    parts = {
        ref: await call("get_component", path=sheet, ref=ref)
        for ref in refs
    }
    pins = {
        (part["ref"], pin["number"]): pin
        for part in parts.values() for pin in part["pins"]
    }
    mapped = {(item["ref"], item["pad"]): item["net"] for item in assignments}
    unknown = set(mapped) - set(pins)
    if unknown:
        raise ValueError(f"fixture assignments lack schematic pins: {unknown}")
    await call("remove_no_connects", path=sheet, points=[
        {"x": pins[key]["x"], "y": pins[key]["y"]}
        for key in mapped
    ])
    wires = []
    labels = []
    for key, net in mapped.items():
        pin = pins[key]
        away = {
            0.0: (-1.0, 0.0), 90.0: (0.0, 1.0),
            180.0: (1.0, 0.0), 270.0: (0.0, -1.0),
        }[pin["orientation"] % 360]
        x = pin["x"] + away[0] * 7.62
        y = pin["y"] + away[1] * 7.62
        wires.append({"x1": pin["x"], "y1": pin["y"], "x2": x, "y2": y})
        labels.append({
            "x": x, "y": y, "text": net, "kind": "global",
            "rotation": 90 if away[1] < 0 else 270 if away[1] > 0 else 0,
            "justify": "right" if away[0] < 0 else "left",
        })
    await call("add_wires", path=sheet, wires=wires)
    await call("add_labels", path=sheet, labels=labels)
    await call("save_sheet", path=sheet)
    return await call(
        "update_board_from_schematic", schematic_path=sheet,
        board_path=board_path,
    )
