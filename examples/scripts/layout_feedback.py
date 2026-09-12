"""Exercise actionable layout warnings and their activity log through MCP.

The fixture deliberately creates body and label collisions under out/. It
does not change the showcase designs or any user project.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Require body findings, correction readback and useful nested-call logs."""
    output = Path("out/layout-feedback").resolve()
    path = output / "overlaps.kicad_sch"
    async with Client(mcp) as client:
        async def call(name: str, **args):
            result = await client.call_tool(name, {"path": str(path), **args})
            assert result.data["ok"], result.data
            return result.data

        await call("new_sheet")
        await call("add_components", parts=[
            {"lib_id": "Device:R", "ref": "R1", "x": 50.8, "y": 50.8},
            {"lib_id": "Device:R", "ref": "R2", "x": 50.8, "y": 50.8},
        ])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        layout = await call("check_sheet_layout")
        assert not layout["clean"]
        assert layout["kind_counts"]["symbol_symbol_overlap"] == 1
        assert any(("R1" in f["first"] and "R2" in f["second"])
                   or ("R2" in f["first"] and "R1" in f["second"])
                   for f in layout["findings"])
        assert before == hashlib.sha256(path.read_bytes()).hexdigest()

        await call("move_components", moves=[{"ref": "R2", "x": 101.6, "y": 50.8}])
        assert (await call("check_sheet_layout"))["clean"]
        await call("add_labels", labels=[{"text": "RESET", "x": 50.8, "y": 50.8}])
        result = await client.call_tool("batch", {"ops": [{
            "tool": "check_sheet_layout", "args": {"path": str(path)},
        }]})
        # Executing an inspection successfully is distinct from a clean design.
        assert result.data["ok"]
        warning = result.data["results"][0]
        assert not warning["clean"] and warning["kind_counts"]["label_symbol_overlap"]
        rows = [json.loads(line) for line in (output / "logs/mcp.jsonl").read_text(
            encoding="utf-8").splitlines()]
        record = rows[-1]
        assert record["tool"] == "check_sheet_layout"
        assert record["ok"] and not record["result"]["clean"]
        assert record["result"]["kind_counts"]["label_symbol_overlap"]
        assert record["result"]["findings_sample"][0]["first"]
    print("PASS: body/label warnings, caller correction, read-only inspection, "
          "and finding evidence in nested activity logs")


if __name__ == "__main__":
    asyncio.run(main())
