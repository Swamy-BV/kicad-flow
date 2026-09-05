"""0-9 in LEDs: eleven sheets and a two-layer board, built through MCP only.

The example this replaces called the Python API directly. This one makes every
change through an **MCP tool call** -- the same surface an agent talks to -- so
it is the end-to-end check that the tool layer can build a real design and not
just answer questions about one. Roughly 3,000 calls, at ~180/s in process.

The design is ten 5 x 7 numerals, sixteen lit cells in each, so every digit is
the same sixteen-channel circuit and the whole thing is ten of them: 160 LEDs,
160 resistors, 320 parts.

    schematic   a root of ten boxes, one child sheet per numeral. Each child
                draws its sixteen channels as +3V3 -> R -> LED -> GND. Power is
                global, so no sheet needs a port and the root is only boxes.

    board       LEDs on the front in the bitmap grid, each resistor on the back
                directly beneath its LED, and the two pours on opposite faces:

                    F.Cu   GND     under the LEDs -- a cathode sits in the pour
                    B.Cu   +3V3    under the resistors -- so does a supply pad

                which leaves exactly one thing to route per channel: the LED
                anode down to its resistor. One via and two short tracks, 160
                times. Nothing else is routed because nothing else has to be.

**The nets come from the schematic, not from here.** `list_nets` on the root
says which pads share a net; `set_pad_net` applies it. The board never invents
a name -- that is the composition the two contracts exist for.

Nothing here decides anything. The grid, the digit shapes and the board size
are arithmetic in this file; the tools were asked how big each part is, where
its pins and pads landed, and to put them where they were told.

Run it: ``python examples/scripts/led_digits.py``
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

OUT = Path("examples/led_digits")
VCC, GND = "+3V3", "GND"

#: Ten numerals as 5 x 7 bitmaps, sixteen lit cells in every one -- so each
#: digit is the same sixteen-channel circuit.
DIGITS: dict[str, tuple[str, ...]] = {
    "0": (" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "1": ("  ## ", "  ## ", "  ## ", "  ## ", "  ## ", "  ## ", "#### "),
    "2": (" ### ", "#   #", "   ##", "  ## ", " #   ", "#    ", "#####"),
    "3": ("#### ", "    #", "    #", " ####", "    #", "    #", "#### "),
    "4": ("   # ", "  ## ", " # # ", "#  # ", "#####", "  ## ", "  ## "),
    "5": ("#### ", "#    ", "#### ", "    #", "    #", "#   #", " ### "),
    "6": (" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### "),
    "7": ("#####", "   ##", "  ## ", "  ## ", " ##  ", " #   ", "##   "),
    "8": (" ### ", "#   #", "#   #", " ##  ", "#   #", "#   #", " ### "),
    "9": (" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ### "),
}

# -- schematic geometry, all multiples of the 1.27 mm grid -----------------
G = 1.27
COL_X = [24 * G + i * 36 * G for i in range(8)]   # 8 channels across
ROW_Y = [30 * G, 74 * G]                          # 2 rows of them
#: Offsets down one channel from its +3V3 symbol. Measured, not assumed:
#: `Device:R` at rotation 0 puts pin 1 above pin 2, and `Device:LED` at
#: rotation 90 puts the ANODE above the cathode -- which is the way the
#: current has to run, so 90 is the rotation and not 270.
R_DY, D_DY, GND_DY = 9 * G, 19 * G, 27 * G
LED_ROT = 90

# -- board geometry --------------------------------------------------------
PITCH = 3.6              # LED grid pitch, chosen against the 0603 courtyard
COLS, ROWS = 5, 7
ACROSS = 5               # numerals per row of the board
DIGIT_W = (COLS - 1) * PITCH + 5.6
DIGIT_H = (ROWS - 1) * PITCH + 6.4
MARGIN = 5.0
BOARD_W = ACROSS * DIGIT_W + 2 * MARGIN
BOARD_H = 2 * DIGIT_H + 2 * MARGIN
VIA_DX = 1.8             # via sits clear of the LED courtyard, to its right
#: ``(diameter, drill)`` per digit, so the board carries ten via sizes rather
#: than one. Same net and same geometry each time -- only the hole changes --
#: which is what makes DRC's answer about them worth reading. All ten are
#: inside the rules the board was created with; the one that is NOT is tried
#: separately, on a scratch board, because it is a deliberate failure.
VIA_SIZES = [(0.60, 0.30), (0.65, 0.30), (0.70, 0.35), (0.75, 0.35),
             (0.70, 0.40), (0.60, 0.35), (0.65, 0.35), (0.70, 0.30),
             (0.75, 0.40), (0.75, 0.30)]
#: The sweep is how the ceiling was found. At a 3.6 mm pitch with the via
#: 1.8 mm off centre, 0.90 mm leaves 0.125 mm to the neighbouring LED pad and
#: 0.80 mm leaves 0.175 mm, both against a 0.2 mm rule -- 14 clearance errors
#: each time. 0.75 is the largest that fits, and it fits by 0.025 mm. Guessing
#: would not have found that; only DRC on a board with ten sizes on it did.

LED_FP = "LED_SMD:LED_0603_1608Metric"
RES_FP = "Resistor_SMD:R_0402_1005Metric"


def channels() -> list[tuple[int, str, int, int]]:
    """``(index, digit, col, row)`` for all 160 lit cells, in order."""
    out = []
    for digit, rows in DIGITS.items():
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                if ch == "#":
                    out.append((len(out), digit, c, r))
    return out


def cell_xy(digit: str, col: int, row: int) -> tuple[float, float]:
    """Where one lit cell sits on the board, in mm."""
    d = list(DIGITS).index(digit)
    dx = MARGIN + (d % ACROSS) * DIGIT_W
    dy = MARGIN + (d // ACROSS) * DIGIT_H
    return (round(dx + 2.8 + col * PITCH, 3), round(dy + 3.2 + row * PITCH, 3))


async def build(client: Client) -> int:
    """Build both halves and report. Returns a non-zero failure count."""
    failures = 0
    calls = 0
    used_tools: set[str] = set()

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures, calls
        calls += 1
        used_tools.add(tool)
        if tool == "batch":
            used_tools.update(str(op.get("tool")) for op in kw.get("ops", [])
                              if isinstance(op, dict) and op.get("tool"))
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            why = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool} {kw.get('ref', kw.get('net', ''))}: {why}")
            failures += 1
            return {}
        return data

    def pin(part: dict[str, Any], number: str) -> tuple[float, float]:
        """One pin of a placed part, by number."""
        for p in part["pins"]:
            if p["number"] == number:
                return (p["x"], p["y"])
        raise KeyError(f"{part.get('ref')} has no pin {number}")

    if OUT.exists():
        shutil.rmtree(OUT)
    started = time.time()

    # -- 1. the root: ten boxes and nothing else ---------------------------
    root = str(OUT / "led_digits.kicad_sch")
    await call("new_sheet", path=root, paper="A4",
               title="0-9 in LEDs -- root")
    boxes: dict[str, dict[str, Any]] = {}
    for i, digit in enumerate(DIGITS):
        got = await call("add_sheets", path=root, sheets=[{
            "name": f"Digit {digit}", "filename": f"digit_{digit}.kicad_sch",
            "x": 25.4 + (i % 5) * 50.8, "y": 38.1 + (i // 5) * 50.8,
            "width": 38.1, "height": 33.02, "ports": []}])
        if got:
            boxes[digit] = got["sheets"][0]
    if failures:
        return failures
    await call("save_sheet", path=root)
    print(f"root: {len(boxes)} child sheets, no ports "
          f"(power is global, so the boxes carry no signals)")

    # -- 2. one child per numeral, sixteen channels each -------------------
    #
    # Two batches per sheet, not 144 calls. The split is forced and is the
    # right one anyway: every wire below is drawn to a coordinate that
    # `add_component` and `add_power` REPORT, so the parts have to land and
    # answer before the wires can be computed.
    for digit, rows in DIGITS.items():
        child = str(OUT / f"digit_{digit}.kicad_sch")
        await call("new_sheet", path=child, paper="A3", title=f"Digit {digit}",
                   instance_path=boxes[digit]["instance_path"])
        base = list(DIGITS).index(digit) * 16
        lit = [(c, r) for r, line in enumerate(rows)
               for c, ch in enumerate(line) if ch == "#"]

        # phase 1: everything that occupies a position. Two calls, because
        # parts and power symbols are different things -- 32 parts in one and
        # 32 rails in the other, rather than 64 round trips.
        parts: list[dict[str, Any]] = []
        rails: list[dict[str, Any]] = []
        for i in range(len(lit)):
            x, top, n = COL_X[i % 8], ROW_Y[i // 8], base + i + 1
            parts += [
                {"lib_id": "Device:R", "ref": f"R{n}",
                 "x": x, "y": top + R_DY, "value": "330R"},
                {"lib_id": "Device:LED", "ref": f"D{n}", "x": x,
                 "y": top + D_DY, "value": "RED", "rotation": LED_ROT},
            ]
            rails += [
                {"x": x, "y": top, "net": VCC},
                {"x": x, "y": top + GND_DY, "net": GND, "rotation": 180},
            ]
        if digit == "0":
            # ERC has no way to know a rail is fed: every power symbol on
            # +3V3 and GND is an INPUT, and a net of only inputs reads as
            # undriven. Two flags cover the design, since both nets are global.
            # Below the channels, not beside them: at x=8G they printed on the
            # sheet border, which the render showed and ERC did not.
            for i, net in enumerate((VCC, GND)):
                rails.append({"x": (24 + i * 30) * G, "y": 126 * G,
                              "net": net})
        made = await call("add_components", path=child, parts=parts)
        powered = await call("add_power", path=child, symbols=rails)
        if not (made and powered):
            continue
        got_parts, got_rails = made["parts"], powered["symbols"]
        flags: list[dict[str, Any]] = []
        if digit == "0":
            f = await call("add_power_flags", path=child, flags=[
                {"x": (24 + i * 30) * G, "y": 118 * G} for i in range(2)])
            flags = f["flags"] if f else []

        # phase 2: the wires, aimed at the pins phase 1 reported
        wires: list[dict[str, Any]] = []
        labels: list[dict[str, Any]] = []

        def link(a: dict[str, Any], an: str, b: dict[str, Any], bn: str,
                 into: list[dict[str, Any]] = wires) -> None:
            """One wire between two pins the server just located."""
            pa, pb = pin(a, an), pin(b, bn)
            into.append({"x1": pa[0], "y1": pa[1], "x2": pb[0], "y2": pb[1]})

        for i, (col, row) in enumerate(lit):
            res, led = got_parts[i * 2:i * 2 + 2]
            up, dn = got_rails[i * 2:i * 2 + 2]
            # +3V3 -> R.1, R.2 -> LED anode(2), LED cathode(1) -> GND.
            link(up, "1", res, "1")
            link(res, "2", led, "2")
            link(led, "1", dn, "1")
            # The cell this channel lights, so the board can be read back
            # against the schematic rather than against this file.
            labels.append({"x": COL_X[i % 8],
                           "y": ROW_Y[i // 8] + D_DY - 4 * G,
                           "text": f"D{digit}_{col}{row}", "kind": "local"})
        for k, flag in enumerate(flags):        # each PWR_FLAG to its rail
            link(flag, "1", got_rails[len(lit) * 2 + k], "1")
        await call("add_wires", path=child, wires=wires)
        await call("add_labels", path=child, labels=labels)
        await call("save_sheet", path=child)
    print(f"sheets: 10 children x 16 channels = "
          f"{len(DIGITS) * 16} LEDs and as many resistors")

    report = await call("check_sheet", path=root)
    nets = await call("list_nets", path=root)
    print(f"ERC on the root: {report.get('errors', '?')} errors, "
          f"{report.get('warnings', '?')} warnings; "
          f"{len(nets.get('nets', []))} nets")

    # -- 3. the board ------------------------------------------------------
    #
    # Three batches for the whole board, on the same two-phase shape the
    # sheets used: place, read the pads out of the reply, then lay copper at
    # the coordinates the server reported.
    board = str(OUT / "led_digits.kicad_pcb")
    await call("new_board", path=board, layers=2)
    await call("add_outline", path=board, points=[
        [0, 0], [BOARD_W, 0], [BOARD_W, BOARD_H], [0, BOARD_H]])
    print(f"\nboard {BOARD_W} x {BOARD_H} mm, 2 layers")

    placed: list[tuple[int, str, float, float]] = []
    puts: list[dict[str, Any]] = []
    for i, digit, col, row in channels():
        x, y = cell_xy(digit, col, row)
        n = i + 1
        puts += [
            {"tool": "place_footprint", "args": {
                "path": board, "fp_id": LED_FP, "ref": f"D{n}",
                "x": x, "y": y, "value": "RED"}},
            {"tool": "place_footprint", "args": {
                "path": board, "fp_id": RES_FP, "ref": f"R{n}", "x": x,
                "y": y, "rotation": 90, "side": "B", "value": "330R"}},
        ]
        placed.append((n, digit, x, y))
    parts_out = (await call("batch", ops=puts)).get("results", [])
    # The pads came back with the placement, already turned and already on the
    # right side. Nothing needs asking again.
    pads_of: dict[str, dict[str, dict[str, float]]] = {}
    for fp in parts_out:
        pads_of[fp["ref"]] = {p["number"]: p for p in fp["pads"]}
    print(f"placed {len(placed) * 2} footprints: "
          f"{len(placed)} LEDs front, {len(placed)} resistors back")

    # -- 4. the nets, from the schematic -----------------------------------
    #
    # A library footprint carries no nets. Which pad is on which net is a fact
    # the SCHEMATIC holds, so read it there and apply it here.
    of: dict[str, str] = {}
    sets: list[dict[str, Any]] = []
    for net in nets.get("nets", []):
        for p in net["pins"]:
            sets.append({"tool": "set_pad_net", "args": {
                "path": board, "ref": p["ref"], "pad": p["pin"],
                "net": net["name"]}})
            of[f"{p['ref']}.{p['pin']}"] = net["name"]
    applied = await call("batch", ops=sets, stop_on_error=False)
    assigned = applied.get("ran", 0) - len(applied.get("failed", []))
    print(f"nets from the schematic: {assigned} pads assigned")

    # -- 5. one via and two tracks per channel -----------------------------
    #
    # The anode is the only thing that has to cross. The cathode sits in the
    # F.Cu ground pour and the resistor's far pad in the B.Cu supply pour, so
    # neither needs any copper drawn for it at all.
    vias = tracks = 0
    sizes: dict[tuple[float, float], int] = {}
    copper: list[dict[str, Any]] = []
    for n, digit, x, y in placed:
        anode = pads_of.get(f"D{n}", {}).get("2")
        rpad = pads_of.get(f"R{n}", {}).get("2")
        net = of.get(f"D{n}.2", "")
        if not (anode and rpad and net):
            continue
        vx, vy = round(x + VIA_DX, 3), y
        dia, drill = VIA_SIZES[list(DIGITS).index(digit)]
        copper += [
            {"tool": "add_via", "args": {
                "path": board, "x": vx, "y": vy, "net": net,
                "diameter": dia, "drill": drill}},
            {"tool": "add_track", "args": {
                "path": board, "x1": anode["x"], "y1": anode["y"],
                "x2": vx, "y2": vy, "layer": "F.Cu", "width": 0.25,
                "net": net}},
            {"tool": "add_track", "args": {
                "path": board, "x1": vx, "y1": vy, "x2": rpad["x"],
                "y2": rpad["y"], "layer": "B.Cu", "width": 0.25, "net": net}},
        ]
        sizes[(dia, drill)] = sizes.get((dia, drill), 0) + 1
        vias += 1
        tracks += 2
    await call("batch", ops=copper)
    print(f"routed {vias} vias and {tracks} tracks -- one anode crossing "
          f"per channel, and nothing else")
    print("  via sizes, one per digit: "
          + ", ".join(f"{d}/{k}x{n}" for (d, k), n in sorted(sizes.items())))

    # -- 6. the two pours, on opposite faces --------------------------------
    ring = [[1, 1], [BOARD_W - 1, 1], [BOARD_W - 1, BOARD_H - 1], [1, BOARD_H - 1]]
    await call("add_zone", path=board, points=ring, layer="F.Cu", net=GND)
    await call("add_zone", path=board, points=ring, layer="B.Cu", net=VCC)
    await call("add_board_text", path=board, x=BOARD_W / 2, y=BOARD_H - 2.2,
               text="0-9  160 LEDs", layer="F.SilkS", size=1.4)
    await call("save_board", path=board)
    filled = await call("refill_zones", path=board)
    print(f"pours: GND on F.Cu under the LEDs, {VCC} on B.Cu under the "
          f"resistors; {filled.get('filled', '?')} filled")

    # -- 7. what the tools say ----------------------------------------------
    left = await call("unrouted_connections", path=board)
    drc = await call("check_board", path=board)
    errors = [f for f in drc.get("findings", []) if f["severity"] == "error"]
    print(f"\nunrouted connections: {left.get('count', '?')}")
    print(f"DRC: {len(errors)} errors, "
          f"{len(drc.get('findings', [])) - len(errors)} other findings")
    for f in errors[:5]:
        where = f" at {f.get('ref', '')}.{f.get('pad', '')}" if f.get("ref") else ""
        print(f"  {f['kind']}{where}: {f['message'][:70]}")

    # -- 8. does the API report what was actually written? ------------------
    #
    # Everything above WRITES. This section READS, and compares each answer to
    # the number that went in. A wrong fact here is the worst kind of bug this
    # project has: it is silent, and every decision the caller makes downstream
    # rests on it. `check_board` cannot see it -- the file is self-consistent
    # whether the answer is right or not.
    wrong: list[str] = []

    def same(what: str, got: Any, want: Any, tol: float = 0.002) -> None:
        """Record a mismatch rather than raising, so the audit finishes."""
        if isinstance(got, (int, float)) and isinstance(want, (int, float)):
            if abs(got - want) > tol:
                wrong.append(f"{what}: got {got}, wrote {want}")
        elif got != want:
            wrong.append(f"{what}: got {got!r}, wrote {want!r}")

    # (a) the board's own inventory
    fps = await call("list_footprints", path=board)
    cop = await call("list_copper", path=board)
    bnets = await call("list_board_nets", path=board)
    same("list_footprints count", len(fps.get("footprints", [])), 2 * len(placed))
    same("list_copper vias", len(cop.get("vias", [])), vias)
    same("list_copper tracks", len(cop.get("tracks", [])), tracks)
    same("list_board_nets count", len(bnets.get("nets", [])),
         len(nets.get("nets", [])))

    # (b) a placed part is where it was put, and its pads followed it
    n0, _d0, x0, y0 = placed[0]
    fp0 = await call("get_footprint", path=board, ref=f"D{n0}")
    same("get_footprint x", fp0.get("x"), x0)
    same("get_footprint y", fp0.get("y"), y0)
    same("get_footprint side", fp0.get("side"), "F")
    pad0 = await call("get_pad", path=board, ref=f"D{n0}", pad="2")
    inner = next((q for q in fp0.get("pads", []) if q["number"] == "2"), {})
    same("get_pad vs get_footprint x", pad0.get("x"), inner.get("x"))
    same("get_pad vs get_footprint y", pad0.get("y"), inner.get("y"))

    # (c) the unplaced definition, and finding it by name
    fdef = await call("footprint_pads", fp_id=LED_FP)
    same("footprint_pads pad count", len(fdef.get("pads", [])), 2)
    found = await call("find_footprint", query="LED_0603_1608Metric", limit=5)
    if LED_FP not in [q["fp_id"] for q in found.get("footprints", [])]:
        wrong.append(f"find_footprint did not return {LED_FP}")

    # (d) a point query agrees the via landed on both its tracks
    vx0 = round(x0 + VIA_DX, 3)
    here = await call("what_is_on_board", path=board, x=vx0, y=y0, radius=0.05)
    if not here.get("vias"):
        wrong.append(f"what_is_on_board at the via ({vx0},{y0}) reports none")
    if len(here.get("track_ends", [])) < 2:
        wrong.append("what_is_on_board: fewer than 2 track ends meet the via")

    # (e) the schematic side: a placed, ROTATED symbol against its library
    lib = await call("symbol_pins", lib_id="Device:LED")
    off = {q["number"]: (q["x"], q["y"]) for q in lib.get("pins", [])}
    child0 = str(OUT / "digit_0.kicad_sch")
    got_pin = await call("get_pin", path=child0, ref="D1", pin="2")
    comp = await call("get_component", path=child0, ref="D1")
    same("get_component rotation", comp.get("rotation"), float(LED_ROT))
    placed_pin = next((q for q in comp.get("pins", []) if q["number"] == "2"), {})
    same("get_pin vs get_component x", got_pin.get("x"), placed_pin.get("x"))
    same("get_pin vs get_component y", got_pin.get("y"), placed_pin.get("y"))
    # The library puts the anode at +3.81 in X. Turned 90 degrees that has to
    # come out as a Y offset from the part's own origin, and nothing but the
    # API can tell the caller whether it did.
    anode_dx = abs(off.get("2", (0.0, 0.0))[0])
    if abs(abs(got_pin.get("y", 0.0) - comp.get("y", 0.0)) - anode_dx) > 0.01:
        wrong.append("a 90-degree rotation did not turn the anode's "
                     f"{anode_dx} mm X offset into a Y offset")
    flds = await call("get_fields", path=child0, ref="D1")
    same("get_fields Value", flds.get("fields", {}).get("Value"), "RED")
    wires = await call("list_wires", path=child0)
    same("list_wires on one child", len(wires.get("wires", [])), 16 * 3 + 2)
    sym = await call("find_symbol", query="Device:LED", limit=5)
    if "Device:LED" not in [q["lib_id"] for q in sym.get("symbols", [])]:
        wrong.append("find_symbol did not return Device:LED")

    # (f) the schematic mutators, round-tripped the same way as the board's.
    # D1 is wired into its channel, so every step here is undone and its pin
    # checked back to the coordinate the wire was drawn to -- if it did not
    # return, the sheet saved afterwards would be quietly broken.
    d1_before = await call("get_pin", path=child0, ref="D1", pin="2")
    await call("move_components", path=child0, moves=[{
        "ref": "D1", "x": comp.get("x", 0.0) + 12.7, "y": comp.get("y", 0.0)}])
    shifted = await call("get_pin", path=child0, ref="D1", pin="2")
    same("move_components: pin dx",
         shifted.get("x", 0.0) - d1_before.get("x", 0.0), 12.7)
    await call("rotate_components", path=child0,
               turns=[{"ref": "D1", "rotation": 0}])
    turned_s = await call("get_component", path=child0, ref="D1")
    same("rotate_components: reported rotation", turned_s.get("rotation"), 0.0)
    await call("mirror_components", path=child0,
               mirrors=[{"ref": "D1", "axis": "x"}])
    # Back the way it was: no mirror, the original turn, the original place.
    # One call each, because a list of one is how the singular case is
    # spelled now -- there is no other form to reach for.
    await call("mirror_components", path=child0,
               mirrors=[{"ref": "D1", "axis": ""}])
    await call("rotate_components", path=child0,
               turns=[{"ref": "D1", "rotation": LED_ROT}])
    await call("move_components", path=child0, moves=[{
        "ref": "D1", "x": comp.get("x", 0.0), "y": comp.get("y", 0.0)}])
    d1_after = await call("get_pin", path=child0, ref="D1", pin="2")
    same("restore: D1 pin x", d1_after.get("x"), d1_before.get("x"))
    same("restore: D1 pin y", d1_after.get("y"), d1_before.get("y"))
    await call("save_sheet", path=child0)

    print(f"\nreadback: {len(wrong)} answer(s) disagreed with what was written")
    for w in wrong:
        print(f"  WRONG {w}")


    # (g) the editing primitives, round-tripped like everything else. These
    # exist because a design is not written once: a label lands on a pin, a
    # wire goes to the wrong pad, a sheet box wants moving. Before them the
    # only way back was to rebuild the page.
    scratch_sch = str(OUT / "_edit.kicad_sch")
    await call("new_sheet", path=scratch_sch, title="edit round trip")
    made = await call("add_components", path=scratch_sch, parts=[
        {"lib_id": "Device:R", "ref": "R1", "x": 50, "y": 50}])
    rp = {q["number"]: q for q in made["parts"][0]["pins"]}
    top, bot = rp["1"], rp["2"]
    await call("add_wires", path=scratch_sch, wires=[
        {"x1": top["x"], "y1": top["y"], "x2": top["x"], "y2": top["y"] - 10.16}])
    await call("add_labels", path=scratch_sch, labels=[
        {"x": top["x"], "y": top["y"] - 10.16, "text": "TOP"}])
    await call("add_junctions", path=scratch_sch,
               points=[{"x": bot["x"], "y": bot["y"]}])
    await call("add_no_connects", path=scratch_sch, points=[{"x": 90, "y": 90}])
    await call("set_fields", path=scratch_sch,
               fields=[{"ref": "R1", "name": "MPN", "value": "TEMP"}])
    await call("add_texts", path=scratch_sch, notes=[{
        "x": 80, "y": 40, "text": "line one\nline two\t(tab)", "size": 1.27}])
    await call("add_sheets", path=scratch_sch, sheets=[
        {"name": "Box", "filename": "box.kicad_sch", "x": 30, "y": 120}])

    same("next_ref after R1", (await call(
        "next_ref", path=scratch_sch, prefix="R")).get("ref"), "R2")
    at_top = await call("what_is_at", path=scratch_sch,
                        x=top["x"], y=top["y"])
    same("what_is_at sees R1.1", len(at_top.get("pins", [])), 1)
    same("what_is_at sees its wire", at_top.get("wire_ends"), 1)
    same("move_fields found one", len((await call(
        "move_fields", path=scratch_sch, moves=[{
            "ref": "R1", "name": "Reference", "dx": 2.54, "dy": -2.54}]))
        .get("moved", [])), 1)

    # Move the wire and put it back. Both operations must find the same
    # segment; the final remover below proves its endpoints were restored.
    wire_args = {"x1": top["x"], "y1": top["y"],
                 "x2": top["x"], "y2": top["y"] - 10.16}
    same("move_wires found one", (await call(
        "move_wires", path=scratch_sch,
        wires=[{**wire_args, "dx": 2.54, "dy": 0}])).get("moved"), 1)
    same("move_wires back", (await call(
        "move_wires", path=scratch_sch, wires=[{
            "x1": top["x"] + 2.54, "y1": top["y"],
            "x2": top["x"] + 2.54, "y2": top["y"] - 10.16,
            "dx": -2.54, "dy": 0}])).get("moved"), 1)

    # move the label, then put it back, and check it landed where it started
    same("move_labels found one",
         (await call("move_labels", path=scratch_sch, moves=[
             {"x": top["x"], "y": top["y"] - 10.16, "dx": 2.54, "dy": 0}]))
         .get("moved"), 1)
    same("move_labels back",
         (await call("move_labels", path=scratch_sch, moves=[
             {"x": top["x"] + 2.54, "y": top["y"] - 10.16, "dx": -2.54, "dy": 0}]))
         .get("moved"), 1)
    same("rotate_labels found one",
         (await call("rotate_labels", path=scratch_sch, turns=[
             {"x": top["x"], "y": top["y"] - 10.16, "rotation": 90}]))
         .get("turned"), 1)

    # a sheet box moves without its identity changing -- that is the point of
    # it, since re-creating a root to move a box orphans every child.
    box_before = (await call("add_sheets", path=scratch_sch, sheets=[
        {"name": "Keep", "filename": "keep.kicad_sch", "x": 90, "y": 120}]))
    keep = box_before["sheets"][0] if box_before else {}
    moved_box = await call("move_sheets", path=scratch_sch,
                           moves=[{"name": "Keep", "x": 120, "y": 150}])
    after_box = moved_box["sheets"][0] if moved_box else {}
    same("move_sheets keeps the uuid", after_box.get("uuid"), keep.get("uuid"))
    same("move_sheets keeps instance_path",
         after_box.get("instance_path"), keep.get("instance_path"))

    # and the removers, each reporting what it actually found
    for tool, kw, key, want in (
        ("remove_labels", {"points": [{"x": top["x"], "y": top["y"] - 10.16}]},
         "removed", 1),
        ("remove_wires", {"wires": [{"x1": top["x"], "y1": top["y"],
                                     "x2": top["x"], "y2": top["y"] - 10.16}]},
         "removed", 1),
        ("remove_junctions", {"points": [{"x": bot["x"], "y": bot["y"]}]},
         "removed", 1),
        ("remove_no_connects", {"points": [{"x": 90, "y": 90}]}, "removed", 1),
        ("remove_sheets", {"names": ["Box", "Keep"]}, "count", 2),
        ("remove_fields", {"fields": [{"ref": "R1", "name": "MPN"}]},
         "count", 1),
    ):
        same(f"{tool} affected", (await call(tool, path=scratch_sch, **kw))
             .get(key), want)

    # nothing at a point is not an error, and does not read as success either
    empty = await call("remove_labels", path=scratch_sch,
                       points=[{"x": 5, "y": 5}])
    same("removing nothing reports 0", empty.get("removed"), 0)
    left = await call("list_wires", path=scratch_sch)
    same("sheet is empty of wires again", left.get("count"), 0)
    fields_left = await call("get_fields", path=scratch_sch, ref="R1")
    if "MPN" in fields_left.get("fields", {}):
        wrong.append("remove_fields left MPN behind")
    # Force KiCad itself to parse the multiline note written above. The
    # in-process S-expression parser accepting its own output is not enough.
    await call("save_sheet", path=scratch_sch)
    await call("list_nets", path=scratch_sch)
    Path(scratch_sch).unlink(missing_ok=True)

    # -- 9. move it, turn it, flip it, and put it back ----------------------
    #
    # The mutators, round-tripped on the last LED. Each step is checked against
    # the transform it claims to apply, and the part is then restored -- if the
    # restore is exact, the board rendered below is the board that was checked.
    n_last, _dl, x_last, y_last = placed[-1]
    ref = f"D{n_last}"
    before = await call("get_pad", path=board, ref=ref, pad="2")
    moved = await call("move_footprint", path=board, ref=ref,
                       x=x_last + 5, y=y_last + 5)
    after = await call("get_pad", path=board, ref=ref, pad="2")
    same("move: pad dx", after.get("x", 0.0) - before.get("x", 0.0), 5.0)
    same("move: pad dy", after.get("y", 0.0) - before.get("y", 0.0), 5.0)
    turned = await call("rotate_footprint", path=board, ref=ref, rotation=90)
    same("rotate: reported rotation", turned.get("rotation"), 90.0)
    rp = await call("get_pad", path=board, ref=ref, pad="2")
    # A pad offset purely in X must become an offset purely in Y at 90 degrees.
    same("rotate: X offset became Y offset",
         abs(rp.get("y", 0.0) - moved.get("y", 0.0)),
         abs(after.get("x", 0.0) - moved.get("x", 0.0)), tol=0.01)
    flipped = await call("flip_footprint", path=board, ref=ref, side="B")
    same("flip: side", flipped.get("side"), "B")
    await call("set_footprint_field", path=board, ref=ref, name="Value",
               value="AUDIT")
    fields = await call("get_footprint_fields", path=board, ref=ref)
    same("set/get footprint field", fields.get("fields", {}).get("Value"),
         "AUDIT")
    await call("move_footprint_field", path=board, ref=ref, name="Reference",
               dx=0.0, dy=-1.2, hide=True)
    # Put it back, exactly.
    await call("flip_footprint", path=board, ref=ref, side="F")
    await call("rotate_footprint", path=board, ref=ref, rotation=0)
    await call("move_footprint", path=board, ref=ref, x=x_last, y=y_last)
    await call("set_footprint_field", path=board, ref=ref, name="Value",
               value="RED")
    restored = await call("get_pad", path=board, ref=ref, pad="2")
    same("restore: pad x", restored.get("x"), before.get("x"))
    same("restore: pad y", restored.get("y"), before.get("y"))
    print(f"mutators: {ref} moved +5/+5, turned 90, flipped to B, and put "
          f"back; pad 2 returned to ({restored.get('x')}, {restored.get('y')})")

    # -- 10. the destructive ones, on a board of their own -------------------
    #
    # `set_board_layers` and `remove_copper` change what is already there, and
    # an undersized drill is a deliberate failure -- none of that belongs on
    # the design above, so it gets a scratch board that is deleted after.
    scratch = str(OUT / "_scratch.kicad_pcb")
    await call("new_board", path=scratch, layers=2)
    await call("add_outline", path=scratch,
               points=[[0, 0], [20, 0], [20, 20], [0, 20]])
    await call("add_track", path=scratch, x1=2, y1=2, x2=18, y2=2,
               layer="F.Cu", width=0.25, net="N1")
    await call("add_via", path=scratch, x=10, y=2, net="N1",
               diameter=0.60, drill=0.30)
    # Under the minimum the board was created with. Nothing on the contract
    # can change that minimum, which is the gap -- the finding is the point.
    await call("add_via", path=scratch, x=14, y=2, net="N1",
               diameter=0.45, drill=0.20)
    # The other end of the sweep -- a via too big for its room -- is NOT
    # demonstrated here. Two 0.9 mm vias 0.9 mm apart on different nets were
    # tried and DRC returned only `via_dangling`, no clearance error, because
    # neither is connected to anything. The ceiling was found properly on the
    # real board instead, by the sweep; see VIA_SIZES.
    await call("save_board", path=scratch)
    sdrc = await call("check_board", path=scratch)
    small = [f for f in sdrc.get("findings", [])
             if "drill" in f["kind"] or "annular" in f["kind"]]
    # The removers, where removing something costs nothing.
    await call("place_footprint", path=scratch, fp_id=LED_FP, ref="DX",
               x=4, y=15, value="SPARE")
    n_before = len((await call("list_footprints", path=scratch))
                   .get("footprints", []))
    await call("remove_footprint", path=scratch, ref="DX")
    n_after = len((await call("list_footprints", path=scratch))
                  .get("footprints", []))
    same("remove_footprint dropped one", n_before - n_after, 1)
    spare = str(OUT / "_scratch.kicad_sch")
    await call("new_sheet", path=spare, title="scratch")
    await call("add_components", path=spare, parts=[
        {"lib_id": "Device:R", "ref": "RX", "x": 50, "y": 50}])
    # `count`, not a guess at the list's name: `list_components` returns its
    # parts under "parts", and `list_footprints` under "footprints". Every
    # reply carries `count`, which is the one key that does not need guessing.
    c_before = (await call("list_components", path=spare)).get("count", 0)
    await call("remove_components", path=spare, refs=["RX"])
    c_after = (await call("list_components", path=spare)).get("count", 0)
    same("remove_components dropped one", c_before - c_after, 1)

    grew = await call("set_board_layers", path=scratch, count=4)
    gone = await call("remove_copper", path=scratch, net="N1", layer="F.Cu")
    left = await call("list_copper", path=scratch)
    print(f"scratch: a 0.2 mm drill -> {len(small)} finding(s), graded "
          f"against a minimum nothing on this contract can set. "
          f"set_board_layers 2 -> "
          f"{len(grew.get('layers', []))}; remove_copper took "
          f"{gone.get('removed', '?')}, leaving "
          f"{len(left.get('tracks', []))} track(s) and "
          f"{len(left.get('vias', []))} via(s)")
    Path(scratch).unlink(missing_ok=True)
    Path(spare).unlink(missing_ok=True)
    await call("render_board", path=board,
               output_file=str(OUT / "led_digits-top.png"), side="top")
    await call("render_board", path=board,
               output_file=str(OUT / "led_digits-bottom.png"), side="bottom")
    await call("render_schematic", path=root, output_dir=str(OUT))
    advertised = {tool.name for tool in await client.list_tools()}
    missing = sorted(advertised - used_tools)
    if missing:
        wrong.append("MCP tools not exercised: " + ", ".join(missing))
    if wrong:
        print("")
        print(f"{len(wrong)} disagreement(s) in total:")
        for w in wrong:
            print(f"  WRONG {w}")
    print(f"API coverage: {len(used_tools & advertised)}/{len(advertised)} tools")
    took = time.time() - started
    print(f"\n{calls} MCP calls in {took:.1f}s ({calls / took:.0f}/s), "
          f"{failures} failed")
    return failures + int(left.get("count") or 0) + len(errors) + len(wrong)


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
