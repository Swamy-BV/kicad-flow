"""Exercise explicit differential-pair observations through MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from _board_fixture import export_footprints, schematic_nets
from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Route a pair, introduce faults, and check the measured repair feedback."""
    root = Path("out/differential-pairs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "pair.kicad_pcb")
    pair = {
        "first": "P", "second": "N",
        "first_start": {"ref": "A1", "pad": "1", "layer": "F.Cu"},
        "first_end": {"ref": "A2", "pad": "1", "layer": "F.Cu"},
        "second_start": {"ref": "B1", "pad": "1", "layer": "F.Cu"},
        "second_end": {"ref": "B2", "pad": "1", "layer": "F.Cu"},
        "gap_min": 1.7, "gap_max": 1.9, "max_uncoupled": 0, "max_skew": 0.1,
    }

    def track(net: str, x1: float, y1: float, x2: float, y2: float,
              layer: str = "F.Cu") -> dict[str, Any]:
        return {"net": net, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "width": 0.2, "layer": layer}

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            result = (await client.call_tool(tool, args)).data
            assert result["ok"], result
            return result

        async def inspect() -> dict[str, Any]:
            result = await call("measure_routes", path=path, pairs=[pair])
            return result["pairs"][0]["inspection"]

        async def copper(tracks: list[dict[str, Any]]) -> None:
            await call("remove_copper", path=path, net="P")
            await call("remove_copper", path=path, net="N")
            await call("add_tracks", path=path, tracks=tracks)

        await call("new_board", path=path)
        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts", "x1": 0, "y1": 0,
            "x2": 40, "y2": 40}])
        await export_footprints(call, path, [{
            "fp_id": "TestPoint:TestPoint_Pad_D1.0mm", "ref": ref, "x": x, "y": y}
            for ref, x, y in (("A1", 10, 20), ("A2", 30, 20),
                              ("B1", 10, 22), ("B2", 30, 22))])
        await schematic_nets(call, path, [{
            "ref": ref, "pad": "1", "net": net}
            for ref, net in (("A1", "P"), ("A2", "P"), ("B1", "N"), ("B2", "N"))])
        straight = [track("P", 10, 20, 30, 20), track("N", 10, 22, 30, 22)]
        await copper(straight)
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        clean = await inspect()
        assert clean["within_requested_limits"] is True, clean
        assert clean["path_skew"] == 0 and clean["first"]["path_length"] == 20
        assert clean["spacing"][0]["lengths"]["within_gap"] == 20
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before
        malformed = await client.call_tool("measure_routes", {
            "path": path, "pairs": [{"first": "P", "second": "N", "gap_min": 1.0}],
        }, raise_on_error=False)
        assert malformed.is_error
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before
        assert (await call("check_board", path=path))["errors"] == 0
        await call("render_board_layout", path=path,
                   output_file=str(root / "parallel.png"))

        # Parallel 45-degree segments measure in their local coordinate frame.
        await copper([track(net, *a, *b)
                      for net, y in (("P", 20), ("N", 22))
                      for a, b in (((10, y), (14, y)),
                                   ((14, y), (18, y + 4)),
                                   ((18, y + 4), (26, y + 4)),
                                   ((26, y + 4), (30, y)))])
        diagonal = (await call("measure_routes", path=path, pairs=[{
            **pair, "gap_min": 1.0, "gap_max": 1.9, "max_uncoupled": 3.0,
        }]))["pairs"][0]["inspection"]
        assert diagonal["within_requested_limits"] is True, diagonal
        assert abs(diagonal["first"]["path_length"] - (12 + 8 * math.sqrt(2))) < 1e-6
        unpaired = diagonal["spacing"][0]["lengths"]["unpaired"]
        assert abs(unpaired - 2 * math.sqrt(2)) < 1e-6
        await call("render_board_layout", path=path,
                   output_file=str(root / "diagonal.png"))

        await copper(straight)
        await call("add_zones", path=path, zones=[{
            "net": "P", "layer": "F.Cu",
            "points": [[8, 18], [32, 18], [32, 21], [8, 21]],
        }])
        plane = await inspect()
        assert plane["first"]["status"] == "unresolved"
        assert plane["first"]["issues"] == [{"kind": "unsupported_zone_path"}]
        await call("remove_copper", path=path, net="P", tracks=False,
                   vias=False, zones=True)

        faulty = [straight[0], track("N", 10, 22, 10, 23),
                  track("N", 10, 23, 30, 23), track("N", 30, 23, 30, 22)]
        # Detached copper equalizes authored totals but not the terminal path.
        await copper([*faulty, track("P", 10, 30, 12, 30)])
        observed = await call("measure_routes", path=path, pairs=[pair])
        fault = observed["pairs"][0]["inspection"]
        assert observed["pairs"][0]["skew"] == 0
        assert fault["path_skew"] == 2
        assert fault["first"]["off_path_track_ids"]
        assert {v["kind"] for v in fault["violations"]} == {
            "gap_out_of_range", "uncoupled_limit", "path_skew_limit"}
        assert any(s["gap"] == 2.8 and s["uuid"] for s in fault["spacing"][0]["spans"])
        (root / "gap-and-skew.json").write_text(json.dumps(observed, indent=2))
        await call("render_board_layout", path=path,
                   output_file=str(root / "gap-and-skew.png"))
        await copper([straight[0], faulty[1]])
        broken = await inspect()
        assert broken["second"]["status"] == "unresolved"
        assert broken["path_skew"] is None

        await copper([*straight, track("P", 20, 20, 20, 18)])
        branch = await inspect()
        assert branch["first"]["status"] == "ambiguous"
        assert branch["within_requested_limits"] is None

        # A connected serpentine has several projected partners: do not choose.
        await copper([straight[0], track("N", 10, 22, 25, 22),
                      track("N", 25, 22, 25, 24), track("N", 25, 24, 15, 24),
                      track("N", 15, 24, 15, 26), track("N", 15, 26, 30, 26),
                      track("N", 30, 26, 30, 22)])
        ambiguous = await inspect()
        assert ambiguous["spacing"][0]["lengths"]["ambiguous"] > 0

        # Matched layer changes have an unknown barrel length without stackup.
        await copper([track(net, 10, y, 15, y) for net, y in (("P", 20), ("N", 22))]
                     + [track(net, 15, y, 25, y, "B.Cu")
                        for net, y in (("P", 20), ("N", 22))]
                     + [track(net, 25, y, 30, y) for net, y in (("P", 20), ("N", 22))])
        await call("add_vias", path=path, vias=[{
            "x": x, "y": y, "net": net, "diameter": 0.6, "drill": 0.3,
            "layers": ["F.Cu", "B.Cu"]}
            for net, y in (("P", 20), ("N", 22)) for x in (15, 25)])
        unknown = await inspect()
        assert unknown["first"]["path_length"] is None
        assert unknown["path_skew"] is None
        await call("set_stackup", path=path, layers=[
            {"name": "F.Cu", "kind": "copper", "thickness": 0.035},
            {"name": "dielectric 1", "kind": "core", "thickness": 1.53},
            {"name": "B.Cu", "kind": "copper", "thickness": 0.035}])
        layered = await inspect()
        assert abs(layered["first"]["via_length"] - 3.13) < 1e-6, layered
        assert abs(layered["first"]["path_length"] - 23.13) < 1e-6
        assert layered["path_skew"] == 0
        assert layered["within_requested_limits"] is None  # Via spacing unassessed.
        await call("render_board_layout", path=path, side="bottom",
                   output_file=str(root / "back-side.png"))
        refused = (await client.call_tool("measure_routes", {
            "path": path, "pairs": [pair], "max_bytes": 1000})).data
        assert not refused["ok"] and "max_bytes" in refused["error"]
        await copper(straight)
        assert (await inspect())["within_requested_limits"] is True
        print(f"Straight pair: 20 mm each, 1.8 mm edge gap; "
              f"{len(json.dumps(clean).encode())} inspection bytes")
        print("Fault: authored totals match but terminal paths differ by 2 mm")
        print("Layered pair: 20 mm track + 3.13 mm traversed barrel per side")
        print("PASS: gap/skew faults, 45-degree bends, zone refusal, disconnected "
              "paths, branches, ambiguous partners, stackup readback, "
              "back-side geometry, response cap and repair")


if __name__ == "__main__":
    asyncio.run(main())
