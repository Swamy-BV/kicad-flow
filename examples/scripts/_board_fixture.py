"""Small schematic sources for board-only MCP inspection examples."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


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
