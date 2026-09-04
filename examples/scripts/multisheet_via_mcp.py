"""A three-page design, drawn through the MCP primitives.

    top.kicad_sch       three boxes and the signals between them
      power.kicad_sch   5 V in, regulated to 3.3 V
      analog.kicad_sch  two unity-gain buffers on an LM358
      io.kicad_sch      the connectors

**Two different things cross a sheet boundary, and they cross differently.**

  * **Power crosses by NAME.** A ``+3V3`` symbol on the analog page is the
    same net as a ``+3V3`` symbol on the power page. Nothing is drawn between
    them and no port is needed -- which is why the power sheet has no ports at
    all despite being what feeds the other two.
  * **Signals cross by PORT.** A port on the box in the root pairs with a
    hierarchical label of the same name inside the child. The pairing is by
    name and nothing checks it while you draw: a port with no matching label
    is simply an unconnected pin, and `check_sheet` on the ROOT is what says
    so.

The other thing a hierarchy needs is the child's *instance path*. `add_sheet`
returns one; `new_sheet` takes it. Get it wrong and the child's parts are
annotated against a sheet that does not exist, so their nets never merge into
the design -- and, characteristically, nothing says so until you read the
netlist.

Run it::

    python examples/scripts/multisheet_via_mcp.py
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

OUT = Path("examples/multisheet")
G = 1.27

#: Where each child's ports sit on the root, and what they are. The root wires
#: analog to io; nothing else crosses.
SHEETS = [
    ("Power", "power.kicad_sch", 63.5, 76.2, ()),
    ("Analog", "analog.kicad_sch", 133.35, 76.2,
     (("IN_A", "input"), ("IN_B", "input"),
      ("OUT_A", "output"), ("OUT_B", "output"))),
    ("IO", "io.kicad_sch", 203.2, 76.2,
     (("IN_A", "output"), ("IN_B", "output"),
      ("OUT_A", "input"), ("OUT_B", "input"))),
]


async def build(client: Any) -> int:
    """Draw all four files and report what the design became."""
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
    root = str(OUT / "top.kicad_sch")
    await call("new_sheet", path=root, paper="A4",
               title="2-channel buffer -- root")

    # -- the root: three boxes --------------------------------------------
    boxes: dict[str, dict[str, Any]] = {}
    for name, filename, x, y, ports in SHEETS:
        got = await call("add_sheet", path=root, name=name, filename=filename,
                         x=x, y=y, width=38.1, height=38.1,
                         ports=[{"name": n, "kind": k} for n, k in ports])
        if got:
            boxes[name] = got
            print(f"  {name:7s} {filename:18s} "
                  f"{len(got['pins'])} ports  {got['instance_path'][:20]}...")
    if failures:
        return failures

    def port(sheet: str, name: str) -> tuple[float, float]:
        """Where a sheet's port sits on the root."""
        for p in boxes[sheet]["pins"]:
            if p["name"] == name:
                return (p["x"], p["y"])
        raise KeyError(f"{sheet} has no port {name}")

    async def wire(a: tuple[float, float], b: tuple[float, float]) -> None:
        """One straight segment."""
        await call("add_wire", path=root, x1=a[0], y1=a[1], x2=b[0], y2=b[1])

    async def run(*points: tuple[float, float]) -> None:
        """A chain of straight segments through the given corners."""
        for a, b in itertools.pairwise(points):
            await wire(a, b)

    # Analog's ports are on its left edge and so are IO's, so every signal
    # leaves Analog to the left, goes round, and comes back to IO's left edge.
    # Four wires at four heights, none crossing another.
    for step, name in enumerate(("IN_A", "IN_B", "OUT_A", "OUT_B")):
        lane = 127.0 + step * 2.54     # its own lane, clear of the boxes above
        a, b = port("Analog", name), port("IO", name)
        await run(a, (a[0] - 5 * G, a[1]), (a[0] - 5 * G, lane),
                  (b[0] - 5 * G, lane), (b[0] - 5 * G, b[1]), b)
    await call("save_sheet", path=root)

    # -- power.kicad_sch: no ports, because power crosses by name ----------
    power = str(OUT / "power.kicad_sch")
    await call("new_sheet", path=power, paper="A4", title="Power",
               instance_path=boxes["Power"]["instance_path"])
    j = await call("add_component", path=power,
                   lib_id="Connector:Conn_01x02_Pin", ref="J3",
                   x=63.5, y=88.9, value="5V IN")
    u = await call("add_component", path=power,
                   lib_id="Regulator_Linear:AP2112K-3.3", ref="U2",
                   x=114.3, y=91.44, value="AP2112K-3.3")
    if failures:
        return failures

    def pin_of(part: dict[str, Any], number: str) -> tuple[float, float]:
        """Where a placed pin is."""
        for p in part["pins"]:
            if p["number"] == number or p["name"] == number:
                return (p["x"], p["y"])
        raise KeyError(number)

    async def gnd(path: str, at: tuple[float, float]) -> None:
        """A ground symbol below a point, wired up to it."""
        sym = await call("add_power", path=path, x=at[0], y=at[1] + 5 * G,
                         net="GND")
        if sym:
            await call("add_wire", path=path, x1=at[0], y1=at[1],
                       x2=sym["pins"][0]["x"], y2=sym["pins"][0]["y"])

    vin, en = pin_of(u, "VIN"), pin_of(u, "EN")
    await call("add_wire", path=power, x1=pin_of(j, "1")[0],
               y1=pin_of(j, "1")[1], x2=vin[0], y2=pin_of(j, "1")[1])
    await call("add_wire", path=power, x1=vin[0], y1=pin_of(j, "1")[1],
               x2=vin[0], y2=vin[1])
    await call("add_wire", path=power, x1=en[0], y1=en[1],
               x2=en[0] - 3 * G, y2=en[1])
    await call("add_wire", path=power, x1=en[0] - 3 * G, y1=en[1],
               x2=en[0] - 3 * G, y2=pin_of(j, "1")[1])
    await call("add_junction", path=power, x=en[0] - 3 * G,
               y=pin_of(j, "1")[1])
    v5 = await call("add_power", path=power, x=en[0] - 3 * G,
                    y=pin_of(j, "1")[1] - 5 * G, net="+5V")
    if v5:
        await call("add_wire", path=power, x1=v5["pins"][0]["x"],
                   y1=v5["pins"][0]["y"], x2=en[0] - 3 * G,
                   y2=pin_of(j, "1")[1])
    # These two symbols are the whole of the power sheet's interface to the
    # rest of the design.
    vout = pin_of(u, "VOUT")
    v3 = await call("add_power", path=power, x=vout[0] + 6 * G, y=vout[1],
                    net="+3V3")
    if v3:
        await call("add_wire", path=power, x1=vout[0], y1=vout[1],
                   x2=v3["pins"][0]["x"], y2=v3["pins"][0]["y"])
    await gnd(power, pin_of(u, "GND"))
    await gnd(power, pin_of(j, "2"))
    # +5V arrives on a connector, so nothing on any page drives it. Its flag
    # belongs here, on the page the supply enters.
    f5 = await call("add_power_flag", path=power, x=en[0] - 3 * G,
                    y=pin_of(j, "1")[1] - 10 * G)
    if f5:
        await call("add_wire", path=power, x1=f5["pins"][0]["x"],
                   y1=f5["pins"][0]["y"], x2=en[0] - 3 * G,
                   y2=pin_of(j, "1")[1] - 5 * G)
    flag = await call("add_power_flag", path=power, x=en[0] - 9 * G,
                      y=pin_of(j, "2")[1] + 5 * G)
    if flag:
        await call("add_wire", path=power, x1=flag["pins"][0]["x"],
                   y1=flag["pins"][0]["y"], x2=pin_of(j, "2")[0],
                   y2=pin_of(j, "2")[1] + 5 * G)
        await call("add_junction", path=power, x=pin_of(j, "2")[0],
                   y=pin_of(j, "2")[1] + 5 * G)
    await call("save_sheet", path=power)

    # -- analog.kicad_sch: the two buffers ---------------------------------
    analog = str(OUT / "analog.kicad_sch")
    await call("new_sheet", path=analog, paper="A4", title="Analog",
               instance_path=boxes["Analog"]["instance_path"])
    for unit, y, chan in ((1, 88.9, "A"), (2, 127.0, "B")):
        op = await call("add_component", path=analog,
                        lib_id="Amplifier_Operational:LM358", ref="U1",
                        x=114.3, y=y, value="LM358", unit=unit)
        if not op:
            continue
        plus, minus = pin_of(op, "+"), pin_of(op, "-")
        out = pin_of(op, "1" if unit == 1 else "7")
        # In from the port, out to the port, feedback under the part.
        await call("add_wire", path=analog, x1=plus[0] - 10 * G, y1=plus[1],
                   x2=plus[0], y2=plus[1])
        # The wire arrives from the right, so the port points right and its
        # box grows away from the op-amp.
        await call("add_label", path=analog, x=plus[0] - 10 * G, y=plus[1],
                   text=f"IN_{chan}", kind="hierarchical", justify="right")
        tap = (out[0] + 4 * G, out[1])
        await call("add_wire", path=analog, x1=out[0], y1=out[1],
                   x2=tap[0], y2=tap[1])
        await call("add_wire", path=analog, x1=tap[0], y1=tap[1],
                   x2=tap[0] + 6 * G, y2=tap[1])
        await call("add_label", path=analog, x=tap[0] + 6 * G, y=tap[1],
                   text=f"OUT_{chan}", kind="hierarchical", justify="left")
        await call("add_junction", path=analog, x=tap[0], y=tap[1])
        for a, b in itertools.pairwise([
                tap, (tap[0], y + 8 * G), (minus[0] - 2 * G, y + 8 * G),
                (minus[0] - 2 * G, minus[1]), minus]):
            await call("add_wire", path=analog, x1=a[0], y1=a[1],
                       x2=b[0], y2=b[1])
    sup = await call("add_component", path=analog,
                     lib_id="Amplifier_Operational:LM358", ref="U1",
                     x=190.5, y=76.2, value="LM358", unit=3)
    if sup:
        vp, vm = pin_of(sup, "V+"), pin_of(sup, "V-")
        r3 = await call("add_power", path=analog, x=vp[0], y=vp[1] - 5 * G,
                        net="+3V3")
        if r3:
            await call("add_wire", path=analog, x1=r3["pins"][0]["x"],
                       y1=r3["pins"][0]["y"], x2=vp[0], y2=vp[1])
        await gnd(analog, vm)
        c = await call("add_component", path=analog, lib_id="Device:C",
                       ref="C1", x=215.9, y=76.2, value="100n")
        if c:
            top, bot = pin_of(c, "1"), pin_of(c, "2")
            d3 = await call("add_power", path=analog, x=top[0],
                            y=top[1] - 5 * G, net="+3V3")
            if d3:
                await call("add_wire", path=analog, x1=d3["pins"][0]["x"],
                           y1=d3["pins"][0]["y"], x2=top[0], y2=top[1])
            await gnd(analog, bot)
    await call("save_sheet", path=analog)

    # -- io.kicad_sch: the connectors --------------------------------------
    io = str(OUT / "io.kicad_sch")
    await call("new_sheet", path=io, paper="A4", title="IO",
               instance_path=boxes["IO"]["instance_path"])
    for ref, value, x, names, kind in (
            ("J1", "SENSOR IN", 76.2, ("IN_A", "IN_B"), "out"),
            ("J2", "BUFFERED OUT", 152.4, ("OUT_A", "OUT_B"), "in")):
        conn = await call("add_component", path=io,
                          lib_id="Connector:Conn_01x03_Pin", ref=ref,
                          x=x, y=88.9, value=value)
        if not conn:
            continue
        for number, net in zip(("1", "3"), names, strict=True):
            px, py = pin_of(conn, number)
            await call("add_wire", path=io, x1=px, y1=py,
                       x2=px + 8 * G, y2=py)
            await call("add_label", path=io, x=px + 8 * G, y=py, text=net,
                       kind="hierarchical", justify="left")
        await gnd(io, pin_of(conn, "2"))
        print(f"  {ref} {kind}: {', '.join(names)}")
    await call("save_sheet", path=io)

    # -- what did the DESIGN become? Ask the root, not a page --------------
    report = await call("check_sheet", path=root)
    print(f"\ncheck_sheet(root): {report.get('errors')} errors, "
          f"{report.get('warnings')} warnings")
    for f in report.get("findings", [])[:10]:
        print(f"   {f.get('severity')} {f.get('kind')} on {f.get('sheet')} "
              f"{f.get('ref', '')}")

    nets = await call("list_nets", path=root)
    print(f"\nlist_nets(root): {nets.get('count')} nets across four files")
    for net in nets.get("nets", []):
        pins = ", ".join(f"{p['ref']}.{p['pin']}" for p in net["pins"])
        print(f"   {net['name']:16s} {pins}")

    return failures + int(report.get("errors") or 0)


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
