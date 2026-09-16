"""Exercise opt-in track angle checks and atomic write refusal through MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Check existing/candidate geometry and reject an off-angle list."""
    root = Path("out/track-angles").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "angles.kicad_pcb")
    straight = {
        "x1": 20, "y1": 20, "x2": 25, "y2": 20,
        "layer": "F.Cu", "width": 0.25,
    }
    diagonal = {
        "x1": 30, "y1": 20, "x2": 33, "y2": 23,
        "layer": "F.Cu", "width": 0.25,
    }
    off_angle = {
        "x1": 40, "y1": 20, "x2": 44, "y2": 22,
        "layer": "F.Cu", "width": 0.25,
    }

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], data
            return data

        await call("new_board", path=path)
        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 10, "y1": 10, "x2": 55, "y2": 35,
        }])
        preview = await call(
            "check_board", path=path,
            tracks=[straight, off_angle, diagonal], track_angle_step=45,
        )
        violations = [
            item for item in preview["new_findings"]
            if item["kind"] == "track_angle"
        ]
        assert len(violations) == 1, violations
        assert violations[0]["input_kind"] == "tracks"
        assert violations[0]["input_index"] == 1
        assert preview["current_finding_count"] == 0

        refused = (await client.call_tool("add_tracks", {
            "path": path, "tracks": [straight, off_angle],
            "track_angle_step": 45,
        })).data
        assert not refused["ok"] and refused["index"] == 1, refused
        assert refused["applied_count"] == 0
        assert not (await call("list_copper", path=path))["tracks"]

        accepted = await call(
            "add_tracks", path=path, tracks=[straight, diagonal],
            track_angle_step=45,
        )
        assert accepted["count"] == 2
        checked = await call("check_board", path=path, track_angle_step=45)
        assert checked["kind_counts"].get("track_angle", 0) == 0

        # Arbitrary angles remain supported when no caller policy is supplied.
        await call("add_tracks", path=path, tracks=[off_angle])
        unrestricted = await call("check_board", path=path)
        assert unrestricted["kind_counts"].get("track_angle", 0) == 0
        strict = await call("check_board", path=path, track_angle_step=45)
        actual = [
            item for item in strict["findings"] if item["kind"] == "track_angle"
        ]
        assert len(actual) == 1 and actual[0]["uuid"], actual
        print("PASS: candidate index, atomic refusal, allowed 0/45 routing, "
              "opt-in inspection of existing arbitrary-angle track")


if __name__ == "__main__":
    asyncio.run(main())
