"""Exercise local routing eyes and focused schematic repair through MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


def encoded_size(data: Any) -> int:
    """Measure compact JSON bytes for repeatable response comparisons."""
    return len(json.dumps(data, separators=(",", ":")).encode())


async def main() -> None:
    """Read geometry/rules, repair a conflict, and check scope-safe deltas."""
    root = Path("out/local-observations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sch, pcb = str(root / "local.kicad_sch"), str(root / "local.kicad_pcb")
    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        await call("new_sheet", path=sch)
        await call("add_components", path=sch, parts=[
            {"lib_id": "Device:R", "ref": f"R{i}", "x": 25.4 * i, "y": 50.8}
            for i in range(1, 7)
        ])
        await call("add_labels", path=sch, labels=[
            {"text": "COLLISION", "x": 25.4, "y": 50.8}])
        before = hashlib.sha256(Path(sch).read_bytes()).hexdigest()
        full = await call("inspect_schematic_scene", path=sch)
        focused = await call("inspect_schematic_scene", path=sch, detail="conflicts")
        compact = await call("inspect_schematic_scene", path=sch, detail="compact")
        assert focused["finding_count"] and not focused["obstacles_complete"]
        assert focused["returned_object_count"] < full["object_count"]
        assert compact["object_count"] == full["object_count"]
        assert hashlib.sha256(Path(sch).read_bytes()).hexdigest() == before
        refused_scene = (await client.call_tool("inspect_schematic_scene",
                                               {"path": sch, "max_bytes": 1000})).data
        assert not refused_scene["ok"] and "max_bytes" in refused_scene["error"]
        await call("move_components", path=sch,
                   moves=[{"ref": "R1", "x": 25.4, "y": 101.6}])
        cleared = await call("inspect_schematic_scene", path=sch,
                             detail="conflicts", since=focused["revision"])
        assert cleared["mode"] == "delta" and not cleared["findings"]
        assert cleared["removed_findings"] and cleared["removed"]
        reset = await call("inspect_schematic_scene", path=sch,
                           detail="compact", since=focused["revision"])
        assert reset["mode"] == "full" and reset["reset"]
        assert (await call("check_sheet_layout", path=sch))["clean"]
        await call("render_schematic", path=sch, output_dir=str(root / "renders"))

        await call("new_board", path=pcb)
        await call("add_graphics", path=pcb, graphics=[
            {"kind": "rectangle", "layer": "Edge.Cuts",
             "x1": 10, "y1": 10, "x2": 60, "y2": 60}])
        placed = await call("place_footprints", path=pcb, footprints=[
            {"fp_id": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
             "ref": "J1", "x": 25, "y": 25, "rotation": 45},
            {"fp_id": "Resistor_SMD:R_0603_1608Metric", "ref": "R1",
             "x": 45, "y": 45, "rotation": 90, "side": "B"},
        ])
        await call("set_pad_nets", path=pcb, pads=[
            {"ref": ref, "pad": "1", "net": "SIGNAL"} for ref in ("J1", "R1")])
        await call("set_net_classes", path=pcb,
                   classes=[{"name": "Signal", "track_width": 0.3}])
        await call("assign_net_classes", path=pcb,
                   assignments=[{"net": "SIGNAL", "net_class": "Signal"}])
        files = (Path(pcb), Path(pcb).with_suffix(".kicad_pro"))
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        policy = await call("list_board_nets", path=pcb, net="SIGNAL",
                            include_rules=True)
        rule = policy["routing_policy"]["nets"][0]
        assert rule["dimensions"]["track_width"] == 0.3, rule
        assert rule["sources"]["track_width"] == "Signal", rule
        assert rule["sources"]["clearance"] == "Default", rule
        assert policy["unassigned_pad_count"] == 2
        assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        await call("set_net_classes", path=pcb,
                   classes=[{"name": "Signal", "track_width": 0.4}])
        updated = await call("list_board_nets", path=pcb, net="SIGNAL",
                             include_rules=True)
        assert updated["routing_policy"]["nets"][0]["dimensions"]["track_width"] == 0.4

        scope = {"path": pcb, "x1": 10, "y1": 10, "x2": 60, "y2": 60,
                 "layers": ["B.Cu"]}
        first = await call("query_board_region", **scope)
        assert first["graphics"], "Edge.Cuts omitted under copper filter"
        rear = next(p for p in first["pads"] if p["ref"] == "R1")
        assert rear["shape"] == "roundrect" and rear["rotation"] == 90
        assert rear["copper_layers"] == ["B.Cu"]
        assert rear["geometry_supported"] and rear["bounds"]
        same = await call("query_board_region", **scope, since=first["revision"])
        assert same["mode"] == "delta" and not same["pads"]
        assert not any(same["removed"].values())
        too_small = (await client.call_tool("query_board_region",
                                           {**scope, "max_objects": 1})).data
        assert not too_small["ok"] and "max_objects" in too_small["error"]
        refused_board = (await client.call_tool("query_board_region",
                                               {**scope, "max_bytes": 1000})).data
        assert not refused_board["ok"] and "max_bytes" in refused_board["error"]
        reset_board = await call("query_board_region", **{**scope, "layers": ["F.Cu"]},
                                 since=first["revision"])
        assert reset_board["reset"] and reset_board["mode"] == "full"
        # A square pad rotated 45 degrees must be selected at its extended tip.
        jpad = placed["footprints"][0]["pads"][0]
        dx = jpad["width"] * 0.6
        tip = await call("query_board_region", path=pcb,
                         x1=jpad["x"] + dx, y1=jpad["y"] - 0.01,
                         x2=jpad["x"] + dx + 0.01, y2=jpad["y"] + 0.01,
                         layers=["F.Cu"])
        assert any(p["ref"] == "J1" and p["number"] == "1" for p in tip["pads"])
        await call("move_footprints", path=pcb,
                   moves=[{"ref": "R1", "x": 80, "y": 80}])
        delta = await call("query_board_region", **scope, since=first["revision"])
        assert rear["id"] in delta["removed"]["pads"]
        await call("move_footprints", path=pcb,
                   moves=[{"ref": "R1", "x": 45, "y": 45}])
        await call("add_zones", path=pcb, zones=[{
            "points": [[12, 12], [58, 12], [58, 58], [12, 58]],
            "layer": "B.Cu", "net": "SIGNAL"}])
        await call("refill_zones", path=pcb)
        boundaries = await call("query_board_region", **scope)
        filled = await call("query_board_region", **scope, include_fills=True,
                            since=boundaries["revision"])
        assert filled["reset"] and filled["mode"] == "full"
        assert "fill_contours" not in boundaries["zones"][0]
        contours = filled["zones"][0]["fill_contours"]
        assert contours and all(c["layer"] == "B.Cu" for c in contours)
        assert all(len(c["points"]) >= 3 for c in contours)
        await call("render_board_layout", path=pcb, side="bottom",
                   output_file=str(root / "renders/board-bottom.png"))
        print(f"Schematic full/compact/conflicts: {encoded_size(full)}/"
              f"{encoded_size(compact)}/{encoded_size(focused)} bytes")
        print(f"Board region/unchanged delta: {encoded_size(first)}/"
              f"{encoded_size(same)} bytes")
        print("PASS: read-only rule inheritance, back-side geometry, rotated pad "
              "bounds, stored zone fills, response limits, scoped deltas, "
              "conflict repair")


if __name__ == "__main__":
    asyncio.run(main())
