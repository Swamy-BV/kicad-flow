"""One LED schematic, six boards of it, to find where placement runs out.

The board before this one asked whether the primitives could lay out a real
circuit. They could. This one asks a harder question: **how much freedom does
a caller actually have?** So it builds the same five-channel LED circuit six
different ways and measures what each one cost.

    1..5   the same channels packed N at a time, tightening the gap each
           time, to find how close two parts can be put
    6      four LEDs in the corners at four rotations, resistors on the BACK
           joined by vias, and designators moved around the board

Everything is SMT: 0603 LEDs, 0402 resistors. Nothing here decides anything --
every coordinate is arithmetic in this file against sizes the API reports.

**Every call goes through MCP.** This script used to reach for the Python API
directly, which made it a test of `Board` and not of the surface an agent
actually talks to. Both halves now go through `Client(mcp)`, so a tool that
answers differently from the object beneath it fails here.

Run it: ``python examples/scripts/led_matrix_pcb.py``. It prints what each
board cost and, at the end, what the API would not let it do.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/led_matrix")
CHANNELS = 5
LED_FP = "LED_SMD:LED_0603_1608Metric"
RES_FP = "Resistor_SMD:R_0402_1005Metric"
HDR_FP = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"

#: What building this needed that the primitives do not offer. Each was hit
#: writing the code above, and each is worked around in it.
GAPS = [
    "place_footprint/move_footprint take the footprint ORIGIN, which can sit "
    "on pad 1. Every caller here adds courtyard_offset by hand to place by "
    "centre; an anchor argument would carry that fact instead of each caller "
    "repeating it.",
    "No next_ref on the board side: a board cannot name a part it adds, so "
    "every reference here is hard-coded. The schematic side has one.",
    "No texts() readback. add_board_text writes a legend and nothing can read "
    "it, so a caller cannot check its own silkscreen for overlap.",
    "No way to set design rules. check_board reports violations against rules "
    "nothing on the contract can set -- clearance, track width, via size all "
    "come from whatever the board was created with.",
    "unrouted_connections is O(pads^2) per net. Fine at 7 nets; a ground net "
    "with 60 pads would be felt.",
]

Box = tuple[str, float, float, float, float]


def spacing(footprints: list[dict[str, Any]], refs: list[str]) -> float:
    """The closest two courtyards come on this board, in mm.

    Negative means they overlap. This is the number the exercise is about:
    how tightly the caller was allowed to pack.
    """
    boxes: list[Box] = [
        (f["ref"], f["x"] + f["courtyard_offset"]["x"],
         f["y"] + f["courtyard_offset"]["y"], *f["courtyard"])
        for f in footprints if f["ref"] in refs
    ]
    closest = 1e9
    for i, (_, ax, ay, aw, ah) in enumerate(boxes):
        for _, bx, by, bw, bh in boxes[i + 1:]:
            dx = abs(ax - bx) - (aw + bw) / 2
            dy = abs(ay - by) - (ah + bh) / 2
            closest = min(closest, max(dx, dy))
    return round(closest, 3) if boxes else 0.0


async def build(client: Client) -> int:
    """Build the six boards through MCP and report what placement cost."""
    failures = 0

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            why = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool} {kw.get('ref', '')}: {why}")
            failures += 1
            return {}
        return data

    def pin(part: dict[str, Any], number: str) -> dict[str, float]:
        """One pin of a placed symbol, by number."""
        for p in part.get("pins", []):
            if p["number"] == number:
                return p
        raise KeyError(f"{part.get('ref')} has no pin {number}")

    # -- the schematic -----------------------------------------------------
    async def build_schematic(path: str) -> None:
        """Five LEDs, each with a series resistor, between VCC and GND."""
        await call("new_sheet", path=path, title="LED matrix -- 5 channels")
        j1 = await call("add_component", path=path,
                        lib_id="Connector:Conn_01x02_Pin", ref="J1",
                        x=40.64, y=63.5, value="PWR")
        await call("set_field", path=path, ref="J1", name="Footprint",
                   value=HDR_FP)
        a, b = pin(j1, "1"), pin(j1, "2")
        vcc = await call("add_power", path=path, x=a["x"] + 10.16,
                         y=a["y"] - 5.08, net="VCC")
        vp = pin(vcc, "1")
        await call("add_wire", path=path, x1=a["x"], y1=a["y"],
                   x2=vp["x"], y2=a["y"])
        await call("add_wire", path=path, x1=vp["x"], y1=a["y"],
                   x2=vp["x"], y2=vp["y"])
        gnd = await call("add_power", path=path, x=b["x"] + 10.16,
                         y=b["y"] + 5.08, net="GND")
        gp = pin(gnd, "1")
        await call("add_wire", path=path, x1=b["x"], y1=b["y"],
                   x2=gp["x"], y2=b["y"])
        await call("add_wire", path=path, x1=gp["x"], y1=b["y"],
                   x2=gp["x"], y2=gp["y"])
        await call("add_power_flag", path=path, x=a["x"] - 5.08,
                   y=a["y"] - 2.54)
        await call("add_wire", path=path, x1=a["x"] - 5.08, y1=a["y"] - 2.54,
                   x2=a["x"] - 5.08, y2=a["y"])
        await call("add_wire", path=path, x1=a["x"] - 5.08, y1=a["y"],
                   x2=a["x"], y2=a["y"])
        await call("add_power_flag", path=path, x=b["x"] - 5.08,
                   y=b["y"] + 2.54, rotation=180)
        await call("add_wire", path=path, x1=b["x"] - 5.08, y1=b["y"] + 2.54,
                   x2=b["x"] - 5.08, y2=b["y"])
        await call("add_wire", path=path, x1=b["x"] - 5.08, y1=b["y"],
                   x2=b["x"], y2=b["y"])

        # `Device:R` is drawn VERTICALLY in the library and `Device:LED`
        # horizontally, so the same rotation points them different ways -- and
        # an LED's pins sit 0.33 mm off its own origin. Neither is guessed
        # here: each part is placed, its pins are read back from the reply,
        # and the chain is wired to whichever pin actually came out on top.
        for n in range(1, CHANNELS + 1):
            x = 76.2 + (n - 1) * 25.4
            res = await call("add_component", path=path, lib_id="Device:R",
                             ref=f"R{n}", x=x, y=63.5, value="330")
            led = await call("add_component", path=path, lib_id="Device:LED",
                             ref=f"D{n}", x=x, y=88.9, value="RED",
                             rotation=270)
            await call("set_field", path=path, ref=f"R{n}", name="Footprint",
                       value=RES_FP)
            await call("set_field", path=path, ref=f"D{n}", name="Footprint",
                       value=LED_FP)
            r_top, r_bot = sorted(res["pins"], key=lambda p: p["y"])
            d_top, d_bot = sorted(led["pins"], key=lambda p: p["y"])
            top = await call("add_power", path=path, x=r_top["x"],
                             y=r_top["y"] - 7.62, net="VCC")
            await call("add_wire", path=path, x1=r_top["x"], y1=r_top["y"],
                       x2=r_top["x"], y2=pin(top, "1")["y"])
            # The two are not on the same column -- the LED's origin is
            # offset -- so the link between them turns once.
            mid = d_top["y"] - 2.54
            await call("add_wire", path=path, x1=r_bot["x"], y1=r_bot["y"],
                       x2=r_bot["x"], y2=mid)
            await call("add_wire", path=path, x1=r_bot["x"], y1=mid,
                       x2=d_top["x"], y2=mid)
            await call("add_wire", path=path, x1=d_top["x"], y1=mid,
                       x2=d_top["x"], y2=d_top["y"])
            bottom = await call("add_power", path=path, x=d_bot["x"],
                                y=d_bot["y"] + 7.62, net="GND")
            await call("add_wire", path=path, x1=d_bot["x"], y1=d_bot["y"],
                       x2=d_bot["x"], y2=pin(bottom, "1")["y"])
        await call("save_sheet", path=path)

    # -- boards 1..5 -------------------------------------------------------
    async def channel_board(n: int, gap: float) -> tuple[str, float, list[float]]:
        """Board with *n* LED channels in a row, packed *gap* mm apart."""
        path = str(OUT / f"leds_{n}.kicad_pcb")
        await call("new_board", path=path, layers=2)
        width = 6.0 + n * 6.0
        size = await call("add_outline", path=path,
                          points=[[0, 0], [width, 0], [width, 16.0], [0, 16.0]])
        j1 = await call("place_footprint", path=path, fp_id=HDR_FP, ref="J1",
                        x=3.0, y=8.0, rotation=90)
        # Start clear of the header's own courtyard rather than at a guessed x.
        x = j1["x"] + j1["courtyard_offset"]["x"] + j1["courtyard"][0] / 2 + gap
        for i in range(1, n + 1):
            led = await call("place_footprint", path=path, fp_id=LED_FP,
                             ref=f"D{i}", x=0, y=0, value="RED")
            res = await call("place_footprint", path=path, fp_id=RES_FP,
                             ref=f"R{i}", x=0, y=0, value="330")
            lw, rw = led["courtyard"][0], res["courtyard"][0]
            await call("move_footprint", path=path, ref=f"D{i}",
                       x=x + lw / 2, y=6.0)
            await call("move_footprint", path=path, ref=f"R{i}",
                       x=x + rw / 2, y=11.0)
            x += max(lw, rw) + gap
        fps = (await call("list_footprints", path=path)).get("footprints", [])
        return path, spacing(fps, [f["ref"] for f in fps]), size.get("size", [])

    # -- board 6 -----------------------------------------------------------
    async def corner_board() -> str:
        """Four LEDs in the corners, resistors on the back, labels moved."""
        path = str(OUT / "leds_corner.kicad_pcb")
        await call("new_board", path=path, layers=2)
        w = h = 24.0
        await call("add_outline", path=path,
                   points=[[0, 0], [w, 0], [w, h], [0, h]])
        # LEDs at the four corners, each turned to face outward. Any angle: a
        # board is not on a 90-degree grid, so 45 is as ordinary as 90.
        corners = [(5.0, 5.0, 45.0), (w - 5.0, 5.0, 135.0),
                   (w - 5.0, h - 5.0, 225.0), (5.0, h - 5.0, 315.0)]
        for i, (cx, cy, rot) in enumerate(corners, start=1):
            await call("place_footprint", path=path, fp_id=LED_FP, ref=f"D{i}",
                       x=cx, y=cy, rotation=rot, value="RED")
            # The series resistor goes on the BACK, directly under its LED,
            # and is reached with a via on each side.
            await call("place_footprint", path=path, fp_id=RES_FP, ref=f"R{i}",
                       x=cx, y=cy + 2.0, rotation=rot, side="B", value="330")
        await call("place_footprint", path=path, fp_id=HDR_FP, ref="J1",
                   x=w / 2, y=h / 2, rotation=90)
        return path

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    sheet = str(OUT / "led_matrix.kicad_sch")
    await build_schematic(sheet)
    report = await call("check_sheet", path=sheet)
    parts = await call("list_components", path=sheet)
    nets = await call("list_nets", path=sheet)
    errors = int(report.get("errors") or 0)
    print(f"schematic: {parts.get('count')} parts, {nets.get('count')} nets, "
          f"{errors} ERC errors")
    for f in report.get("findings", [])[:5]:
        if f["severity"] == "error":
            print(f"  {f['severity']} {f['kind']} "
                  f"{f.get('ref', '')}.{f.get('pin', '')}")

    # -- boards 1..5: the same channels, packed tighter each time ----------
    print("\nboards 1-5, tightening the gap:")
    print(f"  {'n':>2s} {'gap asked':>9s} {'closest':>8s} {'size':>12s} "
          f"{'parts':>5s}")
    for n in range(1, CHANNELS + 1):
        asked = 1.0 - (n - 1) * 0.2          # 1.0, 0.8, 0.6, 0.4, 0.2 mm
        path, closest, size = await channel_board(n, asked)
        await call("save_board", path=path)
        count = (await call("list_footprints", path=path)).get("count", 0)
        print(f"  {n:2d} {asked:9.2f} {closest:8.3f} "
              f"{size[0]:5.1f} x {size[1]:4.1f} {count:5d}")

    # -- board 6: orientations, both sides, vias, moved designators --------
    print("\nboard 6: corners, two sides, vias")
    board = await corner_board()

    # nets, then the copper that realises them
    for i in range(1, 5):
        await call("set_pad_net", path=board, ref=f"D{i}", pad="1",
                   net="VCC_LED")
        await call("set_pad_net", path=board, ref=f"D{i}", pad="2", net=f"K{i}")
        await call("set_pad_net", path=board, ref=f"R{i}", pad="1", net=f"K{i}")
        await call("set_pad_net", path=board, ref=f"R{i}", pad="2", net="GND")
    await call("set_pad_net", path=board, ref="J1", pad="1", net="VCC_LED")
    await call("set_pad_net", path=board, ref="J1", pad="2", net="GND")

    vias = 0
    for i in range(1, 5):
        # LED cathode on the front, resistor pad on the back: one via each,
        # placed at the pad the API reports rather than where it "should" be.
        k = await call("get_pad", path=board, ref=f"D{i}", pad="2")
        r = await call("get_pad", path=board, ref=f"R{i}", pad="1")
        await call("add_via", path=board, x=k["x"], y=k["y"], net=f"K{i}",
                   diameter=0.6, drill=0.3)
        await call("add_track", path=board, x1=k["x"], y1=k["y"],
                   x2=r["x"], y2=r["y"], layer="B.Cu", width=0.3, net=f"K{i}")
        vias += 1
    fps = (await call("list_footprints", path=board)).get("footprints", [])
    print(f"  {len(fps)} parts, {vias} vias, closest courtyard "
          f"{spacing(fps, [f['ref'] for f in fps])} mm")

    # Designators moved around -- the thing this board exists to test.
    # A library places these and cannot know what ends up beside them; here
    # every LED is at a different angle, so each label needs its own home.
    for i, (dx, dy, rot) in enumerate(
            [(-2.4, -2.4, 0.0), (2.4, -2.4, 0.0),
             (2.4, 2.4, 0.0), (-2.4, 2.4, 0.0)], start=1):
        await call("move_footprint_field", path=board, ref=f"D{i}",
                   name="Reference", dx=dx, dy=dy, rotation=rot)
        # The value would print on top of the part at this size; keep it for
        # the fab and take it off the silkscreen.
        await call("move_footprint_field", path=board, ref=f"D{i}",
                   name="Value", dx=0.0, dy=0.0, hide=True)
    # The header's designator lands on its own pads. Move it clear.
    await call("move_footprint_field", path=board, ref="J1", name="Reference",
               dx=0.0, dy=-3.6, rotation=0.0)
    await call("move_footprint_field", path=board, ref="J1", name="Value",
               dx=0.0, dy=3.6)
    for i in range(1, 5):
        await call("move_footprint_field", path=board, ref=f"R{i}",
                   name="Value", dx=0.0, dy=0.0, hide=True)
    await call("add_board_text", path=board, x=12.0, y=9.0, text="LED x4",
               layer="F.SilkS", size=1.2)
    await call("add_board_text", path=board, x=12.0, y=13.5,
               text="back: R1-R4", layer="B.SilkS", size=0.8, mirror=True)

    await call("add_zone", path=board,
               points=[[0.5, 0.5], [23.5, 0.5], [23.5, 23.5], [0.5, 23.5]],
               layer="B.Cu", net="GND")
    await call("save_board", path=board)
    await call("refill_zones", path=board)
    drc = await call("check_board", path=board)
    bad = [f for f in drc.get("findings", []) if f["severity"] == "error"]
    left = await call("unrouted_connections", path=board)
    fps = (await call("list_footprints", path=board)).get("footprints", [])
    front = sum(1 for f in fps if f["side"] == "F")
    back = sum(1 for f in fps if f["side"] == "B")
    copper = await call("list_copper", path=board)
    print(f"  {front} on the front, {back} on the back, "
          f"{left.get('count')} unrouted, {len(bad)} DRC errors, "
          f"{len(copper.get('zones', []))} zone(s)")
    await call("render_board", path=board,
               output_file=str(OUT / "leds_corner-top.png"), side="top")
    await call("render_board", path=board,
               output_file=str(OUT / "leds_corner-bottom.png"), side="bottom")
    print("  rendered leds_corner-top.png and the back")

    if GAPS:
        print("\nwhat the API would not do:")
        for g in GAPS:
            print(f"  - {g}")
    return failures


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
