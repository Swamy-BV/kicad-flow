"""Draw visual functional groups on a schematic through MCP primitives.

The rectangles and text are independent, non-electrical objects. Outputs live
under ``out/schematic-groups`` so tracked example fixtures remain untouched.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("out/schematic-groups")


async def build(client: Client[Any]) -> None:
    """Exercise add/list/move/remove, rollback, scene readback and rendering."""
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    sheet = str((OUT / "groups.kicad_sch").resolve())

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        result = await client.call_tool(tool, {"path": sheet, **arguments})
        data: dict[str, Any] = result.data
        assert data.get("ok") is True, (tool, data)
        return data

    await call("new_sheet", title="Visual functional groups")
    made = await call(
        "add_schematic_graphics",
        graphics=[
            {
                "kind": "rectangle",
                "x1": 20.32,
                "y1": 25.4,
                "x2": 88.9,
                "y2": 76.2,
                "width": 0.3,
                "stroke": "dash",
            },
            {
                "kind": "rectangle",
                "x1": 101.6,
                "y1": 25.4,
                "x2": 170.18,
                "y2": 76.2,
                "width": 0.3,
                "stroke": "dash",
            },
            {
                "kind": "rectangle",
                "x1": 182.88,
                "y1": 25.4,
                "x2": 251.46,
                "y2": 76.2,
                "width": 0.3,
                "stroke": "dash",
            },
            {
                "kind": "polyline",
                "points": [[20.32, 88.9], [251.46, 88.9]],
                "width": 0.2,
                "stroke": "dot",
            },
        ],
    )
    assert len({item["uuid"] for item in made["graphics"]}) == 4

    notes = await call(
        "add_texts",
        notes=[
            {"x": 24.13, "y": 33.02, "text": "POWER", "size": 2.54,
             "bold": True},
            {"x": 24.13, "y": 38.1, "text": "Protection and regulation"},
            {"x": 105.41, "y": 33.02, "text": "CONTROL", "size": 2.54,
             "bold": True},
            {"x": 105.41, "y": 38.1, "text": "MCU, sensing and timing"},
            {"x": 186.69, "y": 33.02, "text": "OUTPUTS", "size": 2.54,
             "bold": True},
            {"x": 186.69, "y": 38.1, "text": "Drivers and connectors"},
            {"x": 20.32, "y": 95.25,
             "text": "Rectangles organize the page; they do not create nets."},
            {"x": 20.32, "y": 101.6, "text": "temporary note"},
        ],
    )
    listed_notes = await call("list_texts")
    assert listed_notes["count"] == 8
    updated = await call("update_texts", updates=[{
        "uuid": notes["notes"][0]["uuid"], "text": "POWER INPUT",
    }])
    assert updated["notes"][0]["text"] == "POWER INPUT"
    await call("remove_texts", uuids=[notes["notes"][-1]["uuid"]])
    assert (await call("list_texts"))["count"] == 7

    listed = await call("list_schematic_graphics")
    assert listed["count"] == 4 and listed["graphics"] == made["graphics"]
    moved = await call(
        "move_schematic_graphics",
        moves=[{"uuid": made["graphics"][3]["uuid"], "dx": 0, "dy": 1.27}],
    )
    assert moved["graphics"][0]["points"][0]["y"] == 90.17
    await call(
        "remove_schematic_graphics", uuids=[made["graphics"][3]["uuid"]]
    )

    refused = await client.call_tool(
        "add_schematic_graphics",
        {
            "path": sheet,
            "graphics": [
                {"kind": "rectangle", "x1": 20.32, "y1": 101.6,
                 "x2": 88.9, "y2": 127},
                {"kind": "rectangle", "x1": 100, "y1": 100,
                 "x2": 100, "y2": 120},
            ],
        },
    )
    assert refused.data["ok"] is False and refused.data["applied_count"] == 0
    assert (await call("list_schematic_graphics"))["count"] == 3

    scene = await call("inspect_schematic_scene")
    assert len([item for item in scene["objects"] if item["kind"] == "polyline"]) == 3
    await call("save_sheet")
    rendered = await call("render_schematic", output_dir=str(OUT.resolve()))
    assert rendered["count"] == 1
    print(
        "PASS: 3 caller-defined group rectangles, independent editable text, "
        "stable IDs, atomic rollback, scene readback and native render"
    )
    print(rendered["images"][0])


async def main() -> None:
    """Run the example in-process through the public MCP tool layer."""
    async with Client(mcp) as client:
        await build(client)


if __name__ == "__main__":
    asyncio.run(main())
