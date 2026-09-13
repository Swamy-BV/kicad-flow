"""Exercise preview rule isolation and nominal thickness through MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Check unchanged proposals, real faults, and explicit ordering thickness."""
    root = Path("out/routing-preview").resolve()
    root.mkdir(parents=True, exist_ok=True)
    board = root / "preview.kicad_pcb"
    path = str(board)

    def hashes() -> dict[str, str]:
        return {suffix: hashlib.sha256(
                    board.with_suffix(suffix).read_bytes()).hexdigest()
                for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru")
                if board.with_suffix(suffix).exists()}

    def stackup(total: float) -> list[dict[str, Any]]:
        return [
            {"name": "F.Cu", "kind": "copper", "thickness": 0.035},
            {"name": "dielectric 1", "kind": "prepreg", "thickness": 0.2104},
            {"name": "In1.Cu", "kind": "copper", "thickness": 0.0152},
            {"name": "dielectric 2", "kind": "core",
             "thickness": total - 0.5212},
            {"name": "In2.Cu", "kind": "copper", "thickness": 0.0152},
            {"name": "dielectric 3", "kind": "prepreg", "thickness": 0.2104},
            {"name": "B.Cu", "kind": "copper", "thickness": 0.035},
        ]

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            result = (await client.call_tool(tool, {"path": path, **args})).data
            assert result["ok"], result
            return result

        async def unchanged() -> dict[str, Any]:
            current = await call("check_board")
            before = hashes()
            candidate = await call("check_board", tracks=[])
            assert candidate["kind_counts"] == current["kind_counts"], candidate
            assert candidate["new_findings"] == [], candidate
            assert candidate["resolved_findings"] == [], candidate
            assert hashes() == before, "preview changed the source project"
            assert not list(root.glob(".preview.route-check-*"))
            return candidate

        await call("new_board", layers=4, thickness=1.6)
        await call("add_graphics", graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts", "x1": 0, "y1": 0,
            "x2": 50, "y2": 40}])
        await call("set_fabrication_profile", inner_copper_oz=0.5)
        await call("set_stackup", layers=stackup(1.5862))
        await call("set_net_classes", classes=[{
            "name": "Default", "clearance": 0.15, "track_width": 0.2}])
        await call("set_board_constraints", rules=[{
            "name": "P width", "condition": "A.NetName == 'P'",
            "constraints": [{"kind": "track_width", "min": 0.09}]}])
        await call("place_footprints", footprints=[{
            "fp_id": "TestPoint:TestPoint_Pad_D1.0mm", "ref": ref, "x": x, "y": y}
            for ref, x, y in (("A1", 10, 20), ("A2", 30, 20),
                              ("B1", 10, 22), ("B2", 30, 22), ("G1", 40, 25))])
        await call("set_pad_nets", pads=[{"ref": ref, "pad": "1", "net": net}
                   for ref, net in (("A1", "P"), ("A2", "P"),
                                    ("B1", "N"), ("B2", "N"), ("G1", "GND"))])
        await call("add_tracks", tracks=[{
            "net": net, "layer": "F.Cu", "width": 0.2,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2}
            for net, x1, y1, x2, y2 in (
                ("P", 10, 20, 30, 20), ("N", 10, 22, 11, 22),
                ("N", 11, 22, 12.64, 20.36), ("N", 12.64, 20.36, 28, 20.36),
                ("N", 28, 20.36, 29.64, 22), ("N", 29.64, 22, 30, 22))])
        await call("add_vias", vias=[{
            "net": "GND", "x": 40, "y": 25, "diameter": 0.45, "drill": 0.2,
            "layers": ["F.Cu", "B.Cu"]}])
        await call("add_zones", zones=[{
            "net": "GND", "layer": "In1.Cu", "boundary": "board_outline",
            "inset": 0.3, "pad_connection": "solid"}])
        await call("refill_zones")
        initial = await unchanged()
        assert initial["errors"] == 0, initial

        # A custom rule must survive too, including pre-existing violations.
        await call("set_board_constraints", rules=[{
            "name": "P width", "condition": "A.NetName == 'P'",
            "constraints": [{"kind": "track_width", "min": 0.25}]}])
        custom = await unchanged()
        assert custom["kind_counts"].get("track_width", 0) > 0, custom
        before = hashes()
        short = await call("check_board", tracks=[{
            "net": "P", "layer": "F.Cu", "width": 0.25,
            "x1": 15, "y1": 20, "x2": 15, "y2": 21}])
        assert any(f["kind"] in ("shorting_items", "tracks_crossing")
                   and (f.get("input_kind") == "tracks"
                        or f.get("other_input_kind") == "tracks")
                   for f in short["new_findings"]), short
        assert hashes() == before
        assert not list(root.glob(".preview.route-check-*"))

        # Setting a profile after stackup requires an explicit nominal choice.
        refused = (await client.call_tool("set_fabrication_profile", {
            "path": path, "inner_copper_oz": 0.5})).data
        assert not refused["ok"], refused
        profile = await call("set_fabrication_profile", inner_copper_oz=0.5,
                             nominal_thickness=1.6)
        assert profile["profile"]["selection"]["thickness"] == 1.6
        assert abs(profile["profile"]["thickness_tolerance_mm"] - 0.16) < 1e-9
        for total, expected in ((1.44, False), (1.76, False), (1.77, True)):
            await call("set_stackup", layers=stackup(total))
            result = await call("check_board")
            assert ("provider_board_thickness" in result["kind_counts"]) == expected
        incompatible = (await client.call_tool("set_fabrication_profile", {
            "path": path, "inner_copper_oz": 0.5, "nominal_thickness": 1.6})).data
        assert not incompatible["ok"], incompatible
        await call("set_stackup", layers=stackup(1.5862))

        # Legacy sidecars keep strict checking until explicitly re-resolved.
        sidecar = board.with_suffix(".kicad-flow.json")
        legacy = json.loads(sidecar.read_text(encoding="utf-8"))
        legacy["fabrication_profile"].pop("thickness_tolerance_mm")
        sidecar.write_text(json.dumps(legacy), encoding="utf-8")
        assert "provider_board_thickness" in (await call("check_board"))["kind_counts"]
        await call("set_fabrication_profile", inner_copper_oz=0.5,
                   nominal_thickness=1.6)
        await unchanged()
        print("PASS: preview project/custom rules, source isolation, real short "
              "detection, nominal thickness boundaries, and legacy profile refresh")


if __name__ == "__main__":
    asyncio.run(main())
