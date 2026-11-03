"""Check headless native SES import, curved outlines and refusal isolation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Import a recorded FreeRouting result through unchanged MCP tools."""
    parent = Path("out/routing-ipc")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix="run-", dir=parent)).resolve()
    sheet, board = str(root / "pair.kicad_sch"), str(root / "pair.kicad_pcb")
    fixture = Path(__file__).resolve().parents[1] / "assets/routing-pair.ses"
    session = fixture.read_text(encoding="utf-8")
    # The fixture is an actual FreeRouting 2.4.1 output for these two poses.
    async with Client(mcp) as client:

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data: dict[str, Any] = (await client.call_tool(tool, args)).data
            assert data.get("ok"), (tool, data)
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
        pins = {(part["ref"], p["number"]): p for part in parts for p in part["pins"]}
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
        graphics = [
            {"kind": "line", "x1": 12, "y1": 10, "x2": 43, "y2": 10},
            {"kind": "arc", "x1": 43, "y1": 10, "xm": 44.414214,
             "ym": 10.585786, "x2": 45, "y2": 12},
            {"kind": "line", "x1": 45, "y1": 12, "x2": 45, "y2": 28},
            {"kind": "arc", "x1": 45, "y1": 28, "xm": 44.414214,
             "ym": 29.414214, "x2": 43, "y2": 30},
            {"kind": "line", "x1": 43, "y1": 30, "x2": 12, "y2": 30},
            {"kind": "arc", "x1": 12, "y1": 30, "xm": 10.585786,
             "ym": 29.414214, "x2": 10, "y2": 28},
            {"kind": "line", "x1": 10, "y1": 28, "x2": 10, "y2": 12},
            {"kind": "arc", "x1": 10, "y1": 12, "xm": 10.585786,
             "ym": 10.585786, "x2": 12, "y2": 10},
            {"kind": "circle", "x": 15, "y": 15, "radius": 1},
        ]
        for offset in range(0, len(graphics), 5):
            await call("add_graphics", path=board, graphics=[
                {**g, "layer": "Edge.Cuts"} for g in graphics[offset:offset + 5]
            ])
        await call("set_board_grid", path=board, spacing_mm=0.2)
        await call("save_board", path=board)
        before = Path(board).read_bytes()
        metadata = Path(board).with_suffix(".kicad-flow.json").read_bytes()
        refused = [
            ("malformed", "(session", ""),
            ("unknown-reference", session.replace("(place R1 ", "(place UNKNOWN "),
             "skipped"),
            ("moved-part", session.replace("(place R1 200000", "(place R1 210000"),
             "footprint placement"),
        ]
        for name, contents, message in refused:
            path = root / f"{name}.ses"
            path.write_text(contents, encoding="utf-8")
            output = root / f"{name}.kicad_pcb"
            data = (await client.call_tool("import_routing_session", {
                "path": board, "session_file": str(path), "output_file": str(output),
            })).data
            assert not data["ok"], data
            assert message in data["error"], data
            assert not output.exists(), output
            assert not output.with_suffix(".kicad-flow.json").exists()
            assert Path(board).read_bytes() == before
            assert (await call("unrouted_connections", path=board))["count"] == 1

        routed = str(root / "pair-routed.kicad_pcb")
        result = await call("import_routing_session", path=board,
                            session_file=str(fixture), output_file=routed)
        assert result["unrouted_count"] == 0 and result["drc_errors"] == 0, result
        assert Path(board).read_bytes() == before
        assert Path(routed).with_suffix(".kicad-flow.json").read_bytes() == metadata
        source_graphics = (await call("list_graphics", path=board))["graphics"]
        routed_graphics = (await call("list_graphics", path=routed))["graphics"]
        assert routed_graphics == source_graphics
        poses = (await call("list_footprints", path=routed))["footprints"]
        assert {(p["ref"], p["x"], p["y"], p["side"]) for p in poses} == {
            ("R1", 20.0, 20.0, "F"), ("R2", 35.0, 20.0, "B"),
        }, poses
        for side in ("top", "bottom"):
            await call("render_board_layout", path=routed, side=side,
                       output_file=str(root / f"{side}.png"))
        print(json.dumps({"ok": True, "directory": str(root),
                          "refused_imports": len(refused), **result}))


if __name__ == "__main__":
    asyncio.run(main())
