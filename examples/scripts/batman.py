"""A sixteen-LED bat signal: schematic and shaped PCB, through MCP only.

Sixteen identical +5V -> resistor -> LED -> GND channels form a bat emblem.
The LEDs live on the front, their resistors directly behind them, and one via
per channel joins the only net that must cross layers. A front GND pour and a
back +5V pour complete the power paths. The PCB outline and all silkscreen art
are composed from the public graphical primitives.

Run it: ``python examples/scripts/batman.py``
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/batman")
G = 1.27
VCC, GND = "+5V", "GND"
LED_FP = "LED_SMD:LED_0603_1608Metric"
RES_FP = "Resistor_SMD:R_0402_1005Metric"
POWER_FP = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"

# The placement is the drawing: paired wing points, two eyes, a chest and the
# lower tail. Every coordinate is chosen here, never inferred by the API.
LED_AT = [
    (18.0, 25.0), (26.0, 24.0), (34.0, 27.0), (22.0, 34.0), (34.0, 36.0),
    (82.0, 25.0), (74.0, 24.0), (66.0, 27.0), (78.0, 34.0), (66.0, 36.0),
    (45.0, 18.0), (55.0, 18.0), (46.0, 25.0), (54.0, 25.0),
    (44.0, 42.0), (56.0, 42.0),
]

# Clockwise bat silhouette. A polygon is one exact closed contour; the two
# circular contours below become mounting cutouts by containment, a KiCad fact.
BAT_OUTLINE = [
    [50, 12], [54, 16], [61, 6], [60, 20], [82, 10], [76, 23],
    [96, 20], [86, 30], [96, 40], [72, 35], [78, 52], [60, 41],
    [55, 55], [50, 43], [45, 55], [40, 41], [22, 52], [28, 35],
    [4, 40], [14, 30], [4, 20], [24, 23], [18, 10], [40, 20],
    [39, 6], [46, 16],
]


async def build(client: Client) -> int:
    """Build both design halves, validate them and render the result."""
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    failures = 0
    calls = 0
    started = time.time()

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        nonlocal failures, calls
        calls += 1
        result = await client.call_tool(tool, arguments)
        data = result.structured_content
        reply = data if isinstance(data, dict) else {}
        if reply.get("ok") is not True:
            failures += 1
            print(f"FAILED {tool}: {reply.get('error', result)}")
        return reply

    def pin(part: dict[str, Any], number: str) -> tuple[float, float]:
        for candidate in part["pins"]:
            if candidate["number"] == number:
                return candidate["x"], candidate["y"]
        raise KeyError(f"{part.get('ref')} has no pin {number}")

    # -- schematic -------------------------------------------------------
    sheet = str(OUT / "batman.kicad_sch")
    await call("new_sheet", path=sheet, paper="A3",
               title="BAT SIGNAL // 16 LED beacon")
    parts: list[dict[str, Any]] = []
    for i in range(16):
        col, row = i % 8, i // 8
        x, top = (28 + col * 28) * G, (24 + row * 48) * G
        parts += [
            {"lib_id": "Device:R", "ref": f"R{i + 1}",
             "x": x, "y": top + 9 * G, "value": "330R"},
            {"lib_id": "Device:LED", "ref": f"D{i + 1}",
             "x": x, "y": top + 20 * G, "value": "AMBER",
             "rotation": 90},
        ]
    parts.append({"lib_id": "Connector_Generic:Conn_01x02", "ref": "J1",
                  "x": 270 * G, "y": 45 * G, "value": "5V IN"})
    placed = await call("add_components", path=sheet, parts=parts)
    made = placed.get("parts", [])
    if len(made) != 33:
        print(f"WRONG schematic placements: {len(made)}")
        return failures + 1

    channel_rails: list[dict[str, Any]] = []
    for i in range(16):
        col, row = i % 8, i // 8
        x, top = (28 + col * 28) * G, (24 + row * 48) * G
        channel_rails += [
            {"x": x, "y": top, "net": VCC},
            {"x": x, "y": top + 29 * G, "net": GND, "rotation": 180},
        ]
    # Two isolated rail/flag pairs establish that both global supplies are
    # driven. The connector receives its own visible power-symbol links.
    flag_rails = [
        {"x": 250 * G, "y": 105 * G, "net": VCC},
        {"x": 278 * G, "y": 105 * G, "net": GND},
    ]
    connector = made[-1]
    j1, j2 = pin(connector, "1"), pin(connector, "2")
    connector_rails = [
        {"x": j1[0] + 10 * G, "y": j1[1], "net": VCC,
         "rotation": 270},
        {"x": j2[0] + 10 * G, "y": j2[1], "net": GND,
         "rotation": 270},
    ]
    powered = await call("add_power", path=sheet,
                         symbols=channel_rails + flag_rails + connector_rails)
    flags = await call("add_power_flags", path=sheet, flags=[
        {"x": 250 * G, "y": 97 * G},
        {"x": 278 * G, "y": 97 * G},
    ])
    rail_parts = powered.get("symbols", [])
    flag_parts = flags.get("flags", [])

    wires: list[dict[str, float]] = []

    def link(a: dict[str, Any], an: str,
             b: dict[str, Any], bn: str) -> None:
        pa, pb = pin(a, an), pin(b, bn)
        wires.append({"x1": pa[0], "y1": pa[1],
                      "x2": pb[0], "y2": pb[1]})

    labels: list[dict[str, Any]] = []
    for i in range(16):
        resistor, led = made[i * 2:i * 2 + 2]
        supply, ground = rail_parts[i * 2:i * 2 + 2]
        link(supply, "1", resistor, "1")
        link(resistor, "2", led, "2")
        link(led, "1", ground, "1")
        rp, dp = pin(resistor, "2"), pin(led, "2")
        labels.append({"x": rp[0], "y": (rp[1] + dp[1]) / 2,
                       "text": f"BAT_{i + 1:02d}", "kind": "local"})
    for i in range(2):
        link(flag_parts[i], "1", rail_parts[32 + i], "1")
    link(connector, "1", rail_parts[34], "1")
    link(connector, "2", rail_parts[35], "1")
    await call("add_wires", path=sheet, wires=wires)
    await call("add_labels", path=sheet, labels=labels)
    await call("set_fields", path=sheet, fields=[
        *[{"ref": f"D{i}", "name": "Footprint", "value": LED_FP}
          for i in range(1, 17)],
        *[{"ref": f"R{i}", "name": "Footprint", "value": RES_FP}
          for i in range(1, 17)],
        {"ref": "J1", "name": "Footprint", "value": POWER_FP},
    ])
    await call("add_texts", path=sheet, notes=[{
        "x": 247 * G, "y": 126 * G,
        "text": "GOTHAM BEACON\n16 x AMBER LED\n5 V / 330 ohm",
        "size": 1.5}])
    await call("save_sheet", path=sheet)
    erc = await call("check_sheet", path=sheet)
    nets = await call("list_nets", path=sheet)

    # -- board -----------------------------------------------------------
    board = str(OUT / "batman.kicad_pcb")
    await call("new_board", path=board, layers=2, thickness=1.6)
    outline = await call("add_graphics", path=board, graphics=[
        {"kind": "polygon", "layer": "Edge.Cuts", "points": BAT_OUTLINE},
        {"kind": "circle", "layer": "Edge.Cuts",
         "x": 20, "y": 30, "radius": 1.7},
        {"kind": "circle", "layer": "Edge.Cuts",
         "x": 80, "y": 30, "radius": 1.7},
    ])

    footprints: list[dict[str, Any]] = []
    for i, (x, y) in enumerate(LED_AT, start=1):
        footprints += [
            {"fp_id": LED_FP, "ref": f"D{i}", "x": x, "y": y,
             "value": "AMBER"},
            {"fp_id": RES_FP, "ref": f"R{i}", "x": x, "y": y,
             "rotation": 90, "side": "B", "value": "330R"},
        ]
    footprints.append({"fp_id": POWER_FP, "ref": "J1",
                       "x": 50, "y": 33, "rotation": 90,
                       "side": "B", "value": "5V IN"})
    board_parts = await call("place_footprints", path=board,
                             footprints=footprints)
    pads_of = {
        fp["ref"]: {pad["number"]: pad for pad in fp["pads"]}
        for fp in board_parts.get("footprints", [])
    }
    await call("move_footprint_fields", path=board, moves=[
        {"ref": fp["ref"], "name": "Reference", "dx": 0, "dy": 0,
         "hide": True}
        for fp in board_parts.get("footprints", [])
    ])

    # Apply the schematic's exact net membership; the board invents none.
    pad_nets: list[dict[str, str]] = []
    net_of: dict[str, str] = {}
    for net in nets.get("nets", []):
        for member in net["pins"]:
            key = f"{member['ref']}.{member['pin']}"
            net_of[key] = net["name"]
            pad_nets.append({"ref": member["ref"], "pad": member["pin"],
                             "net": net["name"]})
    await call("set_pad_nets", path=board, pads=pad_nets)

    vias: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for i, (x, y) in enumerate(LED_AT, start=1):
        led = pads_of[f"D{i}"]["2"]
        resistor = pads_of[f"R{i}"]["2"]
        net = net_of[f"D{i}.2"]
        # LED pad 2 is to the right at rotation zero. Keep the short front
        # segment on that side so it never crosses cathode pad 1.
        vx = round(x + 1.8, 3)
        vias.append({"x": vx, "y": y, "net": net,
                     "diameter": 0.65, "drill": 0.3})
        tracks += [
            {"x1": led["x"], "y1": led["y"], "x2": vx, "y2": y,
             "layer": "F.Cu", "width": 0.25, "net": net},
            {"x1": vx, "y1": y, "x2": resistor["x"],
             "y2": resistor["y"], "layer": "B.Cu", "width": 0.25,
             "net": net},
        ]
    await call("add_vias", path=board, vias=vias)
    await call("add_tracks", path=board, tracks=tracks)

    # The zone polygons deliberately extend across the silhouette; KiCad's
    # filler clips them to the exact bat edge and internal cutouts.
    zone_box = [[2, 4], [98, 4], [98, 57], [2, 57]]
    await call("add_zones", path=board, zones=[
        {"points": zone_box, "layer": "F.Cu", "net": GND},
        {"points": zone_box, "layer": "B.Cu", "net": VCC},
    ])

    # Front eye halos and central bat mask; back carries the signal roundel.
    front_art: list[dict[str, Any]] = [
        {"kind": "arc", "layer": "F.SilkS", "width": 0.35,
         "x1": 41, "y1": 18, "xm": 45, "ym": 14, "x2": 49, "y2": 18},
        {"kind": "arc", "layer": "F.SilkS", "width": 0.35,
         "x1": 51, "y1": 18, "xm": 55, "ym": 14, "x2": 59, "y2": 18},
        {"kind": "polygon", "layer": "F.SilkS", "fill": True,
         "points": [[43, 31], [47, 28], [50, 31], [53, 28], [57, 31],
                    [53, 33], [55, 37], [50, 34], [45, 37], [47, 33]]},
        {"kind": "line", "layer": "F.SilkS", "width": 0.3,
         "x1": 31, "y1": 31, "x2": 40, "y2": 33},
        {"kind": "line", "layer": "F.SilkS", "width": 0.3,
         "x1": 69, "y1": 31, "x2": 60, "y2": 33},
    ]
    back_art: list[dict[str, Any]] = [
        {"kind": "circle", "layer": "B.SilkS", "width": 0.45,
         "x": 50, "y": 30, "radius": 14},
        {"kind": "polygon", "layer": "B.SilkS", "fill": True,
         "points": [[36, 30], [42, 26], [46, 28], [48, 24], [50, 28],
                    [52, 24], [54, 28], [58, 26], [64, 30], [57, 31],
                    [60, 36], [53, 33], [50, 38], [47, 33], [40, 36],
                    [43, 31]]},
    ]
    await call("add_graphics", path=board, graphics=front_art + back_art)
    await call("add_board_texts", path=board, texts=[
        {"x": 50, "y": 39, "text": "BAT SIGNAL // 16",
         "layer": "F.SilkS", "size": 0.9},
        {"x": 50, "y": 41, "text": "I AM THE NIGHT",
         "layer": "B.SilkS", "size": 1.1, "mirror": True},
    ])

    await call("save_board", path=board)
    await call("refill_zones", path=board)
    await call("save_board", path=board)
    unrouted = await call("unrouted_connections", path=board)
    drc = await call("check_board", path=board)
    graphics = await call("list_graphics", path=board)

    await call("render_schematic", path=sheet, output_dir=str(OUT))
    await call("render_board", path=board,
               output_file=str(OUT / "batman-top.png"), side="top",
               width=1400, height=900, quality="high")
    await call("render_board", path=board,
               output_file=str(OUT / "batman-bottom.png"), side="bottom",
               width=1400, height=900, quality="high")
    await call("render_board", path=board,
               output_file=str(OUT / "batman-3d.png"), side="top",
               width=1500, height=1000, quality="high",
               rotate="-24,0,22", perspective=True, floor=True, zoom=0.9)

    errors = [finding for finding in drc.get("findings", [])
              if finding.get("severity") == "error"]
    if erc.get("errors") or erc.get("warnings"):
        failures += int(erc.get("errors", 0)) + int(erc.get("warnings", 0))
    if unrouted.get("count"):
        failures += int(unrouted["count"])
    if errors:
        failures += len(errors)
    if outline.get("size") != [92.0, 49.0]:
        failures += 1
        print(f"WRONG board size: {outline.get('size')}")

    took = time.time() - started
    print(f"schematic: 16 LED channels, {len(nets.get('nets', []))} nets; "
          f"ERC {erc.get('errors', '?')}/{erc.get('warnings', '?')}")
    print(f"board: 33 footprints, {len(vias)} vias, {len(tracks)} tracks, "
          f"{graphics.get('count', 0)} graphics")
    print(f"unrouted: {unrouted.get('count', '?')}; "
          f"DRC errors: {len(errors)}")
    print(f"{calls} MCP calls in {took:.1f}s; failures: {failures}")
    return failures


async def main() -> int:
    """Run the complete build against the in-process MCP server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
