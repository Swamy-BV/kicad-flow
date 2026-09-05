"""A cat-shaped board with silkscreen art, built through MCP calls alone.

This is the geometry example: a closed outer contour composed from straight
segments and three-point arcs, two circular internal cutouts, and front/back
silkscreen using every graphical primitive. No copper or footprints are used.

Run it: ``python examples/scripts/art_board.py``
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/art_board")


async def build(client: Client) -> int:
    """Build, inspect, validate and render the art board."""
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    board = str(OUT / "art_board.kicad_pcb")
    failures = 0

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        nonlocal failures
        result = await client.call_tool(tool, arguments)
        data = result.structured_content
        reply = data if isinstance(data, dict) else {}
        if not reply.get("ok", False):
            failures += 1
            print(f"FAILED {tool}: {reply.get('error', result)}")
        return reply

    await call("new_board", path=board, layers=2, thickness=1.6)

    # A cat-head silhouette. Every adjoining endpoint is explicit; the API
    # does not choose a radius, close a gap, or infer a contour.
    edge = [
        {"kind": "line", "layer": "Edge.Cuts",
         "x1": 20, "y1": 12, "x2": 12, "y2": 4},
        {"kind": "line", "layer": "Edge.Cuts",
         "x1": 12, "y1": 4, "x2": 10, "y2": 18},
        {"kind": "arc", "layer": "Edge.Cuts",
         "x1": 10, "y1": 18, "xm": 4, "ym": 30,
         "x2": 10, "y2": 42},
        {"kind": "arc", "layer": "Edge.Cuts",
         "x1": 10, "y1": 42, "xm": 30, "ym": 58,
         "x2": 50, "y2": 42},
        {"kind": "arc", "layer": "Edge.Cuts",
         "x1": 50, "y1": 42, "xm": 56, "ym": 30,
         "x2": 50, "y2": 18},
        {"kind": "line", "layer": "Edge.Cuts",
         "x1": 50, "y1": 18, "x2": 48, "y2": 4},
        {"kind": "line", "layer": "Edge.Cuts",
         "x1": 48, "y1": 4, "x2": 40, "y2": 12},
        {"kind": "arc", "layer": "Edge.Cuts",
         "x1": 40, "y1": 12, "xm": 30, "ym": 8,
         "x2": 20, "y2": 12},
        {"kind": "circle", "layer": "Edge.Cuts",
         "x": 13, "y": 40, "radius": 1.6},
        {"kind": "circle", "layer": "Edge.Cuts",
         "x": 47, "y": 40, "radius": 1.6},
    ]

    # Face, ears, whiskers and bow tie. This deliberately covers line, arc,
    # circle, rectangle and polygon, including both stroked and filled forms.
    front = [
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[18.5, 13], [13, 7], [12, 17]]},
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[41.5, 13], [47, 7], [48, 17]]},
        {"kind": "circle", "layer": "F.SilkS", "width": 0.45,
         "x": 21.5, "y": 27, "radius": 3},
        {"kind": "circle", "layer": "F.SilkS", "width": 0.45,
         "x": 38.5, "y": 27, "radius": 3},
        {"kind": "circle", "layer": "F.SilkS", "fill": True,
         "x": 21.5, "y": 27, "radius": 1.15},
        {"kind": "circle", "layer": "F.SilkS", "fill": True,
         "x": 38.5, "y": 27, "radius": 1.15},
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[27.5, 34], [32.5, 34], [30, 37]]},
        {"kind": "arc", "layer": "F.SilkS", "width": 0.4,
         "x1": 30, "y1": 37, "xm": 27, "ym": 40,
         "x2": 23.5, "y2": 38.5},
        {"kind": "arc", "layer": "F.SilkS", "width": 0.4,
         "x1": 36.5, "y1": 38.5, "xm": 33, "ym": 40,
         "x2": 30, "y2": 37},
        {"kind": "line", "layer": "F.SilkS", "width": 0.35,
         "x1": 24, "y1": 35, "x2": 12, "y2": 31},
        {"kind": "line", "layer": "F.SilkS", "width": 0.35,
         "x1": 24, "y1": 37, "x2": 11, "y2": 37},
        {"kind": "line", "layer": "F.SilkS", "width": 0.35,
         "x1": 36, "y1": 35, "x2": 48, "y2": 31},
        {"kind": "line", "layer": "F.SilkS", "width": 0.35,
         "x1": 36, "y1": 37, "x2": 49, "y2": 37},
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[23, 45], [29, 48], [23, 51]]},
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[37, 45], [31, 48], [37, 51]]},
        {"kind": "rectangle", "layer": "F.SilkS", "fill": True,
         "x1": 28.7, "y1": 46.5, "x2": 31.3, "y2": 49.5},
    ]

    # A reverse-side fish mark makes the back silkscreen independently useful.
    back = [
        {"kind": "polygon", "layer": "B.SilkS", "width": 0.3,
         "points": [[20, 28], [27, 23], [38, 28], [27, 33]]},
        {"kind": "polygon", "layer": "B.SilkS", "fill": True,
         "points": [[38, 28], [45, 23], [45, 33]]},
        {"kind": "circle", "layer": "B.SilkS", "fill": True,
         "x": 24, "y": 27, "radius": 0.8},
    ]

    made = await call("add_graphics", path=board,
                      graphics=edge + front + back)
    expected = len(edge) + len(front) + len(back)
    if made.get("count") != expected or made.get("size") != [52.0, 54.0]:
        failures += 1
        print(f"WRONG graphic result: {made}")

    # Exercise identity-based editing on a real visible mark: create a star,
    # move it to its final position, and remove a temporary construction box.
    edits = await call("add_graphics", path=board, graphics=[
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[28, 18], [29, 20], [31, 20], [29.5, 21.5],
                    [30, 24], [28, 22.5], [26, 24], [26.5, 21.5],
                    [25, 20], [27, 20]]},
        {"kind": "rectangle", "layer": "F.SilkS",
         "x1": 18, "y1": 16, "x2": 42, "y2": 42},
    ])
    edit_shapes = edits.get("graphics", [])
    if len(edit_shapes) == 2:
        await call("move_graphics", path=board, moves=[{
            "uuid": edit_shapes[0]["uuid"], "dx": 2, "dy": -2}])
        await call("remove_graphics", path=board,
                   uuids=[edit_shapes[1]["uuid"]])
    else:
        failures += 1

    await call("add_board_texts", path=board, texts=[
        {"x": 30, "y": 54, "text": "SHAPES, NOT GUESSES",
         "layer": "F.SilkS", "size": 1.1},
        {"x": 30, "y": 40, "text": "KICAD FLOW",
         "layer": "B.SilkS", "size": 1.4, "mirror": True},
    ])
    listed = await call("list_graphics", path=board)
    final_expected = expected + 1
    if listed.get("count") != final_expected:
        failures += 1
        print(f"WRONG list_graphics count: {listed.get('count')}")

    await call("save_board", path=board)
    checked = await call("check_board", path=board)
    findings = checked.get("findings", [])
    errors = [f for f in findings if f.get("severity") == "error"]
    if errors:
        failures += len(errors)
        print(f"DRC errors: {errors}")

    await call("render_board", path=board,
               output_file=str(OUT / "art_board-top.png"),
               side="top", width=1000, height=1000, quality="high")
    await call("render_board", path=board,
               output_file=str(OUT / "art_board-3d.png"),
               side="top", width=1200, height=1000, quality="high",
               rotate="-25,0,20", perspective=True, floor=True, zoom=0.9)
    await call("render_board", path=board,
               output_file=str(OUT / "art_board-bottom.png"),
               side="bottom", width=1000, height=1000, quality="high")

    print(f"graphics: {listed.get('count', 0)} shapes; "
          f"board {made.get('size', ['?', '?'])[0]} x "
          f"{made.get('size', ['?', '?'])[1]} mm")
    print(f"DRC: {checked.get('errors', '?')} errors, "
          f"{checked.get('warnings', '?')} warnings")
    print(f"MCP failures: {failures}")
    return failures


async def main() -> int:
    """Run against the in-process MCP server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
