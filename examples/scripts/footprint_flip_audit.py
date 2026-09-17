"""Audit front/back footprint transforms across package styles and angles.

The board deliberately mixes asymmetric SMD and through-hole packages.  Every
style is placed on both sides at 0, 37, 90, 180 and 270 degrees, saved, loaded
again through MCP, checked against the library pad coordinates, and rendered
from both faces.

Run it: ``python examples/scripts/footprint_flip_audit.py``
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

from _board_fixture import export_footprints
from fastmcp import Client

from kicad_flow.server import mcp

ROOT = Path("out/footprint-flip-audit").resolve()
BOARD = str(ROOT / "footprint-flip-audit.kicad_pcb")
ANGLES = (0.0, 37.0, 90.0, 180.0, 270.0)
STYLES = (
    ("Q", "Package_TO_SOT_SMD:SOT-23", "SMD SOT-23"),
    ("D", "Diode_SMD:D_SMA", "SMD SMA"),
    (
        "J",
        "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
        "THT HEADER",
    ),
    ("U", "Package_DIP:DIP-8_W7.62mm", "THT DIP-8"),
)


def expected_pad(
    x: float,
    y: float,
    dx: float,
    dy: float,
    angle: float,
    side: str,
) -> tuple[float, float]:
    """Apply the board API's documented logical footprint transform."""
    local_x = dx if side == "F" else -dx
    theta = math.radians(angle)
    return (
        round(x + local_x * math.cos(theta) + dy * math.sin(theta), 6),
        round(y - local_x * math.sin(theta) + dy * math.cos(theta), 6),
    )


async def main() -> None:
    """Build, validate and render the transform matrix."""
    ROOT.mkdir(parents=True, exist_ok=True)
    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        definitions = {
            fp_id: await call("footprint_pads", fp_id=fp_id)
            for _prefix, fp_id, _label in STYLES
        }
        await call("new_board", path=BOARD, layers=2, thickness=1.6)
        await call("add_graphics", path=BOARD, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 5, "y1": 5, "x2": 195, "y2": 110,
        }])

        requests: list[dict[str, Any]] = []
        expected: dict[str, dict[str, Any]] = {}
        for row, (prefix, fp_id, label) in enumerate(STYLES):
            y = 22.0 + row * 23.0
            for side_index, side in enumerate(("F", "B")):
                for angle_index, angle in enumerate(ANGLES):
                    index = side_index * len(ANGLES) + angle_index + 1
                    ref = f"{prefix}{index}"
                    x = 24.0 + (index - 1) * 18.0
                    requests.append({
                        "fp_id": fp_id,
                        "ref": ref,
                        "x": x,
                        "y": y,
                        "rotation": angle,
                        "side": side,
                        "value": label,
                    })
                    expected[ref] = {
                        "fp_id": fp_id,
                        "x": x,
                        "y": y,
                        "rotation": angle,
                        "side": side,
                    }

        await export_footprints(call, BOARD, requests)
        await call("move_footprint_fields", path=BOARD, moves=[
            {"ref": item["ref"], "name": "Reference", "dx": 0, "dy": 0,
             "hide": True}
            for item in requests
        ])
        labels: list[dict[str, Any]] = []
        for side, layer, mirror in (
            ("F", "F.SilkS", False),
            ("B", "B.SilkS", True),
        ):
            side_offset = 0 if side == "F" else len(ANGLES)
            for i, angle in enumerate(ANGLES):
                x = 24.0 + (side_offset + i) * 18.0
                labels.append({
                    "x": x,
                    "y": 9,
                    "text": f"{side} {angle:g}",
                    "layer": layer,
                    "size": 0.8,
                    "mirror": mirror,
                })
        for row, (_prefix, _fp_id, label) in enumerate(STYLES):
            for layer, mirror in (("F.SilkS", False), ("B.SilkS", True)):
                labels.append({
                    "x": 10,
                    "y": 22.0 + row * 23.0,
                    "text": label,
                    "layer": layer,
                    "size": 0.8,
                    "rotation": 90,
                    "mirror": mirror,
                })
        await call("add_board_texts", path=BOARD, texts=labels)
        await call("save_board", path=BOARD)

        readback = await call("list_footprints", path=BOARD, with_pads=True)
        assert readback["count"] == len(requests), readback["count"]
        checked_pads = 0
        for footprint in readback["footprints"]:
            want = expected[footprint["ref"]]
            assert footprint["side"] == want["side"], (footprint, want)
            assert abs(footprint["rotation"] - want["rotation"]) < 1e-6, (
                footprint,
                want,
            )
            library_pads = {
                pad["number"]: pad for pad in definitions[want["fp_id"]]["pads"]
            }
            for pad in footprint["pads"]:
                source = library_pads[pad["number"]]
                px, py = expected_pad(
                    want["x"],
                    want["y"],
                    source["x"],
                    source["y"],
                    want["rotation"],
                    want["side"],
                )
                assert abs(pad["x"] - px) < 1e-3, (footprint["ref"], pad, px)
                assert abs(pad["y"] - py) < 1e-3, (footprint["ref"], pad, py)
                checked_pads += 1

        placement = await call("measure_placement", path=BOARD)
        assert placement["valid"], placement
        drc = await call("check_board", path=BOARD)
        mismatches = [
            item for item in drc.get("findings", [])
            if item.get("kind") == "lib_footprint_mismatch"
        ]
        assert not mismatches, mismatches
        errors = [
            item for item in drc.get("findings", [])
            if item.get("severity") == "error"
        ]
        assert not errors, errors

        await call(
            "render_board_layout",
            path=BOARD,
            output_file=str(ROOT / "front-layout.png"),
            side="top",
            dpi=260,
        )
        await call(
            "render_board_layout",
            path=BOARD,
            output_file=str(ROOT / "back-layout.png"),
            side="bottom",
            dpi=260,
        )
        await call(
            "render_board",
            path=BOARD,
            output_file=str(ROOT / "front-3d.png"),
            side="top",
            width=1800,
            height=1000,
            quality="high",
            rotate="-18,0,8",
            perspective=True,
            floor=True,
        )
        await call(
            "render_board",
            path=BOARD,
            output_file=str(ROOT / "back-3d.png"),
            side="bottom",
            width=1800,
            height=1000,
            quality="high",
            rotate="18,0,-8",
            perspective=True,
            floor=True,
        )

        warnings = sum(
            item.get("severity") == "warning" for item in drc.get("findings", [])
        )
        print(
            f"PASS: {len(requests)} footprints, {checked_pads} pads; "
            f"SMD/THT x F/B x {len(ANGLES)} angles"
        )
        print(
            f"placement overlaps {placement['overlap_count']}, "
            f"edge violations {len(placement['edge_violations'])}; "
            f"DRC {len(errors)} errors/{warnings} warnings"
        )
        print(f"renders: {ROOT}")


if __name__ == "__main__":
    asyncio.run(main())
