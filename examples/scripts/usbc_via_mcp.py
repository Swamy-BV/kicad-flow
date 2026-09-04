"""A USB-C to 3.3 V converter, drawn through the MCP primitives.

Every coordinate in this file was chosen here. The server was asked where pins
landed and answered; the layout, the rails, the order of the parts along them
are all decisions made in ordinary Python where they can be read and argued
with.

**Three rails and four ground symbols.** The first version hung a separate GND
symbol off every ground pin -- twelve of them. That is ordinary KiCad practice
and it reads as clutter on a sheet like this one, where the shunt parts already
sit in a row with their lower pins at the same height. So there is a ground
rail under them instead, with one symbol on it. Where a rail cannot reach
without crossing another net -- the LED's cathode, the receptacle shell, the
output header -- a local symbol is still the right answer. Four, not twelve.

The circuit is the usual one: the receptacle on the left, protection and bulk
on the VBUS rail, the regulator in the middle, the 3.3 V rail and its load on
the right.

  * **Both CC pins get their own 5.1 k pulldown.** One resistor across both
    works only in one plug orientation, and a shared pulldown reads as a
    different advertisement to a source.
  * **CC2 goes to the nearer resistor and CC1 to the farther one**, so neither
    wire crosses the other or a resistor body on the way.
  * **A polyfuse then a TVS**, in that order, so the fuse limits the current
    the TVS has to survive.
  * **D+/D-/SBU are marked no-connect**, because this board takes power and
    does not talk.

Run it::

    python examples/scripts/usbc_via_mcp.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/usbc_3v3")
NAME = "usbc_3v3"

#: The schematic grid. Every coordinate below is a multiple of it.
G = 1.27

#: The two horizontal rails: power above the parts, ground below them. Every
#: part reaches both with one vertical wire, which is the whole reason the
#: part coordinates are what they are.
POWER = 86.36
GROUND = 102.87

#: Footprints, so this sheet can become a board without a second pass.
FOOTPRINTS = {
    "J1": "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
    "J2": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "F1": "Fuse:Fuse_1206_3216Metric",
    "D1": "Diode_SMD:D_SOD-323",
    "D2": "LED_SMD:LED_0805_2012Metric",
    "U1": "Package_TO_SOT_SMD:SOT-23-5",
    "C1": "Capacitor_SMD:C_0805_2012Metric",
    "C2": "Capacitor_SMD:C_0805_2012Metric",
    "C3": "Capacitor_SMD:C_0402_1005Metric",
    "R1": "Resistor_SMD:R_0402_1005Metric",
    "R2": "Resistor_SMD:R_0402_1005Metric",
    "R3": "Resistor_SMD:R_0805_2012Metric",
}


async def build(client: Any) -> int:
    """Draw the sheet and report whether it holds together."""
    failures = 0

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            why = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool}: {why}")
            failures += 1
            return {}
        return data

    if OUT.exists():
        shutil.rmtree(OUT)
    sheet = str(OUT / f"{NAME}.kicad_sch")

    r = await call("new_sheet", path=sheet, paper="A4",
                   title="USB-C to 3.3 V converter")
    print(f"sheet {r.get('size')} mm on the {r.get('grid')} mm grid\n")

    # -- the parts --------------------------------------------------------
    #
    # The shunt parts share a y, so their upper pins reach POWER and their
    # lower pins reach GROUND with one straight wire each. The regulator sits
    # so its VIN lands exactly on POWER, which is why its y is not round.
    parts = [
        ("Connector:USB_C_Receptacle_USB2.0_16P", "J1", 63.5, 101.6, "USB-C", 0),
        ("Device:R", "R2", 88.9, 99.06, "5k1", 0),
        ("Device:R", "R1", 96.52, 99.06, "5k1", 0),
        ("Device:Polyfuse", "F1", 109.22, POWER, "500mA", 90),
        ("Device:D_TVS", "D1", 121.92, 93.98, "SMAJ5.0A", 0),
        ("Device:C", "C1", 132.08, 93.98, "10u", 0),
        ("Regulator_Linear:AP2112K-3.3", "U1", 154.94, 88.9, "AP2112K-3.3", 0),
        ("Device:C", "C2", 173.99, 93.98, "10u", 0),
        ("Device:C", "C3", 184.15, 93.98, "100n", 0),
        ("Device:R", "R3", 196.85, 93.98, "1k", 0),
        ("Device:LED", "D2", 196.85, 114.3, "3V3", 90),
        ("Connector:Conn_01x04_Pin", "J2", 219.71, 90.17, "OUT", 0),
    ]
    placed: dict[str, dict[str, Any]] = {}
    for lib_id, ref, x, y, value, rot in parts:
        got = await call("add_component", path=sheet, lib_id=lib_id, ref=ref,
                         x=x, y=y, value=value, rotation=rot)
        if got:
            placed[ref] = got
            print(f"  {ref:3s} {value:12s} {lib_id}")
    if failures:
        return failures

    def pin(ref: str, number: str) -> tuple[float, float]:
        """Where a placed pin is, from what the server already told us."""
        for p in placed[ref]["pins"]:
            if p["number"] == number or p["name"] == number:
                return (p["x"], p["y"])
        raise KeyError(f"{ref} has no pin {number}")

    async def wire(a: tuple[float, float], b: tuple[float, float]) -> None:
        """One straight segment."""
        await call("add_wire", path=sheet, x1=a[0], y1=a[1], x2=b[0], y2=b[1])

    async def tie(ref: str, number: str, rail: float) -> None:
        """Take a pin vertically onto a rail, and mark the tee."""
        px, py = pin(ref, number)
        if py != rail:
            await wire((px, py), (px, rail))
        await call("add_junction", path=sheet, x=px, y=rail)

    # -- VBUS in, through the fuse, onto the +5V rail ---------------------
    await wire(pin("J1", "A4"), pin("F1", "1"))
    await call("add_label", path=sheet, x=pin("J1", "A4")[0] + 2 * G,
               y=POWER, text="VBUS")
    await wire(pin("F1", "2"), pin("U1", "VIN"))
    for ref in ("D1", "C1"):
        await tie(ref, "1", POWER)

    p5 = await call("add_power", path=sheet, x=pin("F1", "2")[0] + 2 * G,
                    y=POWER - 5 * G, net="+5V")
    if p5:
        await wire((p5["pins"][0]["x"], p5["pins"][0]["y"]),
                   (p5["pins"][0]["x"], POWER))
        await call("add_junction", path=sheet, x=p5["pins"][0]["x"], y=POWER)

    # ERC needs something to say this rail is driven: it arrives on a
    # connector. The flag has to sit on the net carrying the power_in pin --
    # +5V, AFTER the fuse, not VBUS before it.
    fx = pin("F1", "2")[0] + 12 * G
    flag = await call("add_power_flag", path=sheet, x=fx, y=POWER - 5 * G)
    if flag:
        await wire((flag["pins"][0]["x"], flag["pins"][0]["y"]), (fx, POWER))
        await call("add_junction", path=sheet, x=fx, y=POWER)

    # EN tied to VIN: this regulator is always on.
    enx, eny = pin("U1", "EN")
    await wire((enx, eny), (enx - 3 * G, eny))
    await wire((enx - 3 * G, eny), (enx - 3 * G, POWER))
    await call("add_junction", path=sheet, x=enx - 3 * G, y=POWER)

    # -- the CC pulldowns -------------------------------------------------
    #
    # CC2 is the lower pin and goes to the nearer resistor; CC1 reaches over
    # it to the farther one. The other way round, the two wires cross.
    for cc, res in (("CC2", "R2"), ("CC1", "R1")):
        cx, cy = pin("J1", cc)
        rx, ry = pin(res, "1")
        await wire((cx, cy), (rx, cy))
        await wire((rx, cy), (rx, ry))
        await call("add_label", path=sheet, x=cx + 2 * G, y=cy, text=cc)

    # -- the 3.3 V rail ---------------------------------------------------
    await wire(pin("U1", "VOUT"), (pin("R3", "1")[0], POWER))
    for ref in ("C2", "C3", "R3"):
        await tie(ref, "1", POWER)

    p3 = await call("add_power", path=sheet, x=pin("U1", "VOUT")[0] + 4 * G,
                    y=POWER - 5 * G, net="+3V3")
    if p3:
        await wire((p3["pins"][0]["x"], p3["pins"][0]["y"]),
                   (p3["pins"][0]["x"], POWER))
        await call("add_junction", path=sheet, x=p3["pins"][0]["x"], y=POWER)

    # -- ONE ground rail, under everything that can reach it --------------
    #
    # Seven pins on one wire and one symbol, instead of seven symbols. The
    # rail stops at C3: carrying it further right would cross the LED's wire,
    # and two nets crossing without a junction is correct and unreadable.
    left, right = pin("R2", "2")[0], pin("C3", "2")[0]
    await wire((left, GROUND), (right, GROUND))
    for ref, number in (("R2", "2"), ("R1", "2"), ("D1", "2"), ("C1", "2"),
                        ("U1", "GND"), ("C2", "2"), ("C3", "2")):
        await tie(ref, number, GROUND)

    gx = 143.51                # clear of C1 on the left and U1 on the right
    gnd = await call("add_power", path=sheet, x=gx, y=GROUND + 5 * G,
                     net="GND")
    if gnd:
        await wire((gx, GROUND), (gnd["pins"][0]["x"], gnd["pins"][0]["y"]))
        await call("add_junction", path=sheet, x=gx, y=GROUND)

    # -- the indicator, which the rail cannot reach -----------------------
    r3x, r3y = pin("R3", "2")
    await wire((r3x, r3y), pin("D2", "A"))
    await call("add_label", path=sheet, x=r3x, y=r3y + 6 * G, text="LED_A")
    kx, ky = pin("D2", "K")
    led = await call("add_power", path=sheet, x=kx, y=ky + 5 * G, net="GND")
    if led:
        await wire((kx, ky), (led["pins"][0]["x"], led["pins"][0]["y"]))

    # -- the receptacle's grounds, shell included -------------------------
    gndx, gndy = pin("J1", "A1")
    await wire(pin("J1", "SH"), (gndx, gndy))
    await call("add_junction", path=sheet, x=gndx, y=gndy)
    shell = await call("add_power", path=sheet, x=gndx, y=gndy + 5 * G,
                       net="GND")
    if shell:
        sp = shell["pins"][0]
        await wire((gndx, gndy), (sp["x"], sp["y"]))
        gflag = await call("add_power_flag", path=sheet, x=gndx - 16 * G,
                           y=sp["y"])
        if gflag:
            await wire((gflag["pins"][0]["x"], gflag["pins"][0]["y"]),
                       (sp["x"], sp["y"]))
            await call("add_junction", path=sheet, x=sp["x"], y=sp["y"])

    # -- the output header ------------------------------------------------
    #
    # 3V3, GND, GND, 5V. Two grounds because a flying lead to a breadboard is
    # the common use and one return pin makes a poor one. They share a symbol.
    # Four nets leaving pins 2.54 mm apart will print their names on top of
    # one another unless they separate first. The rails go up and down at the
    # near column; the two grounds run PAST it to a farther one before they
    # turn, so no wire crosses another net on the way out.
    near = pin("J2", "1")[0] + 4 * G
    far = near + 6 * G
    for number, net, dy in (("1", "+3V3", -5 * G), ("4", "+5V", 5 * G)):
        px, py = pin("J2", number)
        sym = await call("add_power", path=sheet, x=near, y=py + dy, net=net)
        if sym:
            await wire((px, py), (near, py))
            await wire((near, py), (sym["pins"][0]["x"], sym["pins"][0]["y"]))
    top, bottom = pin("J2", "2"), pin("J2", "3")
    for px, py in (top, bottom):
        await wire((px, py), (far, py))
    await wire((far, top[1]), (far, bottom[1]))
    hdr = await call("add_power", path=sheet, x=far, y=bottom[1] + 5 * G,
                     net="GND")
    if hdr:
        await wire((far, bottom[1]),
                   (hdr["pins"][0]["x"], hdr["pins"][0]["y"]))
        await call("add_junction", path=sheet, x=far, y=bottom[1])

    # -- what this board does not do --------------------------------------
    for number in ("A6", "A7", "B6", "B7", "A8", "B8"):
        px, py = pin("J1", number)
        await call("add_no_connect", path=sheet, x=px, y=py)

    for ref, fp_name in FOOTPRINTS.items():
        await call("set_field", path=sheet, ref=ref, name="Footprint",
                   value=fp_name)

    saved = await call("save_sheet", path=sheet)
    listed = await call("list_components", path=sheet)
    grounds = sum(1 for p in listed.get("parts", []) if p.get("value") == "GND")
    print(f"\nwrote {saved.get('path')}: {saved.get('parts')} parts, "
          f"{saved.get('wires')} wires, {saved.get('labels')} labels, "
          f"{grounds} ground symbols")
    return failures


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
