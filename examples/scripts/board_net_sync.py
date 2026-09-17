"""Check that the PCB takes its pad nets from a schematic via MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from _board_fixture import export_footprints
from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Exercise complete transfer, missing pads, NC clearing and copper refusal."""
    root = Path("out/board-net-sync").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sheet = str(root / "pair.kicad_sch")
    board = str(root / "pair.kicad_pcb")
    partial = str(root / "partial.kicad_pcb")
    footprint = "Resistor_SMD:R_0603_1608Metric"
    Path(board).unlink(missing_ok=True)

    async with Client(mcp) as client:
        available = {item.name for item in await client.list_tools()}
        assert "set_pad_nets" not in available
        assert "place_footprints" not in available
        async def call(name: str, **kwargs: Any) -> dict[str, Any]:
            data = (await client.call_tool(name, kwargs)).data
            assert data["ok"], data
            return data

        await call("new_sheet", path=sheet)
        placed = (await call("add_components", path=sheet, parts=[
            {"lib_id": "Device:R", "ref": "R1", "x": 50.8, "y": 50.8},
            {"lib_id": "Device:R", "ref": "R2", "x": 50.8, "y": 63.5},
        ]))["parts"]
        await call("set_fields", path=sheet, fields=[
            {"ref": ref, "name": "Footprint", "value": footprint}
            for ref in ("R1", "R2")
        ])
        pins = {
            (part["ref"], pin["number"]): pin
            for part in placed for pin in part["pins"]
        }
        a, b = pins[("R1", "2")], pins[("R2", "1")]
        assert a["x"] == b["x"]
        await call("add_wires", path=sheet, wires=[{
            "x1": a["x"], "y1": a["y"],
            "x2": b["x"], "y2": b["y"],
        }])
        await call("add_no_connects", path=sheet, points=[
            {"x": pins[key]["x"], "y": pins[key]["y"]}
            for key in (("R1", "1"), ("R2", "2"))
        ])
        await call("save_sheet", path=sheet)
        connected = next(
            net for net in (await call("list_nets", path=sheet))["nets"]
            if net["count"] == 2
        )

        await call("new_board", path=partial)
        await export_footprints(call, partial, [
            {"fp_id": footprint, "ref": "R1", "x": 20, "y": 20},
        ])
        missing = (await client.call_tool("sync_board_nets", {
            "schematic_path": sheet, "board_path": partial,
        })).data
        assert not missing["ok"] and "lack placed board pads" in missing["error"]
        unchanged = await call("get_footprint", path=partial, ref="R1")
        assert all(not pad["net"] for pad in unchanged["pads"])

        missing_poses = (await client.call_tool("update_board_from_schematic", {
            "schematic_path": sheet, "board_path": board,
        })).data
        assert not missing_poses["ok"] and not Path(board).exists()
        exported = await call(
            "update_board_from_schematic", schematic_path=sheet,
            board_path=board, placements=[
                {"ref": "R1", "x": 20, "y": 20},
                {"ref": "R2", "x": 30, "y": 20,
                 "rotation": 90, "side": "B"},
            ], layers=4,
        )
        assert exported["created"] and exported["changed_pad_count"] == 2
        assert Path(board).exists()
        for name in ("Footprint", "Value", "Reference"):
            refused_field = (await client.call_tool("set_footprint_fields", {
                "path": board,
                "fields": [{"ref": "R1", "name": name, "value": "WRONG"}],
            })).data
            assert not refused_field["ok"] and "belongs to the schematic" in (
                refused_field["error"]
            )
        back = await call("get_footprint", path=board, ref="R2")
        assert back["side"] == "B" and back["rotation"] == 90
        repeated = await call(
            "update_board_from_schematic", schematic_path=sheet,
            board_path=board,
        )
        assert not repeated["created"] and repeated["changed_pad_count"] == 0
        await call("remove_wires", path=sheet, wires=[{
            "x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"],
        }])
        await call("add_no_connects", path=sheet, points=[
            {"x": a["x"], "y": a["y"]},
            {"x": b["x"], "y": b["y"]},
        ])
        cleared = await call(
            "sync_board_nets", schematic_path=sheet, board_path=board,
        )
        assert cleared["changed_count"] == 2
        pads = (await call("get_footprint", path=board, ref="R1"))["pads"]
        assert not next(p for p in pads if p["number"] == "2")["net"]
        await call("remove_no_connects", path=sheet, points=[
            {"x": a["x"], "y": a["y"]},
            {"x": b["x"], "y": b["y"]},
        ])
        await call("add_wires", path=sheet, wires=[{
            "x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"],
        }])
        restored = await call(
            "update_board_from_schematic", schematic_path=sheet,
            board_path=board,
        )
        assert restored["changed_pad_count"] == 2
        signal = next(p for p in (
            await call("get_footprint", path=board, ref="R1"))["pads"]
            if p["number"] == "2")
        await call("add_tracks", path=board, tracks=[{
            "x1": signal["x"], "y1": signal["y"],
            "x2": signal["x"] + 2, "y2": signal["y"],
            "layer": "F.Cu", "width": 0.2, "net": connected["name"],
        }])
        preview = await call(
            "sync_board_nets", schematic_path=sheet, board_path=board,
            net_names={connected["name"]: "RENAMED"}, dry_run=True,
        )
        assert preview["changed_count"] == 2 and not preview["safe_to_apply"]
        refused = (await client.call_tool("sync_board_nets", {
            "schematic_path": sheet, "board_path": board,
            "net_names": {connected["name"]: "RENAMED"},
        })).data
        assert not refused["ok"] and "existing copper" in refused["error"]
        print("PASS: PCB nets follow schematic; unsafe updates are refused")


if __name__ == "__main__":
    asyncio.run(main())
