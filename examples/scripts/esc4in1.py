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
FC_HEADER = "Connector_JST:JST_SH_BM08B-SRSS-TB_1x08-1MP_P1.00mm_Vertical"
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
    calls = 0

    async def call(tool: str, **arguments: Any) -> dict[str, Any]:
        """Call one MCP tool and refuse malformed or unsuccessful replies."""
        nonlocal calls
        calls += 1
        result = await client.call_tool(tool, arguments)
        data = result.data if hasattr(result, "data") else result.structured_content
        reply = data if isinstance(data, dict) else {}
        if not reply.get("ok", False):
            raise RuntimeError(f"{tool} failed: {reply.get('error', result)}")
        return reply

    def many(result: dict[str, Any], key: str, expected: int) -> list[dict[str, Any]]:
        """Require one returned item for every item in a plural request."""
        values = result.get(key, [])
        if result.get("count") != expected or len(values) != expected:
            raise RuntimeError(
                f"{key}: requested {expected}, returned "
                f"count={result.get('count')!r} and {len(values)} item(s)"
            )
        return values

    def indexed(parts: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
        """Index placed or predicted parts without losing multi-unit symbols."""
        out = {(part["ref"], int(part.get("unit", 1))): part for part in parts}
        if len(out) != len(parts):
            raise RuntimeError("duplicate (ref, unit) in component reply")
        return out

    def pin(part: dict[str, Any], name: str) -> dict[str, Any]:
        for item in part.get("pins", []):
            if item["number"] == name or item["name"] == name:
                return item
        raise KeyError(f"{part.get('ref', '?')} has no pin {name}")

    def xy(item: dict[str, Any]) -> dict[str, float]:
        """Project a richer reply object onto an exact point request."""
        return {"x": float(item["x"]), "y": float(item["y"])}

    def connect(
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        elbow_x: float | None = None,
    ) -> list[dict[str, float]]:
        """Return explicit orthogonal segments; never make an MCP call."""
        a, b = first, second
        if a["x"] == b["x"] and a["y"] == b["y"]:
            return []
        if a["x"] == b["x"] or a["y"] == b["y"]:
            return [
                {
                    "x1": a["x"],
                    "y1": a["y"],
                    "x2": b["x"],
                    "y2": b["y"],
                }
            ]
        x = elbow_x if elbow_x is not None else b["x"]
        segments = [
            {"x1": a["x"], "y1": a["y"], "x2": x, "y2": a["y"]},
            {"x1": x, "y1": a["y"], "x2": x, "y2": b["y"]},
            {"x1": x, "y1": b["y"], "x2": b["x"], "y2": b["y"]},
        ]
        return [
            segment
            for segment in segments
            if (segment["x1"], segment["y1"]) != (segment["x2"], segment["y2"])
        ]

    def label_pin(
        part: dict[str, Any],
        pin_name: str,
        text: str,
        *,
        kind: str = "global",
        length: float = 5 * G,
    ) -> tuple[list[dict[str, float]], dict[str, Any], dict[str, float]]:
        """Return an explicit stub and label payload for one pin."""
        p = pin(part, pin_name)
        away = {
            0.0: (-1.0, 0.0),
            180.0: (1.0, 0.0),
            90.0: (0.0, 1.0),
            270.0: (0.0, -1.0),
        }[p["orientation"] % 360]
        end = {"x": p["x"] + away[0] * length, "y": p["y"] + away[1] * length}
        segments = [
            {
                "x1": p["x"],
                "y1": p["y"],
                "x2": end["x"],
                "y2": end["y"],
            }
        ]
        label = {
            "x": end["x"],
            "y": end["y"],
            "text": text,
            "kind": kind,
            "rotation": 90 if away[1] < 0 else 270 if away[1] > 0 else 0,
            "justify": "right" if away[0] < 0 else "left",
        }
        return segments, label, end

    def power_pin(
        part: dict[str, Any],
        pin_name: str,
        net: str,
        *,
        distance: float = 4 * G,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a power-symbol request and the component pin it serves."""
        p = pin(part, pin_name)
        away = {
            0.0: (-1.0, 0.0),
            180.0: (1.0, 0.0),
            90.0: (0.0, 1.0),
            270.0: (0.0, -1.0),
        }[p["orientation"] % 360]
        x, y = p["x"] + away[0] * distance, p["y"] + away[1] * distance
        return {"x": x, "y": y, "net": net}, p

    # Confirm the exact provider records used in the design notes and BOM.
    status = await call("get_parts_provider_status", provider="jlcpcb")
    driver_parts = await call(
        "search_parts",
        provider="jlcpcb",
        query="STSPIN32F0A",
        limit=3,
        manufacturer="STMicroelectronics",
        min_stock=1,
    )
    fet_parts = await call(
        "search_parts",
        provider="jlcpcb",
        query="CSD18540Q5B",
        limit=3,
        manufacturer="Texas Instruments",
        min_stock=1,
    )
    print(
        f"JLC catalogue: {status.get('part_count', '?')} parts; "
        f"STSPIN matches {driver_parts.get('count', 0)}, "
        f"MOSFET matches {fet_parts.get('count', 0)}"
    )

    # -- schematic root -------------------------------------------------
    # Library geometry is a preflight fact, not something to discover after
    # mutating the page. Multi-unit symbols are queried one unit at a time.
    schematic_started = calls
    for lib_id, units in (
        (DRIVER_SYM, range(1, 5)),
        (FET_SYM, range(1, 2)),
        ("Device:R", range(1, 2)),
        ("Device:C", range(1, 2)),
        ("Connector_Generic:Conn_01x01", range(1, 2)),
        ("Connector_Generic:Conn_01x08", range(1, 2)),
    ):
        for unit in units:
            await call("symbol_pins", lib_id=lib_id, unit=unit)

    root = str(OUT / "esc4in1.kicad_sch")
    await call(
        "new_sheet", path=root, paper="A4", title="AM32 4-in-1 ESC — 2-6S reference"
    )
    sheet_plans = [
        {
            "name": f"Motor {motor}",
            "filename": f"motor{motor}.kicad_sch",
            "x": 85.09,
            "y": y,
            "width": 76.2,
            "height": 22.86,
            "ports": [{"name": f"DSHOT{motor}", "kind": "bidirectional"}],
        }
        for motor, y in enumerate((76.2, 104.14, 132.08, 160.02), start=1)
    ]
    boxes = many(
        await call("add_sheets", path=root, sheets=sheet_plans),
        "sheets",
        len(sheet_plans),
    )

    root_plan = [
        {
            "lib_id": "Connector_Generic:Conn_01x01",
            "ref": "J1",
            "x": 38.1,
            "y": 33.02,
            "value": "VBAT+",
        },
        {
            "lib_id": "Connector_Generic:Conn_01x01",
            "ref": "J2",
            "x": 38.1,
            "y": 43.18,
            "value": "VBAT-",
        },
        {
            "lib_id": "Connector_Generic:Conn_01x08",
            "ref": "J3",
            "x": 50.8,
            "y": 68.58,
            "value": "FC / DSHOT",
            "rotation": 270,
        },
    ] + [
        {"lib_id": "Device:C", "ref": f"C{index}", "x": x, "y": y, "value": "47u 50V"}
        for index, (x, y) in enumerate(
            ((71.12, 35.56), (91.44, 35.56), (111.76, 35.56), (132.08, 35.56)),
            start=101,
        )
    ]
    root_measure = await call("measure_schematic_placement", path=root, parts=root_plan)
    if not root_measure.get("clean", False):
        raise RuntimeError(
            f"root placement preflight: {root_measure.get('findings', [])}"
        )
    root_parts = indexed(
        many(
            await call("add_components", path=root, parts=root_plan),
            "parts",
            len(root_plan),
        )
    )
    bat_p = root_parts[("J1", 1)]
    bat_n = root_parts[("J2", 1)]
    fc = root_parts[("J3", 1)]
    bulks = [root_parts[(f"C{index}", 1)] for index in range(101, 105)]

    root_wires: list[dict[str, float]] = []
    root_labels: list[dict[str, Any]] = []
    stub, label, _ = label_pin(bat_p, "1", "VBAT")
    root_wires += stub
    root_labels.append(label)
    for motor, box in enumerate(boxes, start=1):
        root_wires += connect(
            pin(fc, str(motor)),
            box["pins"][0],
            elbow_x=pin(fc, str(motor))["x"],
        )
    for pin_name, text in (("6", "VBAT"), ("7", "+3V3")):
        stub, label, _ = label_pin(fc, pin_name, text)
        root_wires += stub
        root_labels.append(label)

    root_power_specs = []
    root_power_sources = []
    for part, pin_name in ((bat_n, "1"), (fc, "8")):
        spec, source = power_pin(part, pin_name, "GND")
        root_power_specs.append(spec)
        root_power_sources.append(source)
    root_powers = many(
        await call("add_power", path=root, symbols=root_power_specs),
        "symbols",
        len(root_power_specs),
    )
    for source, power in zip(root_power_sources, root_powers, strict=True):
        root_wires += connect(source, power["pins"][0])

    vbat_bus_y = 27.94
    gnd_bus_y = 43.18
    for bulk in bulks:
        root_wires += connect(
            pin(bulk, "1"),
            {"x": pin(bulk, "1")["x"], "y": vbat_bus_y},
        )
        root_wires += connect(
            pin(bulk, "2"),
            {"x": pin(bulk, "2")["x"], "y": gnd_bus_y},
        )
    root_wires += [
        {
            "x1": pin(bulks[0], "1")["x"],
            "y1": vbat_bus_y,
            "x2": pin(bulks[-1], "1")["x"],
            "y2": vbat_bus_y,
        },
        {
            "x1": pin(bulks[0], "2")["x"],
            "y1": gnd_bus_y,
            "x2": pin(bulks[-1], "2")["x"],
            "y2": gnd_bus_y,
        },
    ]
    root_wires += connect(
        pin(bat_p, "1"),
        {"x": pin(bulks[0], "1")["x"], "y": vbat_bus_y},
        elbow_x=55.88,
    )
    root_wires += connect(
        pin(bat_n, "1"),
        {"x": pin(bulks[0], "2")["x"], "y": gnd_bus_y},
        elbow_x=60.96,
    )

    root_flags = many(
        await call(
            "add_power_flags",
            path=root,
            flags=[
                {"x": 190.5, "y": 20.32},
                {"x": 147.32, "y": 25.4},
                {"x": 147.32, "y": 40.64},
            ],
        ),
        "flags",
        3,
    )
    named_flag_pin = root_flags[0]["pins"][0]
    named_end = {"x": named_flag_pin["x"] + 12 * G, "y": named_flag_pin["y"]}
    root_wires += connect(named_flag_pin, named_end)
    root_labels.append(
        {
            "x": named_end["x"],
            "y": named_end["y"],
            "text": "+3V3",
            "kind": "global",
            "justify": "left",
        }
    )
    root_wires += connect(
        {"x": pin(bulks[-1], "1")["x"], "y": vbat_bus_y},
        root_flags[1]["pins"][0],
        elbow_x=pin(bulks[-1], "1")["x"],
    )
    root_wires += connect(
        {"x": pin(bulks[-1], "2")["x"], "y": gnd_bus_y},
        root_flags[2]["pins"][0],
        elbow_x=pin(bulks[-1], "2")["x"],
    )

    await call("add_wires", path=root, wires=root_wires)
    await call("add_labels", path=root, labels=root_labels)
    await call("add_no_connects", path=root, points=[xy(pin(fc, "5"))])
    await call(
        "add_junctions",
        path=root,
        points=[
            {"x": pin(bulk, pad)["x"], "y": vbat_bus_y if pad == "1" else gnd_bus_y}
            for bulk in bulks
            for pad in ("1", "2")
        ],
    )
    await call(
        "add_texts",
        path=root,
        notes=[
            {
                "x": 88.9,
                "y": 195.58,
                "text": "AM32 target required; DShot signal is bidirectional",
                "size": 1.27,
            }
        ],
    )
    await call("save_sheet", path=root)

    # -- four repeated motor channels ----------------------------------
    for motor, box in enumerate(boxes, start=1):
        sheet = str(OUT / f"motor{motor}.kicad_sch")
        await call(
            "new_sheet",
            path=sheet,
            paper="A4",
            title=f"AM32 motor {motor}",
            instance_path=box["instance_path"],
        )
        # Compose the complete page before the first placement call. Three
        # half bridges occupy explicit horizontal bands; repeated channels use
        # the same plan with reference substitutions, never incremental nudges.
        page_plan: list[dict[str, Any]] = [
            {
                "lib_id": DRIVER_SYM,
                "ref": f"U{motor}",
                "x": 55.88,
                "y": 101.6,
                "value": "STSPIN32F0A",
            }
        ]
        connected_driver = {"8", "10", "13", "44", "48", "49"}
        phase_rows = (63.5, 101.6, 139.7)
        phase_plan: list[dict[str, Any]] = []
        for phase_index, phase in enumerate(PHASES):
            phase_y = phase_rows[phase_index]
            fet_x = 157.48
            qh_ref = f"Q{(motor - 1) * 6 + phase_index * 2 + 1}"
            ql_ref = f"Q{(motor - 1) * 6 + phase_index * 2 + 2}"
            high_pin, low_pin = GATE_PINS[phase]
            connected_driver.update(
                (high_pin, low_pin, OUT_PINS[phase], BOOT_PINS[phase])
            )
            rh_ref = f"R{(motor - 1) * 6 + phase_index * 2 + 1}"
            rl_ref = f"R{(motor - 1) * 6 + phase_index * 2 + 2}"
            pad_ref = f"J{10 + (motor - 1) * 3 + phase_index + 1}"
            boot_ref = f"C{(motor - 1) * 3 + phase_index + 1}"
            qh_y, ql_y = phase_y - 4 * G, phase_y + 4 * G
            page_plan += [
                {
                    "lib_id": FET_SYM,
                    "ref": qh_ref,
                    "x": fet_x,
                    "y": qh_y,
                    "value": "CSD18540Q5B",
                },
                {
                    "lib_id": FET_SYM,
                    "ref": ql_ref,
                    "x": fet_x,
                    "y": ql_y,
                    "value": "CSD18540Q5B",
                },
                {
                    "lib_id": "Device:R",
                    "ref": rh_ref,
                    "x": 139.7,
                    "y": qh_y,
                    "value": "10R",
                    "rotation": 90,
                },
                {
                    "lib_id": "Device:R",
                    "ref": rl_ref,
                    "x": 139.7,
                    "y": ql_y,
                    "value": "10R",
                    "rotation": 90,
                },
                {
                    "lib_id": "Connector_Generic:Conn_01x01",
                    "ref": pad_ref,
                    "x": 193.04,
                    "y": phase_y,
                    "value": f"M{motor}_{phase}",
                },
                {
                    "lib_id": "Device:C",
                    "ref": boot_ref,
                    "x": 125.73,
                    "y": phase_y - 10.16,
                    "value": "100n",
                    "rotation": 90,
                },
            ]
            phase_plan.append(
                {
                    "index": phase_index,
                    "phase": phase,
                    "y": phase_y,
                    "qh": qh_ref,
                    "ql": ql_ref,
                    "rh": rh_ref,
                    "rl": rl_ref,
                    "pad": pad_ref,
                    "boot": boot_ref,
                }
            )

        # The STSPIN symbol has three current-sense amplifier units. They are
        # shown and explicitly unused rather than silently omitted.
        for unit, x in enumerate((35.56, 58.42, 81.28), start=2):
            page_plan.append(
                {
                    "lib_id": DRIVER_SYM,
                    "ref": f"U{motor}",
                    "x": x,
                    "y": 177.8,
                    "value": "STSPIN32F0A",
                    "unit": unit,
                }
            )

        measured = await call(
            "measure_schematic_placement", path=sheet, parts=page_plan
        )
        blocking = [
            item
            for item in measured.get("findings", [])
            if item.get("kind") in ("symbol_symbol_overlap", "page_bounds")
        ]
        if blocking:
            raise RuntimeError(f"motor {motor} placement preflight: {blocking}")
        parts = indexed(
            many(
                await call("add_components", path=sheet, parts=page_plan),
                "parts",
                len(page_plan),
            )
        )
        driver = parts[(f"U{motor}", 1)]

        page_wires: list[dict[str, float]] = []
        page_labels: list[dict[str, Any]] = []
        page_junctions = [xy(pin(driver, "44"))]
        page_power_specs: list[dict[str, Any]] = []
        page_power_sources: list[dict[str, Any]] = []
        page_flag_specs: list[dict[str, Any]] = []
        phase_nodes: list[dict[str, Any]] = []

        for pin_name, text, kind, length in (
            ("13", f"DSHOT{motor}", "hierarchical", 8 * G),
            ("8", "VBAT", "global", 10 * G),
            ("10", "+3V3", "global", 5 * G),
        ):
            stub, label, _ = label_pin(driver, pin_name, text, kind=kind, length=length)
            page_wires += stub
            page_labels.append(label)
        page_wires += connect(pin(driver, "10"), pin(driver, "48"))
        spec, source = power_pin(driver, "44", "GND", distance=5 * G)
        page_power_specs.append(spec)
        page_power_sources.append(source)

        for phase_item in phase_plan:
            phase_index = int(phase_item["index"])
            phase = str(phase_item["phase"])
            phase_y = float(phase_item["y"])
            qh = parts[(str(phase_item["qh"]), 1)]
            ql = parts[(str(phase_item["ql"]), 1)]
            rh = parts[(str(phase_item["rh"]), 1)]
            rl = parts[(str(phase_item["rl"]), 1)]
            motor_pad = parts[(str(phase_item["pad"]), 1)]
            boot = parts[(str(phase_item["boot"]), 1)]
            high_pin, low_pin = GATE_PINS[phase]

            page_wires += connect(
                pin(driver, high_pin),
                pin(rh, "1"),
                elbow_x=106.68 + phase_index * 3 * G,
            )
            page_wires += connect(pin(rh, "2"), pin(qh, "G"))
            page_wires += connect(
                pin(driver, low_pin),
                pin(rl, "1"),
                elbow_x=110.49 + phase_index * 3 * G,
            )
            page_wires += connect(pin(rl, "2"), pin(ql, "G"))
            stub, label, _ = label_pin(qh, "D", "VBAT", length=3 * G)
            page_wires += stub
            page_labels.append(label)
            spec, source = power_pin(ql, "S", "GND", distance=3 * G)
            page_power_specs.append(spec)
            page_power_sources.append(source)

            phase_node = {"x": pin(qh, "S")["x"], "y": phase_y}
            phase_nodes.append(phase_node)
            page_junctions.append(phase_node)
            page_wires += connect(pin(qh, "S"), phase_node)
            page_wires += connect(pin(ql, "D"), phase_node)
            page_wires += connect(
                pin(driver, OUT_PINS[phase]),
                phase_node,
                elbow_x=118.11 + phase_index * 3 * G,
            )
            page_wires += connect(phase_node, pin(motor_pad, "1"))
            page_flag_specs.append({"x": 180.34, "y": phase_y - 7.62})
            page_wires += connect(
                pin(driver, BOOT_PINS[phase]),
                pin(boot, "1"),
                elbow_x=114.3 + phase_index * 3 * G,
            )
            page_wires += connect(
                pin(boot, "2"),
                phase_node,
                elbow_x=132.08 + phase_index * G,
            )

        powers = many(
            await call("add_power", path=sheet, symbols=page_power_specs),
            "symbols",
            len(page_power_specs),
        )
        for source, power in zip(page_power_sources, powers, strict=True):
            page_wires += connect(source, power["pins"][0])
        flags = many(
            await call("add_power_flags", path=sheet, flags=page_flag_specs),
            "flags",
            len(page_flag_specs),
        )
        for phase_node, flag in zip(phase_nodes, flags, strict=True):
            page_wires += connect(phase_node, flag["pins"][0], elbow_x=phase_node["x"])

        await call("add_wires", path=sheet, wires=page_wires)
        await call("add_labels", path=sheet, labels=page_labels)
        await call("add_junctions", path=sheet, points=page_junctions)

        # Keep every unused pin explicit. This is a reference topology; the
        # AM32 target review decides which ADC/test pins become BEMF/current.
        unused = [
            p for p in driver.get("pins", []) if p["number"] not in connected_driver
        ]
        no_connects = [{"x": p["x"], "y": p["y"]} for p in unused]
        for unit in range(2, 5):
            no_connects += [
                {"x": p["x"], "y": p["y"]}
                for p in parts[(f"U{motor}", unit)].get("pins", [])
            ]
        await call("add_no_connects", path=sheet, points=no_connects)
        await call(
            "move_fields",
            path=sheet,
            moves=[
                {"ref": f"U{motor}", "name": "Reference", "dx": -12.7, "dy": -40.64},
                {"ref": f"U{motor}", "name": "Value", "dx": -12.7, "dy": 40.64},
            ]
            + [
                {
                    "ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                    "name": field,
                    "dx": -8.89 if field == "Reference" else -3.81,
                    "dy": -3.81 if field == "Reference" else 3.81,
                }
                for phase_index in range(3)
                for field in ("Reference", "Value")
            ],
        )
        await call("save_sheet", path=sheet)

    erc = await call("check_sheet", path=root)
    layout = await call("check_sheet_layout", path=root)
    nets = await call("list_nets", path=root)
    if not erc.get("clean", False):
        raise RuntimeError(f"schematic ERC: {erc.get('kind_counts', {})}")
    if not layout.get("clean", False):
        raise RuntimeError(f"schematic layout: {layout.get('kind_counts', {})}")
    await call("render_schematic", path=root, output_dir=str(OUT))
    schematic_calls = calls - schematic_started
    if schematic_calls > 70:
        raise RuntimeError(f"schematic call budget exceeded: {schematic_calls} > 70")
    print(
        f"schematic: {nets.get('count', '?')} nets; "
        f"ERC {erc.get('errors', '?')}/{erc.get('warnings', '?')}; "
        f"layout {layout.get('errors', '?')}/{layout.get('warnings', '?')}; "
        f"{schematic_calls} calls"
    )

    # -- board construction ---------------------------------------------
    board = str(OUT / "esc4in1.kicad_pcb")
    await call("new_board", path=board, layers=4, thickness=1.6)
    await call(
        "set_fabrication_profile",
        path=board,
        provider="jlcpcb",
        board_type="rigid_fr4",
        material="FR-4",
        outer_copper_oz=2.0,
        inner_copper_oz=1.0,
        finish="ENIG",
        soldermask_color="black",
        outline_process="routed",
        impedance_control=False,
        tier="recommended",
    )
    await call(
        "set_stackup",
        path=board,
        layers=[
            {"name": "F.Cu", "kind": "copper", "thickness": 0.07},
            {
                "name": "dielectric 1",
                "kind": "prepreg",
                "thickness": 0.18,
                "material": "FR4",
                "epsilon_r": 4.2,
                "loss_tangent": 0.02,
            },
            {"name": "In1.Cu", "kind": "copper", "thickness": 0.035},
            {
                "name": "dielectric 2",
                "kind": "core",
                "thickness": 1.03,
                "material": "FR4",
                "epsilon_r": 4.3,
                "loss_tangent": 0.02,
            },
            {"name": "In2.Cu", "kind": "copper", "thickness": 0.035},
            {
                "name": "dielectric 3",
                "kind": "prepreg",
                "thickness": 0.18,
                "material": "FR4",
                "epsilon_r": 4.2,
                "loss_tangent": 0.02,
            },
            {"name": "B.Cu", "kind": "copper", "thickness": 0.07},
        ],
        copper_finish="ENIG",
        dielectric_constraints=True,
    )
    radius = 3.0
    mid = radius * (2**0.5 - 1)
    await call(
        "add_graphics",
        path=board,
        graphics=[
            {
                "kind": "line",
                "layer": "Edge.Cuts",
                "x1": radius,
                "y1": 0,
                "x2": BOARD_W - radius,
                "y2": 0,
            },
            {
                "kind": "arc",
                "layer": "Edge.Cuts",
                "x1": BOARD_W - radius,
                "y1": 0,
                "xm": BOARD_W - mid,
                "ym": mid,
                "x2": BOARD_W,
                "y2": radius,
            },
            {
                "kind": "line",
                "layer": "Edge.Cuts",
                "x1": BOARD_W,
                "y1": radius,
                "x2": BOARD_W,
                "y2": BOARD_H - radius,
            },
            {
                "kind": "arc",
                "layer": "Edge.Cuts",
                "x1": BOARD_W,
                "y1": BOARD_H - radius,
                "xm": BOARD_W - mid,
                "ym": BOARD_H - mid,
                "x2": BOARD_W - radius,
                "y2": BOARD_H,
            },
            {
                "kind": "line",
                "layer": "Edge.Cuts",
                "x1": BOARD_W - radius,
                "y1": BOARD_H,
                "x2": radius,
                "y2": BOARD_H,
            },
            {
                "kind": "arc",
                "layer": "Edge.Cuts",
                "x1": radius,
                "y1": BOARD_H,
                "xm": mid,
                "ym": BOARD_H - mid,
                "x2": 0,
                "y2": BOARD_H - radius,
            },
            {
                "kind": "line",
                "layer": "Edge.Cuts",
                "x1": 0,
                "y1": BOARD_H - radius,
                "x2": 0,
                "y2": radius,
            },
            {
                "kind": "arc",
                "layer": "Edge.Cuts",
                "x1": 0,
                "y1": radius,
                "xm": mid,
                "ym": mid,
                "x2": radius,
                "y2": 0,
            },
        ],
    )

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
        ((6.75, 6.5), (37.25, 6.5), (37.25, 37.0), (6.75, 37.0)), start=1
    ):
        fixed.append(
            {
                "fp_id": MOUNT,
                "ref": f"H{index}",
                "x": x,
                "y": y,
                "anchor": "courtyard_center",
                "value": "M4",
            }
        )
    fixed += [
        {
            "fp_id": BATTERY_PAD,
            "ref": "J1",
            "x": 18.5,
            "y": 21.75,
            "anchor": "courtyard_center",
            "side": "F",
            "value": "VBAT+",
        },
        {
            "fp_id": BATTERY_PAD,
            "ref": "J2",
            "x": 25.5,
            "y": 21.75,
            "anchor": "courtyard_center",
            "side": "F",
            "value": "GND",
        },
        {
            "fp_id": FC_HEADER,
            "ref": "J3",
            "x": 22,
            "y": 2.6,
            "rotation": 0,
            "anchor": "courtyard_center",
            "side": "B",
            "value": "FC",
        },
    ]
    motor_positions = {
        1: [(28.5, 2.5, 0), (22.0, 2.5, 0), (15.5, 2.5, 0)],
        2: [(41.5, 27.5, 90), (41.5, 21.75, 90), (41.5, 16.0, 90)],
        3: [(15.5, 41.0, 0), (22.0, 41.0, 0), (28.5, 41.0, 0)],
        4: [(2.5, 16.0, 90), (2.5, 21.75, 90), (2.5, 27.5, 90)],
    }
    for motor, positions in motor_positions.items():
        for phase_index, (x, y, rotation) in enumerate(positions):
            fixed.append(
                {
                    "fp_id": MOTOR_PAD,
                    "ref": f"J{10 + (motor - 1) * 3 + phase_index + 1}",
                    "x": x,
                    "y": y,
                    "rotation": rotation,
                    "anchor": "courtyard_center",
                    "value": f"M{motor}_{PHASES[phase_index]}",
                }
            )
    await place_stage("fixed", fixed)

    driver_positions = {
        1: (17.8, 17.3, 270),
        2: (26.2, 17.3, 180),
        3: (26.2, 26.2, 90),
        4: (17.8, 26.2, 0),
    }
    await place_stage(
        "controllers",
        [
            {
                "fp_id": DRIVER_FP,
                "ref": f"U{motor}",
                "x": x,
                "y": y,
                "rotation": rotation,
                "anchor": "courtyard_center",
                "side": "B",
                "value": "STSPIN32F0A",
            }
            for motor, (x, y, rotation) in driver_positions.items()
        ],
    )

    # Each half bridge is a face-to-face pair: high side on F.Cu and low side
    # at the same XY coordinate on B.Cu. Commercial 4-in-1 ESCs use this to
    # put twelve power packages on each face while retaining short phase paths.
    phase_centres = {
        1: [(28.5, 9.0), (22.0, 9.0), (15.5, 9.0)],
        2: [(35.0, 27.5), (35.0, 21.75), (35.0, 16.0)],
        3: [(15.5, 34.5), (22.0, 34.5), (28.5, 34.5)],
        4: [(9.0, 16.0), (9.0, 21.75), (9.0, 27.5)],
    }
    # Orient every bridge radially: the high-side source pads and low-side
    # drain pad face the motor terminal, while VBAT/GND face the board centre.
    # These are explicit caller choices made from the returned footprint pad
    # geometry; the placement primitive does not infer electrical topology.
    fet_rotations = {1: 270.0, 2: 180.0, 3: 90.0, 4: 0.0}
    fet_positions: dict[int, list[tuple[float, float, float, str]]] = {}
    for motor, centres in phase_centres.items():
        rotation = fet_rotations[motor]
        fet_positions[motor] = [
            item
            for x, y in centres
            for item in ((x, y, rotation, "F"), (x, y, rotation, "B"))
        ]
    power_parts: list[dict[str, Any]] = []
    for motor, positions in fet_positions.items():
        for offset, (x, y, rotation, side) in enumerate(positions):
            power_parts.append(
                {
                    "fp_id": FET_FP,
                    "ref": f"Q{(motor - 1) * 6 + offset + 1}",
                    "x": x,
                    "y": y,
                    "rotation": rotation,
                    "anchor": "courtyard_center",
                    "side": side,
                    "value": "CSD18540Q5B",
                }
            )
    await place_stage("power stages", power_parts)

    # Each channel gets the same explicit radial support cluster in the gap
    # between its FET and driver. This is a caller-authored placement template,
    # not a backend floorplanner: all coordinates and ordering remain visible.
    motor_radial = {
        1: (0.0, -1.0),
        2: (1.0, 0.0),
        3: (0.0, 1.0),
        4: (-1.0, 0.0),
    }
    support: list[dict[str, Any]] = []
    for motor, centres in phase_centres.items():
        dx, dy = motor_radial[motor]
        tx, ty = -dy, dx
        rotation = (fet_rotations[motor] + 180.0) % 360.0
        for phase_index, (cx, cy) in enumerate(centres):
            high_q = (motor - 1) * 6 + phase_index * 2 + 1
            low_q = high_q + 1
            bx, by = cx - 4.8 * dx, cy - 4.8 * dy
            for ref, fp_id, value, tangent in (
                (f"R{high_q}", R_FP, "10R", -1.5),
                (f"R{low_q}", R_FP, "10R", 0.0),
                (
                    f"C{(motor - 1) * 3 + phase_index + 1}",
                    C_FP,
                    "100n",
                    1.5,
                ),
            ):
                support.append(
                    {
                        "fp_id": fp_id,
                        "ref": ref,
                        "x": bx + tangent * tx,
                        "y": by + tangent * ty,
                        "rotation": rotation,
                        "side": "F",
                        "anchor": "courtyard_center",
                        "value": value,
                    }
                )
    await place_stage("support", support)

    # At the four inner corners, the last capacitor in one radial cluster and
    # first resistor in the next need the same square of front copper. Move
    # those four capacitors into the deliberately reserved central-front gaps.
    await call(
        "move_footprints",
        path=board,
        moves=[
            {"ref": "C1", "x": 28.5, "y": 15.8, "anchor": "courtyard_center"},
            {"ref": "C4", "x": 28.2, "y": 27.5, "anchor": "courtyard_center"},
            {"ref": "C7", "x": 15.5, "y": 27.7, "anchor": "courtyard_center"},
            {"ref": "C10", "x": 15.8, "y": 16.0, "anchor": "courtyard_center"},
        ],
    )

    # The first layout put these over gate support parts. A back-side row fits
    # between the lower bridge courtyards and the board edge.
    bulk_positions = [
        (13.8, 40.5),
        (19.2, 40.5),
        (24.6, 40.5),
        (30.0, 40.5),
    ]
    await place_stage(
        "bulk capacitors",
        [
            {
                "fp_id": BULK_C_FP,
                "ref": f"C{index}",
                "x": x,
                "y": y,
                "rotation": 0,
                "anchor": "courtyard_center",
                "side": "B",
                "value": "47u 50V",
            }
            for index, (x, y) in enumerate(bulk_positions, start=101)
        ],
    )

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
        ("DSHOT1", "DSHOT2", "DSHOT3", "DSHOT4", "CURRENT", "VBAT", "+3V3", "GND"),
        start=1,
    ):
        pad_nets.append({"ref": "J3", "pad": str(pad), "net": net})
    for motor in range(1, 5):
        pad_nets += [
            {"ref": f"U{motor}", "pad": "8", "net": "VBAT"},
            {"ref": f"U{motor}", "pad": "10", "net": "+3V3"},
            {"ref": f"U{motor}", "pad": "44", "net": "GND"},
            {"ref": f"U{motor}", "pad": "48", "net": "+3V3"},
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
                {
                    "ref": f"U{motor}",
                    "pad": BOOT_PINS[phase],
                    "net": f"M{motor}_{phase}_BOOT",
                },
                {"ref": f"R{high_q}", "pad": "1", "net": high_drive},
                {"ref": f"R{high_q}", "pad": "2", "net": high_gate},
                {"ref": f"R{low_q}", "pad": "1", "net": low_drive},
                {"ref": f"R{low_q}", "pad": "2", "net": low_gate},
                {"ref": f"Q{high_q}", "pad": "4", "net": high_gate},
                {"ref": f"Q{high_q}", "pad": "5", "net": "VBAT"},
                {"ref": f"Q{low_q}", "pad": "4", "net": low_gate},
                {"ref": f"Q{low_q}", "pad": "5", "net": phase_net},
                {
                    "ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                    "pad": "1",
                    "net": f"M{motor}_{phase}_BOOT",
                },
                {
                    "ref": f"C{(motor - 1) * 3 + phase_index + 1}",
                    "pad": "2",
                    "net": phase_net,
                },
                {
                    "ref": f"J{10 + (motor - 1) * 3 + phase_index + 1}",
                    "pad": "1",
                    "net": phase_net,
                },
            ]
            for source_pad in ("1", "2", "3"):
                pad_nets += [
                    {"ref": f"Q{high_q}", "pad": source_pad, "net": phase_net},
                    {"ref": f"Q{low_q}", "pad": source_pad, "net": "GND"},
                ]
    await call("set_pad_nets", path=board, pads=pad_nets)
    await call(
        "set_net_classes",
        path=board,
        classes=[
            {
                "name": "POWER",
                "clearance": 0.25,
                "track_width": 1.5,
                "via_diameter": 0.8,
                "via_drill": 0.3,
            },
            {
                "name": "GATE",
                "clearance": 0.18,
                "track_width": 0.25,
                "via_diameter": 0.6,
                "via_drill": 0.3,
            },
            {
                "name": "SIGNAL",
                "clearance": 0.18,
                "track_width": 0.2,
                "via_diameter": 0.6,
                "via_drill": 0.3,
            },
        ],
    )
    assignments = [
        {"net": "VBAT", "net_class": "POWER"},
        {"net": "GND", "net_class": "POWER"},
    ]
    for motor in range(1, 5):
        assignments.append({"net": f"DSHOT{motor}", "net_class": "SIGNAL"})
        for phase in PHASES:
            assignments.append({"net": f"M{motor}_{phase}", "net_class": "POWER"})
            for suffix in ("GH", "GL", "DH", "DL", "BOOT"):
                assignments.append(
                    {"net": f"M{motor}_{phase}_{suffix}", "net_class": "GATE"}
                )
    await call("assign_net_classes", path=board, assignments=assignments)

    # Read the transformed board coordinates back from the placement call
    # boundary. Routing never reimplements footprint rotation or mirroring.
    placed_reply = await call("list_footprints", path=board, with_pads=True)
    placed = {item["ref"]: item for item in placed_reply["footprints"]}

    def board_pad(ref: str, number: str) -> dict[str, Any]:
        matches = [
            item for item in placed[ref]["pads"] if item["number"] == number
        ]
        if len(matches) != 1:
            raise RuntimeError(f"{ref}.{number}: expected one pad, got {len(matches)}")
        return matches[0]

    def point(item: dict[str, Any]) -> tuple[float, float]:
        return float(item["x"]), float(item["y"])

    tracks: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []

    def track(
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        layer: str,
        width: float,
        net: str,
    ) -> None:
        if start == end:
            return
        tracks.append(
            {
                "x1": start[0],
                "y1": start[1],
                "x2": end[0],
                "y2": end[1],
                "layer": layer,
                "width": width,
                "net": net,
            }
        )

    for motor in range(1, 5):
        dx, dy = motor_radial[motor]
        tx, ty = -dy, dx
        for phase_index, phase in enumerate(PHASES):
            high_q = (motor - 1) * 6 + phase_index * 2 + 1
            low_q = high_q + 1
            net = f"M{motor}_{phase}"
            cx, cy = phase_centres[motor][phase_index]

            # The front VBAT drain and rear phase drain overlap through the
            # laminate, so a through-via may not land in that field.  Put the
            # phase transition in a tangential package gap instead.  U and V
            # use their positive-tangent gap; W uses the outer negative gap.
            tangent_offset = 2.9
            phase_via = (
                cx + tangent_offset * tx,
                cy + tangent_offset * ty,
            )
            vias.append(
                {
                    "x": phase_via[0],
                    "y": phase_via[1],
                    "net": net,
                    "diameter": 0.6,
                    "drill": 0.3,
                }
            )

            high_sources = [
                point(board_pad(f"Q{high_q}", source_pad))
                for source_pad in ("1", "2", "3")
            ]
            high_middle = high_sources[1]
            for source in (high_sources[0], high_sources[2]):
                track(
                    source,
                    high_middle,
                    layer="F.Cu",
                    width=0.45,
                    net=net,
                )
            high_outer = (
                high_middle[0] + dx,
                high_middle[1] + dy,
            )
            track(
                high_middle,
                high_outer,
                layer="F.Cu",
                width=0.45,
                net=net,
            )
            high_corner = (
                phase_via[0] if tx else high_outer[0],
                phase_via[1] if ty else high_outer[1],
            )
            track(
                high_outer,
                high_corner,
                layer="F.Cu",
                width=0.45,
                net=net,
            )
            track(
                high_corner,
                phase_via,
                layer="F.Cu",
                width=0.35,
                net=net,
            )
            low_drain = point(board_pad(f"Q{low_q}", "5"))
            low_corner = (
                phase_via[0] if tx else low_drain[0],
                phase_via[1] if ty else low_drain[1],
            )
            track(
                low_drain,
                low_corner,
                layer="B.Cu",
                width=0.35,
                net=net,
            )
            track(
                low_corner,
                phase_via,
                layer="B.Cu",
                width=0.35,
                net=net,
            )
            track(
                point(board_pad(f"J{11 + (motor - 1) * 3 + phase_index}", "1")),
                high_middle,
                layer="F.Cu",
                width=1.5,
                net=net,
            )

    # The bottom capacitors use a common via row placed between the three
    # The rear bulk-capacitor row shares the motor-3 escape corridor. Its
    # dogbones are intentionally deferred until that corridor is complete;
    # crossing a phase route merely to reduce the ratsnest is not acceptable.

    # The SMD flight-controller header reaches the planes through two short
    # back-side dogbones clear of the top motor terminals.
    header_vbat = point(board_pad("J3", "6"))
    header_vbat_corner = (header_vbat[0], 5.3)
    header_vbat_via = (32.5, 5.3)
    vias.append(
        {
            "x": header_vbat_via[0],
            "y": header_vbat_via[1],
            "net": "VBAT",
            "diameter": 0.8,
            "drill": 0.3,
        }
    )
    track(
        header_vbat,
        header_vbat_corner,
        layer="B.Cu",
        width=0.35,
        net="VBAT",
    )
    track(
        header_vbat_corner,
        header_vbat_via,
        layer="B.Cu",
        width=0.35,
        net="VBAT",
    )

    # Declare unfilled planes before the non-mutating copper preflight. The
    # scratch board fills them only after candidate copper is present, so stale
    # fill cannot merge nets while pcbnew rebuilds connectivity.
    zones: list[dict[str, Any]] = [
        {
            "boundary": "board_outline",
            "inset": 0.5,
            "layer": "F.Cu",
            "net": "VBAT",
            "clearance": 0.25,
            "pad_connection": "solid",
        },
        {
            "boundary": "board_outline",
            "inset": 0.5,
            "layer": "In1.Cu",
            "net": "GND",
            "clearance": 0.25,
            "pad_connection": "solid",
        },
        {
            "boundary": "board_outline",
            "inset": 0.5,
            "layer": "In2.Cu",
            "net": "VBAT",
            "clearance": 0.25,
            "pad_connection": "solid",
        },
        {
            "boundary": "board_outline",
            "inset": 0.5,
            "layer": "B.Cu",
            "net": "GND",
            "clearance": 1.0,
            "pad_connection": "solid",
        },
    ]
    await call("add_zones", path=board, zones=zones)

    copper_preflight = await call(
        "check_board", path=board, tracks=tracks, vias=vias
    )
    new_critical = [
        finding
        for finding in copper_preflight.get("new_findings", [])
        if finding.get("severity") == "error"
        and finding.get("kind") not in {"unconnected_items", "isolated_copper"}
    ]
    if new_critical:
        critical_kinds: dict[str, int] = {}
        for finding in new_critical:
            kind = str(finding.get("kind", "unknown"))
            critical_kinds[kind] = critical_kinds.get(kind, 0) + 1
        print(f"routing preflight rejected physical violations: {critical_kinds}")
        for finding in new_critical:
            print(f"  {finding}")
        raise RuntimeError(
            f"routing preflight introduced {len(new_critical)} physical violation(s)"
        )
    new_kind_counts: dict[str, int] = {}
    for finding in copper_preflight.get("new_findings", []):
        kind = str(finding.get("kind", "unknown"))
        new_kind_counts[kind] = new_kind_counts.get(kind, 0) + 1
    print(
        "routing preflight: "
        f"{copper_preflight.get('new_error_count', '?')} new DRC error(s), "
        f"0 shorts/crossings, kinds={new_kind_counts}; applying the inspected copper"
    )
    await call("add_vias", path=board, vias=vias)
    await call("add_tracks", path=board, tracks=tracks)
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
    await call(
        "move_footprint_fields",
        path=board,
        moves=[
            {"ref": ref, "name": "Value", "dx": 0, "dy": 0, "hide": True}
            for ref in all_refs
        ]
        + [
            {"ref": ref, "name": "Reference", "dx": 0, "dy": 0, "hide": True}
            for ref in (
                [f"R{i}" for i in range(1, 25)] + [f"C{i}" for i in range(1, 13)]
            )
        ],
    )
    await call(
        "add_board_texts",
        path=board,
        texts=[
            {
                "x": 22.0,
                "y": 21.75,
                "text": "AM32 4-in-1",
                "layer": "F.SilkS",
                "size": 1.0,
            },
            {
                "x": 22.0,
                "y": 40.0,
                "text": "2-6S • PROPS OFF",
                "layer": "B.SilkS",
                "size": 0.8,
                "mirror": True,
            },
        ],
    )
    await call("save_board", path=board)
    await call("refill_zones", path=board)
    final_placement = await call(
        "measure_placement", path=board, edge_clearance=0.4, net_limit=12
    )
    unrouted = await call("unrouted_connections", path=board)
    drc = await call("check_board", path=board)
    drc_errors = [
        item for item in drc.get("findings", []) if item.get("severity") == "error"
    ]
    physical_errors = [
        item for item in drc_errors if item.get("kind") != "unconnected_items"
    ]
    if physical_errors:
        kinds: dict[str, int] = {}
        for finding in physical_errors:
            kind = str(finding.get("kind", "unknown"))
            kinds[kind] = kinds.get(kind, 0) + 1
        raise RuntimeError(f"final board has physical DRC errors: {kinds}")
    print(
        f"board: {final_placement.get('footprint_count', '?')} footprints, "
        f"{final_placement.get('overlap_count', '?')} overlaps, "
        f"{unrouted.get('count', '?')} unrouted, {len(drc_errors)} DRC errors"
    )

    await call(
        "render_board_layout",
        path=board,
        output_file=str(OUT / "esc4in1-layout-top.png"),
        side="top",
    )
    await call(
        "render_board_layout",
        path=board,
        output_file=str(OUT / "esc4in1-layout-bottom.png"),
        side="bottom",
    )
    await call(
        "render_board",
        path=board,
        output_file=str(OUT / "esc4in1-3d.png"),
        width=1400,
        height=1100,
        quality="high",
        rotate="-28,0,32",
        perspective=True,
        floor=True,
        zoom=0.78,
    )
    await call(
        "render_board",
        path=board,
        output_file=str(OUT / "esc4in1-3d-bottom.png"),
        width=1400,
        height=1100,
        quality="high",
        rotate="152,0,32",
        perspective=True,
        floor=True,
        zoom=0.78,
    )
    print(f"{calls} MCP calls in {time.time() - started:.1f}s; 0 failed")
    return 0


async def main() -> int:
    """Run the example against the same in-process MCP used by other tests."""
    async with Client(mcp) as client:
        return await build(client)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
