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
                        "layer": layer, "size": 1.2, "mirror": mirror,
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
            for field in ("text", "justify", "vertical_justify", "mirror", "rotation"):
                assert written[field] == request[field], (field, request, written)

        # Legacy defaults and literal escapes retain their meaning.
        default = {"x": 50, "y": 115, "text": r"literal \n", "layer": "F.SilkS"}
        result = await call("add_board_texts", path=path, texts=[default])
        written = result["texts"][0]
        assert written["justify"] == written["vertical_justify"] == "center"
        assert written["text"] == default["text"] and not written["mirror"]
        await call("save_board", path=path)
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for changes in ({"justify": "flush"}, {"vertical_justify": "middle"},
                        {"size": 0}, {"size": -1}):
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
        print("PASS: 30 text blocks, both sides, horizontal/vertical alignment, "
              "90/270-degree rotation, default centering, literal escapes, "
              "invalid-list refusal and native renders")


if __name__ == "__main__":
    asyncio.run(main())
