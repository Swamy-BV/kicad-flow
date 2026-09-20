"""Exercise five-component PCB transfer batches through the MCP interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp
from kicad_flow.server.limits import BATCH_LIMIT


async def main() -> None:
    """Check 5/5/2 transfer, disk resume, strict sync and atomic refusals."""
    assert BATCH_LIMIT == 5, "Run this regression with the mandated five-item limit"
    root = Path("out/staged-board-transfer").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sheet = str(root / "twelve.kicad_sch")
    board = str(root / "twelve.kicad_pcb")
    Path(board).unlink(missing_ok=True)
    footprint = "Resistor_SMD:R_0603_1608Metric"
    replacement = "Resistor_SMD:R_0805_2012Metric"
    refs = [f"R{i + 1}" for i in range(12)]
    poses = [
        {"ref": ref, "x": 20 + (i % 6) * 5, "y": 20 + (i // 6) * 5}
        for i, ref in enumerate(refs)
    ]
    async with Client(mcp) as client:

        async def call(name: str, **args: Any) -> Any:
            result = (await client.call_tool(name, args)).data
            assert result["ok"], result
            return result

        async def transfer(items: list[dict[str, Any]]) -> Any:
            return await call(
                "update_board_from_schematic", schematic_path=sheet,
                board_path=board, placements=items,
            )

        async def refused(items: list[dict[str, Any]], message: str) -> None:
            before = Path(board).read_bytes() if Path(board).exists() else None
            result = (await client.call_tool("update_board_from_schematic", {
                "schematic_path": sheet, "board_path": board, "placements": items,
            })).data
            assert not result["ok"] and message in result["error"], result
            after = Path(board).read_bytes() if Path(board).exists() else None
            assert before == after, "A failed batch changed the saved PCB"

        await call("new_sheet", path=sheet)
        for start in range(0, 12, 5):
            chunk = refs[start:start + 5]
            placed = (await call("add_components", path=sheet, parts=[
                {"ref": ref, "lib_id": "Device:R", "x": 25.4 + i * 12.7,
                 "y": 25.4 + start * 2.54}
                for i, ref in enumerate(chunk)
            ]))["parts"]
            await call("set_fields", path=sheet, fields=[
                {"ref": ref, "name": "Footprint", "value": footprint}
                for ref in chunk
            ])
            for number, net in (("1", "BUS"), ("2", "RETURN")):
                await call("add_labels", path=sheet, labels=[
                    {"text": net, "x": pin["x"], "y": pin["y"], "kind": "global"}
                    for part in placed for pin in part["pins"]
                    if pin["number"] == number
                ])
        await call("save_sheet", path=sheet)
        schema = next(t for t in await client.list_tools()
                      if t.name == "update_board_from_schematic").input_schema
        assert any(v.get("maxItems") == 5 for v in
                   schema["properties"]["placements"]["anyOf"])
        oversized = await client.call_tool("update_board_from_schematic", {
            "schematic_path": sheet, "board_path": board, "placements": poses[:6],
        }, raise_on_error=False)
        assert oversized.is_error and not Path(board).exists()
        await refused([], "supply a placement batch")
        await refused([poses[0], poses[0]], "duplicate")
        await refused([{"ref": "R99", "x": 20, "y": 20}], "unexpected refs")

        first = await transfer(poses[:5])
        assert first["created"] and not first["complete"]
        assert first["remaining_refs"] == sorted(refs[5:])
        assert first["changed_pad_count"] == 10
        saved_first = (await call("get_footprint", path=board, ref="R1"))
        await refused(poses[:5], "unexpected refs")
        await call("close_board", path=board)
        strict = (await client.call_tool("sync_board_nets", {
            "schematic_path": sheet, "board_path": board,
        })).data
        assert not strict["ok"] and "lack placed board pads" in strict["error"]

        # R6 is placed before R7 fails pin validation: the whole stage rolls back.
        await call("set_fields", path=sheet, fields=[{
            "ref": "R7", "name": "Footprint",
            "value": "TestPoint:TestPoint_Pad_D1.0mm",
        }])
        await refused(poses[5:10], "lack placed board pads")
        assert len((await call("list_footprints", path=board))["footprints"]) == 5
        await call("set_fields", path=sheet, fields=[{
            "ref": "R7", "name": "Footprint", "value": footprint,
        }])
        second = await transfer(poses[5:10])
        assert not second["created"] and not second["complete"]
        assert second["remaining_refs"] == sorted(refs[10:])
        last = await transfer(poses[10:])
        assert last["complete"] and last["remaining_refs"] == []
        assert (await call("get_footprint", path=board, ref="R1")) == saved_first
        footprints = (await call("list_footprints", path=board,
                                 with_pads=True))["footprints"]
        assert len(footprints) == 12
        assert {p["net"] for f in footprints for p in f["pads"]} == {"BUS", "RETURN"}
        repeated = await transfer([])
        assert repeated["complete"] and repeated["changed_pad_count"] == 0
        assert (await call("sync_board_nets", schematic_path=sheet,
                           board_path=board))["changed_count"] == 0

        # Replacement batches leave deferred, already placed footprints intact.
        before_r6 = await call("get_footprint", path=board, ref="R6")
        await call("set_fields", path=sheet, fields=[
            {"ref": ref, "name": "Footprint", "value": replacement}
            for ref in ("R1", "R6")
        ])
        changed = await transfer([poses[0]])
        assert changed["changed_footprints"] == ["R1"]
        assert changed["remaining_refs"] == ["R6"] and not changed["complete"]
        assert (await call("get_footprint", path=board, ref="R6")) == before_r6
        assert (await transfer([poses[5]]))["complete"]

        # A later copper-blocked replacement must also undo an earlier one.
        old_r1 = await call("get_footprint", path=board, ref="R1")
        old_r6 = await call("get_footprint", path=board, ref="R6")
        pad = old_r6["pads"][0]
        await call("add_tracks", path=board, tracks=[{
            "x1": pad["x"], "y1": pad["y"], "x2": pad["x"] + 2,
            "y2": pad["y"], "layer": "F.Cu", "width": 0.2, "net": pad["net"],
        }])
        await call("set_fields", path=sheet, fields=[
            {"ref": ref, "name": "Footprint", "value": footprint}
            for ref in ("R1", "R6")
        ])
        await refused([poses[0], poses[5]], "existing copper")
        assert (await call("get_footprint", path=board, ref="R1")) == old_r1
        assert (await call("get_footprint", path=board, ref="R6")) == old_r6
    print("PASS: 5/5/2 transfer, six-item rejection, disk resume, net membership, "
          "strict sync, deferred replacements and atomic failure recovery")


if __name__ == "__main__":
    asyncio.run(main())
