"""Check native PCB editing through the unchanged MCP primitives."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Exercise both faces, rotations, copper, text, rollback and native saves."""
    directory = Path(mkdtemp(prefix="run-", dir="out/pcb-ipc")).resolve()
    schematic = str(directory / "native.kicad_sch")
    pcb = str(directory / "native.kicad_pcb")
    poses = [
        {
            "ref": f"{prefix}{i + 1}",
            "x": 25 + (i % 4) * 18,
            "y": 25 + (i // 4) * 20 + (45 if prefix == "J" else 0),
            "rotation": (i % 4) * 90,
            "side": "F" if i < 4 else "B",
        }
        for prefix in ("R", "J")
        for i in range(8)
    ]
    async with Client(mcp) as client:

        async def call(tool: str, **args: Any) -> dict[str, Any]:
            result: dict[str, Any] = (await client.call_tool(tool, args)).data
            assert result.get("ok"), (tool, result)
            return result

        mode = os.environ.get("KICAD_FLOW_PCB_MODE")
        try:
            # Library-only observations must work with either editor mode and
            # must never create a project or try to attach a throwaway document.
            for selected in ("headless", "gui"):
                os.environ["KICAD_FLOW_PCB_MODE"] = selected
                definition = await call(
                    "footprint_pads",
                    project_dir=str(directory),
                    fp_id="Resistor_SMD:R_0603_1608Metric",
                )
                assert len(definition["pads"]) == 2
                found = await call(
                    "find_footprint",
                    project_dir=str(directory),
                    query="Resistor_SMD:R_0603_1608Metric",
                    limit=1,
                )
                assert found["footprints"]
            assert not list(directory.glob("*.kicad_*"))
            assert not list(directory.glob("_query*"))
        finally:
            if mode is None:
                os.environ.pop("KICAD_FLOW_PCB_MODE", None)
            else:
                os.environ["KICAD_FLOW_PCB_MODE"] = mode
        await call("new_sheet", path=schematic)
        for offset in range(0, len(poses), 5):
            batch = poses[offset : offset + 5]
            await call(
                "add_components",
                path=schematic,
                parts=[
                    {
                        "lib_id": "Device:R"
                        if p["ref"].startswith("R")
                        else "Connector_Generic:Conn_02x02_Odd_Even",
                        "ref": p["ref"],
                        "x": 25.4 + (offset + i) % 8 * 25.4,
                        "y": 50.8 + (offset + i) // 8 * 38.1,
                    }
                    for i, p in enumerate(batch)
                ],
            )
            await call(
                "set_fields",
                path=schematic,
                fields=[
                    {
                        "ref": p["ref"],
                        "name": "Footprint",
                        "value": "Resistor_SMD:R_0603_1608Metric"
                        if p["ref"].startswith("R")
                        else (
                            "Connector_PinHeader_2.54mm:PinHeader_2x02_P2.54mm_Vertical"
                        ),
                    }
                    for p in batch
                ],
            )
        components = (await call("list_components", path=schematic, with_pins=True))[
            "parts"
        ]
        labels = [
            {
                "x": pin["x"],
                "y": pin["y"],
                "text": "RAIL_A" if pin["number"] in ("1", "3") else "RAIL_B",
                "kind": "global",
            }
            for component in components
            for pin in component["pins"]
        ]
        for offset in range(0, len(labels), 5):
            await call("add_labels", path=schematic, labels=labels[offset : offset + 5])
        await call("save_sheet", path=schematic)
        for offset in range(0, len(poses), 5):
            await call(
                "update_board_from_schematic",
                schematic_path=schematic,
                board_path=pcb,
                placements=poses[offset : offset + 5],
            )
        await call(
            "add_graphics",
            path=pcb,
            graphics=[
                {
                    "kind": "rectangle",
                    "layer": "Edge.Cuts",
                    "x1": 10,
                    "y1": 10,
                    "x2": 100,
                    "y2": 110,
                },
                {
                    "kind": "circle",
                    "layer": "F.SilkS",
                    "x": 90,
                    "y": 15,
                    "radius": 2,
                    "fill": True,
                },
                {
                    "kind": "polygon",
                    "layer": "B.SilkS",
                    "points": [[85, 100], [90, 100], [87.5, 105]],
                    "fill": True,
                },
                {
                    "kind": "arc",
                    "layer": "B.SilkS",
                    "x1": 80,
                    "y1": 100,
                    "xm": 82.5,
                    "ym": 97.5,
                    "x2": 85,
                    "y2": 100,
                },
            ],
        )
        shapes = (await call("list_graphics", path=pcb))["graphics"]
        circle = next(s for s in shapes if s["kind"] == "circle")
        assert circle["fill"] and circle["points"] == [
            {"x": 90.0, "y": 15.0},
            {"x": 92.0, "y": 15.0},
        ]
        assert next(s for s in shapes if s["kind"] == "polygon")["fill"]
        parts = (await call("list_footprints", path=pcb, with_pads=True))["footprints"]
        assert len(parts) == len(poses)
        assert {pad["net"] for part in parts for pad in part["pads"]} == {
            "RAIL_A",
            "RAIL_B",
        }
        for part in parts:
            pose = next(p for p in poses if p["ref"] == part["ref"])
            assert part["side"] == pose["side"], (part, pose)
            assert part["rotation"] == pose["rotation"], (part, pose)
            assert len(part["pads"]) == (4 if part["ref"].startswith("J") else 2), part
            # Library pad origins are asymmetric on the headers. Verify the
            # reported native world coordinates at each explicit pose.
            if part["ref"].startswith("J"):
                import math

                rotation = math.radians(pose["rotation"])
                dx = 2.54 if pose["side"] == "F" else -2.54
                expected = (
                    pose["x"] + dx * math.cos(rotation),
                    pose["y"] - dx * math.sin(rotation),
                )
                pad = next(p for p in part["pads"] if p["number"] == "2")
                assert abs(pad["x"] - expected[0]) < 0.001, (part, expected)
                assert abs(pad["y"] - expected[1]) < 0.001, (part, expected)
        # Exercise edits as well as initial placement. Both flips must retain
        # the caller's logical angle, and exact quarter turns must not drift.
        for delta in (90, 0):
            for offset in range(0, len(poses), 5):
                await call(
                    "rotate_footprints",
                    path=pcb,
                    turns=[
                        {"ref": p["ref"], "rotation": (p["rotation"] + delta) % 360}
                        for p in poses[offset : offset + 5]
                    ],
                )
        for opposite in (True, False):
            for offset in range(0, len(poses), 5):
                await call(
                    "flip_footprints",
                    path=pcb,
                    flips=[
                        {
                            "ref": p["ref"],
                            "side": ("B" if p["side"] == "F" else "F")
                            if opposite
                            else p["side"],
                        }
                        for p in poses[offset : offset + 5]
                    ],
                )
        restored = (await call("list_footprints", path=pcb, with_pads=True))[
            "footprints"
        ]
        for part in restored:
            original = next(p for p in parts if p["ref"] == part["ref"])
            assert part["rotation"] == original["rotation"], (part, original)
            assert part["side"] == original["side"], (part, original)
            for pad in part["pads"]:
                old = next(p for p in original["pads"] if p["number"] == pad["number"])
                assert abs(pad["x"] - old["x"]) < 0.000001, (part, old)
                assert abs(pad["y"] - old["y"]) < 0.000001, (part, old)
        findings = (await call("check_board", path=pcb))["findings"]
        assert not [f for f in findings if f["kind"] == "lib_footprint_mismatch"], (
            findings
        )
        text = (
            await call(
                "add_board_texts",
                path=pcb,
                texts=[
                    {
                        "text": "DIRECT NATIVE IPC",
                        "x": 55,
                        "y": 15,
                        "layer": "F.SilkS",
                        "width": 1.4,
                        "height": 1.6,
                    }
                ],
            )
        )["texts"][0]
        await call(
            "update_board_texts",
            path=pcb,
            updates=[
                {
                    "uuid": text["uuid"],
                    "width": 1.0,
                    "height": 1.2,
                    "text": "IPC VERIFIED",
                }
            ],
        )
        before = await call("get_footprint", path=pcb, ref="R1")
        failed = (
            await client.call_tool(
                "move_footprints",
                {
                    "path": pcb,
                    "moves": [
                        {"ref": "R1", "x": 50, "y": 50},
                        {"ref": "MISSING", "x": 60, "y": 60},
                    ],
                },
            )
        ).data
        assert (
            not failed["ok"] and failed["index"] == 1 and failed["applied_count"] == 0
        )
        assert (await call("get_footprint", path=pcb, ref="R1")) == before
        await call(
            "add_tracks",
            path=pcb,
            tracks=[
                {
                    "x1": 15,
                    "y1": 100,
                    "x2": 35,
                    "y2": 100,
                    "layer": "F.Cu",
                    "width": 0.25,
                    "net": "TEST",
                }
            ],
        )
        await call(
            "add_vias",
            path=pcb,
            vias=[{"x": 35, "y": 100, "net": "TEST", "diameter": 0.6, "drill": 0.3}],
        )
        await call("save_board", path=pcb)
        await call(
            "set_board_limits",
            path=pcb,
            min_track_width=0.12,
            min_solder_mask_bridge=0.1,
        )
        await call(
            "set_net_classes",
            path=pcb,
            classes=[
                {
                    "name": "Rail",
                    "track_width": 0.4,
                    "clearance": 0.2,
                    "pcb_color": "#E04040",
                }
            ],
        )
        await call(
            "set_net_class_patterns",
            path=pcb,
            patterns=[{"pattern": "RAIL_*", "net_class": "Rail"}],
        )
        await call(
            "assign_net_classes",
            path=pcb,
            assignments=[{"net": "RAIL_A", "net_class": "Rail"}],
        )
        await call(
            "set_board_constraints",
            path=pcb,
            rules=[
                {
                    "name": "Rail width",
                    "condition": "A.NetName == 'RAIL_A'",
                    "constraints": [{"kind": "track_width", "min": 0.12}],
                }
            ],
        )
        reopened = await call("reload_board", path=pcb)
        assert (
            reopened["footprints"] == 16
            and reopened["tracks"] == 1
            and reopened["vias"] == 1
        )
        await call(
            "render_board_layout",
            path=pcb,
            output_file=str(directory / "top.png"),
            side="top",
        )
        await call(
            "render_board_layout",
            path=pcb,
            output_file=str(directory / "bottom.png"),
            side="bottom",
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "directory": str(directory),
                    "footprints": 16,
                    "sides": ["F", "B"],
                    "angles": [0, 90, 180, 270],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    os.environ.setdefault("KICAD_FLOW_MONITOR", "0")
    Path("out/pcb-ipc").mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
