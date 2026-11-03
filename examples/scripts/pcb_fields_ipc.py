"""Preserve a native 37-degree footprint during field-only MCP edits."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Check field values, movement, rollback and native save/reload fidelity."""
    parent = Path("out/pcb-fields-ipc")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="run-", dir=parent)).resolve()
    fixture = Path(__file__).resolve().parents[1] / "assets/native-angle37.kicad_pcb"
    # Recorded from native PlaceFootprintFromLibrary at 37 degrees. Its local
    # geometry is exact; cloning the whole object through IPC changes it.
    path = str(root / "native.kicad_pcb")
    shutil.copyfile(fixture, path)
    async with Client(mcp) as client:

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data: dict[str, Any] = (await client.call_tool(tool, args)).data
            assert data.get("ok"), (tool, data)
            return data

        async def verify() -> None:
            result = await call("check_board", path=path)
            mismatches = [f for f in result["findings"]
                          if f["kind"] == "lib_footprint_mismatch"]
            assert not mismatches, mismatches
            parts = (await call("list_footprints", path=path, with_pads=True))[
                "footprints"
            ]
            assert len(parts) == 1 and parts[0]["rotation"] == 37, parts
            assert parts[0]["pads"] == original["pads"], parts

        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 37, "y1": 17, "x2": 47, "y2": 27,
        }])
        original = (await call("list_footprints", path=path, with_pads=True))[
            "footprints"
        ][0]
        await verify()
        await call("set_footprint_fields", path=path, fields=[
            {"ref": "Q1", "name": "Datasheet", "value": "field-only IPC"},
            {"ref": "Q1", "name": "Description", "value": "native child update"},
        ])
        fields = (await call("get_footprint_fields", path=path, ref="Q1"))["fields"]
        assert fields["Description"] == "native child update"
        assert fields["Datasheet"] == "field-only IPC"
        await verify()
        refused = (await client.call_tool("set_footprint_fields", {
            "path": path, "fields": [
                {"ref": "Q1", "name": "Datasheet", "value": "must roll back"},
                {"ref": "MISSING", "name": "Datasheet", "value": "invalid"},
            ],
        })).data
        assert not refused["ok"] and refused["index"] == 1, refused
        after = await call("get_footprint_fields", path=path, ref="Q1")
        assert after["fields"] == fields
        refused = (await client.call_tool("move_footprint_fields", {
            "path": path, "moves": [{
                "ref": "Q1", "name": "Reference", "dx": 0, "dy": -3.5,
                "rotation": 0, "layer": "F.SilkS", "hide": False,
            }],
        })).data
        assert not refused["ok"] and refused["index"] == 0, refused
        assert "absolute text angle" in refused["error"], refused
        await verify()
        await call("move_footprint_fields", path=path, moves=[{
            "ref": "Q1", "name": "Reference", "dx": 0, "dy": -3.5,
            "rotation": 37, "layer": "F.SilkS", "hide": False,
        }])
        await verify()
        await call("save_board", path=path)
        await call("reload_board", path=path)
        await verify()
        await call("render_board_layout", path=path,
                   output_file=str(root / "fields.png"))
        print(json.dumps({"ok": True, "directory": str(root),
                          "angle": 37, "library_mismatches": 0,
                          "refused_native_angles": 1}))


if __name__ == "__main__":
    asyncio.run(main())
