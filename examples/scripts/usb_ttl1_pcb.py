"""A one-channel USB-to-TTL board, laid out through the board primitives.

This is the board counterpart of `usbc_via_mcp.py`: every coordinate here was
chosen by the caller, and the API was asked only for facts. It exists to find
out whether the primitives are enough, so it deliberately uses ALL of them --
including the ones a real layout might skip.

**The server places nothing.** There is no autoplacer here and none in the
library any more. Where each part sits is arithmetic in this file against the
courtyard sizes the API reports; the API's job was to say how big each part is
and where its pads landed once placed and turned.

**Every call goes through MCP.** This script used to reach for the Python API
directly, which meant the tool layer it was supposed to be proving went
untested. It now runs on `Client(mcp)`, so a tool that disagrees with the
object beneath it fails here rather than in front of an agent.

Run it: ``python examples/scripts/usb_ttl1_pcb.py``. It prints what it built
and what it could not, and returns non-zero if the PLACEMENT fails.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

SCHEMATIC = str(Path("examples/usb_ttl4/usb_ttl4.kicad_sch"))

OUT = Path("examples/usb_ttl1_pcb")

#: The channel-1 slice of usb_ttl4: USB-C in, protection, a regulator, the
#: bridge, its crystal, decoupling, and one 6-pin TTL header out.
PARTS = [
    ("J1", "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11", "USB-C"),
    ("F1", "Fuse:Fuse_1206_3216Metric", "500mA"),
    ("D1", "Diode_SMD:D_SOD-323", "SMAJ5.0A"),
    ("U2", "Package_TO_SOT_SMD:SOT-23-5", "AP2112K-3.3"),
    ("C1", "Capacitor_SMD:C_0805_2012Metric", "10u"),
    ("C2", "Capacitor_SMD:C_0805_2012Metric", "10u"),
    ("U1", "Package_QFP:LQFP-64_10x10mm_P0.5mm", "FT4232H"),
    ("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "12MHz"),
    ("C6", "Capacitor_SMD:C_0402_1005Metric", "100n"),
    ("C7", "Capacitor_SMD:C_0402_1005Metric", "100n"),
    ("R1", "Resistor_SMD:R_0402_1005Metric", "12k"),
    ("J2", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", "A TTL"),
]

BOARD_W, BOARD_H = 46.0, 34.0
EDGE = 2.0          # keep courtyards this far inside the outline
GAP = 0.8           # and this far from each other


def overlaps(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float], gap: float) -> bool:
    """Whether two courtyard boxes come within *gap* of one another."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (abs(ax - bx) * 2 < aw + bw + gap * 2
            and abs(ay - by) * 2 < ah + bh + gap * 2)


