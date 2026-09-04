"""A four-channel USB-to-TTL adapter, drawn through the MCP primitives.

An FT4232H: one high-speed USB port in, four independent 3.3 V TTL serial
ports out, each on the standard six-pin FTDI cable header (GND, CTS, VCC, TXD,
RXD, RTS).

**Where wires are used and where labels are.** The four channels wire straight
across, because the chip's ADBUS0/1/2 sit exactly 2.54 mm apart and so do the
header's TXD/RXD/RTS -- three parallel wires, no turns. Everything on the left
of the chip is labelled instead. That is not laziness: REF, RESET, the crystal
and the USB pair all leave the same edge heading for different places, and any
two of them routed as wires cross each other. A label crossing nothing beats a
wire crossing three.

CTS is the exception that proves it. The chip orders the channel
TXD/RXD/RTS/CTS top to bottom; the cable header orders it CTS then TXD/RXD/RTS.
CTS therefore has to get from the bottom of one group to the top of the other,
across the three wires that just went straight. It is a label.

Design notes worth stating:

  * **No EEPROM.** The FT4232H enumerates on its internal defaults without
    one, and KiCad's libraries carry no 93LC46. EECS/EECLK/EEDATA are left
    unconnected, which is what FTDI's own note says to do in that case.
  * **REF wants 12 k 1 %** to ground -- it sets the USB transceiver's current
    reference, and is not a pullup to be guessed at.
  * **VPHY and VPLL come off VREGOUT through ferrite beads**, separately from
    VCORE, so the PHY and PLL supplies do not share the core's switching noise.
  * **VBUS drives VREGIN**; the internal regulator makes the 1.8 V core rail,
    and an external LDO makes the 3.3 V I/O rail.

Run it::

    python examples/scripts/usb_ttl4_via_mcp.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/usb_ttl4")
NAME = "usb_ttl4"

G = 1.27

#: The chip, and the rails that hang off it. Every other coordinate is derived
#: from these, so moving the chip moves the sheet.
U1_AT = (170.18, 149.86)
RAIL_TOP = 85.09       # +3V3 and +1V8 both run here, over different x
RAIL_GND = 212.09      # under the chip's nine ground pins
HDR_X = 250.19         # the four output headers, mirrored to face the chip

#: Channel A/B/C/D: the chip's bus prefix, the header, and the y its bus 0 pin
#: sits at. The other three signals follow at 2.54 mm intervals.
CHANNELS = [("A", "AD", "J2", 101.6), ("B", "BD", "J3", 124.46),
            ("C", "CD", "J4", 147.32), ("D", "DD", "J5", 170.18)]

FOOTPRINTS = {
    "U1": "Package_QFP:LQFP-64_10x10mm_P0.5mm",
    "U2": "Package_TO_SOT_SMD:SOT-23-5",
    "J1": "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
    "Y1": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
    "F1": "Fuse:Fuse_1206_3216Metric",
    "D1": "Diode_SMD:D_SOD-323",
    "FB1": "Inductor_SMD:L_0603_1608Metric",
    "FB2": "Inductor_SMD:L_0603_1608Metric",
}
for _ref in ("R1", "R2", "R3", "R4"):
    FOOTPRINTS[_ref] = "Resistor_SMD:R_0402_1005Metric"
for _n in range(1, 5):
    FOOTPRINTS[f"J{_n + 1}"] = (
        "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")


async def build(client: Any) -> int:
    """Draw the sheet and report what came out."""
    failures = 0

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            why = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool}({kw.get('ref') or kw.get('lib_id') or ''}):"
                  f" {why}")
            failures += 1
            return {}
        return data

    if OUT.exists():
        shutil.rmtree(OUT)
    sheet = str(OUT / f"{NAME}.kicad_sch")
    r = await call("new_sheet", path=sheet, paper="A3",
                   title="4-channel USB to TTL serial (FT4232H)")
    print(f"sheet {r.get('size')} mm\n")

    placed: dict[str, dict[str, Any]] = {}

    async def put(lib_id: str, ref: str, x: float, y: float, value: str = "",
                  rotation: float = 0.0, mirror: str = "") -> None:
        """Place a part and remember its pins."""
        got = await call("add_component", path=sheet, lib_id=lib_id, ref=ref,
                         x=x, y=y, value=value, rotation=rotation,
                         mirror=mirror)
        if got:
            placed[ref] = got

    def pin(ref: str, number: str) -> tuple[float, float]:
        """Where a placed pin is, from what the server already told us."""
        for p in placed[ref]["pins"]:
            if p["number"] == number or p["name"] == number:
                return (p["x"], p["y"])
        raise KeyError(f"{ref} has no pin {number}")

    async def wire(a: tuple[float, float], b: tuple[float, float]) -> None:
        """One straight segment."""
        await call("add_wire", path=sheet, x1=a[0], y1=a[1], x2=b[0], y2=b[1])

    async def junction(x: float, y: float) -> None:
        """Mark a tee."""
        await call("add_junction", path=sheet, x=x, y=y)

    async def tie(ref: str, number: str, rail: float) -> None:
        """Take a pin vertically onto a rail and mark the tee."""
        px, py = pin(ref, number)
        if py != rail:
            await wire((px, py), (px, rail))
        await junction(px, rail)

    async def stub(ref: str, number: str, text: str, dx: float = -8.89) -> None:
        """Run a pin out to a short wire and name the net on it.

        These are LOCAL labels -- text on a wire, not a flag -- so justify
        moves the text to the far side of the anchor and keeps it off the
        stub it is naming.
        """
        px, py = pin(ref, number)
        await wire((px, py), (px + dx, py))
        await call("add_label", path=sheet, x=px + dx, y=py, text=text,
                   justify="right" if dx < 0 else "left")

    async def rail_power(x: float, y: float, net: str,
                         down: float = 5 * G) -> None:
        """A power symbol above a rail, wired down onto it."""
        sym = await call("add_power", path=sheet, x=x, y=y - down, net=net)
        if sym:
            await wire((sym["pins"][0]["x"], sym["pins"][0]["y"]), (x, y))
            await junction(x, y)

    async def to_gnd(x: float, y: float, down: float = 5 * G) -> None:
        """A ground symbol below a point, wired up to it."""
        sym = await call("add_power", path=sheet, x=x, y=y + down, net="GND")
        if sym:
            await wire((x, y), (sym["pins"][0]["x"], sym["pins"][0]["y"]))

    # -- the chip ---------------------------------------------------------
    await put("Interface_USB:FT4232H", "U1", U1_AT[0], U1_AT[1], "FT4232H")
    if failures:
        return failures
    print(f"  U1  FT4232H, {len(placed['U1']['pins'])} pins")

    # -- ground: nine pins in a row, one rail, one symbol ------------------
    gnd_pins = ["1", "5", "10", "11", "15", "25", "35", "47", "51"]
    xs = sorted(pin("U1", n)[0] for n in gnd_pins)
    await wire((xs[0], RAIL_GND), (xs[-1] + 2 * G, RAIL_GND))
    for number in gnd_pins:
        await tie("U1", number, RAIL_GND)
    await to_gnd(xs[-1] + 2 * G, RAIL_GND)

    # -- the two supply rails, side by side over the top edge --------------
    #
    # VCORE and VCCIO are separate groups of adjacent pins, so their rails sit
    # at the same height over different x and never meet.
    core = sorted(pin("U1", n)[0] for n in ("12", "37", "64"))
    io = sorted(pin("U1", n)[0] for n in ("20", "31", "42", "56"))
    await wire((core[0], RAIL_TOP), (core[-1], RAIL_TOP))
    for number in ("12", "37", "64"):
        await tie("U1", number, RAIL_TOP)
    # On a drop point, not between two: the rail shortened when the beads moved
    # away and this landed 1.3 mm off its left end, quietly taking VCORE off
    # the rail with it.
    await rail_power(core[1], RAIL_TOP, "+1V8")

    await wire((io[0], RAIL_TOP), (io[-1], RAIL_TOP))
    for number in ("20", "31", "42", "56"):
        await tie("U1", number, RAIL_TOP)
    await rail_power(io[1], RAIL_TOP, "+3V3")

    # VPHY and VPLL: off +1V8 through their own ferrite beads, so the PHY and
    # the PLL do not sit on the core's noise.
    #
    # The beads are NOT above their pins. VPHY and VPLL are 2.54 mm apart, and
    # a bead is 7.6 mm long with a reference and a value to print -- put them
    # there and the strip over the chip becomes unreadable, which is exactly
    # what the first attempt looked like. Each signal comes up out of the chip,
    # runs left at its own height into clear space, and meets its bead there.
    for ref, number, up, bead_x, flag_x in (
            ("FB1", "VPHY", 76.2, 127.0, 121.92),
            ("FB2", "VPLL", 71.12, 143.51, 138.43)):
        px, py = pin("U1", number)
        await wire((px, py), (px, up))
        await wire((px, up), (bead_x, up))
        await put("Device:FerriteBead", ref, bead_x, 66.04, "600R@100M")
        await wire((bead_x, up), pin(ref, "2"))
        rail = await call("add_power", path=sheet, x=bead_x,
                          y=pin(ref, "1")[1] - 4 * G, net="+1V8")
        if rail:
            await wire(pin(ref, "1"),
                       (rail["pins"][0]["x"], rail["pins"][0]["y"]))
        # A bead is passive, so ERC cannot see the regulator through it and
        # calls these rails undriven. The flag says what is true.
        bx, by = pin(ref, "2")
        await wire((bx, by), (flag_x, by))
        await junction(bx, by)
        pf = await call("add_power_flag", path=sheet, x=flag_x, y=by)
        if pf:
            await wire((flag_x, by), (pf["pins"][0]["x"], pf["pins"][0]["y"]))

    # The chip's own reference would otherwise print into those rails.
    await call("move_field", path=sheet, ref="U1", name="Reference",
               dx=-25.4, dy=-59.69, justify="left")

    # -- the left edge: labelled, because these all go different ways ------
    for number, text in (("7", "USB_D-"), ("8", "USB_D+"), ("6", "REF"),
                         ("14", "RESET_N"), ("2", "OSC_I"), ("3", "OSC_O")):
        await stub("U1", number, text)
    for number in ("63", "62", "61"):          # EECS/EECLK/EEDATA, no EEPROM
        await call("add_no_connect", path=sheet, x=pin("U1", number)[0],
                   y=pin("U1", number)[1])
    for number in ("60", "36"):                # PWREN#, SUSPEND#
        await call("add_no_connect", path=sheet, x=pin("U1", number)[0],
                   y=pin("U1", number)[1])

    vrx, vry = pin("U1", "VREGIN")
    v5 = await call("add_power", path=sheet, x=vrx - 8.89, y=vry, net="+5V")
    if v5:
        await wire((vrx, vry), (v5["pins"][0]["x"], v5["pins"][0]["y"]))
    vox, voy = pin("U1", "VREGOUT")
    v18 = await call("add_power", path=sheet, x=vox - 8.89, y=voy, net="+1V8")
    if v18:
        await wire((vox, voy), (v18["pins"][0]["x"], v18["pins"][0]["y"]))
    tx, ty = pin("U1", "TEST")                  # TEST ties to ground
    await wire((tx, ty), (tx - 8.89, ty))
    await to_gnd(tx - 8.89, ty)

    # -- the four channels -------------------------------------------------
    #
    # TXD, RXD and RTS run straight across: the chip's bus 0/1/2 and the
    # header's pins 4/5/6 are both on a 2.54 mm pitch. CTS is a label, because
    # it starts below the group and has to arrive above it.
    for name, bus, hdr, y0 in CHANNELS:
        await put("Connector:Conn_01x06_Pin", hdr, HDR_X, y0 - 2 * G,
                  f"{name} TTL", mirror="y")
        for step, number in enumerate(("4", "5", "6")):
            await wire((pin("U1", f"{bus}BUS{step}")), pin(hdr, number))
        cx, cy = pin("U1", f"{bus}BUS3")        # CTS# at the chip
        await wire((cx, cy), (cx + 4 * G, cy))
        await call("add_label", path=sheet, x=cx + 4 * G, y=cy,
                   text=f"{name}_CTS", justify="left")
        hx, hy = pin(hdr, "2")                  # CTS at the header
        await wire((hx, hy), (hx - 4 * G, hy))
        await call("add_label", path=sheet, x=hx - 4 * G, y=hy,
                   text=f"{name}_CTS", justify="right")
        vx, vy = pin(hdr, "3")                  # VCC out to the cable
        sym = await call("add_power", path=sheet, x=vx - 5 * G, y=vy,
                         net="+3V3")
        if sym:
            await wire((vx, vy), (sym["pins"][0]["x"], sym["pins"][0]["y"]))
        gx, gy = pin(hdr, "1")
        sym = await call("add_power", path=sheet, x=gx - 10 * G, y=gy,
                         net="GND")
        if sym:
            await wire((gx, gy), (sym["pins"][0]["x"], sym["pins"][0]["y"]))
        for step in range(4, 8):                # the bus pins this does not use
            px, py = pin("U1", f"{bus}BUS{step}")
            await call("add_no_connect", path=sheet, x=px, y=py)

    # -- REF, RESET, and the crystal: each in its own block ----------------
    await put("Device:R", "R3", 88.9, 165.1, "12k 1%")
    await call("add_label", path=sheet, x=pin("R3", "1")[0], y=88.9 * 0 + 158.75,
               text="REF")
    await wire((pin("R3", "1")[0], 158.75), pin("R3", "1"))
    await to_gnd(*pin("R3", "2"))

    await put("Device:R", "R4", 106.68, 165.1, "10k")
    await call("add_label", path=sheet, x=pin("R4", "1")[0], y=158.75,
               text="RESET_N")
    await wire((pin("R4", "1")[0], 158.75), pin("R4", "1"))
    p3 = await call("add_power", path=sheet, x=pin("R4", "2")[0],
                    y=pin("R4", "2")[1] + 5 * G, net="+3V3")
    if p3:
        await wire(pin("R4", "2"),
                   (p3["pins"][0]["x"], p3["pins"][0]["y"]))

    # The crystal stands on its side so its two ends face the two labels, and
    # is mirrored so its ground pins leave to the left, away from them.
    await put("Device:Crystal_GND24", "Y1", 100.33, 190.5, "12MHz",
              rotation=90, mirror="y")
    for number, text in (("3", "OSC_I"), ("1", "OSC_O")):
        px, py = pin("Y1", number)
        await wire((px, py), (px + 6 * G, py))
        await call("add_label", path=sheet, x=px + 6 * G, y=py, text=text,
                   justify="left")
    for field, dy in (("Reference", -10.16), ("Value", 10.16)):
        await call("move_field", path=sheet, ref="Y1", name=field,
                   dx=0.0, dy=dy)
    ygx, ygy = pin("Y1", "2")
    await wire((ygx, ygy), (ygx - 6 * G, ygy))
    await to_gnd(ygx - 6 * G, ygy)

    # -- USB-C in, protection, and the 3.3 V regulator ---------------------
    await put("Connector:USB_C_Receptacle_USB2.0_16P", "J1", 55.88, 130.81,
              "USB-C")
    vbus_y = pin("J1", "A4")[1]
    await put("Device:Polyfuse", "F1", 86.36, vbus_y, "500mA", rotation=90)
    await wire(pin("J1", "A4"), pin("F1", "1"))
    await call("add_label", path=sheet, x=pin("J1", "A4")[0] + 2 * G, y=vbus_y,
               text="VBUS")
    await put("Device:D_TVS", "D1", 96.52, vbus_y + 5 * G, "SMAJ5.0A")
    await put("Device:C", "C1", 104.14, vbus_y + 5 * G, "10u")
    await put("Regulator_Linear:AP2112K-3.3", "U2", 120.65, vbus_y + 2 * G,
              "AP2112K-3.3")
    await wire(pin("F1", "2"), pin("U2", "VIN"))
    for ref in ("D1", "C1"):
        await tie(ref, "1", vbus_y)
    await rail_power(pin("U2", "VIN")[0] - 3 * G, vbus_y, "+5V")
    flg = await call("add_power_flag", path=sheet,
                     x=pin("F1", "2")[0] + 3 * G, y=vbus_y - 5 * G)
    if flg:
        await wire((flg["pins"][0]["x"], flg["pins"][0]["y"]),
                   (flg["pins"][0]["x"], vbus_y))
        await junction(flg["pins"][0]["x"], vbus_y)

    enx, eny = pin("U2", "EN")                  # always on
    await wire((enx, eny), (enx - 3 * G, eny))
    await wire((enx - 3 * G, eny), (enx - 3 * G, vbus_y))
    await junction(enx - 3 * G, vbus_y)

    await put("Device:C", "C2", 137.16, vbus_y + 5 * G, "10u")
    vox, voy = pin("U2", "VOUT")
    await wire((vox, voy), (pin("C2", "1")[0] + 4 * G, voy))
    await tie("C2", "1", voy)
    await rail_power(pin("C2", "1")[0] + 4 * G, voy, "+3V3")

    # One ground rail under this whole block.
    block_gnd = pin("C1", "2")[1]
    await wire((pin("D1", "2")[0], block_gnd), (pin("C2", "2")[0], block_gnd))
    for ref in ("D1", "C1", "C2"):
        await tie(ref, "2", block_gnd)
    await tie("U2", "GND", block_gnd)
    # Tapping the MIDDLE of a rail needs a junction; tapping its end does not.
    # Without it this whole block sat on its own ground net, and only U2's
    # power_in pin complained.
    tap = pin("C1", "2")[0] + 4 * G
    await to_gnd(tap, block_gnd)
    await junction(tap, block_gnd)

    # CC pulldowns: the lower pin to the nearer resistor, as ever.
    await put("Device:R", "R2", 81.28, 127.0, "5k1")
    await put("Device:R", "R1", 88.9, 128.27, "5k1")
    await wire(pin("J1", "CC2"), pin("R2", "1"))
    cc1 = pin("J1", "CC1")
    await wire(cc1, (pin("R1", "1")[0], cc1[1]))
    await wire((pin("R1", "1")[0], cc1[1]), pin("R1", "1"))
    for ref in ("R2", "R1"):
        await to_gnd(*pin(ref, "2"))

    # The two D+ and the two D- pins are the same signal; join and name each.
    for a, b, text in (("A7", "B7", "USB_D-"), ("A6", "B6", "USB_D+")):
        ax, ay = pin("J1", a)
        bx, by = pin("J1", b)
        await wire((ax, ay), (ax + 4 * G, ay))
        await wire((bx, by), (bx + 4 * G, by))
        await wire((ax + 4 * G, ay), (bx + 4 * G, by))
        await call("add_label", path=sheet, x=ax + 4 * G, y=(ay + by) / 2,
                   text=text)
    for number in ("A8", "B8"):
        await call("add_no_connect", path=sheet, x=pin("J1", number)[0],
                   y=pin("J1", number)[1])

    sgx, sgy = pin("J1", "A1")
    await wire(pin("J1", "SH"), (sgx, sgy))
    await junction(sgx, sgy)
    await to_gnd(sgx, sgy)
    gflag = await call("add_power_flag", path=sheet, x=sgx - 12 * G,
                       y=sgy + 5 * G)
    if gflag:
        await wire((gflag["pins"][0]["x"], gflag["pins"][0]["y"]),
                   (sgx, sgy + 5 * G))
        await junction(sgx, sgy + 5 * G)

    # -- decoupling, as two banks rather than caps scattered over the chip --
    #
    # Nine supply pins want a capacitor each. Beside their pins they would sit
    # on top of the rails; in a bank they are one row, one rail and one symbol,
    # and the layout tool is what decides where they physically go anyway.
    for net, x0, values in (("+3V3", 215.9, ["100n", "100n", "100n", "100n",
                                             "10u"]),
                            ("+1V8", 271.78, ["100n", "100n", "100n", "4u7"])):
        top, low = 236.22, 243.84
        xs = [x0 + i * 6 * G for i in range(len(values))]
        for i, (x, value) in enumerate(zip(xs, values, strict=True)):
            await put("Device:C", f"C{10 if net == '+3V3' else 20}{i}",
                      x, (top + low) / 2, value)
        await wire((xs[0], top), (xs[-1], top))
        await wire((xs[0], low), (xs[-1], low))
        for i in range(len(values)):
            ref = f"C{10 if net == '+3V3' else 20}{i}"
            await junction(*pin(ref, "1"))
            await junction(*pin(ref, "2"))
        sym = await call("add_power", path=sheet, x=xs[0] - 6 * G,
                         y=top, net=net)
        if sym:
            await wire((sym["pins"][0]["x"], sym["pins"][0]["y"]), (xs[0], top))
        await to_gnd(xs[-1], low)

    for ref, fp in FOOTPRINTS.items():
        await call("set_field", path=sheet, ref=ref, name="Footprint",
                   value=fp)

    saved = await call("save_sheet", path=sheet)
    print(f"\nwrote {saved.get('path')}: {saved.get('parts')} parts, "
          f"{saved.get('wires')} wires, {saved.get('labels')} labels")
    return failures


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
