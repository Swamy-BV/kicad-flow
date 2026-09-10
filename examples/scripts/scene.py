"""Exercise image-free schematic observations and deltas through MCP only.

Run from the repository root. Outputs live under out/scene so existing design
fixtures are not overwritten. The drawing intentionally contains overlaps.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("out/scene")


async def build(client: Client[Any]) -> None:
    """Verify identities, filtering, changes, budgets, rollback and reloading."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = str((OUT / "scene.kicad_sch").resolve())
    calls = 0

    async def call(tool: str, **args: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        result = await client.call_tool(tool, {"path": path, **args})
        data: dict[str, Any] = result.data
        assert data.get("ok"), (tool, data)
        return data

    async def refused(tool: str, **args: Any) -> None:
        result = await client.call_tool(tool, {"path": path, **args})
        assert result.data.get("ok") is False, result.data

    started = time.perf_counter()
    await call("new_sheet", title="Scene contract example")
    placed = await call("add_components", parts=[
        {"lib_id": "Device:R", "ref": "R1", "x": 50.8, "y": 50.8},
        {"lib_id": "Device:R", "ref": "R2", "x": 50.8, "y": 50.8},
        {"lib_id": "Device:C", "ref": "C1", "x": 101.6, "y": 50.8,
         "rotation": 90, "mirror": "x"},
        {"lib_id": "Amplifier_Operational:LM358", "ref": "U1",
         "unit": 1, "x": 152.4, "y": 50.8},
        {"lib_id": "Amplifier_Operational:LM358", "ref": "U1",
         "unit": 2, "x": 203.2, "y": 50.8},
    ])
    await call("add_sheets", sheets=[{
        "name": "Child", "filename": "not-created.kicad_sch",
        "x": 25.4, "y": 101.6,
        "ports": [{"name": "SIGNAL", "kind": "input"}],
    }])
    await call("add_labels", labels=[
        {"x": 25.4, "y": 76.2, "text": "LOCAL"},
        {"x": 76.2, "y": 76.2, "text": "GLOBAL", "kind": "global"},
        {"x": 127, "y": 76.2, "text": "PORT", "kind": "hierarchical"},
    ])
    await call("add_texts", notes=[{
        "x": 25.4, "y": 152.4, "text": "<scene & text>"}])
    p = placed["parts"][0]["pins"][0]
    await call("add_wires", wires=[{
        "x1": p["x"], "y1": p["y"], "x2": 25.4, "y2": p["y"]}])
    await call("add_junctions", points=[{"x": 25.4, "y": p["y"]}])
    await call("add_no_connects", points=[{"x": 228.6, "y": 50.8}])

    before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    first = await call("inspect_schematic_scene")
    assert before == hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert first["mode"] == "full" and not first["reset"]
    assert not first["connectivity_verified"] and first["geometry_only"]
    objects = {obj["id"]: obj for obj in first["objects"]}
    assert len(objects) == first["object_count"]
    assert {"symbol", "pin", "field", "wire", "label", "text", "sheet",
            "sheet_pin", "junction", "no_connect"} <= {
                obj["kind"] for obj in objects.values()}
    assert any(f["kind"] == "body_overlap" for f in first["findings"])
    # Native pin readback must agree for mirrored, rotated and multi-unit parts.
    for part in placed["parts"]:
        pins = [obj for obj in objects.values() if obj["kind"] == "pin"
                and obj["properties"]["ref"] == part["ref"]
                and obj["properties"]["unit"] == part["unit"]]
        assert {(o["properties"]["number"], o["x"], o["y"]) for o in pins} == {
            (pin["number"], pin["x"], pin["y"]) for pin in part["pins"]}

    same = await call("inspect_schematic_scene", since=first["revision"])
    assert same["mode"] == "delta" and same["objects"] == []
    assert same["removed"] == same["findings"] == same["removed_findings"] == []
    region = {"x1": 45.72, "y1": 38.1, "x2": 60.96, "y2": 63.5}
    local = await call("inspect_schematic_scene", **region)
    assert local["object_count"] < first["object_count"]
    # A wire crossing the selected region is retained whole.
    assert any(o["kind"] == "wire" and o["points"][1]["x"] == 25.4
               for o in local["objects"])
    reset = await call("inspect_schematic_scene", since=local["revision"])
    assert reset["reset"] and reset["mode"] == "full"
    empty = await call("inspect_schematic_scene", x1=270, y1=180, x2=280, y2=190)
    assert empty["object_count"] == 0
    await refused("inspect_schematic_scene", x1=0)
    await refused("inspect_schematic_scene", x1=10, y1=0, x2=0, y2=10)
    await refused("inspect_schematic_scene", max_objects=1)
    await refused("inspect_schematic_scene", max_objects=0)

    await call("move_components", moves=[{"ref": "R2", "x": 76.2, "y": 50.8}])
    delta = await call("inspect_schematic_scene", since=first["revision"])
    assert delta["mode"] == "delta" and delta["removed_findings"]
    changed = {o["id"] for o in delta["objects"]}
    assert changed <= objects.keys()  # Moving did not invent new object IDs.
    for obj in delta["objects"]:
        assert obj["properties"].get("ref", "R2") == "R2"
    for identity in delta["removed"]:
        objects.pop(identity)
    objects.update({o["id"]: o for o in delta["objects"]})
    current = await call("inspect_schematic_scene")
    assert objects == {o["id"]: o for o in current["objects"]}
    exited = await call("inspect_schematic_scene", since=local["revision"], **region)
    assert exited["removed"]

    # Failed atomic writes cannot change the scene or its content revision.
    await refused("move_components", moves=[
        {"ref": "R1", "x": 88.9, "y": 88.9},
        {"ref": "MISSING", "x": 88.9, "y": 88.9},
    ])
    rolled = await call("inspect_schematic_scene", since=current["revision"])
    assert rolled["revision"] == current["revision"] and not rolled["objects"]
    await call("remove_components", refs=["R2"])
    removed = await call("inspect_schematic_scene", since=current["revision"])
    assert removed["removed"] and not removed["objects"]
    # Eviction must reset instead of returning an incomplete delta.
    for index in range(66):
        await call("move_components", moves=[{
            "ref": "R1", "x": 50.8 + index * 1.27, "y": 50.8}])
        await call("inspect_schematic_scene")
    expired = await call("inspect_schematic_scene", since=first["revision"])
    assert expired["mode"] == "full" and expired["reset"]
    assert (await call("inspect_schematic_scene", since="unknown"))["reset"]
    # Child references do not require loading the child. Remove the intentionally
    # missing child before native rendering and root-netlist verification.
    await call("remove_sheets", names=["Child"])
    await call("save_sheet")
    final = await call("inspect_schematic_scene")
    netlist = await call("list_nets")
    await call("render_schematic", output_dir=str(OUT.resolve()))
    (OUT / "scene.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    env = dict(os.environ, KICAD_FLOW_MONITOR="0")
    fresh = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "--inspect", path], capture_output=True,
                           text=True, check=True, timeout=30, env=env)
    reloaded = json.loads(fresh.stdout)
    assert reloaded["objects"] == final["objects"]
    assert reloaded["revision"] == final["revision"]
    print(f"scene: {calls} successful MCP calls; {final['object_count']} objects; "
          f"{len(netlist['nets'])} native nets; "
          f"{time.perf_counter() - started:.2f}s")
    print("PASS: read-only snapshots, pin geometry, stable IDs, full/delta, "
          "region exits, rollback, removal, cursor reset and fresh-process reload")


async def main() -> None:
    """Run the example, or inspect its saved sheet from a fresh process."""
    async with Client(mcp) as client:
        if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
            result = await client.call_tool("inspect_schematic_scene",
                                            {"path": sys.argv[2]})
            print(json.dumps(result.data))
        else:
            await build(client)


if __name__ == "__main__":
    asyncio.run(main())
