"""Exercise the configurable advisory PCB placement grid through MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Report grid departures while preserving caller-chosen coordinates."""
    root = Path("out/board-grid").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "grid.kicad_pcb")
    resistor = "Resistor_SMD:R_0603_1608Metric"

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        created = await call("new_board", path=path)
        assert created["placement_grid_mm"] == 0.1
        await call("place_footprints", path=path, footprints=[
            {"fp_id": resistor, "ref": "R1", "x": 20.2, "y": 20},
            {"fp_id": resistor, "ref": "R2", "x": 24.35, "y": 20},
        ])
        initial = await call("get_board_grid", path=path)
        assert initial["spacing_mm"] == 0.1
        assert initial["off_grid_refs"] == ["R2"]

        changed = await call("set_board_grid", path=path, spacing_mm=0.5)
        assert changed["spacing_mm"] == 0.5
        assert changed["off_grid_refs"] == ["R1", "R2"]
        assert (await call("get_footprint", path=path, ref="R1"))["x"] == 20.2
        assert (await call("get_footprint", path=path, ref="R2"))["x"] == 24.35
        await call("move_footprints", path=path, moves=[
            {"ref": "R1", "x": 20.75, "y": 20},
        ])
        assert (await call("get_footprint", path=path, ref="R1"))["x"] == 20.75
        await call("save_board", path=path)

        invalid = (await client.call_tool("set_board_grid", {
            "path": path, "spacing_mm": 0,
        })).data
        assert not invalid["ok"], invalid
        assert (await call("get_board_grid", path=path))["spacing_mm"] == 0.5
        print("PASS: 0.1 mm default, configurable readback, off-grid report "
              "and exact placement/move coordinates after save")


if __name__ == "__main__":
    asyncio.run(main())