async def build(client: Client) -> int:
    """Build the board and report what the primitives could and could not do."""
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

    async def maybe(tool: str, **kw: Any) -> dict[str, Any]:
        """A call whose failure is an expected answer, not a fault."""
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        return data if isinstance(data, dict) else {}

    async def place_row(refs: list[str], y: float, x0: float,
                        boxes: dict[str, tuple[float, float, float, float]],
                        board: str) -> float:
        """Lay *refs* left to right at *y*, packed by their real courtyards.

        The caller's own packing, in ten lines, against sizes the API reports.
        This is the thing that used to be `place_board`.
        """
        x = x0
        for ref in refs:
            part = await call("get_footprint", path=board, ref=ref)
            w, h = part["courtyard"]
            # `at` is the footprint ORIGIN, which can sit on pad 1. Place by
            # centre, which is what a courtyard is measured about.
            off = part["courtyard_offset"]
            cx = x + w / 2
            await call("move_footprint", path=board, ref=ref,
                       x=cx - off["x"], y=y - off["y"])
            boxes[ref] = (cx, y, w, h)
            x += w + GAP
        return x

    if OUT.exists():
        shutil.rmtree(OUT)
    gaps: list[str] = []

    board = str(OUT / "usb_ttl1.kicad_pcb")
    made = await call("new_board", path=board, layers=2)
    print(f"created usb_ttl1.kicad_pcb: layers {made.get('layers')}")

    # -- 1. the outline, and a keep-out for the USB shell ------------------
    size = await call("add_outline", path=board, points=[
        [0, 0], [BOARD_W, 0], [BOARD_W, BOARD_H], [0, BOARD_H]])
    w, h = size.get("size", [0, 0])
    print(f"outline {w} x {h} mm")

    # -- 2. what the parts need, before placing any of them ----------------
    print("\nfootprint sizes, from the API:")
    for ref, fp_id, _ in PARTS:
        definition = await maybe("footprint_pads", fp_id=fp_id)
        if definition.get("ok") is not True:
            print(f"  {ref:4s} MISSING {definition.get('error')}")
            return 1
        cw, ch = definition["courtyard"]
        print(f"  {ref:4s} {fp_id.split(':')[-1][:34]:34s} "
              f"{cw:6.2f} x {ch:5.2f}  {definition['pad_count']:2d} pads"
              f"{'  PTH' if definition['has_pth'] else ''}")

    # -- 3. place, then pack into rows the caller chooses ------------------
    for ref, fp_id, value in PARTS:
        await call("place_footprint", path=board, fp_id=fp_id, ref=ref,
                   x=0, y=0, value=value)
    boxes: dict[str, tuple[float, float, float, float]] = {}
    # Four bands the caller chose, each wide enough for its own row. The API
    # supplied every courtyard; the arithmetic is here.
    await place_row(["J1"], 7.5, EDGE + 0.5, boxes, board)
    await place_row(["F1", "D1"], 15.0, EDGE + 0.5, boxes, board)
    await place_row(["U2", "C1"], 19.5, EDGE + 0.5, boxes, board)
    await place_row(["C2", "R1"], 24.5, EDGE + 0.5, boxes, board)
    await place_row(["C6", "C7"], 29.0, EDGE + 0.5, boxes, board)
    await place_row(["U1"], 10.5, 14.0, boxes, board)
    await place_row(["Y1"], 21.0, 16.0, boxes, board)
    await place_row(["J2"], 12.0, 40.0, boxes, board)

    # -- 4. the nets, from the schematic -----------------------------------
    #
    # A library footprint carries no nets. Which pad is on which net is a fact
    # the SCHEMATIC holds, so read it from there and apply it here: the two
    # contracts compose and neither knows the other's format.
    listed = await call("list_footprints", path=board)
    placed = {f["ref"] for f in listed.get("footprints", [])}
    assigned = skipped = 0
    for net in (await call("list_nets", path=SCHEMATIC)).get("nets", []):
        for p in net["pins"]:
            if p["ref"] not in placed:
                continue
            got = await maybe("set_pad_net", path=board, ref=p["ref"],
                              pad=p["pin"], net=net["name"])
            if got.get("ok") is True:
                assigned += 1
            else:
                skipped += 1
    print(f"\nnets from {Path(SCHEMATIC).name}: {assigned} pads assigned, "
          f"{skipped} pad numbers the footprint does not have")

    # -- 5. did the caller's packing actually work? ------------------------
    clashes = [(a, b) for i, a in enumerate(boxes)
               for b in list(boxes)[i + 1:]
               if overlaps(boxes[a], boxes[b], GAP)]
    off_board = [r for r, (x, y, bw, bh) in boxes.items()
                 if x - bw / 2 < EDGE or x + bw / 2 > BOARD_W - EDGE
                 or y - bh / 2 < EDGE or y + bh / 2 > BOARD_H - EDGE]
    print(f"\nplaced {len(boxes)} parts: {len(clashes)} courtyard clashes, "
          f"{len(off_board)} off board")
    for a, b in clashes:
        print(f"  CLASH {a}/{b}")
    for r in off_board:
        x, y, bw, bh = boxes[r]
        print(f"  OFF   {r}: x {x - bw / 2:.1f}..{x + bw / 2:.1f}, "
              f"y {y - bh / 2:.1f}..{y + bh / 2:.1f} "
              f"(board {EDGE}..{BOARD_W - EDGE} x {EDGE}..{BOARD_H - EDGE})")

    # -- 6. copper: a track, a via, and the pad positions to aim at --------
    #
    # Wire VBUS from the USB connector to the fuse, by hand, using the pad
    # positions the API reports.
    #
    # The net NAME comes from the board, not from memory: the schematic calls
    # it "/VBUS", and a track laid on "VBUS" is quietly a second, different
    # net that DRC then reports as shorting the first.
    board_nets = (await call("list_board_nets", path=board)).get("nets", [])
    vbus = next((n["name"] for n in board_nets if n["name"].endswith("VBUS")), "")
    gnd = next((n["name"] for n in board_nets if n["name"].endswith("GND")), "GND")
    a = await call("get_pad", path=board, ref="J1", pad="A4")
    z = await call("get_pad", path=board, ref="F1", pad="1")
    mid_x = (a["x"] + z["x"]) / 2
    # All on the front: the back is a solid ground pour, and copper laid into
    # it on another net is a short, not a route.
    for x1, y1, x2, y2 in ((a["x"], a["y"], mid_x, a["y"]),
                           (mid_x, a["y"], mid_x, z["y"]),
                           (mid_x, z["y"], z["x"], z["y"])):
        await call("add_track", path=board, x1=x1, y1=y1, x2=x2, y2=y2,
                   layer="F.Cu", width=0.4, net=vbus)
    # One via anyway, to exercise the primitive and stitch the pour.
    await call("add_via", path=board, x=BOARD_W - 4.0, y=BOARD_H - 4.0,
               net=gnd, diameter=0.6, drill=0.3)
    copper = await call("list_copper", path=board)
    print(f"\nrouted {vbus} J1.A4 -> F1.1 with "
          f"{len(copper.get('tracks', []))} segments and "
          f"{len(copper.get('vias', []))} via")

    # -- 7. a ground pour on the back, and a keep-out under the crystal ----
    await call("add_zone", path=board,
               points=[[1, 1], [BOARD_W - 1, 1], [BOARD_W - 1, BOARD_H - 1],
                       [1, BOARD_H - 1]], layer="B.Cu", net="GND")
    y1 = await call("get_footprint", path=board, ref="Y1")
    kw, kh = y1["courtyard"]
    cx, cy = y1["x"], y1["y"]
    await call("add_zone", path=board,
               points=[[cx - kw / 2, cy - kh / 2], [cx + kw / 2, cy - kh / 2],
                       [cx + kw / 2, cy + kh / 2], [cx - kw / 2, cy + kh / 2]],
               layer="B.Cu", forbids=["tracks", "vias", "pours"])
    await call("add_board_text", path=board, x=BOARD_W / 2, y=BOARD_H - 1.2,
               text="USB-TTL ch1", layer="F.SilkS", size=1.2)
    zones = (await call("list_copper", path=board)).get("zones", [])
    print(f"zones: {len(zones)} "
          f"({sum(1 for z in zones if z.get('forbids'))} keep-out)")

    # -- 8. read it back ---------------------------------------------------
    left = await call("unrouted_connections", path=board)
    print(f"\nnets {len(board_nets)}, unrouted connections "
          f"{left.get('count')}")
    for c in sorted(left.get("connections", []),
                    key=lambda c: c["distance"])[:4]:
        print(f"  {c['net']:10s} {c['from']['ref']}.{c['from']['pad']} -> "
              f"{c['to']['ref']}.{c['to']['pad']} {c['distance']:5.1f} mm")
    here = await call("what_is_on_board", path=board, x=a["x"], y=a["y"],
                      radius=0.05)
    print(f"at J1.A4: {len(here.get('pads', []))} pad(s), "
          f"{len(here.get('track_ends', []))} track end(s)")

    await call("save_board", path=board)

    # -- 9. what the tool says ---------------------------------------------
    filled = await call("refill_zones", path=board)
    drc = await call("check_board", path=board)
    findings = drc.get("findings", [])
    errors = [f for f in findings if f["severity"] == "error"]
    print(f"\nrefilled {filled.get('filled')} zone(s)")
    print(f"DRC: {len(errors)} errors, "
          f"{len(findings) - len(errors)} other findings")
    for f in errors[:6]:
        where = f" at {f['ref']}.{f.get('pad', '')}" if f.get("ref") else ""
        print(f"  {f['kind']}{where}: {f['message'][:70]}")

    await call("render_board", path=board,
               output_file=str(OUT / "usb_ttl1-top.png"), side="top")
    print("rendered usb_ttl1-top.png")

    # -- 10. what the API could not do --------------------------------------
    if gaps:
        print("\nAPI gaps hit while building this:")
        for g in gaps:
            print(f"  - {g}")
    # The gate is the PLACEMENT, not DRC. This board is deliberately left
    # unrouted, so DRC is information, not a pass.
    return failures + (1 if (clashes or off_board) else 0)


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
