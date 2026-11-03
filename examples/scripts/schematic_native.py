"""Check native schematic transforms, CRUD, connectivity and observation replies."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Exercise unchanged primitives through MCP without automatic saves."""
    directory = Path(mkdtemp(prefix="native-", dir="out/schematic-ipc")).resolve()
    path = str(directory / "transforms.kicad_sch")
    async with Client(mcp) as client:

        async def call(tool: str, **values: Any) -> dict[str, Any]:
            data: dict[str, Any] = (await client.call_tool(tool, values)).data
            assert data.get("ok"), (tool, data)
            return data

        await call("new_sheet", path=path)
        placed = []
        for row, mirror in enumerate(("", "x", "y")):
            batch = [
                {
                    "lib_id": "Device:R",
                    "ref": f"R{row * 4 + column + 1}",
                    "x": 40.64 + column * 55.88,
                    "y": 40.64 + row * 45.72,
                    "rotation": angle,
                    "mirror": mirror,
                }
                for column, angle in enumerate((0, 90, 180, 270))
            ]
            placed.extend(
                (await call("add_components", path=path, parts=batch))["parts"]
            )
        for part in placed:
            actual = await call("get_component", path=path, ref=part["ref"])
            assert actual["pins"] == part["pins"], (actual, part)
            wires, labels = [], []
            for pin in actual["pins"]:
                angle = math.radians(pin["orientation"])
                x = round(pin["x"] - 10.16 * math.cos(angle), 3)
                y = round(pin["y"] + 10.16 * math.sin(angle), 3)
                wires.append({"x1": pin["x"], "y1": pin["y"], "x2": x, "y2": y})
                labels.append(
                    {
                        "x": x,
                        "y": y,
                        "text": part["ref"] + "_" + pin["number"],
                        "kind": "global",
                    }
                )
            await call("add_wires", path=path, wires=wires)
            await call("add_labels", path=path, labels=labels)
        nets = (await call("list_nets", path=path))["nets"]
        assert len(nets) == 24 and all(len(n["pins"]) == 1 for n in nets), nets
        for net in nets:
            assert net["name"] == net["pins"][0]["ref"] + "_" + net["pins"][0]["pin"], (
                net
            )
        checked = await call("check_sheet", path=path)
        # Each deliberately distinct test net has one pin. All pin/label
        # endpoints must connect; only the expected single-pin notices remain.
        assert checked["errors"] == 0, checked
        assert checked["kind_counts"] == {"isolated_pin_label": 24}, checked
        # Native KiCad discards a free junction. Refuse it before applying a
        # batch, then verify that an actual crossing junction survives edits.
        refused = (
            await client.call_tool(
                "add_junctions", {"path": path, "points": [{"x": 25.4, "y": 160.02}]}
            )
        ).data
        assert not refused["ok"] and refused["applied_count"] == 0, refused
        await call(
            "add_wires",
            path=path,
            wires=[
                {"x1": 15.24, "y1": 160.02, "x2": 35.56, "y2": 160.02},
                {"x1": 25.4, "y1": 154.94, "x2": 25.4, "y2": 165.1},
            ],
        )
        await call("add_junctions", path=path, points=[{"x": 25.4, "y": 160.02}])
        await call(
            "set_fields",
            path=path,
            fields=[{"ref": "R1", "name": "MPN", "value": "TEST"}],
        )
        note = (
            await call(
                "add_texts",
                path=path,
                notes=[
                    {
                        "x": 25.4,
                        "y": 20.32,
                        "text": "NATIVE SCHEMATIC IPC",
                        "bold": True,
                    }
                ],
            )
        )["notes"][0]
        updated = (
            await call(
                "update_texts",
                path=path,
                updates=[{"uuid": note["uuid"], "size": 2.54}],
            )
        )["notes"][0]
        assert updated["justify"] == note["justify"]
        listed = (await call("list_texts", path=path))["notes"]
        assert listed == [updated], (listed, updated)
        removed = await call(
            "remove_junctions", path=path, points=[{"x": 25.4, "y": 160.02}]
        )
        assert removed["removed"] == 1, removed
        await call("remove_fields", path=path, fields=[{"ref": "R1", "name": "MPN"}])
        graphic = (
            await call(
                "add_schematic_graphics",
                path=path,
                graphics=[
                    {
                        "kind": "rectangle",
                        "x1": 15.24,
                        "y1": 10.16,
                        "x2": 264.16,
                        "y2": 157.48,
                        "stroke": "dash",
                    }
                ],
            )
        )["graphics"][0]
        moved = (
            await call(
                "move_schematic_graphics",
                path=path,
                moves=[{"uuid": graphic["uuid"], "dx": 1.27, "dy": 0}],
            )
        )["graphics"][0]
        assert (await call("list_schematic_graphics", path=path))["graphics"] == [moved]
        await call("remove_schematic_graphics", path=path, uuids=[graphic["uuid"]])
        await call(
            "add_components",
            path=path,
            parts=[
                {
                    "lib_id": "Amplifier_Operational:LM358",
                    "ref": "U1",
                    "unit": unit,
                    "x": 50.8 + unit * 50.8,
                    "y": 172.72,
                }
                for unit in (1, 2, 3)
            ],
        )
        for unit, expected in (
            (1, {"1", "2", "3"}),
            (2, {"5", "6", "7"}),
            (3, {"4", "8"}),
        ):
            part = await call("get_component", path=path, ref="U1", unit=unit)
            assert {p["number"] for p in part["pins"]} == expected, part
        await call("remove_components", path=path, refs=["U1"], unit=1)
        await call("remove_components", path=path, refs=["U1"], unit=2)
        await call("remove_components", path=path, refs=["U1"], unit=3)
        # Asymmetric connectors expose reflections a two-pin vertical resistor
        # cannot distinguish. Verify their pins against the library and nets.
        pose_sheet = str(directory / "poses.kicad_sch")
        await call("new_sheet", path=pose_sheet)
        definition = await call(
            "symbol_pins", lib_id="Connector_Generic:Conn_02x02_Odd_Even"
        )
        for row, mirror in enumerate(("", "x", "y")):
            requests = [
                {
                    "lib_id": definition["lib_id"],
                    "ref": f"J{row * 4 + i + 1}",
                    "x": 50.8 + i * 40.64,
                    "y": 50.8 + row * 38.1,
                    "rotation": angle,
                    "mirror": mirror,
                }
                for i, angle in enumerate((0, 90, 180, 270))
            ]
            parts = (await call("add_components", path=pose_sheet, parts=requests))[
                "parts"
            ]
            for request, part in zip(requests, parts, strict=True):
                for pin in part["pins"]:
                    local = next(
                        p for p in definition["pins"] if p["number"] == pin["number"]
                    )
                    x, y = local["x"], -local["y"]
                    x, y = ((x, y), (y, -x), (-x, -y), (-y, x))[
                        request["rotation"] // 90
                    ]
                    if mirror == "x":
                        y = -y
                    if mirror == "y":
                        x = -x
                    assert abs(pin["x"] - request["x"] - x) < 0.001, (request, pin)
                    assert abs(pin["y"] - request["y"] - y) < 0.001, (request, pin)
                await call(
                    "add_labels",
                    path=pose_sheet,
                    labels=[
                        {
                            "x": p["x"],
                            "y": p["y"],
                            "text": part["ref"] + "_" + p["number"],
                            "kind": "global",
                        }
                        for p in part["pins"]
                    ],
                )
        observed = (await call("list_nets", path=pose_sheet))["nets"]
        assert len(observed) == 48 and all(len(n["pins"]) == 1 for n in observed), (
            observed
        )
        for net in observed:
            assert net["name"] == net["pins"][0]["ref"] + "_" + net["pins"][0]["pin"], (
                net
            )
        label_sheet = str(directory / "labels.kicad_sch")
        await call("new_sheet", path=label_sheet)
        for row, kind in enumerate(("local", "global", "hierarchical")):
            for justify in ("left", "right"):
                requests = [
                    {
                        "x": 50.8 + 25.4 * i,
                        "y": 25.4 + row * 25.4 + (12.7 if justify == "right" else 0),
                        "text": f"{kind}{i}",
                        "kind": kind,
                        "rotation": angle,
                        "justify": justify,
                    }
                    for i, angle in enumerate((0, 90, 180, 270))
                ]
                made = (await call("add_labels", path=label_sheet, labels=requests))[
                    "labels"
                ]
                listed = {
                    label["uuid"]: label
                    for label in (await call("list_labels", path=label_sheet))["labels"]
                }
                assert all(label == listed[label["uuid"]] for label in made), (
                    made,
                    listed,
                )
        assert not Path(path).exists(), "Native edits should still be unsaved"
        await call("save_sheet", path=path)
        rendered = await call(
            "render_schematic",
            path=path,
            output_dir=str(directory),
            dpi=150,
            save=False,
        )
        print(
            {
                "ok": True,
                "directory": str(directory),
                "images": rendered["images"],
                "nets": len(nets),
            },
            flush=True,
        )


if __name__ == "__main__":
    os.environ.setdefault("KICAD_FLOW_MONITOR", "0")
    os.environ["KICAD_FLOW_AUTOSAVE"] = "0"
    Path("out/schematic-ipc").mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
