"""A sixteen-LED bat signal: schematic and shaped PCB, through MCP only.

Sixteen identical +5V -> resistor -> LED -> GND channels form a bat emblem.
A 250 mA resettable fuse and series Schottky diode protect the 5 V input, while
10 uF bulk and 100 nF ceramic capacitors decouple the protected rail. The LEDs
live on the front and the support circuitry lives on the back. A front GND pour
and back +5V pour complete the power paths.

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
FUSE_FP = "Fuse:Fuse_1206_3216Metric"
DIODE_FP = "Diode_SMD:D_SMA"
BULK_FP = "Capacitor_SMD:C_1206_3216Metric"
DECOUPLING_FP = "Capacitor_SMD:C_0603_1608Metric"

# The placement follows the silhouette: five lights on each wing and six across
# the head and body. Every coordinate is chosen here, never inferred by the API.
LED_AT = [
    (17.0, 21.0), (27.0, 21.0), (36.0, 26.0), (29.0, 29.0), (38.0, 32.0),
    (83.0, 21.0), (73.0, 21.0), (64.0, 26.0), (71.0, 29.0), (62.0, 32.0),
    (46.0, 22.0), (54.0, 22.0), (43.0, 27.0), (57.0, 27.0),
    (44.0, 35.0), (56.0, 35.0),
]

# Clockwise bat silhouette. A polygon is one exact closed contour.
BAT_OUTLINE = [
    [50, 17], [54, 11], [55, 19], [61, 22], [70, 19], [82, 14],
    [96, 10], [91, 25], [88, 31], [84, 28], [78, 29], [72, 35],
    [66, 32], [60, 36], [56, 44], [52, 48], [50, 54], [48, 48],
    [44, 44], [40, 36], [34, 32], [28, 35], [22, 29], [16, 28],
    [12, 31], [9, 25], [4, 10], [18, 14], [30, 19], [39, 22],
    [45, 19], [46, 11],
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
    await call("new_sheet", path=sheet, paper="A4",
               title="BAT SIGNAL // 16 LED beacon")
    parts: list[dict[str, Any]] = []
    for i in range(16):
        col, row = i % 4, i // 4
        x, top = (30 + col * 42) * G, (12 + row * 30) * G
        parts += [
            {"lib_id": "Device:R", "ref": f"R{i + 1}",
             "x": x, "y": top + 7 * G, "value": "330R"},
            {"lib_id": "Device:LED", "ref": f"D{i + 1}",
             "x": x, "y": top + 15 * G, "value": "AMBER",
             "rotation": 90},
        ]
    parts += [
        {"lib_id": "Connector_Generic:Conn_01x02", "ref": "J1",
         "x": 210 * G, "y": 36 * G, "value": "5V IN"},
        {"lib_id": "Device:Fuse", "ref": "F1", "x": 190 * G,
         "y": 36 * G, "value": "250mA PTC", "rotation": 90},
        {"lib_id": "Device:D_Schottky", "ref": "D17", "x": 175 * G,
         "y": 36 * G, "value": "SS14"},
        {"lib_id": "Device:C", "ref": "C1", "x": 180 * G,
         "y": 58 * G, "value": "10uF"},
        {"lib_id": "Device:C", "ref": "C2", "x": 204 * G,
         "y": 58 * G, "value": "100nF"},
    ]
    await call("measure_schematic_placement", path=sheet, parts=parts)
    placed = await call("add_components", path=sheet, parts=parts)
    made = placed.get("parts", [])
    if len(made) != 37:
        print(f"WRONG schematic placements: {len(made)}")
        return failures + 1
    by_ref = {part["ref"]: part for part in made}

    channel_rails: list[dict[str, Any]] = []
    for i in range(16):
        col, row = i % 4, i // 4
        x, top = (30 + col * 42) * G, (12 + row * 30) * G
        channel_rails += [
            {"x": x, "y": top, "net": VCC},
            {"x": x, "y": top + 23 * G, "net": GND, "rotation": 180},
        ]
    # Two isolated rail/flag pairs establish that both global supplies are
    # driven. The connector receives its own visible power-symbol links.
    flag_rails = [
        {"x": 190 * G, "y": 82 * G, "net": VCC},
        {"x": 210 * G, "y": 82 * G, "net": GND},
    ]
    connector = by_ref["J1"]
    fuse = by_ref["F1"]
    protector = by_ref["D17"]
    bulk = by_ref["C1"]
    decoupling = by_ref["C2"]
    j2 = pin(connector, "2")
    protected = pin(protector, "1")
    support_rails = [
        {"x": j2[0] + 10 * G, "y": j2[1], "net": GND,
         "rotation": 270},
        {"x": protected[0] - 10 * G, "y": protected[1], "net": VCC,
         "rotation": 90},
        {"x": pin(bulk, "1")[0], "y": pin(bulk, "1")[1] - 8 * G,
         "net": VCC},
        {"x": pin(bulk, "2")[0], "y": pin(bulk, "2")[1] + 8 * G,
         "net": GND, "rotation": 180},
        {"x": pin(decoupling, "1")[0],
         "y": pin(decoupling, "1")[1] - 8 * G, "net": VCC},
        {"x": pin(decoupling, "2")[0],
         "y": pin(decoupling, "2")[1] + 8 * G,
         "net": GND, "rotation": 180},
    ]
    powered = await call("add_power", path=sheet,
                         symbols=channel_rails + flag_rails + support_rails)
    flags = await call("add_power_flags", path=sheet, flags=[
        {"x": 190 * G, "y": 74 * G},
        {"x": 210 * G, "y": 74 * G},
    ])
    rail_parts = powered.get("symbols", [])
    flag_parts = flags.get("flags", [])

    wires: list[dict[str, float]] = []

    def link(a: dict[str, Any], an: str,
             b: dict[str, Any], bn: str) -> None:
        pa, pb = pin(a, an), pin(b, bn)
        wires.append({"x1": pa[0], "y1": pa[1],
                      "x2": pb[0], "y2": pb[1]})

    for i in range(16):
        resistor, led = made[i * 2:i * 2 + 2]
        supply, ground = rail_parts[i * 2:i * 2 + 2]
        link(supply, "1", resistor, "1")
        link(resistor, "2", led, "2")
        link(led, "1", ground, "1")
    for i in range(2):
        link(flag_parts[i], "1", rail_parts[32 + i], "1")
    link(connector, "1", fuse, "1")
    link(fuse, "2", protector, "2")
    link(protector, "1", rail_parts[35], "1")
    link(connector, "2", rail_parts[34], "1")
    link(bulk, "1", rail_parts[36], "1")
    link(bulk, "2", rail_parts[37], "1")
    link(decoupling, "1", rail_parts[38], "1")
    link(decoupling, "2", rail_parts[39], "1")
    await call("add_wires", path=sheet, wires=wires)
    await call("set_fields", path=sheet, fields=[
        *[{"ref": f"D{i}", "name": "Footprint", "value": LED_FP}
          for i in range(1, 17)],
        *[{"ref": f"R{i}", "name": "Footprint", "value": RES_FP}
          for i in range(1, 17)],
        {"ref": "J1", "name": "Footprint", "value": POWER_FP},
        {"ref": "F1", "name": "Footprint", "value": FUSE_FP},
        {"ref": "D17", "name": "Footprint", "value": DIODE_FP},
        {"ref": "C1", "name": "Footprint", "value": BULK_FP},
        {"ref": "C2", "name": "Footprint", "value": DECOUPLING_FP},
    ])
    await call("add_texts", path=sheet, notes=[{
        "x": 185 * G, "y": 112 * G,
        "text": "GOTHAM BEACON\n16 x AMBER LED\n5 V / 330 ohm / ~150 mA",
        "size": 1.5}])
    await call("save_sheet", path=sheet)
    erc = await call("check_sheet", path=sheet)
    nets = await call("list_nets", path=sheet)

    # -- board -----------------------------------------------------------
    board = str(OUT / "batman.kicad_pcb")
    await call("new_board", path=board, layers=2, thickness=1.6)
    await call("set_stackup", path=board, layers=[
        {"name": "Top Silk Screen", "kind": "Top Silk Screen",
         "color": "White"},
        {"name": "Top Solder Mask", "kind": "Top Solder Mask",
         "color": "Black"},
        {"name": "F.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "dielectric 1", "kind": "core", "thickness": 1.53,
         "material": "FR4", "epsilon_r": 4.2, "loss_tangent": 0.02},
        {"name": "B.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "Bottom Solder Mask", "kind": "Bottom Solder Mask",
         "color": "Black"},
        {"name": "Bottom Silk Screen", "kind": "Bottom Silk Screen",
         "color": "White"},
    ], copper_finish="ENIG")
    outline = await call("add_graphics", path=board, graphics=[
        {"kind": "polygon", "layer": "Edge.Cuts", "points": BAT_OUTLINE},
    ])

    footprints: list[dict[str, Any]] = []
    for i, (x, y) in enumerate(LED_AT, start=1):
        footprints += [
            {"fp_id": LED_FP, "ref": f"D{i}", "x": x, "y": y,
             "value": "AMBER"},
            {"fp_id": RES_FP, "ref": f"R{i}", "x": x, "y": y,
             "rotation": 90, "side": "B", "value": "330R"},
        ]
    footprints += [
        {"fp_id": POWER_FP, "ref": "J1", "x": 51.27, "y": 39,
         "rotation": 270, "side": "B", "value": "5V IN"},
        {"fp_id": FUSE_FP, "ref": "F1", "x": 49.87, "y": 34,
         "side": "B", "value": "250mA PTC"},
        {"fp_id": DIODE_FP, "ref": "D17", "x": 50.47, "y": 28,
         "side": "B", "value": "SS14"},
        {"fp_id": BULK_FP, "ref": "C1", "x": 43, "y": 31,
         "rotation": 90, "side": "B", "value": "10uF"},
        {"fp_id": DECOUPLING_FP, "ref": "C2", "x": 57, "y": 31,
         "rotation": 90, "side": "B", "value": "100nF"},
    ]
    exported = await call(
        "update_board_from_schematic", schematic_path=sheet, board_path=board,
        placements=[{key: value for key, value in fp.items()
                     if key in ("ref", "x", "y", "rotation", "side", "anchor")}
                    for fp in footprints],
    )
    if len(exported["placed"]) != len(footprints):
        raise RuntimeError("schematic export missed a board footprint")
    board_parts = await call("list_footprints", path=board, with_pads=True)
    pads_of = {
        fp["ref"]: {pad["number"]: pad for pad in fp["pads"]}
        for fp in board_parts.get("footprints", [])
    }
    await call("move_footprint_fields", path=board, moves=[
        {"ref": fp["ref"], "name": "Reference", "dx": 0, "dy": 0,
         "hide": True}
        for fp in board_parts.get("footprints", [])
    ])

    # The schematic's exact net membership was applied by export.
    net_of: dict[str, str] = {}
    for net in nets.get("nets", []):
        for member in net["pins"]:
            key = f"{member['ref']}.{member['pin']}"
            net_of[key] = net["name"]

    vias: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for i, (x, y) in enumerate(LED_AT, start=1):
        led = pads_of[f"D{i}"]["2"]
        resistor = pads_of[f"R{i}"]["2"]
        net = net_of[f"D{i}.2"]
        # LED pad 2 is to the right at rotation zero. Keep the short front
        # segment on that side so it never crosses cathode pad 1.
        vx = round(x + 1.8, 3)
        # One 45-degree step takes up the Y offset to the back-side resistor;
        # finish horizontally into the pad. The front run remains straight.
        elbow_x = round(vx - abs(resistor["y"] - y), 3)
        vias.append({"x": vx, "y": y, "net": net,
                     "diameter": 0.65, "drill": 0.3})
        tracks += [
            {"x1": led["x"], "y1": led["y"], "x2": vx, "y2": y,
             "layer": "F.Cu", "width": 0.25, "net": net},
            {"x1": vx, "y1": y, "x2": elbow_x,
             "y2": resistor["y"], "layer": "B.Cu", "width": 0.25,
             "net": net},
            {"x1": elbow_x, "y1": resistor["y"],
             "x2": resistor["x"], "y2": resistor["y"],
             "layer": "B.Cu", "width": 0.25,
             "net": net},
        ]
    # The protected input chain stays on the back. Both capacitor ground pads
    # reach the front ground plane through a nearby via.
    for first_ref, first_pad, second_ref, second_pad in [
        ("J1", "1", "F1", "1"),
        ("F1", "2", "D17", "2"),
    ]:
        first = pads_of[first_ref][first_pad]
        second = pads_of[second_ref][second_pad]
        tracks.append({
            "x1": first["x"], "y1": first["y"],
            "x2": second["x"], "y2": second["y"],
            "layer": "B.Cu", "width": 0.6,
            "net": net_of[f"{first_ref}.{first_pad}"],
        })
    for ref, direction in [("C1", -1.0), ("C2", 1.0)]:
        ground = pads_of[ref]["2"]
        vx = round(ground["x"] + direction * 1.5, 3)
        vias.append({"x": vx, "y": ground["y"], "net": GND,
                     "diameter": 0.65, "drill": 0.3})
        tracks.append({
            "x1": ground["x"], "y1": ground["y"],
            "x2": vx, "y2": ground["y"],
            "layer": "B.Cu", "width": 0.4, "net": GND,
        })
    await call("add_vias", path=board, vias=vias)
    await call("add_tracks", path=board, tracks=tracks,
               track_angle_step=45)

    # Follow the silhouette with a deliberate copper-to-edge inset.
    await call("add_zones", path=board, zones=[
        {"boundary": "board_outline", "inset": 0.6,
         "layer": "F.Cu", "net": GND, "pad_connection": "solid"},
        {"boundary": "board_outline", "inset": 0.6,
         "layer": "B.Cu", "net": VCC, "pad_connection": "solid"},
    ])

    # Keep the silkscreen sparse so the LEDs and silhouette stay legible.
    await call("add_board_texts", path=board, texts=[
        {"x": 50, "y": 31, "text": "GOTHAM // 16",
         "layer": "F.SilkS", "width": 0.9, "height": 0.9,
         "thickness": 0.135},
        {"x": 55, "y": 39, "text": "+5V", "layer": "B.SilkS",
         "width": 0.8, "height": 0.8, "thickness": 0.12, "mirror": True},
        {"x": 45, "y": 41.5, "text": "GND", "layer": "B.SilkS",
         "width": 0.8, "height": 0.8, "thickness": 0.12, "mirror": True},
    ])

    await call("save_board", path=board)
    await call("refill_zones", path=board)
    await call("save_board", path=board)
    unrouted = await call("unrouted_connections", path=board)
    drc = await call("check_board", path=board, track_angle_step=45,
                     schematic_parity=True)
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
    if outline.get("size") != [92.0, 44.0]:
        failures += 1
        print(f"WRONG board size: {outline.get('size')}")

    took = time.time() - started
    print(f"schematic: 16 LED channels, {len(nets.get('nets', []))} nets; "
          f"ERC {erc.get('errors', '?')}/{erc.get('warnings', '?')}")
    print(f"board: 37 footprints, {len(vias)} vias, {len(tracks)} tracks, "
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
