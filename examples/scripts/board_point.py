"""Check that point inspection distinguishes overlapping board layers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from _board_fixture import export_footprints, schematic_nets
from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """A front/back pad pair joins only after a via spans both layers."""
    root = Path("out/board-point").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "point.kicad_pcb")
    resistor = "Resistor_SMD:R_0603_1608Metric"

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        await call("new_board", path=path)
        await export_footprints(call, path, [
            {"fp_id": resistor, "ref": "R1", "x": 20, "y": 20},
            {"fp_id": resistor, "ref": "R2", "x": 20, "y": 20, "side": "B"},
        ])
        front = (await call("get_footprint", path=path, ref="R1"))["pads"]
        back = (await call("get_footprint", path=path, ref="R2"))["pads"]
        pair = next(
            (first, second)
            for first in front for second in back
            if (first["x"], first["y"]) == (second["x"], second["y"])
        )
        await schematic_nets(call, path, [
            {"ref": "R1", "pad": pair[0]["number"], "net": "SIGNAL"},
            {"ref": "R2", "pad": pair[1]["number"], "net": "SIGNAL"},
        ], two_pin_refs={"R1", "R2"})
        x, y = pair[0]["x"], pair[0]["y"]
        separated = await call("what_is_on_board", path=path, x=x, y=y)
        assert not separated["connected"], separated
        assert len([p for p in separated["pads"] if p["net"] == "SIGNAL"]) == 2
        assert len([g for g in separated["connected_groups"]
                    if g["net"] == "SIGNAL"]) == 2
        unrouted = await call("unrouted_connections", path=path, limit=0)
        assert unrouted["count"] == 1, unrouted

        await call("add_vias", path=path, vias=[
            {"x": x, "y": y, "net": "SIGNAL", "diameter": 0.6,
             "drill": 0.3},
        ])
        joined = await call("what_is_on_board", path=path, x=x, y=y)
        assert joined["connected"], joined
        assert len([g for g in joined["connected_groups"]
                    if g["net"] == "SIGNAL"]) == 1
        unrouted = await call("unrouted_connections", path=path, limit=0)
        assert unrouted["count"] == 0, unrouted
        print("PASS: opposite-side pads stay separate until a via joins them")


if __name__ == "__main__":
    asyncio.run(main())
