"""Draw a schematic through the MCP primitives, and nothing else.

This is the whole point of the primitive API, so it is worth being clear about
what the script does and does not do.

**The server places nothing.** Every coordinate here was chosen by the caller.
The server was asked where pins landed and answered; the routing, the spacing,
the decision to run a wire across before it turns down -- all of that is in
this file, in ordinary Python, where it can be read and argued with.

That is the trade being tested. The old path had an autoplacer, a floorplanner
and a wire router, and a caller who disagreed with any of them had nowhere to
say so. Here a caller who wants a part 2 mm to the left moves it 2 mm to the
left.

Run it::

    python examples/scripts/draw_via_mcp.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/mcp_sheet")
NAME = "regulator"

#: The 1.27 mm schematic grid. Every coordinate below is a multiple of it, so
#: nothing lands between grid lines and quietly fails to connect.
G = 1.27


async def build(client: Any) -> int:
    """Draw the sheet, then report what is on it."""
    failures = 0

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            reason = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool}: {reason}")
            failures += 1
            return {}
        return data

    if OUT.exists():
        shutil.rmtree(OUT)
    sheet = str(OUT / f"{NAME}.kicad_sch")

    r = await call("new_sheet", path=sheet, title="5 V to 3.3 V regulator",
                   paper="A4")
    print(f"sheet {r.get('size')} mm, grid {r.get('grid')} mm")

    # -- the parts, at coordinates this file chose ------------------------
    #
    # Laid out left to right the way the signal flows: input connector, input
    # cap, regulator, output cap, then the LED that shows the rail is up.
    # A regulator at x=120 with caps at 95 and 145 leaves ~25 mm between
    # columns, which is room for a wire and its label without crowding.
    parts = [
        ("Connector:Conn_01x02_Pin", "J1", 60.96, 88.9, "5V IN", 0),
        ("Device:C", "C1", 88.9, 95.25, "10u", 0),
        ("Regulator_Linear:AP2112K-3.3", "U1", 121.92, 88.9, "AP2112K-3.3", 0),
        ("Device:C", "C2", 152.4, 95.25, "10u", 0),
        ("Device:R", "R1", 177.8, 95.25, "1k", 0),
        # Rotated 90 so the ANODE is uppermost: the resistor feeds the
        # anode and the cathode goes to ground. At 270 it drew just as
        # neatly and ERC passed -- with the LED reverse-biased, which
        # tells you exactly what ERC does and does not check.
        ("Device:LED", "D1", 177.8, 110.49, "PWR", 90),
    ]
    placed: dict[str, dict[str, Any]] = {}
    for lib_id, ref, x, y, value, rot in parts:
        got = await call("add_component", path=sheet, lib_id=lib_id, ref=ref,
                         x=x, y=y, value=value, rotation=rot)
        if got:
            placed[ref] = got
            pins = ", ".join(f"{p['number']}@({p['x']},{p['y']})"
                             for p in got["pins"])
            print(f"  {ref:3s} {lib_id:32s} {pins}")

    if failures:
        return failures

    def pin(ref: str, number: str) -> tuple[float, float]:
        """Where a placed pin is, from what the server already told us."""
        for p in placed[ref]["pins"]:
            if p["number"] == number or p["name"] == number:
                return (p["x"], p["y"])
        raise KeyError(f"{ref} has no pin {number}")

    # -- the rails --------------------------------------------------------
    #
    # Both rails run at the regulator's own pin height, so every part drops
    # onto them with a single vertical wire and every junction sits where two
    # wires genuinely cross. The first attempt put the junctions at the rail
    # height but ran the wire at the connector's height, so they crossed 2.5 mm
    # apart and ERC found three dangling wires. Nothing warned about it -- the
    # sheet LOOKED wired. That is the cost of owning the routing.
    rail = pin("U1", "VIN")[1]

    async def drop(ref: str, number: str) -> None:
        """Wire a pin straight up (or down) onto the rail, and junction it."""
        px, py = pin(ref, number)
        await call("add_wire", path=sheet, x1=px, y1=py, x2=px, y2=rail)
        await call("add_junction", path=sheet, x=px, y=rail)

    # Input side: J1 up onto the rail, then the rail across to VIN.
    j1x, j1y = pin("J1", "1")
    await call("add_wire", path=sheet, x1=j1x, y1=j1y, x2=j1x, y2=rail)
    await call("add_wire", path=sheet, x1=j1x, y1=rail,
               x2=pin("U1", "VIN")[0], y2=rail)
    await drop("C1", "1")

    # EN tied high: left out of the pin, up onto the rail. Always-on is a
    # decision about the circuit, so it is made here.
    enx, eny = pin("U1", "EN")
    await call("add_wire", path=sheet, x1=enx, y1=eny, x2=enx - 3 * G, y2=eny)
    await call("add_wire", path=sheet, x1=enx - 3 * G, y1=eny,
               x2=enx - 3 * G, y2=rail)
    await call("add_junction", path=sheet, x=enx - 3 * G, y=rail)

    # Output side: VOUT across to the far end, everything dropping onto it.
    voutx = pin("U1", "VOUT")[0]
    endx = pin("R1", "1")[0] + 10 * G
    await call("add_wire", path=sheet, x1=voutx, y1=rail, x2=endx, y2=rail)
    await drop("C2", "1")
    await drop("R1", "1")

    # The indicator: R1 down to the LED, straight, and named. The name is not
    # decoration -- an unnamed net does not appear in KiCad's netlist export at
    # all, so a board built from this sheet would simply not have it.
    r2x, r2y = pin("R1", "2")
    await call("add_wire", path=sheet, x1=r2x, y1=r2y,
               x2=pin("D1", "A")[0], y2=pin("D1", "A")[1])
    await call("add_label", path=sheet, x=r2x, y=r2y, text="LED_A",
               kind="local")

    # NC is deliberately unconnected; say so, or ERC will ask.
    ncx, ncy = pin("U1", "4")
    await call("add_no_connect", path=sheet, x=ncx, y=ncy)

    # -- power symbols ----------------------------------------------------
    #
    # Each is placed below the pin it serves and wired straight down.
    # `add_power` returns the symbol's own pin, so the wire runs between two
    # known points rather than to where the symbol is assumed to keep it.
    for ref, number in [("J1", "2"), ("C1", "2"), ("U1", "GND"),
                        ("C2", "2"), ("D1", "K")]:
        px, py = pin(ref, number)
        sym = await call("add_power", path=sheet, x=px, y=py + 5 * G, net="GND")
        if sym:
            gp = sym["pins"][0]
            await call("add_wire", path=sheet, x1=px, y1=py,
                       x2=gp["x"], y2=gp["y"])

    # The rail symbols sit on the rail itself, at each end.
    p5 = await call("add_power", path=sheet, x=j1x, y=rail - 5 * G, net="+5V")
    if p5:
        await call("add_wire", path=sheet, x1=p5["pins"][0]["x"],
                   y1=p5["pins"][0]["y"], x2=j1x, y2=rail)
        await call("add_junction", path=sheet, x=j1x, y=rail)
    p3 = await call("add_power", path=sheet, x=endx, y=rail - 5 * G, net="+3V3")
    if p3:
        await call("add_wire", path=sheet, x1=p3["pins"][0]["x"],
                   y1=p3["pins"][0]["y"], x2=endx, y2=rail)

    # PWR_FLAG: ERC needs something to say a rail is driven. 5 V arrives on a
    # connector and GND leaves on one, and ERC cannot know either is a supply.
    flag5 = await call("add_power_flag", path=sheet, x=j1x - 6 * G,
                       y=rail - 5 * G)
    if flag5:
        fp = flag5["pins"][0]
        await call("add_wire", path=sheet, x1=fp["x"], y1=fp["y"],
                   x2=fp["x"], y2=rail)
        await call("add_wire", path=sheet, x1=fp["x"], y1=rail, x2=j1x, y2=rail)

    gx, gy = pin("J1", "2")
    flagg = await call("add_power_flag", path=sheet, x=gx - 6 * G, y=gy + 5 * G)
    if flagg:
        fp = flagg["pins"][0]
        await call("add_wire", path=sheet, x1=fp["x"], y1=fp["y"],
                   x2=gx, y2=fp["y"])
        await call("add_junction", path=sheet, x=gx, y=fp["y"])

    # -- footprints, so this can become a board ---------------------------
    header = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    for ref, fp in (("J1", header),
                    ("C1", "Capacitor_SMD:C_0805_2012Metric"),
                    ("C2", "Capacitor_SMD:C_0805_2012Metric"),
                    ("R1", "Resistor_SMD:R_0805_2012Metric"),
                    ("D1", "LED_SMD:LED_0805_2012Metric"),
                    ("U1", "Package_TO_SOT_SMD:SOT-23-5")):
        await call("set_field", path=sheet, ref=ref, name="Footprint", value=fp)

    # -- did it actually connect? -----------------------------------------
    #
    # `what_is_at` is the check that matters: a wire drawn to where a pin
    # looked like it was reports one thing here, not two.
    print("\nconnectivity, by asking the sheet:")
    for ref, number in [("U1", "VIN"), ("U1", "VOUT"), ("U1", "GND"),
                        ("C1", "1"), ("R1", "1"), ("D1", "1")]:
        px, py = pin(ref, number)
        at = await call("what_is_at", path=sheet, x=px, y=py)
        mark = "ok " if at.get("connected") else "OPEN"
        print(f"  {mark} {ref}.{number:5s} pins={len(at.get('pins', []))} "
              f"wire_ends={at.get('wire_ends')}")
        if not at.get("connected"):
            failures += 1

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
