"""A two-channel analogue input buffer, drawn through the MCP primitives.

Two sensor inputs, each through an RC low-pass into a unity-gain buffer, out
through a series resistor to a header. The op-amp is an LM358, and that is the
point of this example: it is a **multi-unit symbol**. Three units share the
reference U1 -- one op-amp, the other op-amp, and a third holding the supply
pins -- and each is placed separately, where it belongs on the sheet:

    add_component(..., ref="U1", unit=1, x=..., y=...)   channel A
    add_component(..., ref="U1", unit=2, x=..., y=...)   channel B
    add_component(..., ref="U1", unit=3, x=..., y=...)   V+ and V-

Before units were understood, this circuit could not be drawn correctly at all.
The API reported all eight pins at once, with pins 3 and 5 at the same
coordinates and pins 1 and 7 at the same coordinates, so wiring to one input
silently shorted it to the other channel's. It produced a wrong netlist and no
error.

The script also uses the two calls that check work rather than do it:

  * ``next_ref`` allocates references, so nothing here counts resistors.
  * ``list_nets`` says what the sheet ACTUALLY connects, read back from KiCad.
  * ``check_sheet`` names violations by part and pin, not by coordinate.

Run it::

    python examples/scripts/dual_buffer_via_mcp.py
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/dual_buffer")
NAME = "dual_buffer"
G = 1.27

#: The two channels are the same drawing 29.21 mm apart, so every coordinate
#: below is written once and offset.
CHANNEL_A = 88.9
CHANNEL_B = 118.11

FOOTPRINTS = {
    "U1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "J1": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "J2": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "J3": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
}


async def build(client: Any) -> int:
    """Draw the sheet, then ask the sheet what it became."""
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
    await call("new_sheet", path=sheet, paper="A4",
               title="2-channel analogue input buffer")

    placed: dict[str, dict[str, Any]] = {}

    async def put(lib_id: str, ref: str, x: float, y: float, value: str = "",
                  rotation: float = 0.0, mirror: str = "",
                  unit: int = 1) -> str:
        """Place a part, remember its pins, and hand back the reference."""
        got = await call("add_component", path=sheet, lib_id=lib_id, ref=ref,
                         x=x, y=y, value=value, rotation=rotation,
                         mirror=mirror, unit=unit)
        if got:
            placed[f"{ref}.{unit}" if unit != 1 else ref] = got
        return ref

    async def fresh(prefix: str) -> str:
        """The next free reference with this prefix -- no counters here."""
        got = await call("next_ref", path=sheet, prefix=prefix)
        return str(got.get("ref", f"{prefix}?"))

    def pin(key: str, number: str) -> tuple[float, float]:
        """Where a placed pin is, from what the server already told us."""
        for p in placed[key]["pins"]:
            if p["number"] == number or p["name"] == number:
                return (p["x"], p["y"])
        raise KeyError(f"{key} has no pin {number}")

    async def wire(a: tuple[float, float], b: tuple[float, float]) -> None:
        """One straight segment."""
        await call("add_wire", path=sheet, x1=a[0], y1=a[1], x2=b[0], y2=b[1])

    async def run(*points: tuple[float, float]) -> None:
        """A chain of straight segments through the given corners."""
        for a, b in itertools.pairwise(points):
            await wire(a, b)

    async def to_gnd(x: float, y: float, down: float = 5 * G) -> None:
        """A ground symbol below a point, wired up to it."""
        sym = await call("add_power", path=sheet, x=x, y=y + down, net="GND")
        if sym:
            await wire((x, y), (sym["pins"][0]["x"], sym["pins"][0]["y"]))

    # -- the op-amp, one unit at a time -----------------------------------
    #
    # Units 1 and 2 sit in their own channels; unit 3 holds V+ and V- and goes
    # wherever the supply is drawn, which is nowhere near either of them.
    await put("Amplifier_Operational:LM358", "U1", 127.0, CHANNEL_A, "LM358")
    await put("Amplifier_Operational:LM358", "U1", 127.0, CHANNEL_B, "LM358",
              unit=2)
    await put("Amplifier_Operational:LM358", "U1", 165.1, 63.5, "LM358",
              unit=3)
    if failures:
        return failures

    # -- connectors -------------------------------------------------------
    await put("Connector:Conn_01x04_Pin", "J1", 66.04, 100.33, "SENSOR IN")
    await put("Connector:Conn_01x04_Pin", "J2", 177.8, 100.33, "BUFFERED OUT",
              mirror="y")
    await put("Connector:Conn_01x02_Pin", "J3", 66.04, 63.5, "5V IN")

    # -- one channel, twice ------------------------------------------------
    for unit, y, in_pin, out_pin, in_x, out_y in (
            (1, CHANNEL_A, "1", "1", 78.74, 96.52),
            (2, CHANNEL_B, "4", "4", 83.82, 104.14)):
        key = "U1" if unit == 1 else f"U1.{unit}"
        plus = pin(key, "+")
        minus = pin(key, "-")
        out = pin(key, "1" if unit == 1 else "7")

        # Input filter: series resistor, shunt capacitor, into the + input.
        #
        # The row sits at the + INPUT's height, not the part's centre. They are
        # 2.54 mm apart, and putting the resistor on the centre line made the
        # wire into the op-amp a diagonal that missed the capacitor entirely --
        # which check_sheet reported as C1.1 and C2.1, by name.
        sig = plus[1]
        rin = await fresh("R")
        await put("Device:R", rin, 95.25, sig, "1k", rotation=90)
        cin = await fresh("C")
        await put("Device:C", cin, 105.41, sig + 3 * G, "100n")

        jx, jy = pin("J1", in_pin)
        await run((jx, jy), (in_x, jy), (in_x, sig), pin(rin, "1"))
        await run(pin(rin, "2"), plus)
        await call("add_junction", path=sheet, x=pin(cin, "1")[0], y=sig)
        await to_gnd(*pin(cin, "2"))

        # Unity gain: the output comes back to the - input, routed BELOW the
        # part. Over the top it would cross the + input on its way in.
        rout = await fresh("R")
        await put("Device:R", rout, 147.32, y, "100R", rotation=90)
        tap = (out[0] + 5 * G, out[1])
        await run(out, tap, pin(rout, "1"))
        await call("add_junction", path=sheet, x=tap[0], y=tap[1])
        await run(tap, (tap[0], y + 7 * G), (minus[0] - 2 * G, y + 7 * G),
                  (minus[0] - 2 * G, minus[1]), minus)

        ox, oy = pin("J2", out_pin)
        await run(pin(rout, "2"), (166.37, y), (166.37, out_y), (ox, oy))

    # -- the supply, and the ground pins on both connectors ----------------
    vplus = pin("U1.3", "V+")
    vminus = pin("U1.3", "V-")
    rail = await call("add_power", path=sheet, x=vplus[0], y=vplus[1] - 5 * G,
                      net="+5V")
    if rail:
        await wire((rail["pins"][0]["x"], rail["pins"][0]["y"]), vplus)
    await to_gnd(*vminus)

    cdec = await fresh("C")
    await put("Device:C", cdec, 180.34, 63.5, "100n")
    dec = await call("add_power", path=sheet, x=pin(cdec, "1")[0],
                     y=pin(cdec, "1")[1] - 5 * G, net="+5V")
    if dec:
        await wire((dec["pins"][0]["x"], dec["pins"][0]["y"]), pin(cdec, "1"))
    await to_gnd(*pin(cdec, "2"))

    # J3 brings the supply in, so ERC needs a flag on each rail: nothing on
    # this sheet drives them.
    px, py = pin("J3", "1")
    supply = await call("add_power", path=sheet, x=px + 6 * G, y=py, net="+5V")
    if supply:
        await wire((px, py), (supply["pins"][0]["x"], supply["pins"][0]["y"]))
        flag = await call("add_power_flag", path=sheet, x=px + 6 * G,
                          y=py - 5 * G)
        if flag:
            await wire((flag["pins"][0]["x"], flag["pins"][0]["y"]),
                       (px + 6 * G, py))
            await call("add_junction", path=sheet, x=px + 6 * G, y=py)
    gx, gy = pin("J3", "2")
    await run((gx, gy), (gx + 6 * G, gy))
    await to_gnd(gx + 6 * G, gy)
    gflag = await call("add_power_flag", path=sheet, x=gx + 12 * G, y=gy)
    if gflag:
        await wire((gflag["pins"][0]["x"], gflag["pins"][0]["y"]),
                   (gx + 6 * G, gy))
        await call("add_junction", path=sheet, x=gx + 6 * G, y=gy)

    # Both connectors carry two ground pins; each pair shares a symbol.
    for ref, a, b, dx in (("J1", "2", "3", 4 * G), ("J2", "2", "3", -4 * G)):
        ax, ay = pin(ref, a)
        bx, by = pin(ref, b)
        await run((ax, ay), (ax + dx, ay), (bx + dx, by), (bx, by))
        await to_gnd(ax + dx, (ay + by) / 2)
        await call("add_junction", path=sheet, x=ax + dx, y=(ay + by) / 2)

    # J2's ground symbol lands where its value prints. The automatic side is
    # a default, not a rule -- this is the case it does not cover.
    await call("move_field", path=sheet, ref="J2", name="Value",
               dx=6.35, dy=-8.89, justify="left")

    for ref, fp in FOOTPRINTS.items():
        await call("set_field", path=sheet, ref=ref, name="Footprint",
                   value=fp)
    for ref in [p["ref"] for p in placed.values()]:
        if ref.startswith(("R", "C")):
            await call("set_field", path=sheet, ref=ref, name="Footprint",
                       value="Resistor_SMD:R_0603_1608Metric"
                       if ref.startswith("R")
                       else "Capacitor_SMD:C_0603_1608Metric")

    await call("save_sheet", path=sheet)

    # -- ask the sheet what it became, rather than assuming ----------------
    report = await call("check_sheet", path=sheet)
    print(f"check_sheet: {report.get('errors')} errors, "
          f"{report.get('warnings')} warnings")
    for finding in report.get("findings", [])[:8]:
        print(f"   {finding.get('severity')} {finding.get('kind')} "
              f"{finding.get('ref')}.{finding.get('pin')}")

    nets = await call("list_nets", path=sheet)
    print(f"\nlist_nets: {nets.get('count')} nets")
    for net in nets.get("nets", []):
        pins = ", ".join(f"{p['ref']}.{p['pin']}" for p in net["pins"])
        print(f"   {net['name']:22s} {pins}")

    return failures + int(report.get("errors") or 0)


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
