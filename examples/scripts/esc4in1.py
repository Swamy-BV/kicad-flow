"""AM32 2-6S four-in-one ESC reference design, built only through MCP.

This is intentionally a reviewable reference design, not a current rating.
Four STSPIN32F0A devices each run one independent AM32 channel and drive six
external 60 V MOSFETs.  The board exercise stresses staged placement,
courtyard measurements, four-layer construction, high-current net classes,
rounded outlines, zones, and both 2D and 3D inspection.

Run it::

    python examples/scripts/esc4in1.py
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp

OUT = Path("examples/esc4in1")
G = 1.27
BOARD_W = 44.0
BOARD_H = 43.5

DRIVER_SYM = "Driver_Motor:STSPIN32F0A"
DRIVER_FP = "Package_DFN_QFN:VQFN-48-1EP_7x7mm_P0.5mm_EP2.6x2.6mm"
FET_SYM = "Transistor_FET:CSD18540Q5B"
FET_FP = "Package_TO_SOT_SMD:TDSON-8-1"
R_FP = "Resistor_SMD:R_0402_1005Metric"
C_FP = "Capacitor_SMD:C_0402_1005Metric"
MOTOR_PAD = "Connector_Wire:SolderWirePad_1x01_SMD_2x4mm"
BATTERY_PAD = "Connector_Wire:SolderWirePad_1x01_SMD_4x8mm"
FC_HEADER = "Connector_PinHeader_2.00mm:PinHeader_1x08_P2.00mm_Vertical"
MOUNT = "MountingHole:MountingHole_4.3mm_M4"
BULK_C_FP = "Capacitor_SMD:C_1210_3225Metric"

PHASES = ("U", "V", "W")
GATE_PINS = {
    "U": ("33", "36"),
    "V": ("29", "32"),
    "W": ("25", "28"),
}
OUT_PINS = {"U": "34", "V": "30", "W": "26"}
BOOT_PINS = {"U": "35", "V": "31", "W": "27"}


async def build(client: Client) -> int:
    """Build the hierarchy and board, then measure and render both."""
    if OUT.exists():
        # KiCad keeps a local, read-only Git history here. Preserve it while
        # replacing only the example's generated artifacts.
        for artifact in OUT.iterdir():
            if artifact.name == ".history":
                continue
            if artifact.is_dir():
                shutil.rmtree(artifact)
            else:
                artifact.unlink()
    else:
        OUT.mkdir(parents=True)
    started = time.time()
    failures = 0
    calls = 0

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        nonlocal calls, failures
        calls += 1
        result = await client.call_tool(tool, arguments)
        data = result.data if hasattr(result, "data") else result.structured_content
        reply = data if isinstance(data, dict) else {}
        if not reply.get("ok", False):
            failures += 1
            print(f"FAILED {tool}: {reply.get('error', result)}")
        return reply

    def one(result: dict[str, Any], key: str) -> dict[str, Any]:
        values = result.get(key, [])
        return values[0] if values else {}

    def pin(part: dict[str, Any], name: str) -> dict[str, Any]:
        for item in part.get("pins", []):
            if item["number"] == name or item["name"] == name:
                return item
        raise KeyError(f"{part.get('ref', '?')} has no pin {name}")

    async def put(
        sheet: str,
        lib_id: str,
        ref: str,
        x: float,
        y: float,
        value: str = "",
        rotation: float = 0.0,
        unit: int = 1,
    ) -> dict[str, Any]:
        return one(await call("add_components", path=sheet, parts=[{
            "lib_id": lib_id,
            "ref": ref,
            "x": x,
            "y": y,
            "value": value,
            "rotation": rotation,
            "unit": unit,
        }]), "parts")

    async def wires(sheet: str, items: list[dict[str, float]]) -> None:
        if items:
            await call("add_wires", path=sheet, wires=items)

    async def connect(
        sheet: str,
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        elbow_x: float | None = None,
    ) -> None:
        """Connect two points orthogonally; every bend remains explicit."""
        a, b = first, second
        if a["x"] == b["x"] or a["y"] == b["y"]:
            await wires(sheet, [{
                "x1": a["x"], "y1": a["y"],
                "x2": b["x"], "y2": b["y"],
            }])
            return
        x = elbow_x if elbow_x is not None else b["x"]
        await wires(sheet, [
            {"x1": a["x"], "y1": a["y"], "x2": x, "y2": a["y"]},
            {"x1": x, "y1": a["y"], "x2": x, "y2": b["y"]},
            {"x1": x, "y1": b["y"], "x2": b["x"], "y2": b["y"]},
        ])

    async def label_pin(
        sheet: str,
        part: dict[str, Any],
        pin_name: str,
        text: str,
        *,
        kind: str = "global",
        length: float = 5 * G,
    ) -> dict[str, float]:
        p = pin(part, pin_name)
        away = {
            0.0: (-1.0, 0.0),
            180.0: (1.0, 0.0),
            90.0: (0.0, 1.0),
            270.0: (0.0, -1.0),
        }[p["orientation"] % 360]
        end = {"x": p["x"] + away[0] * length,
               "y": p["y"] + away[1] * length}
        await wires(sheet, [{
            "x1": p["x"], "y1": p["y"],
            "x2": end["x"], "y2": end["y"],
        }])
        await call("add_labels", path=sheet, labels=[{
            "x": end["x"], "y": end["y"], "text": text, "kind": kind,
            "rotation": 90 if away[1] < 0 else 270 if away[1] > 0 else 0,
            "justify": "right" if away[0] < 0 else "left",
        }])
        return end

    async def flag_net(
        sheet: str,
        point: dict[str, Any],
        *,
        x: float,
        y: float,
        rotation: float = 0.0,
    ) -> None:
        """Place a PWR_FLAG and visibly wire its pin to an explicit net."""
        flag = one(await call("add_power_flags", path=sheet, flags=[{
            "x": x, "y": y, "rotation": rotation,
        }]), "flags")
        if flag:
            await connect(sheet, point, flag["pins"][0], elbow_x=point["x"])

    async def flag_named_net(
        sheet: str,
        net: str,
        *,
        x: float,
        y: float,
    ) -> None:
        """Drive a named global rail without crossing a nearby pin fanout."""
        flag = one(await call("add_power_flags", path=sheet, flags=[{
            "x": x, "y": y,
        }]), "flags")
        if not flag:
            return
        p = flag["pins"][0]
        end = {"x": p["x"] + 12 * G, "y": p["y"]}
        await connect(sheet, p, end)
        await call("add_labels", path=sheet, labels=[{
            "x": end["x"], "y": end["y"], "text": net,
            "kind": "global", "justify": "left",
        }])

    async def power_pin(
        sheet: str,
        part: dict[str, Any],
        pin_name: str,
        net: str,
        *,
        distance: float = 4 * G,
    ) -> None:
        p = pin(part, pin_name)
        away = {
            0.0: (-1.0, 0.0),
            180.0: (1.0, 0.0),
            90.0: (0.0, 1.0),
            270.0: (0.0, -1.0),
        }[p["orientation"] % 360]
        x, y = p["x"] + away[0] * distance, p["y"] + away[1] * distance
        symbol = one(await call("add_power", path=sheet, symbols=[{
            "x": x, "y": y, "net": net,
        }]), "symbols")
        if symbol:
            await connect(sheet, p, symbol["pins"][0])

    # Confirm the exact provider records used in the design notes and BOM.
    status = await call("get_parts_provider_status", provider="jlcpcb")
    driver_parts = await call(
        "search_parts", provider="jlcpcb", query="STSPIN32F0A", limit=3,
        manufacturer="STMicroelectronics", min_stock=1,
    )
    fet_parts = await call(
        "search_parts", provider="jlcpcb", query="CSD18540Q5B", limit=3,
        manufacturer="Texas Instruments", min_stock=1,
    )
    print(
        f"JLC catalogue: {status.get('part_count', '?')} parts; "
        f"STSPIN matches {driver_parts.get('count', 0)}, "
        f"MOSFET matches {fet_parts.get('count', 0)}"
    )

    # -- schematic root -------------------------------------------------
    root = str(OUT / "esc4in1.kicad_sch")
    await call("new_sheet", path=root, paper="A4",
               title="AM32 4-in-1 ESC — 2-6S reference")
    boxes: list[dict[str, Any]] = []
    for motor, y in enumerate(
        (76.2, 104.14, 132.08, 160.02), start=1
    ):
        box = one(await call("add_sheets", path=root, sheets=[{
            "name": f"Motor {motor}",
            "filename": f"motor{motor}.kicad_sch",
            "x": 85.09,
            "y": y,
            "width": 76.2,
            "height": 22.86,
            "ports": [{"name": f"DSHOT{motor}", "kind": "bidirectional"}],
        }]), "sheets")
        boxes.append(box)

    bat_p = await put(root, "Connector_Generic:Conn_01x01", "J1",
                      38.1, 33.02, "VBAT+")
    bat_n = await put(root, "Connector_Generic:Conn_01x01", "J2",
                      38.1, 43.18, "VBAT-")
    fc = await put(root, "Connector_Generic:Conn_01x08", "J3",
                   50.8, 68.58, "FC / DSHOT", rotation=270)
    await label_pin(root, bat_p, "1", "VBAT")
    await power_pin(root, bat_n, "1", "GND")
    for motor, box in enumerate(boxes, start=1):
        await connect(root, pin(fc, str(motor)), box["pins"][0],
                      elbow_x=pin(fc, str(motor))["x"])
    await call("add_no_connects", path=root, points=[pin(fc, "5")])
    await label_pin(root, fc, "6", "VBAT")
    await label_pin(root, fc, "7", "+3V3")
    await power_pin(root, fc, "8", "GND")
    await flag_named_net(root, "+3V3", x=190.5, y=20.32)
    bulks: list[dict[str, Any]] = []
    for index, (x, y) in enumerate(
        ((71.12, 35.56), (91.44, 35.56),
         (111.76, 35.56), (132.08, 35.56)), start=101
    ):
        bulk = await put(root, "Device:C", f"C{index}", x, y,
                         "47u 50V")
        bulks.append(bulk)
    vbat_bus_y = 27.94
    gnd_bus_y = 43.18
    for bulk in bulks:
        await connect(root, pin(bulk, "1"),
                      {"x": pin(bulk, "1")["x"], "y": vbat_bus_y})
        await connect(root, pin(bulk, "2"),
                      {"x": pin(bulk, "2")["x"], "y": gnd_bus_y})
    await wires(root, [
        {"x1": pin(bulks[0], "1")["x"], "y1": vbat_bus_y,
         "x2": pin(bulks[-1], "1")["x"], "y2": vbat_bus_y},
        {"x1": pin(bulks[0], "2")["x"], "y1": gnd_bus_y,
         "x2": pin(bulks[-1], "2")["x"], "y2": gnd_bus_y},
    ])
    await connect(root, pin(bat_p, "1"),
                  {"x": pin(bulks[0], "1")["x"], "y": vbat_bus_y},
                  elbow_x=55.88)
    await connect(root, pin(bat_n, "1"),
                  {"x": pin(bulks[0], "2")["x"], "y": gnd_bus_y},
                  elbow_x=60.96)
    await call("add_junctions", path=root, points=[
        {"x": pin(bulk, pad)["x"],
         "y": vbat_bus_y if pad == "1" else gnd_bus_y}
        for bulk in bulks
        for pad in ("1", "2")
    ])
    await flag_net(
        root,
        {"x": pin(bulks[-1], "1")["x"], "y": vbat_bus_y},
        x=147.32, y=25.4,
    )
    await flag_net(
        root,
        {"x": pin(bulks[-1], "2")["x"], "y": gnd_bus_y},
        x=147.32, y=40.64,
    )
    await call("add_texts", path=root, notes=[{
        "x": 88.9, "y": 195.58,
        "text": "AM32 target required; DShot signal is bidirectional",
        "size": 1.27,
    }])
    await call("save_sheet", path=root)

    # -- four repeated motor channels ----------------------------------
    for motor, box in enumerate(boxes, start=1):
        sheet = str(OUT / f"motor{motor}.kicad_sch")
        await call("new_sheet", path=sheet, paper="A4",
                   title=f"AM32 motor {motor}",
                   instance_path=box["instance_path"])
        driver = await put(sheet, DRIVER_SYM, f"U{motor}",
                           55.88, 101.6, "STSPIN32F0A")
        await label_pin(sheet, driver, "13", f"DSHOT{motor}",
                        kind="hierarchical", length=8 * G)
        await label_pin(sheet, driver, "8", "VBAT", length=10 * G)
        await label_pin(sheet, driver, "10", "+3V3", length=5 * G)
        await connect(sheet, pin(driver, "10"), pin(driver, "48"))
        await power_pin(sheet, driver, "44", "GND", distance=5 * G)
        await call("add_junctions", path=sheet,
                   points=[pin(driver, "44")])

        # Three half bridges. Each occupies a separate horizontal band; all
        # coordinates and wire corridors are deliberate caller decisions.
        connected_driver = {"8", "10", "13", "44", "48", "49"}
        phase_rows = (63.5, 101.6, 139.7)
        for phase_index, phase in enumerate(PHASES):
            phase_y = phase_rows[phase_index]
            fet_x = 157.48
            qh_ref = f"Q{(motor - 1) * 6 + phase_index * 2 + 1}"
            ql_ref = f"Q{(motor - 1) * 6 + phase_index * 2 + 2}"
            qh = await put(sheet, FET_SYM, qh_ref,
                           fet_x, phase_y - 4 * G,
                           "CSD18540Q5B")
            ql = await put(sheet, FET_SYM, ql_ref,
                           fet_x, phase_y + 4 * G,
                           "CSD18540Q5B")
            high_pin, low_pin = GATE_PINS[phase]
            connected_driver.update((high_pin, low_pin, OUT_PINS[phase],
                                     BOOT_PINS[phase]))
            rh_ref = f"R{(motor - 1) * 6 + phase_index * 2 + 1}"
            rl_ref = f"R{(motor - 1) * 6 + phase_index * 2 + 2}"
            rh = await put(sheet, "Device:R", rh_ref, 139.7,
                           pin(qh, "G")["y"], "10R", rotation=90)
            rl = await put(sheet, "Device:R", rl_ref, 139.7,
                           pin(ql, "G")["y"], "10R", rotation=90)
            await connect(sheet, pin(driver, high_pin), pin(rh, "1"),
                          elbow_x=106.68 + phase_index * 3 * G)
            await connect(sheet, pin(rh, "2"), pin(qh, "G"))
            await connect(sheet, pin(driver, low_pin), pin(rl, "1"),
                          elbow_x=110.49 + phase_index * 3 * G)
            await connect(sheet, pin(rl, "2"), pin(ql, "G"))
            await label_pin(sheet, qh, "D", "VBAT", length=3 * G)
            await power_pin(sheet, ql, "S", "GND", distance=3 * G)

            phase_node = {"x": pin(qh, "S")["x"], "y": phase_y}
            await connect(sheet, pin(qh, "S"), phase_node)
            await connect(sheet, pin(ql, "D"), phase_node)
            await connect(sheet, pin(driver, OUT_PINS[phase]), phase_node,
                          elbow_x=118.11 + phase_index * 3 * G)
            await call("add_junctions", path=sheet, points=[phase_node])
            motor_pad = await put(
                sheet, "Connector_Generic:Conn_01x01",
                f"J{10 + (motor - 1) * 3 + phase_index + 1}",
                193.04, phase_y,
                f"M{motor}_{phase}",
            )
            await connect(sheet, phase_node, pin(motor_pad, "1"))
            await flag_net(sheet, phase_node, x=180.34,
                           y=phase_y - 7.62)

            boot = await put(
                sheet, "Device:C",
                f"C{(motor - 1) * 3 + phase_index + 1}",
                125.73, phase_y - 10.16, "100n", rotation=90,
            )
            await connect(sheet, pin(driver, BOOT_PINS[phase]), pin(boot, "1"),
                          elbow_x=114.3 + phase_index * 3 * G)
            await connect(sheet, pin(boot, "2"), phase_node,
                          elbow_x=132.08 + phase_index * G)

        # The STSPIN symbol has three current-sense amplifier units. They are
        # shown and explicitly unused rather than silently omitted.
        for unit, x in enumerate((35.56, 58.42, 81.28), start=2):
            amplifier = await put(
                sheet, DRIVER_SYM, f"U{motor}", x, 177.8,
                "STSPIN32F0A", unit=unit,
            )
            await call("add_no_connects", path=sheet, points=[
                {"x": p["x"], "y": p["y"]}
                for p in amplifier.get("pins", [])
            ])

        # Keep every unused pin explicit. This is a reference topology; the
        # AM32 target review decides which ADC/test pins become BEMF/current.
        unused = [
            p for p in driver.get("pins", [])
            if p["number"] not in connected_driver
        ]
        await call("add_no_connects", path=sheet, points=[
            {"x": p["x"], "y": p["y"]} for p in unused
        ])
        await call("move_fields", path=sheet, moves=[
            {"ref": f"U{motor}", "name": "Reference",
             "dx": -12.7, "dy": -40.64},
            {"ref": f"U{motor}", "name": "Value",
             "dx": -12.7, "dy": 40.64},
        ] + [
            {"ref": f"C{(motor - 1) * 3 + phase_index + 1}",
             "name": field,
             "dx": -8.89 if field == "Reference" else -3.81,
             "dy": -3.81 if field == "Reference" else 3.81}
            for phase_index in range(3)
            for field in ("Reference", "Value")
        ])
        await call("save_sheet", path=sheet)

    erc = await call("check_sheet", path=root)
    layout = await call("check_sheet_layout", path=root)
    nets = await call("list_nets", path=root)
    if not erc.get("clean", False):
        failures += 1
        print(f"FAILED schematic ERC: {erc.get('kind_counts', {})}")
    if not layout.get("clean", False):
        failures += 1
        print(f"FAILED schematic layout: {layout.get('kind_counts', {})}")
    print(
        f"schematic: {nets.get('count', '?')} nets; "
        f"ERC {erc.get('errors', '?')}/{erc.get('warnings', '?')}; "
        f"layout {layout.get('errors', '?')}/{layout.get('warnings', '?')}"
    )

    # -- board construction ---------------------------------------------
    board = str(OUT / "esc4in1.kicad_pcb")
    await call("new_board", path=board, layers=4, thickness=1.6)
    await call(
        "set_fabrication_profile", path=board, provider="jlcpcb",
        board_type="rigid_fr4", material="FR-4", outer_copper_oz=2.0,
        inner_copper_oz=1.0, finish="ENIG", soldermask_color="black",
        outline_process="routed", impedance_control=False,
        tier="recommended",
    )
    await call("set_stackup", path=board, layers=[
        {"name": "F.Cu", "kind": "copper", "thickness": 0.07},
        {"name": "dielectric 1", "kind": "prepreg", "thickness": 0.18,
         "material": "FR4", "epsilon_r": 4.2, "loss_tangent": 0.02},
        {"name": "In1.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "dielectric 2", "kind": "core", "thickness": 1.03,
         "material": "FR4", "epsilon_r": 4.3, "loss_tangent": 0.02},
        {"name": "In2.Cu", "kind": "copper", "thickness": 0.035},
        {"name": "dielectric 3", "kind": "prepreg", "thickness": 0.18,
         "material": "FR4", "epsilon_r": 4.2, "loss_tangent": 0.02},
        {"name": "B.Cu", "kind": "copper", "thickness": 0.07},
    ], copper_finish="ENIG", dielectric_constraints=True)
    radius = 3.0
    mid = radius * (2 ** 0.5 - 1)
    await call("add_graphics", path=board, graphics=[
        {"kind": "line", "layer": "Edge.Cuts", "x1": radius, "y1": 0,
         "x2": BOARD_W - radius, "y2": 0},
        {"kind": "arc", "layer": "Edge.Cuts", "x1": BOARD_W - radius,
         "y1": 0, "xm": BOARD_W - mid, "ym": mid,
         "x2": BOARD_W, "y2": radius},
        {"kind": "line", "layer": "Edge.Cuts", "x1": BOARD_W, "y1": radius,
         "x2": BOARD_W, "y2": BOARD_H - radius},
        {"kind": "arc", "layer": "Edge.Cuts", "x1": BOARD_W,
         "y1": BOARD_H - radius, "xm": BOARD_W - mid,
         "ym": BOARD_H - mid, "x2": BOARD_W - radius, "y2": BOARD_H},
        {"kind": "line", "layer": "Edge.Cuts", "x1": BOARD_W - radius,
         "y1": BOARD_H, "x2": radius, "y2": BOARD_H},
        {"kind": "arc", "layer": "Edge.Cuts", "x1": radius,
         "y1": BOARD_H, "xm": mid, "ym": BOARD_H - mid,
         "x2": 0, "y2": BOARD_H - radius},
        {"kind": "line", "layer": "Edge.Cuts", "x1": 0,
         "y1": BOARD_H - radius, "x2": 0, "y2": radius},
        {"kind": "arc", "layer": "Edge.Cuts", "x1": 0, "y1": radius,
         "xm": mid, "ym": mid, "x2": radius, "y2": 0},
    ])

    async def place_stage(name: str, placements: list[dict[str, Any]]) -> None:
        await call("place_footprints", path=board, footprints=placements)
        measured = await call(
            "measure_placement", path=board, edge_clearance=0.4, net_limit=8
        )
        print(
            f"placement {name}: {measured.get('footprint_count', '?')} parts, "
            f"{measured.get('overlap_count', '?')} overlap(s), "
            f"{measured.get('edge_violation_count', '?')} edge violation(s), "
            f"{measured.get('courtyard_area_ratio', 0):.1%} area"
        )

    fixed: list[dict[str, Any]] = []
    for index, (x, y) in enumerate(
        ((6.75, 6.5), (37.25, 6.5),
         (37.25, 37.0), (6.75, 37.0)), start=1
    ):
        fixed.append({"fp_id": MOUNT, "ref": f"H{index}", "x": x, "y": y,
                      "anchor": "courtyard_center", "value": "M4"})
    fixed += [
        {"fp_id": BATTERY_PAD, "ref": "J1", "x": 18.5, "y": 21.75,
         "anchor": "courtyard_center", "side": "F", "value": "VBAT+"},
        {"fp_id": BATTERY_PAD, "ref": "J2", "x": 25.5, "y": 21.75,
         "anchor": "courtyard_center", "side": "F", "value": "GND"},
        {"fp_id": FC_HEADER, "ref": "J3", "x": 22, "y": 2.0,
         "rotation": 90, "anchor": "courtyard_center", "side": "B",
         "value": "FC"},
    ]
    motor_positions = {
        1: [(15.5, 2.5, 0), (22.0, 2.5, 0), (28.5, 2.5, 0)],
        2: [(41.5, 16.0, 90), (41.5, 21.75, 90), (41.5, 27.5, 90)],
        3: [(28.5, 41.0, 0), (22.0, 41.0, 0), (15.5, 41.0, 0)],
        4: [(2.5, 27.5, 90), (2.5, 21.75, 90), (2.5, 16.0, 90)],
    }
    for motor, positions in motor_positions.items():
        for phase_index, (x, y, rotation) in enumerate(positions):
            fixed.append({
                "fp_id": MOTOR_PAD,
                "ref": f"J{10 + (motor - 1) * 3 + phase_index + 1}",
                "x": x, "y": y, "rotation": rotation,
                "anchor": "courtyard_center",
                "value": f"M{motor}_{PHASES[phase_index]}",
            })
    await place_stage("fixed", fixed)

    driver_positions = {1: (26.2, 26.0, 270), 2: (26.2, 17.5, 0),
                        3: (17.8, 26.0, 180), 4: (17.8, 17.5, 90)}
    await place_stage("controllers", [
        {"fp_id": DRIVER_FP, "ref": f"U{motor}", "x": x, "y": y,
         "rotation": rotation, "anchor": "courtyard_center",
         "side": "B", "value": "STSPIN32F0A"}
        for motor, (x, y, rotation) in driver_positions.items()
    ])

    # Each half bridge is a face-to-face pair: high side on F.Cu and low side
    # at the same XY coordinate on B.Cu. Commercial 4-in-1 ESCs use this to
    # put twelve power packages on each face while retaining short phase paths.
    phase_centres = {
        1: [(15.5, 9.0), (22.0, 9.0), (28.5, 9.0)],
        2: [(35.0, 16.0), (35.0, 21.75), (35.0, 27.5)],
        3: [(28.5, 34.5), (22.0, 34.5), (15.5, 34.5)],
        4: [(9.0, 27.5), (9.0, 21.75), (9.0, 16.0)],
    }
    fet_positions: dict[int, list[tuple[float, float, float, str]]] = {}
    for motor, centres in phase_centres.items():
        rotation = 90.0 if motor in (1, 3) else 0.0
        fet_positions[motor] = [
            item
            for x, y in centres
            for item in ((x, y, rotation, "F"), (x, y, rotation, "B"))
        ]
    power_parts: list[dict[str, Any]] = []
    for motor, positions in fet_positions.items():
        for offset, (x, y, rotation, side) in enumerate(positions):
            power_parts.append({
                "fp_id": FET_FP, "ref": f"Q{(motor - 1) * 6 + offset + 1}",
                "x": x, "y": y, "rotation": rotation,
                "anchor": "courtyard_center", "side": side,
                "value": "CSD18540Q5B",
            })
    await place_stage("power stages", power_parts)

    # Gate resistors and bootstrap capacitors live on the back, directly below
    # their channel. Their exact positions are caller data, not inferred groups.
    support: list[dict[str, Any]] = []
    for motor, positions in fet_positions.items():
        for offset, (x, y, rotation, _side) in enumerate(positions):
            dx = -4.8 if motor == 2 else 4.8 if motor == 4 else 0.0
            dy = 4.8 if motor == 1 else -4.8 if motor == 3 else 0.0
            tangent_x = 2.0 if offset % 2 and motor in (1, 3) else 0.0
            tangent_y = 2.0 if offset % 2 and motor in (2, 4) else 0.0
            support.append({
                "fp_id": R_FP, "ref": f"R{(motor - 1) * 6 + offset + 1}",
                "x": x + dx + tangent_x, "y": y + dy + tangent_y,
                "rotation": rotation, "side": "F",
                "anchor": "courtyard_center", "value": "10R",
            })
        _dx, _dy, rotation = driver_positions[motor]
        for phase_index in range(3):
            x, y = phase_centres[motor][phase_index]
            if motor == 1:
                x, y = x - 2.0, y + 4.8
            elif motor == 2:
                x, y = x - 4.8, y - 2.0
            elif motor == 3:
                x, y = x - 2.0, y - 4.8
            else:
                x, y = x + 4.8, y - 2.0
            support.append({
                "fp_id": C_FP,
                "ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                "x": x, "y": y, "rotation": rotation, "side": "F",
                "anchor": "courtyard_center", "value": "100n",
            })
    await place_stage("support", support)

    bulk_positions = [
        (18.5, 15.5), (25.5, 15.5),
        (18.5, 28.0), (25.5, 28.0),
    ]
    await place_stage("bulk capacitors", [
        {"fp_id": BULK_C_FP, "ref": f"C{index}", "x": x, "y": y,
         "rotation": 0, "anchor": "courtyard_center", "side": "F",
         "value": "47u 50V"}
        for index, (x, y) in enumerate(bulk_positions, start=101)
    ])

    # Net assignment is explicit. It mirrors the visible schematic topology.
    pad_nets: list[dict[str, str]] = [
        {"ref": "J1", "pad": "1", "net": "VBAT"},
        {"ref": "J2", "pad": "1", "net": "GND"},
    ]
    for index in range(101, 105):
        pad_nets += [
            {"ref": f"C{index}", "pad": "1", "net": "VBAT"},
            {"ref": f"C{index}", "pad": "2", "net": "GND"},
        ]
    for pad, net in enumerate(
        ("DSHOT1", "DSHOT2", "DSHOT3", "DSHOT4",
         "CURRENT", "VBAT", "+3V3", "GND"), start=1
    ):
        pad_nets.append({"ref": "J3", "pad": str(pad), "net": net})
    for motor in range(1, 5):
        pad_nets += [
            {"ref": f"U{motor}", "pad": "8", "net": "VBAT"},
            {"ref": f"U{motor}", "pad": "44", "net": "GND"},
            {"ref": f"U{motor}", "pad": "49", "net": "GND"},
            {"ref": f"U{motor}", "pad": "13", "net": f"DSHOT{motor}"},
        ]
        for phase_index, phase in enumerate(PHASES):
            high_q = (motor - 1) * 6 + phase_index * 2 + 1
            low_q = high_q + 1
            phase_net = f"M{motor}_{phase}"
            high_gate = f"M{motor}_{phase}_GH"
            low_gate = f"M{motor}_{phase}_GL"
            high_drive = f"M{motor}_{phase}_DH"
            low_drive = f"M{motor}_{phase}_DL"
            high_pin, low_pin = GATE_PINS[phase]
            pad_nets += [
                {"ref": f"U{motor}", "pad": high_pin, "net": high_drive},
                {"ref": f"U{motor}", "pad": low_pin, "net": low_drive},
                {"ref": f"U{motor}", "pad": OUT_PINS[phase], "net": phase_net},
                {"ref": f"U{motor}", "pad": BOOT_PINS[phase],
                 "net": f"M{motor}_{phase}_BOOT"},
                {"ref": f"R{high_q}", "pad": "1", "net": high_drive},
                {"ref": f"R{high_q}", "pad": "2", "net": high_gate},
                {"ref": f"R{low_q}", "pad": "1", "net": low_drive},
                {"ref": f"R{low_q}", "pad": "2", "net": low_gate},
                {"ref": f"Q{high_q}", "pad": "4", "net": high_gate},
                {"ref": f"Q{high_q}", "pad": "5", "net": "VBAT"},
                {"ref": f"Q{low_q}", "pad": "4", "net": low_gate},
                {"ref": f"Q{low_q}", "pad": "5", "net": phase_net},
                {"ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                 "pad": "1", "net": f"M{motor}_{phase}_BOOT"},
                {"ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                 "pad": "2", "net": phase_net},
                {"ref": f"J{10 + (motor - 1) * 3 + phase_index + 1}",
                 "pad": "1", "net": phase_net},
            ]
            for source_pad in ("1", "2", "3"):
                pad_nets += [
                    {"ref": f"Q{high_q}", "pad": source_pad,
                     "net": phase_net},
                    {"ref": f"Q{low_q}", "pad": source_pad, "net": "GND"},
                ]
    await call("set_pad_nets", path=board, pads=pad_nets)
    await call("set_net_classes", path=board, classes=[
        {"name": "POWER", "clearance": 0.25, "track_width": 1.5,
         "via_diameter": 0.9, "via_drill": 0.4},
        {"name": "GATE", "clearance": 0.18, "track_width": 0.25,
         "via_diameter": 0.6, "via_drill": 0.3},
        {"name": "SIGNAL", "clearance": 0.18, "track_width": 0.2,
         "via_diameter": 0.6, "via_drill": 0.3},
    ])
    assignments = [
        {"net": "VBAT", "net_class": "POWER"},
        {"net": "GND", "net_class": "POWER"},
    ]
    for motor in range(1, 5):
        assignments.append({"net": f"DSHOT{motor}", "net_class": "SIGNAL"})
        for phase in PHASES:
            assignments.append({"net": f"M{motor}_{phase}",
                                "net_class": "POWER"})
            for suffix in ("GH", "GL", "DH", "DL", "BOOT"):
                assignments.append({"net": f"M{motor}_{phase}_{suffix}",
                                    "net_class": "GATE"})
    await call("assign_net_classes", path=board, assignments=assignments)

    # Continuous inner planes. Phase copper is routed explicitly below; using
    # broad rectangular phase zones here would silently cover gate and supply
    # pads, which is precisely the kind of compact-looking bad board this
    # example is intended to catch.
    zones: list[dict[str, Any]] = [
        {"boundary": "board_outline", "inset": 0.5, "layer": "In1.Cu",
         "net": "GND", "clearance": 0.25},
        {"boundary": "board_outline", "inset": 0.5, "layer": "In2.Cu",
         "net": "VBAT", "clearance": 0.25},
    ]
    await call("add_zones", path=board, zones=zones)
    all_refs = (
        ["J1", "J2", "J3"]
        + [f"H{i}" for i in range(1, 5)]
        + [f"U{i}" for i in range(1, 5)]
        + [f"Q{i}" for i in range(1, 25)]
        + [f"R{i}" for i in range(1, 25)]
        + [f"C{i}" for i in range(1, 13)]
        + [f"C{i}" for i in range(101, 105)]
        + [f"J{i}" for i in range(11, 23)]
    )
    await call("move_footprint_fields", path=board, moves=[
        {"ref": ref, "name": "Value", "dx": 0, "dy": 0, "hide": True}
        for ref in all_refs
    ] + [
        {"ref": ref, "name": "Reference", "dx": 0, "dy": 0, "hide": True}
        for ref in ([f"R{i}" for i in range(1, 25)]
                    + [f"C{i}" for i in range(1, 13)])
    ])
    await call("add_board_texts", path=board, texts=[
        {"x": 22.0, "y": 21.75, "text": "AM32 4-in-1",
         "layer": "F.SilkS", "size": 1.0},
        {"x": 22.0, "y": 40.0, "text": "2-6S • PROPS OFF",
         "layer": "B.SilkS", "size": 0.8, "mirror": True},
    ])
    await call("save_board", path=board)
    await call("refill_zones", path=board)
    final_placement = await call(
        "measure_placement", path=board, edge_clearance=0.4, net_limit=12
    )
    unrouted = await call("unrouted_connections", path=board)
    drc = await call("check_board", path=board)
    drc_errors = [item for item in drc.get("findings", [])
                  if item.get("severity") == "error"]
    print(
        f"board: {final_placement.get('footprint_count', '?')} footprints, "
        f"{final_placement.get('overlap_count', '?')} overlaps, "
        f"{unrouted.get('count', '?')} unrouted, {len(drc_errors)} DRC errors"
    )

    await call("render_board_layout", path=board,
               output_file=str(OUT / "esc4in1-layout-top.png"), side="top")
    await call("render_board_layout", path=board,
               output_file=str(OUT / "esc4in1-layout-bottom.png"), side="bottom")
    await call("render_board", path=board,
               output_file=str(OUT / "esc4in1-3d.png"), width=1400,
               height=1100, quality="high", rotate="-28,0,32",
               perspective=True, floor=True, zoom=0.78)
    await call("render_board", path=board,
               output_file=str(OUT / "esc4in1-3d-bottom.png"), width=1400,
               height=1100, quality="high", rotate="152,0,32",
               perspective=True, floor=True, zoom=0.78)
    await call("render_schematic", path=root, output_dir=str(OUT))
    print(f"{calls} MCP calls in {time.time() - started:.1f}s; {failures} failed")
    return failures


async def main() -> int:
    """Run the example against the same in-process MCP used by other tests."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
