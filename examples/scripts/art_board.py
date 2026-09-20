"""Mooncat: a two-sided celestial PCB charm, built through MCP calls alone.

This is the geometry example: a closed outer contour composed from straight
segments and three-point arcs, two circular internal cutouts, and front/back
silkscreen using every graphical primitive. No copper or footprints are used.

Run it: ``python examples/scripts/art_board.py``
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/art_board")


async def build(client: Client) -> int:
    """Build, inspect, validate and render the art board."""
    OUT.mkdir(parents=True, exist_ok=True)
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

    # Deliberately authored art: two stroke weights, wide quiet margins and
    # one crescent focal point. Coordinate generation belongs to the caller.
    def polygon(points: list[list[float]], side: str = "F.SilkS",
                fill: bool = True, width: float = 0.2) -> dict[str, Any]:
        return {"kind": "polygon", "layer": side, "fill": fill,
                "width": width, "points": points}

    def line(x1: float, y1: float, x2: float, y2: float,
             side: str = "F.SilkS", width: float = 0.3) -> dict[str, Any]:
        return {"kind": "line", "layer": side, "width": width,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2}

    def arc(x1: float, y1: float, xm: float, ym: float, x2: float,
            y2: float, side: str = "F.SilkS", width: float = 0.4) -> dict[str, Any]:
        return {"kind": "arc", "layer": side, "width": width,
                "x1": x1, "y1": y1, "xm": xm, "ym": ym, "x2": x2, "y2": y2}

    def star(x: float, y: float, size: float,
             side: str = "F.SilkS") -> dict[str, Any]:
        return polygon([[x, y-size], [x+size*0.26, y-size*0.26],
                        [x+size, y], [x+size*0.26, y+size*0.26],
                        [x, y+size], [x-size*0.26, y+size*0.26],
                        [x-size, y], [x-size*0.26, y-size*0.26]], side)

    # A crescent is a single closed polygon, so no white overprinting tricks.
    moon = [[30 + 4 * math.cos(t), 19 + 4 * math.sin(t)]
            for t in [math.radians(-90 - i * 180 / 40) for i in range(41)]]
    moon += [[30 - 1.7 * math.cos(t), 19 + 4 * math.sin(t)]
             for t in [math.radians(90 - i * 180 / 40) for i in range(41)]]
    front = [
        polygon([[13.8, 9.5], [18, 14], [13, 16]], fill=False, width=0.35),
        polygon([[46.2, 9.5], [42, 14], [47, 16]], fill=False, width=0.35),
        polygon(moon, width=0.15),
        # Relaxed eyelids and three short lashes on each eye.
        arc(16, 28, 21.5, 31, 26, 28, width=0.65),
        arc(34, 28, 38.5, 31, 44, 28, width=0.65),
        line(17.4, 29.4, 16.4, 30.8), line(20, 30.5, 19.6, 32),
        line(42.6, 29.4, 43.6, 30.8), line(40, 30.5, 40.4, 32),
        polygon([[28.8, 34.3], [31.2, 34.3], [30, 35.5]]),
        line(30, 35.5, 30, 36.5),
        arc(26.5, 37, 28.4, 38, 30, 36.5, width=0.35),
        arc(30, 36.5, 31.6, 38, 33.5, 37, width=0.35),
        line(11, 32, 22, 35, width=0.25),
        line(10, 35, 21, 36.5, width=0.25),
        line(49, 32, 38, 35, width=0.25),
        line(50, 35, 39, 36.5, width=0.25),
        star(35, 18, 1.4), star(21, 20, 0.85), star(41, 23, 0.65),
        # Rings deliberately keep 0.75 mm of material around each cutout.
        {"kind": "circle", "layer": "F.SilkS", "width": 0.2,
         "x": 13, "y": 40, "radius": 2.45},
        {"kind": "circle", "layer": "F.SilkS", "width": 0.2,
         "x": 47, "y": 40, "radius": 2.45},
        line(20, 44, 26.5, 44, width=0.2),
        line(33.5, 44, 40, 44, width=0.2), star(30, 44, 0.9),
    ]
    for x, y, radius in [(18, 24, 0.3), (38, 15, 0.25), (24, 15, 0.22),
                          (35.5, 23, 0.22), (23.5, 40.5, 0.25), (36.5, 40.5, 0.25)]:
        front.append({"kind": "circle", "layer": "F.SilkS", "fill": True,
                      "x": x, "y": y, "radius": radius, "width": 0.15})

    # The reverse is its own emblem: an orbit, a fish, a small constellation.
    orbit = []
    tilt = math.radians(-20)
    for step in range(80):
        angle = step * 2 * math.pi / 80
        x, y = 13 * math.cos(angle), 5.5 * math.sin(angle)
        orbit.append([30 + x * math.cos(tilt) - y * math.sin(tilt),
                      28 + x * math.sin(tilt) + y * math.cos(tilt)])
    back = [
        {"kind": "circle", "layer": "B.SilkS", "x": 30, "y": 28,
         "radius": 10.5, "width": 0.25},
        polygon(orbit, "B.SilkS", fill=False, width=0.35),
        polygon([[23, 28], [27, 24.8], [33.5, 28], [27, 31.2]],
                "B.SilkS", fill=False, width=0.45),
        polygon([[33.5, 28], [37, 25.5], [37, 30.5]], "B.SilkS"),
        {"kind": "circle", "layer": "B.SilkS", "fill": True,
         "x": 26, "y": 27.6, "radius": 0.45},
        star(41, 19, 1.2, "B.SilkS"), star(19, 36, 1, "B.SilkS"),
        line(24, 13.5, 29, 11.8, "B.SilkS", 0.2),
        line(29, 11.8, 35, 14.2, "B.SilkS", 0.2),
        {"kind": "rectangle", "layer": "B.SilkS", "fill": True,
         "x1": 25, "y1": 49, "x2": 35, "y2": 49.25, "width": 0.15},
    ]
    for x, y in [(24, 13.5), (29, 11.8), (35, 14.2)]:
        back.append({"kind": "circle", "layer": "B.SilkS", "fill": True,
                     "x": x, "y": y, "radius": 0.4, "width": 0.15})

    made = await call("add_graphics", path=board,
                      graphics=edge + front + back)
    expected = len(edge) + len(front) + len(back)
    if made.get("count") != expected or made.get("size") != [52.0, 54.0]:
        failures += 1
        print(f"WRONG graphic result: {made}")

    # Stable-ID editing stays part of the example: reposition the accent star
    # and delete the construction box without redrawing the design.
    edits = await call("add_graphics", path=board, graphics=[
        star(36, 20, 0.6),
        {"kind": "rectangle", "layer": "F.SilkS",
         "x1": 18, "y1": 16, "x2": 42, "y2": 42},
    ])
    edit_shapes = edits.get("graphics", [])
    if len(edit_shapes) == 2:
        await call("move_graphics", path=board, moves=[{
            "uuid": edit_shapes[0]["uuid"], "dx": 1, "dy": 0.5}])
        await call("remove_graphics", path=board,
                   uuids=[edit_shapes[1]["uuid"]])
    else:
        failures += 1

    await call("add_board_texts", path=board, texts=[
        {"x": 30, "y": 48.5, "text": "M O O N C A T",
         "layer": "F.SilkS", "width": 1.45, "height": 1.65, "thickness": 0.22},
        {"x": 30, "y": 52, "text": "AFTER HOURS / 01",
         "layer": "F.SilkS", "width": 0.85, "height": 1, "thickness": 0.15},
        {"x": 30, "y": 42, "text": "STAY CURIOUS",
         "layer": "B.SilkS", "width": 1.3, "height": 1.5,
         "thickness": 0.2, "mirror": True},
        {"x": 30, "y": 46, "text": "KICADFLOW / ART SERIES",
         "layer": "B.SilkS", "width": 0.8, "height": 1,
         "thickness": 0.15, "mirror": True},
    ])
    await call("set_stackup", path=board, copper_finish="ENIG", layers=[
        {"name": "F.SilkS", "kind": "Top Silk Screen", "color": "White"},
        {"name": "F.Mask", "kind": "Top Solder Mask", "color": "Purple"},
        {"name": "F.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "dielectric 1", "kind": "core", "thickness": 1.53,
         "material": "FR4", "epsilon_r": 4.5},
        {"name": "B.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "B.Mask", "kind": "Bottom Solder Mask", "color": "Purple"},
        {"name": "B.SilkS", "kind": "Bottom Silk Screen", "color": "White"},
    ])
    await call("set_fabrication_profile", path=board,
               soldermask_color="purple", finish="ENIG")
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
