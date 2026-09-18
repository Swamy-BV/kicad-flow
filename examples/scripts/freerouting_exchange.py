"""Exercise KiCad DSN/SES exchange and optional FreeRouting through MCP."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Build a tiny schematic-derived board and round-trip a router session."""
    root = Path("out/freerouting-exchange").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sheet = str(root / "pair.kicad_sch")
    board = str(root / "pair.kicad_pcb")
    dsn = str(root / "pair.dsn")
    ses = str(root / "pair.ses")
    routed = str(root / "pair-routed.kicad_pcb")
    for name in (board, dsn, ses, routed):
        Path(name).unlink(missing_ok=True)
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_sch"):
        Path(routed).with_suffix(suffix).unlink(missing_ok=True)

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], (tool, data)
            return data

        await call("new_sheet", path=sheet)
        parts = (await call("add_components", path=sheet, parts=[
            {"lib_id": "Device:R", "ref": "R1", "x": 50.8, "y": 50.8},
            {"lib_id": "Device:R", "ref": "R2", "x": 50.8, "y": 63.5},
        ]))["parts"]
        await call("set_fields", path=sheet, fields=[
            {"ref": ref, "name": "Footprint",
             "value": "Resistor_SMD:R_0603_1608Metric"}
            for ref in ("R1", "R2")
        ])
        pins = {
            (part["ref"], pin["number"]): pin
            for part in parts for pin in part["pins"]
        }
        first, second = pins[("R1", "2")], pins[("R2", "1")]
        await call("add_wires", path=sheet, wires=[{
            "x1": first["x"], "y1": first["y"],
            "x2": second["x"], "y2": second["y"],
        }])
        await call("add_no_connects", path=sheet, points=[
            {"x": pins[key]["x"], "y": pins[key]["y"]}
            for key in (("R1", "1"), ("R2", "2"))
        ])
        await call("save_sheet", path=sheet)
        await call("update_board_from_schematic", schematic_path=sheet,
                   board_path=board, placements=[
                       {"ref": "R1", "x": 20, "y": 20},
                       {"ref": "R2", "x": 35, "y": 20, "side": "B"},
                   ])
        await call("add_graphics", path=board, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 10, "y1": 10, "x2": 45, "y2": 30,
        }])
        await call("save_board", path=board)
        source_bytes = Path(board).read_bytes()
        before = await call("unrouted_connections", path=board)
        assert before["count"] == 1, before
        await call("export_routing_design", path=board, output_file=dsn)
        assert Path(dsn).is_file()

        jar = os.environ.get("FREEROUTING_JAR")
        if not jar:
            print("PASS DSN export; set FREEROUTING_JAR for route/import check")
            return
        await call("run_freerouting", dsn_path=dsn, ses_path=ses,
                   jar_path=jar,
                   java_path=os.environ.get("FREEROUTING_JAVA", "java"),
                   max_passes=5, timeout_seconds=120)
        result = await call("import_routing_session", path=board,
                            session_file=ses, output_file=routed)
        assert result["unrouted_count"] == 0, result
        assert result["drc_errors"] == 0, result
        assert (await call("unrouted_connections", path=board))["count"] == 1
        assert Path(board).read_bytes() == source_bytes
        print("PASS FreeRouting DSN -> SES -> KiCad; source board unchanged")


if __name__ == "__main__":
    asyncio.run(main())
