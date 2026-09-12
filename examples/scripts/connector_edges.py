"""Measure connector edge placement through MCP, with text kept separate."""

from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Align an explicit fabrication edge and check scoped edge exemptions."""
    root = Path("out/connector-edges").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "connector.kicad_pcb")
    connector = "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12"
    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        definition = await call("footprint_pads", fp_id=connector)
        assert definition["fabrication_status"] == "available"
        fab_y = max(p["y"] for p in definition["fabrication_polygon"])
        courtyard_y = max(p["y"] for p in definition["courtyard_polygon"])
        gap = courtyard_y - fab_y
        assert gap > 0
        await call("new_board", path=path)
        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 10, "y1": 10, "x2": 40, "y2": 34}])
        await call("place_footprints", path=path, footprints=[{
            "fp_id": connector, "ref": "J1", "x": 25, "y": 34 - courtyard_y}])
        initial = await call("get_footprint", path=path, ref="J1")
        assert abs(34 - max(p["y"] for p in initial["fabrication_polygon"])
                   - gap) < 1e-6
        assert (await call("measure_placement", path=path))["valid"]
        await call("render_board_layout", path=path,
                   output_file=str(root / "courtyard-inset.png"))
        await call("render_board", path=path,
                   output_file=str(root / "courtyard-inset-3d.png"),
                   width=800, height=800)

        # The caller explicitly chooses the maximum-Y fabrication edge here.
        # The server never guesses which face is the connector's mating face.
        await call("move_footprints", path=path,
                   moves=[{"ref": "J1", "x": 25, "y": 34 - fab_y}])
        flush = await call("get_footprint", path=path, ref="J1")
        assert abs(max(p["y"] for p in flush["fabrication_polygon"]) - 34) < 1e-6
        strict = await call("measure_placement", path=path)
        assert not strict["valid"] and strict["edge_violations"][0]["ref"] == "J1"
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        accepted = await call("measure_placement", path=path, edge_exempt_refs=["J1"])
        assert accepted["valid"] and not accepted["edge_violations"]
        assert accepted["edge_exceptions"][0]["outside"]
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before
        assert not (await call("measure_placement", path=path))["valid"]
        unknown = (await client.call_tool("measure_placement", {
            "path": path, "edge_exempt_refs": ["TYPO"]})).data
        assert not unknown["ok"]

        # Text in front of a connector must never enlarge either envelope.
        await call("move_footprint_fields", path=path, moves=[
            {"ref": "J1", "name": "Reference", "dx": 0, "dy": 12},
            {"ref": "J1", "name": "Value", "dx": 0, "dy": 16}])
        await call("set_footprint_fields", path=path, fields=[{
            "ref": "J1", "name": "Value", "value": "LONG LABEL IN FRONT OF CONNECTOR"}])
        labelled = await call("get_footprint", path=path, ref="J1")
        for key in ("fabrication_polygon", "courtyard_polygon"):
            assert labelled[key] == flush[key], key
        await call("move_footprint_fields", path=path, moves=[
            {"ref": "J1", "name": "Reference", "dx": 0, "dy": -6},
            {"ref": "J1", "name": "Value", "dx": 0, "dy": 0, "hide": True}])
        await call("render_board_layout", path=path,
                   output_file=str(root / "fabrication-flush.png"))
        await call("render_board", path=path,
                   output_file=str(root / "fabrication-flush-3d.png"),
                   width=800, height=800)
        await call("set_pad_nets", path=path, pads=[{
            "ref": "J1", "pad": definition["pads"][0]["number"], "net": "GND"}])
        await call("set_board_limits", path=path, min_copper_edge_clearance=0.25)
        await call("measure_placement", path=path, edge_exempt_refs=["J1"])
        copper = await call("check_board", path=path, tracks=[{
            "x1": 20, "y1": 33.95, "x2": 30, "y2": 33.95,
            "width": 0.4, "layer": "F.Cu", "net": "GND"}])
        assert copper["kind_counts"].get("copper_edge_clearance", 0), copper

        # Envelopes must rotate and mirror with the actual footprint geometry.
        for side in ("F", "B"):
            for rotation in (0, 90, 180, 270, 37):
                await call("flip_footprints", path=path,
                           flips=[{"ref": "J1", "side": side}])
                await call("rotate_footprints", path=path,
                           turns=[{"ref": "J1", "rotation": rotation}])
                pose = await call("get_footprint", path=path, ref="J1")
                angle = math.radians(rotation)
                expected = set()
                for point in definition["fabrication_polygon"]:
                    x, y = point["x"] * (1 if side == "F" else -1), point["y"]
                    expected.add((round(pose["x"] + x * math.cos(angle)
                                        + y * math.sin(angle), 3),
                                  round(pose["y"] - x * math.sin(angle)
                                        + y * math.cos(angle), 3)))
                actual = {(p["x"], p["y"]) for p in pose["fabrication_polygon"]}
                assert expected == actual, (side, rotation, expected, actual)
                shift = 34 - max(p["y"] for p in pose["fabrication_polygon"])
                await call("move_footprints", path=path,
                           moves=[{"ref": "J1", "x": 25, "y": pose["y"] + shift}])
                aligned = await call("get_footprint", path=path, ref="J1")
                assert abs(max(p["y"] for p in aligned["fabrication_polygon"])
                           - 34) < 2e-6

        await call("place_footprints", path=path, footprints=[{
            "fp_id": "Resistor_SMD:R_0603_1608Metric", "ref": "R1", "x": 50, "y": 20}])
        other = await call("measure_placement", path=path, edge_exempt_refs=["J1"])
        assert not other["valid"] and any(
            item["ref"] == "R1" for item in other["edge_violations"])
        overlap = await call("measure_placement", path=path,
                             edge_exempt_refs=["J1", "R1"], placements=[{
                                 "ref": "R1", "x": aligned["x"], "y": aligned["y"],
                                 "side": aligned["side"]}])
        assert not overlap["valid"] and overlap["overlap_count"]
        await call("remove_footprints", path=path, refs=["R1"])
        await call("flip_footprints", path=path, flips=[{"ref": "J1", "side": "F"}])
        await call("rotate_footprints", path=path, turns=[{"ref": "J1", "rotation": 0}])
        await call("move_footprints", path=path,
                   moves=[{"ref": "J1", "x": 25, "y": 34 - fab_y}])
        assert (await call("measure_placement", path=path,
                           edge_exempt_refs=["J1"]))["valid"]
        print(f"USB-C fabrication max-Y={fab_y}, courtyard max-Y={courtyard_y}; "
              f"courtyard-only placement leaves {gap:.3f} mm fabrication gap")
        print("PASS: text exclusion, explicit flush placement, rotation/back side, "
              "read-only exemptions, unknown refs, other edge/overlap "
              "and copper DRC checks")


if __name__ == "__main__":
    asyncio.run(main())
