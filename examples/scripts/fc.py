"""A quadcopter flight controller, drawn through the MCP primitives.

    top.kicad_sch        four boxes
      power.kicad_sch    2-6S battery in, 5 V buck, 3.3 V LDO, battery sense
      mcu.kicad_sch      STM32F405RGT6, 8 MHz crystal, USB, SWD
      sensors.kicad_sch  MPU-6000 gyro on SPI1, W25Q128 blackbox on SPI3
      io.kicad_sch       four motor outputs, three UARTs, buzzer, LED strip

A Betaflight-style F405 target. The pin map is the conventional one: SPI1 to
the gyro, SPI3 to the flash, TIM8 channels to the four motors, USART1/2/3 out
to VTX, receiver and GPS.

**This design crosses sheets with GLOBAL LABELS, not ports, and that is a
choice worth explaining.** The previous multi-sheet example used ports,
because it had four signals between two pages and a port makes the interface
explicit on the root. This one has twenty-two. Twenty-two ports would turn the
root into a wiring diagram of its own -- a page of parallel lines whose only
content is that a name on one page is the same net as that name on another,
which is exactly what a global label says in one symbol. Real flight
controller schematics are drawn this way for the same reason.

So the root here is four boxes and nothing else, and every inter-sheet signal
is a global label at both ends. `check_sheet` on the root is what confirms the
pairing, because nothing checks it while you draw.

Run it::

    python examples/scripts/fc.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/fc")
G = 1.27

#: The MCU pin map. Every one of these becomes a global label on the MCU sheet
#: and another of the same name on the sheet that uses it.
PINMAP = [
    ("PA5", "SPI1_SCK"), ("PA6", "SPI1_MISO"), ("PA7", "SPI1_MOSI"),
    ("PA4", "IMU_CS"), ("PC4", "IMU_INT"),
    ("PC10", "SPI3_SCK"), ("PC11", "SPI3_MISO"), ("PC12", "SPI3_MOSI"),
    ("PB3", "FLASH_CS"),
    ("PC6", "M1"), ("PC7", "M2"), ("PC8", "M3"), ("PC9", "M4"),
    ("PA9", "VTX_TX"), ("PA10", "VTX_RX"),
    ("PA2", "RX_TX"), ("PA3", "RX_RX"),
    ("PB10", "GPS_TX"), ("PB11", "GPS_RX"),
    ("PC13", "BUZZ"), ("PA8", "LED_DATA"), ("PC1", "VBAT_SENSE"),
]


async def build(client: Any) -> int:
    """Draw all five files and report what the design became."""
    failures = 0

    async def call(tool: str, **kw: Any) -> dict[str, Any]:
        """One MCP call, failing loudly rather than continuing on sand."""
        nonlocal failures
        res = await client.call_tool(tool, kw)
        data = res.data if hasattr(res, "data") else res
        if not isinstance(data, dict) or data.get("ok") is not True:
            why = (data or {}).get("error") if isinstance(data, dict) else data
            print(f"  FAILED {tool} {kw.get('ref', kw.get('text', ''))}: {why}")
            failures += 1
            return {}
        return data

    #: Writes whose reply nobody reads. They are queued and sent as ONE
    #: `batch` per sheet instead of one call each -- 280 of this script's 426
    #: calls were of that kind. Anything whose reply IS read (`add_component`,
    #: `add_power`) still goes on its own, because the next coordinate depends
    #: on what comes back.
    pending: list[dict[str, Any]] = []

    def pend(tool: str, **kw: Any) -> None:
        """Queue a write for the next flush."""
        pending.append({"tool": tool, "args": kw})

    async def flush() -> None:
        """Send everything queued, in order, as one call."""
        if pending:
            await call("batch", ops=list(pending))
            pending.clear()

    def pin(part: dict[str, Any], number: str) -> dict[str, Any]:
        """One pin of a placed part, by number or name."""
        for p in part["pins"]:
            if p["number"] == number or p["name"] == number:
                return p
        raise KeyError(f"{part['ref']} has no pin {number}")

    def outward(p: dict[str, Any]) -> tuple[float, float]:
        """The unit direction AWAY from the part, for a stub off this pin.

        A pin's orientation points from its connection back into the body, so
        leaving means going the other way. Taking it from the pin rather than
        from which side of the symbol it looked like it was on is what lets
        one helper serve a 64-pin MCU and a two-pin buzzer alike.
        """
        return {0.0: (-1.0, 0.0), 180.0: (1.0, 0.0),
                90.0: (0.0, 1.0), 270.0: (0.0, -1.0)}[p["orientation"] % 360]

    async def net(sheet: str, part: dict[str, Any], number: str, name: str,
                  length: float = 6 * G, kind: str = "global") -> None:
        """Run a stub off a pin and name the net on its end."""
        p = pin(part, number)
        dx, dy = outward(p)
        end = (p["x"] + dx * length, p["y"] + dy * length)
        pend("add_wire", path=sheet, x1=p["x"], y1=p["y"],
                   x2=end[0], y2=end[1])
        # JUSTIFY is what points a global label, not rotation. `right` puts
        # the flag's tip where the wire arrives and grows the box away from
        # the part; get it backwards and the wire runs through the text. A
        # horizontal global label renders identically at 0 and 180 degrees, so
        # rotation is only for the vertical ones.
        rotation = 90.0 if dy < 0 else 270.0 if dy > 0 else 0.0
        pend("add_label", path=sheet, x=end[0], y=end[1], text=name,
                   kind=kind, rotation=rotation,
                   justify="right" if dx < 0 else "left")

    async def rail(sheet: str, part: dict[str, Any], number: str, name: str,
                   length: float = 4 * G) -> None:
        """Take a pin to a power symbol of its own."""
        p = pin(part, number)
        dx, dy = outward(p)
        end = (p["x"] + dx * length, p["y"] + dy * length)
        sym = await call("add_power", path=sheet, x=end[0], y=end[1], net=name)
        if sym:
            pend("add_wire", path=sheet, x1=p["x"], y1=p["y"],
                       x2=sym["pins"][0]["x"], y2=sym["pins"][0]["y"])

    async def put(sheet: str, lib_id: str, ref: str, x: float, y: float,
                  value: str = "", rotation: float = 0.0,
                  mirror: str = "") -> dict[str, Any]:
        """Place a part and hand back what the server said about it."""
        return await call("add_component", path=sheet, lib_id=lib_id, ref=ref,
                          x=x, y=y, value=value, rotation=rotation,
                          mirror=mirror)

    async def series(sheet: str, a: dict[str, Any], an: str,
                     b: dict[str, Any], bn: str) -> None:
        """Wire two pins that already share an x or a y."""
        pa, pb = pin(a, an), pin(b, bn)
        pend("add_wire", path=sheet, x1=pa["x"], y1=pa["y"],
                   x2=pb["x"], y2=pb["y"])

    if OUT.exists():
        shutil.rmtree(OUT)
    root = str(OUT / "fc.kicad_sch")
    await call("new_sheet", path=root, paper="A4",
               title="F405 flight controller -- root")

    boxes: dict[str, dict[str, Any]] = {}
    for name, filename, x, y in (("Power", "power.kicad_sch", 63.5, 76.2),
                                 ("MCU", "mcu.kicad_sch", 152.4, 76.2),
                                 ("Sensors", "sensors.kicad_sch", 63.5, 133.35),
                                 ("IO", "io.kicad_sch", 152.4, 133.35)):
        got = await call("add_sheet", path=root, name=name, filename=filename,
                         x=x, y=y, width=50.8, height=38.1, ports=[])
        if got:
            boxes[name] = got
    if failures:
        return failures
    await flush()
    await call("save_sheet", path=root)

    # -- power: battery in, 5 V buck, 3.3 V LDO ---------------------------
    power = str(OUT / "power.kicad_sch")
    await call("new_sheet", path=power, paper="A4", title="Power",
               instance_path=boxes["Power"]["instance_path"])
    jb = await put(power, "Connector:Conn_01x02_Pin", "J1", 45.72, 76.2,
                   "VBAT 2-6S")
    cb = await put(power, "Device:C_Polarized", "C1", 66.04, 82.55, "220u/35V")
    buck = await put(power, "Regulator_Switching:TPS54302", "U1", 106.68, 80.01,
                     "TPS54302")
    if failures:
        return failures
    vbat_y = pin(jb, "1")["y"]

    # VBAT rail across to the buck, with the bulk cap on it.
    pend("add_wire", path=power, x1=pin(jb, "1")["x"], y1=vbat_y,
               x2=pin(buck, "VIN")["x"], y2=vbat_y)
    pend("add_wire", path=power, x1=pin(cb, "1")["x"],
               y1=pin(cb, "1")["y"], x2=pin(cb, "1")["x"], y2=vbat_y)
    pend("add_junction", path=power, x=pin(cb, "1")["x"], y=vbat_y)
    pend("add_wire", path=power, x1=pin(buck, "VIN")["x"], y1=vbat_y,
               x2=pin(buck, "VIN")["x"], y2=pin(buck, "VIN")["y"])
    pend("add_label", path=power, x=pin(jb, "1")["x"] + 2 * G, y=vbat_y,
               text="VBAT", kind="global", justify="left")
    await rail(power, jb, "2", "GND")
    await rail(power, cb, "2", "GND")

    # EN pulled to VIN: the converter is on whenever the battery is.
    ren = await put(power, "Device:R", "R1", pin(buck, "EN")["x"] - 6 * G,
                    pin(buck, "EN")["y"], "100k", rotation=90)
    await series(power, buck, "EN", ren, "1")
    pend("add_wire", path=power, x1=pin(ren, "2")["x"],
               y1=pin(ren, "2")["y"], x2=pin(ren, "2")["x"], y2=vbat_y)
    pend("add_junction", path=power, x=pin(ren, "2")["x"], y=vbat_y)

    # The switching node: bootstrap cap, inductor, output cap.
    sw = pin(buck, "SW")
    cboot = await put(power, "Device:C", "C2", sw["x"] + 4 * G,
                      pin(buck, "BOOT")["y"] - 4 * G, "100n", rotation=90)
    pend("add_wire", path=power, x1=pin(buck, "BOOT")["x"],
               y1=pin(buck, "BOOT")["y"], x2=pin(cboot, "1")["x"],
               y2=pin(buck, "BOOT")["y"])
    pend("add_wire", path=power, x1=pin(cboot, "1")["x"],
               y1=pin(buck, "BOOT")["y"], x2=pin(cboot, "1")["x"],
               y2=pin(cboot, "1")["y"])
    ind = await put(power, "Device:L", "L1", sw["x"] + 10 * G, sw["y"],
                    "4.7uH", rotation=90)
    pend("add_wire", path=power, x1=sw["x"], y1=sw["y"],
               x2=pin(ind, "1")["x"], y2=sw["y"])
    pend("add_wire", path=power, x1=pin(cboot, "2")["x"],
               y1=pin(cboot, "2")["y"], x2=pin(cboot, "2")["x"], y2=sw["y"])
    pend("add_junction", path=power, x=pin(cboot, "2")["x"], y=sw["y"])
    await rail(power, buck, "GND", "GND")

    out5 = pin(ind, "2")
    cout = await put(power, "Device:C", "C3", out5["x"] + 6 * G,
                     out5["y"] + 6 * G, "22u")
    pend("add_wire", path=power, x1=out5["x"], y1=out5["y"],
               x2=pin(cout, "1")["x"], y2=out5["y"])
    pend("add_wire", path=power, x1=pin(cout, "1")["x"], y1=out5["y"],
               x2=pin(cout, "1")["x"], y2=pin(cout, "1")["y"])
    await rail(power, cout, "2", "GND")
    v5 = await call("add_power", path=power, x=pin(cout, "1")["x"] + 8 * G,
                    y=out5["y"] - 5 * G, net="+5V")
    if v5:
        pend("add_wire", path=power, x1=pin(cout, "1")["x"] + 8 * G,
                   y1=out5["y"], x2=v5["pins"][0]["x"], y2=v5["pins"][0]["y"])
        pend("add_wire", path=power, x1=pin(cout, "1")["x"], y1=out5["y"],
                   x2=pin(cout, "1")["x"] + 8 * G, y2=out5["y"])

    # Feedback divider sets 5 V against the converter's 0.596 V reference.
    rfb1 = await put(power, "Device:R", "R2", out5["x"] + 2 * G,
                     out5["y"] + 12 * G, "100k")
    rfb2 = await put(power, "Device:R", "R3", out5["x"] + 2 * G,
                     out5["y"] + 22 * G, "13k7")
    pend("add_wire", path=power, x1=pin(rfb1, "1")["x"],
               y1=pin(rfb1, "1")["y"], x2=pin(rfb1, "1")["x"], y2=out5["y"])
    pend("add_junction", path=power, x=pin(rfb1, "1")["x"], y=out5["y"])
    await series(power, rfb1, "2", rfb2, "1")
    await rail(power, rfb2, "2", "GND")
    fb = pin(buck, "FB")
    pend("add_wire", path=power, x1=fb["x"], y1=fb["y"],
               x2=fb["x"], y2=pin(rfb1, "2")["y"])
    pend("add_wire", path=power, x1=fb["x"], y1=pin(rfb1, "2")["y"],
               x2=pin(rfb1, "2")["x"], y2=pin(rfb1, "2")["y"])
    pend("add_junction", path=power, x=pin(rfb1, "2")["x"],
               y=pin(rfb1, "2")["y"])

    # 3.3 V for the MCU and the sensors.
    ldo = await put(power, "Regulator_Linear:AP2112K-3.3", "U2", 170.18, 111.76,
                    "AP2112K-3.3")
    await rail(power, ldo, "VIN", "+5V")
    await rail(power, ldo, "EN", "+5V", length=8 * G)
    await rail(power, ldo, "GND", "GND")
    await rail(power, ldo, "VOUT", "+3V3")
    for ref, x, netname in (("C4", 152.4, "+5V"), ("C5", 190.5, "+3V3")):
        cap = await put(power, "Device:C", ref, x, 132.08, "10u")
        await rail(power, cap, "1", netname)
        await rail(power, cap, "2", "GND")

    # Battery sense: 10:1 into an ADC pin on the MCU sheet.
    rs1 = await put(power, "Device:R", "R4", 76.2, 129.54, "10k")
    rs2 = await put(power, "Device:R", "R5", 76.2, 149.86, "1k")
    await net(power, rs1, "1", "VBAT")
    await series(power, rs1, "2", rs2, "1")
    await rail(power, rs2, "2", "GND")
    mid = pin(rs1, "2")
    pend("add_wire", path=power, x1=mid["x"], y1=mid["y"],
               x2=mid["x"] + 10 * G, y2=mid["y"])
    pend("add_label", path=power, x=mid["x"] + 10 * G, y=mid["y"],
               text="VBAT_SENSE", kind="global", justify="left")

    # Nothing on any page drives the battery, so say so here.
    #
    # VBAT is not one of KiCad's power symbols -- there is no `power:VBAT` --
    # so it is an ordinary global label, and its flag hangs off a stub with
    # that label on it. GND has a symbol and gets the usual treatment.
    flg = await call("add_power_flag", path=power, x=45.72, y=165.1)
    if flg:
        end = (45.72 + 10 * G, 165.1)
        pend("add_wire", path=power, x1=flg["pins"][0]["x"],
                   y1=flg["pins"][0]["y"], x2=end[0], y2=end[1])
        pend("add_label", path=power, x=end[0], y=end[1], text="VBAT",
                   kind="global", justify="left")
    f5 = await call("add_power_flag", path=power, x=127.0, y=165.1)
    s5 = await call("add_power", path=power, x=127.0 + 8 * G, y=165.1,
                    net="+5V")
    if f5 and s5:
        # The 5 V rail comes out of an inductor. ERC sees a passive, not a
        # supply, so nothing tells it this rail is driven.
        pend("add_wire", path=power, x1=f5["pins"][0]["x"],
                   y1=f5["pins"][0]["y"], x2=s5["pins"][0]["x"],
                   y2=s5["pins"][0]["y"])
    gflg = await call("add_power_flag", path=power, x=88.9, y=165.1)
    gsym = await call("add_power", path=power, x=88.9 + 8 * G, y=165.1,
                      net="GND")
    if gflg and gsym:
        pend("add_wire", path=power, x1=gflg["pins"][0]["x"],
                   y1=gflg["pins"][0]["y"], x2=gsym["pins"][0]["x"],
                   y2=gsym["pins"][0]["y"])
    await flush()
    await call("save_sheet", path=power)

    # -- mcu ---------------------------------------------------------------
    mcu = str(OUT / "mcu.kicad_sch")
    await call("new_sheet", path=mcu, paper="A3", title="MCU",
               instance_path=boxes["MCU"]["instance_path"])
    u3 = await put(mcu, "MCU_ST_STM32F4:STM32F405RGTx", "U3", 152.4, 149.86,
                   "STM32F405RGT6")
    if failures:
        return failures
    # Staggered over three columns. All twenty-two at one stub length put
    # their labels in a single line 2.54 mm apart, interleaved with the
    # no-connect crosses on the pins between them, and the result was dense
    # enough to be unreadable while being electrically perfect.
    for index, (number, name) in enumerate(PINMAP):
        await net(mcu, u3, number, name, length=(8 + (index % 3) * 6) * G)
    # Supply pins: four VDD plus VBAT and VDDA, all on the top edge.
    for number in ("1", "19", "32", "48", "64", "13"):
        await rail(mcu, u3, number, "+3V3", length=6 * G)
    for number in ("12", "18", "63"):
        await rail(mcu, u3, number, "GND", length=6 * G)

    # Crystal on PH0/PH1.
    #
    # By label, not by wire. Routing both pins to a crystal standing between
    # them sent two wires down the same column, which shorted PH0 to PH1 --
    # a short that draws perfectly neatly. These are local labels: the crystal
    # is on this page, so the net does not leave it.
    y1 = await put(mcu, "Device:Crystal_GND24", "Y1", 101.6, 114.3, "8MHz",
                   rotation=90, mirror="y")
    await net(mcu, u3, "PH0", "OSC_IN", kind="local")
    await net(mcu, u3, "PH1", "OSC_OUT", kind="local")
    await net(mcu, y1, "3", "OSC_IN", length=8 * G, kind="local")
    await net(mcu, y1, "1", "OSC_OUT", length=8 * G, kind="local")
    await rail(mcu, y1, "2", "GND", length=6 * G)
    for index, (ref, number, name) in enumerate((("C6", "3", "OSC_IN"),
                                                 ("C7", "1", "OSC_OUT"))):
        # Own columns. The crystal's two pins are 7.62 mm apart and a
        # capacitor is 7.62 mm tall, so one column put C6's lower pin exactly
        # on C7's upper one -- OSC_OUT shorted to ground, drawn neatly.
        cap = await put(mcu, "Device:C", ref,
                        pin(y1, number)["x"] + (14 + index * 8) * G,
                        pin(y1, number)["y"] + 4 * G, "20p")
        await net(mcu, cap, "1", name, length=4 * G, kind="local")
        await rail(mcu, cap, "2", "GND")

    # Reset and boot.
    rst = pin(u3, "NRST")
    rr = await put(mcu, "Device:R", "R6", rst["x"] - 10 * G, rst["y"] - 8 * G,
                   "10k")
    pend("add_wire", path=mcu, x1=rst["x"], y1=rst["y"],
               x2=pin(rr, "2")["x"], y2=rst["y"])
    pend("add_wire", path=mcu, x1=pin(rr, "2")["x"], y1=rst["y"],
               x2=pin(rr, "2")["x"], y2=pin(rr, "2")["y"])
    await rail(mcu, rr, "1", "+3V3")
    crst = await put(mcu, "Device:C", "C8", rst["x"] - 18 * G, rst["y"] + 6 * G,
                     "100n")
    pend("add_wire", path=mcu, x1=pin(crst, "1")["x"],
               y1=pin(crst, "1")["y"], x2=pin(crst, "1")["x"], y2=rst["y"])
    pend("add_wire", path=mcu, x1=pin(crst, "1")["x"], y1=rst["y"],
               x2=pin(rr, "2")["x"], y2=rst["y"])
    pend("add_junction", path=mcu, x=pin(rr, "2")["x"], y=rst["y"])
    await rail(mcu, crst, "2", "GND")

    boot = pin(u3, "BOOT0")
    rb = await put(mcu, "Device:R", "R7", boot["x"] - 10 * G, boot["y"] + 8 * G,
                   "10k")
    pend("add_wire", path=mcu, x1=boot["x"], y1=boot["y"],
               x2=pin(rb, "1")["x"], y2=boot["y"])
    pend("add_wire", path=mcu, x1=pin(rb, "1")["x"], y1=boot["y"],
               x2=pin(rb, "1")["x"], y2=pin(rb, "1")["y"])
    await rail(mcu, rb, "2", "GND")

    # VCAP: the core regulator's own capacitors.
    for index, (ref, number) in enumerate((("C9", "VCAP_1"),
                                           ("C10", "VCAP_2"))):
        p = pin(u3, number)
        # Each on its own column. Sharing one shorted the two together, and
        # the drawing looked fine.
        cap = await put(mcu, "Device:C", ref, p["x"] - (8 + index * 6) * G,
                        p["y"] + 5 * G, "2u2")
        pend("add_wire", path=mcu, x1=p["x"], y1=p["y"],
                   x2=pin(cap, "1")["x"], y2=p["y"])
        pend("add_wire", path=mcu, x1=pin(cap, "1")["x"], y1=p["y"],
                   x2=pin(cap, "1")["x"], y2=pin(cap, "1")["y"])
        await rail(mcu, cap, "2", "GND")

    # Decoupling: one per supply pin, in a bank rather than over the part.
    for index in range(5):
        cap = await put(mcu, "Device:C", f"C1{index + 1}", 254.0 + index * 8 * G,
                        88.9, "100n")
        await rail(mcu, cap, "1", "+3V3")
        await rail(mcu, cap, "2", "GND")

    # USB, and the SWD header.
    usb = await put(mcu, "Connector:USB_C_Receptacle_USB2.0_16P", "J2",
                    63.5, 190.5, "USB-C")
    await net(mcu, usb, "A6", "USB_DP", length=8 * G)
    await net(mcu, usb, "A7", "USB_DM", length=8 * G)
    await net(mcu, u3, "PA12", "USB_DP")
    await net(mcu, u3, "PA11", "USB_DM")
    await rail(mcu, usb, "A4", "+5V", length=8 * G)
    await rail(mcu, usb, "A1", "GND", length=6 * G)
    await rail(mcu, usb, "SH", "GND", length=10 * G)
    for number in ("B6", "B7", "A8", "B8"):
        p = pin(usb, number)
        pend("add_no_connect", path=mcu, x=p["x"], y=p["y"])
    for ref, number in (("R8", "CC1"), ("R9", "CC2")):
        p = pin(usb, number)
        res = await put(mcu, "Device:R", ref, p["x"] + 12 * G, p["y"] + 8 * G,
                        "5k1")
        pend("add_wire", path=mcu, x1=p["x"], y1=p["y"],
                   x2=pin(res, "1")["x"], y2=p["y"])
        pend("add_wire", path=mcu, x1=pin(res, "1")["x"], y1=p["y"],
                   x2=pin(res, "1")["x"], y2=pin(res, "1")["y"])
        await rail(mcu, res, "2", "GND")

    swd = await put(mcu, "Connector:Conn_01x04_Pin", "J3", 254.0, 149.86,
                    "SWD", mirror="y")
    await rail(mcu, swd, "1", "+3V3", length=6 * G)
    await net(mcu, swd, "2", "SWDIO", length=6 * G)
    await net(mcu, swd, "3", "SWCLK", length=6 * G)
    await rail(mcu, swd, "4", "GND", length=6 * G)
    await net(mcu, u3, "PA13", "SWDIO")
    await net(mcu, u3, "PA14", "SWCLK")

    # A 64-pin part used for twenty-two signals leaves a lot of pins spare.
    # Marking them is the difference between a decision and an oversight, and
    # it is twenty-odd findings' worth of difference in the report.
    # PINMAP is (pin, net). This wants the PIN names -- taking the net names
    # instead marked twenty-two connected pins as no-connect, and KiCad said so.
    spoken = {number for number, _name in PINMAP} | {
        "PH0", "PH1", "PA11", "PA12", "PA13", "PA14", "NRST", "BOOT0",
        "VCAP_1", "VCAP_2", "VDD", "VBAT", "VDDA", "VSS", "VSSA",
    }
    spare = [p for p in u3["pins"] if p["name"] not in spoken]
    for p in spare:
        pend("add_no_connect", path=mcu, x=p["x"], y=p["y"])
    print(f"  MCU: {len(PINMAP)} signals, {len(spare)} pins marked no-connect")
    await flush()
    await call("save_sheet", path=mcu)

    # -- sensors -----------------------------------------------------------
    sen = str(OUT / "sensors.kicad_sch")
    await call("new_sheet", path=sen, paper="A4", title="Sensors",
               instance_path=boxes["Sensors"]["instance_path"])
    imu = await put(sen, "Sensor_Motion:MPU-6000", "U4", 101.6, 88.9, "MPU-6000")
    if failures:
        return failures
    # Staggered over three columns, and starting well out. Five labels at one
    # stub length sit 2.54 mm apart and print into each other and into the pin
    # numbers -- the same crowding the MCU page had.
    for index, (number, name) in enumerate((("23", "SPI1_SCK"),
                                            ("24", "SPI1_MOSI"),
                                            ("9", "SPI1_MISO"),
                                            ("8", "IMU_CS"),
                                            ("12", "IMU_INT"))):
        await net(sen, imu, number, name, length=(16 + (index % 3) * 9) * G)
    await rail(sen, imu, "13", "+3V3")
    await rail(sen, imu, "18", "GND")
    for number in ("1", "11"):                # CLKIN and FSYNC go to ground
        await rail(sen, imu, number, "GND", length=8 * G)
    for number in ("6", "7"):                 # the auxiliary I2C is unused
        p = pin(imu, number)
        pend("add_no_connect", path=sen, x=p["x"], y=p["y"])
    # REGOUT and CPOUT hang below the part rather than out to its right,
    # which is where IMU_INT's label now reaches.
    for index, (ref, number, value) in enumerate((("C16", "10", "100n"),
                                                  ("C17", "20", "2n2"))):
        p = pin(imu, number)
        cap = await put(sen, "Device:C", ref, p["x"] + (4 + index * 6) * G,
                        p["y"] + 14 * G, value)
        pend("add_wire", path=sen, x1=p["x"], y1=p["y"],
                   x2=pin(cap, "1")["x"], y2=p["y"])
        pend("add_wire", path=sen, x1=pin(cap, "1")["x"], y1=p["y"],
                   x2=pin(cap, "1")["x"], y2=pin(cap, "1")["y"])
        await rail(sen, cap, "2", "GND")
    cimu = await put(sen, "Device:C", "C18", 63.5, 139.7, "100n")
    await rail(sen, cimu, "1", "+3V3")
    await rail(sen, cimu, "2", "GND")

    flash = await put(sen, "Memory_Flash:W25Q128JVS", "U5", 190.5, 96.52,
                      "W25Q128JVS")
    for index, (number, name) in enumerate((("6", "SPI3_SCK"),
                                            ("5", "SPI3_MOSI"),
                                            ("2", "SPI3_MISO"),
                                            ("1", "FLASH_CS"),
                                            ("3", "FLASH_WP"),
                                            ("7", "FLASH_HOLD"))):
        await net(sen, flash, number, name, length=(16 + (index % 3) * 9) * G)
    await rail(sen, flash, "8", "+3V3")
    await rail(sen, flash, "4", "GND")
    # WP and HOLD held inactive through pull-ups rather than tied straight to
    # the rail: an IO pin wired directly to a regulator's output is a pin-type
    # conflict, and a resistor between them is what a real design does anyway.
    #
    # Joined by LABEL, in clear space below the part. Both pins are on the
    # flash's left edge among four signal labels already reaching that way, so
    # a resistor near enough to wire to lands on them. Lying down, so the stub
    # off each end runs sideways and its label reads across.
    for ref, name, y in (("R11", "FLASH_WP", 127.0),
                         ("R12", "FLASH_HOLD", 139.7)):
        res = await put(sen, "Device:R", ref, 203.2, y, "10k", rotation=90)
        await rail(sen, res, "1", "+3V3", length=6 * G)
        await net(sen, res, "2", name, length=6 * G)
    cfl = await put(sen, "Device:C", "C19", 152.4, 139.7, "100n")
    await rail(sen, cfl, "1", "+3V3")
    await rail(sen, cfl, "2", "GND")
    await flush()
    await call("save_sheet", path=sen)

    # -- io ----------------------------------------------------------------
    io = str(OUT / "io.kicad_sch")
    await call("new_sheet", path=io, paper="A4", title="IO",
               instance_path=boxes["IO"]["instance_path"])
    motors = await put(io, "Connector:Conn_01x08_Pin", "J4", 63.5, 88.9,
                       "MOTORS 1-4")
    if failures:
        return failures
    for index, name in enumerate(("M1", "M2", "M3", "M4")):
        await net(io, motors, str(index * 2 + 1), name, length=8 * G)
        await rail(io, motors, str(index * 2 + 2), "GND", length=8 * G)

    for ref, x, value, nets in (
            ("J5", 127.0, "RX", ("RX_TX", "RX_RX")),
            ("J6", 165.1, "VTX", ("VTX_TX", "VTX_RX")),
            ("J7", 203.2, "GPS", ("GPS_TX", "GPS_RX"))):
        conn = await put(io, "Connector:Conn_01x04_Pin", ref, x, 88.9, value)
        await rail(io, conn, "1", "+5V", length=8 * G)
        await net(io, conn, "2", nets[0], length=8 * G)
        await net(io, conn, "3", nets[1], length=8 * G)
        await rail(io, conn, "4", "GND", length=8 * G)

    strip = await put(io, "Connector:Conn_01x03_Pin", "J8", 63.5, 139.7,
                      "LED STRIP")
    await rail(io, strip, "1", "+5V", length=8 * G)
    await net(io, strip, "2", "LED_DATA", length=8 * G)
    await rail(io, strip, "3", "GND", length=8 * G)

    # The buzzer is switched low-side: the MCU pin cannot sink it directly.
    bz = await put(io, "Device:Buzzer", "BZ1", 152.4, 133.35, "5V")
    q1 = await put(io, "Transistor_FET:Q_NMOS_GSD", "Q1", 152.4, 158.75, "2N7002")
    if failures:
        return failures
    await rail(io, bz, "1", "+5V", length=6 * G)
    pend("add_wire", path=io, x1=pin(bz, "2")["x"], y1=pin(bz, "2")["y"],
               x2=pin(q1, "D")["x"], y2=pin(q1, "D")["y"])
    await rail(io, q1, "S", "GND", length=6 * G)
    rg = await put(io, "Device:R", "R10", pin(q1, "G")["x"] - 10 * G,
                   pin(q1, "G")["y"], "100R", rotation=90)
    await series(io, rg, "2", q1, "G")
    await net(io, rg, "1", "BUZZ", length=6 * G)
    await flush()
    await call("save_sheet", path=io)

    # -- what did the design become? --------------------------------------
    report = await call("check_sheet", path=root)
    print(f"check_sheet(root): {report.get('errors')} errors, "
          f"{report.get('warnings')} warnings")
    for f in report.get("findings", [])[:14]:
        print(f"   {f.get('severity')} {f.get('kind')} on {f.get('sheet')} "
              f"{f.get('ref', '')}")
    nets = await call("list_nets", path=root)
    named = [n for n in nets.get("nets", [])
             if not n["name"].startswith("unconnected")]
    print(f"\nlist_nets(root): {nets.get('count')} nets, {len(named)} named")
    for n in named:
        pins = ", ".join(f"{p['ref']}.{p['pin']}" for p in n["pins"])
        print(f"   {n['name']:14s} {pins[:82]}")
    return failures + int(report.get("errors") or 0)


async def main() -> int:
    """Run the build against an in-process server."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
