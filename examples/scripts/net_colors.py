"""Exercise net-class colors and persistent assignments through Client(mcp)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("out/net-colors").resolve()


async def main() -> None:
    """Build a color sample, verify edits and render the schematic."""
    OUT.mkdir(parents=True, exist_ok=True)
    base = OUT / "colors"
    sheet = str(base.with_suffix(".kicad_sch"))
    board = str(base.with_suffix(".kicad_pcb"))
    project = base.with_suffix(".kicad_pro")
    # Only remove this example's previous sidecar, so repeat runs start alike.
    project.unlink(missing_ok=True)
    async with Client(mcp) as client:
        async def call(name: str, **args: Any) -> dict[str, Any]:
            reply = (await client.call_tool(name, args)).data
            assert reply["ok"], reply
            return dict(reply)

        await call("new_sheet", path=sheet, title="Net colors")
        parts = (await call("add_components", path=sheet, parts=[
            {"lib_id": "Device:R", "ref": f"R{i}", "value": "1k",
             "x": 101.6, "y": y, "rotation": 90}
            for i, y in enumerate((50.8, 76.2, 101.6), 1)
        ]))["parts"]
        await call("add_wires", path=sheet, wires=[
            {"x1": 50.8, "y1": pin["y"], "x2": pin["x"], "y2": pin["y"]}
            for part in parts for pin in [min(part["pins"], key=lambda p: p["x"])]
        ])
        await call("add_labels", path=sheet, labels=[
            {"x": 50.8, "y": y, "text": net, "kind": "global"}
            for y, net in ((50.8, "VBUS"), (76.2, "DATA"), (101.6, "VBUS_A"))
        ])
        await call("add_no_connects", path=sheet, points=[
            {"x": pin["x"], "y": pin["y"]}
            for part in parts for pin in part["pins"] if pin["number"] == "2"
        ])
        await call("set_fields", path=sheet, fields=[
            {"ref": f"R{i}", "name": "Footprint",
             "value": "Resistor_SMD:R_0603_1608Metric"}
            for i in range(1, 4)
        ])
        await call("save_sheet", path=sheet)
        before_nets = (await call("list_nets", path=sheet))["nets"]
        assert {"VBUS", "DATA", "VBUS_A"} <= {n["name"] for n in before_nets}
        await call("new_board", path=board)
        await call("update_board_from_schematic", schematic_path=sheet,
                   board_path=board, placements=[
                       {"ref": f"R{i}", "x": 10.0 * i, "y": 10.0}
                       for i in range(1, 4)
                   ])
        await call("save_board", path=board)
        await call("set_net_classes", path=board, classes=[
            {"name": "Power", "track_width": 0.8,
             "pcb_color": "#e53935", "schematic_color": "#E53935"},
            {"name": "Data", "pcb_color": "#1565C0",
             "schematic_color": "#1565C0"},
        ])
        patterns = [{"pattern": "^VBUS$", "net_class": "Power"},
                    {"pattern": "^DATA$", "net_class": "Data"}]
        await call("set_net_class_patterns", path=board, patterns=patterns)
        read = await call("list_net_class_patterns", path=board)
        assert read["patterns"] == patterns

        changed = (await call("set_net_classes", path=board, classes=[
            {"name": "Power", "schematic_color": None, "pcb_color": "#FF880080"},
        ]))["classes"][0]
        assert changed["schematic_color"] == "#E53935"
        assert changed["track_width"] == 0.8 and changed["pcb_color"] == "#FF880080"
        cleared = (await call("set_net_classes", path=board, classes=[
            {"name": "Power", "pcb_color": ""},
        ]))["classes"][0]
        assert cleared["pcb_color"] == "" and cleared["schematic_color"] == "#E53935"
        await call("set_net_classes", path=board, classes=[
            {"name": "Power", "pcb_color": "#E53935"},
        ])
        snapshot = project.read_bytes()
        bad = await client.call_tool("set_net_classes", {
            "path": board, "classes": [
                {"name": "Power", "pcb_color": "#000000"},
                {"name": "Data", "schematic_color": "not-a-color"},
            ],
        }, raise_on_error=False)
        assert bad.is_error and project.read_bytes() == snapshot
        bad_pattern = (await client.call_tool("set_net_class_patterns", {
            "path": board, "patterns": [patterns[0],
                                          {"pattern": ".*", "net_class": "Missing"}],
        })).data
        assert not bad_pattern["ok"] and project.read_bytes() == snapshot

        # These values are resolved by KiCad, not echoed from the JSON we wrote.
        policy = (await call("list_board_nets", path=board,
                             include_rules=True))["routing_policy"]["nets"]
        by_net = {item["net"]: item for item in policy}
        assert by_net["VBUS"]["class_colors"]["pcb_color"] == "#E53935"
        assert by_net["VBUS"]["dimensions"]["track_width"] == 0.8
        assert by_net["DATA"]["class_colors"]["schematic_color"] == "#1565C0"
        assert by_net["VBUS_A"]["effective_class"] == "Default"

        await call("set_net_class_patterns", path=board, patterns=[])
        assert not (await call("list_net_class_patterns", path=board))["patterns"]
        await call("set_net_class_patterns", path=board, patterns=patterns)
        assert (await call("list_nets", path=sheet))["nets"] == before_nets
        await call("render_schematic", path=sheet, output_dir=str(OUT / "colored"))
        await call("render_schematic", path=sheet, output_dir=str(OUT / "mono"),
                   black_and_white=True)
    print("PASS: color set/preserve/clear, alpha, atomic refusals and patterns.")
    print("KiCad-resolved colors, anchored matching and unchanged schematic nets.")
    print(f"Inspect color and monochrome renders under {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
