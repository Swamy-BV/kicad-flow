"""Exercise stale-board refusal and explicit reload/close through MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Prove an external board edit cannot be overwritten by a cached write."""
    root = Path("out/board-session").resolve()
    root.mkdir(parents=True, exist_ok=True)
    board = root / "session.kicad_pcb"
    for suffix in (".kicad_pcb", ".kicad-flow.json"):
        board.with_suffix(suffix).unlink(missing_ok=True)

    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            data = (await client.call_tool(tool, args)).data
            assert data["ok"], (tool, data)
            return data

        await call("new_board", path=str(board))
        await call("add_graphics", path=str(board), graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 0, "y1": 0, "x2": 20, "y2": 10,
        }])
        external = board.read_text(encoding="utf-8").replace(
            '(paper "A4")', '(paper "A3")'
        )
        board.write_text(external, encoding="utf-8")

        refused = (await client.call_tool("add_board_texts", {
            "path": str(board),
            "texts": [{"x": 10, "y": 5, "text": "SAFE", "layer": "F.SilkS"}],
        })).data
        assert not refused["ok"] and "changed on disk" in refused["error"], refused
        assert '(paper "A3")' in board.read_text(encoding="utf-8")

        await call("reload_board", path=str(board))
        await call("add_board_texts", path=str(board), texts=[{
            "x": 10, "y": 5, "text": "SAFE", "layer": "F.SilkS",
        }])
        closed = await call("close_board", path=str(board))
        assert closed["closed"]
        listed = await call("list_board_texts", path=str(board))
        assert listed["count"] == 1
        assert '(paper "A3")' in board.read_text(encoding="utf-8")

    print("PASS: stale write refused; reload, close and automatic reopen work")


if __name__ == "__main__":
    asyncio.run(main())
