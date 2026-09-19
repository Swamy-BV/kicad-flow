"""Exercise multiline PCB text alignment and native renders through MCP."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def main() -> None:
    """Render alignment anchors, unequal line widths, rotation and mirroring."""
    root = Path("out/pcb-text").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "alignment.kicad_pcb")
    async with Client(mcp) as client:
        async def call(tool: str, **args: Any) -> dict[str, Any]:
            result = (await client.call_tool(tool, args)).data
            assert result["ok"], result
            return result

        await call("new_board", path=path)
        await call("add_graphics", path=path, graphics=[{
            "kind": "rectangle", "layer": "Edge.Cuts",
            "x1": 0, "y1": 0, "x2": 100, "y2": 120,
        }])
        notes = []
        guides = []
        for side, mirror in (("F", False), ("B", True)):
            layer = f"{side}.SilkS"
            for x, justify in ((16, "left"), (50, "center"), (84, "right")):
                for y, vertical, angle in (
                    (15, "top", 0), (35, "center", 0), (55, "bottom", 0),
                    (80, "top", 90), (105, "bottom", 270),
                ):
                    notes.append({
                        "x": x, "y": y, "text": "LONG LINE\nMID\nI",
                        "layer": layer, "width": 1.2, "height": 1.2,
                        "thickness": 0.18, "mirror": mirror,
                        "justify": justify, "vertical_justify": vertical,
                        "rotation": angle,
                    })
                    # Short crosses mark authored anchors without obscuring lines.
                    guides.extend([
                        {"kind": "line", "layer": layer, "width": 0.05,
                         "x1": x - 0.4, "y1": y, "x2": x + 0.4, "y2": y},
                        {"kind": "line", "layer": layer, "width": 0.05,
                         "x1": x, "y1": y - 0.4, "x2": x, "y2": y + 0.4},
                    ])
        await call("add_graphics", path=path, graphics=guides)
        result = await call("add_board_texts", path=path, texts=notes)
        assert len(result["texts"]) == 30
        for request, written in zip(notes, result["texts"], strict=True):
            for field in (
                "text", "justify", "vertical_justify", "mirror", "rotation",
                "width", "height", "thickness",
            ):
                assert written[field] == request[field], (field, request, written)
            assert written["uuid"] and written["bounds"]["width"] > 0

        listed = await call("list_board_texts", path=path)
        assert listed["count"] == 30

        # Defaults and literal escapes retain their meaning. Exercise exact
        # update/remove identity without changing the final rendered input.
        default = {"x": 50, "y": 115, "text": r"literal \n", "layer": "F.SilkS"}
        result = await call("add_board_texts", path=path, texts=[default])
        written = result["texts"][0]
        assert written["justify"] == written["vertical_justify"] == "center"
        assert written["text"] == default["text"] and not written["mirror"]
        assert written["width"] == written["height"] == 1.0
        assert written["thickness"] == 0.15 and written["uuid"]
        changed = (await call("update_board_texts", path=path, updates=[{
            "uuid": written["uuid"], "x": 51, "text": "edited",
            "width": 1.1, "height": 0.9, "thickness": 0.12,
        }]))["texts"][0]
        assert changed["x"] == 51 and changed["text"] == "edited"
        assert changed["width"] == 1.1 and changed["height"] == 0.9
        await call("close_board", path=path)
        await call("reload_board", path=path)
        round_trip = next(
            item for item in (await call("list_board_texts", path=path))["texts"]
            if item["uuid"] == written["uuid"]
        )
        assert round_trip["width"] == 1.1 and round_trip["height"] == 0.9
        assert round_trip["thickness"] == 0.12 and round_trip["text"] == "edited"
        await call("remove_board_texts", path=path, uuids=[written["uuid"]])
        await call("add_board_texts", path=path, texts=[default])
        await call("save_board", path=path)
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for changes in ({"justify": "flush"}, {"vertical_justify": "middle"},
                        {"width": 0}, {"height": -1}, {"thickness": 0}):
            refused = await client.call_tool("add_board_texts", {
                "path": path, "texts": [default, {**default, **changes}],
            }, raise_on_error=False)
            assert refused.is_error
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before

        for side in ("top", "bottom"):
            await call("render_board_layout", path=path, side=side, dpi=400,
                       output_file=str(root / f"{side}.png"))
        native = await call("check_board", path=path)
        print(f"Native DRC: {native['errors']} errors (anchor guides intersect text).")
        print("PASS: stable IDs, list/update/remove, native bounds, independent "
              "dimensions, 30 aligned text blocks on both sides, defaults, "
              "invalid-list refusal and native renders")


if __name__ == "__main__":
    asyncio.run(main())
