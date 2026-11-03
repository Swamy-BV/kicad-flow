"""Exercise KiCad 11 schematic hierarchy through unchanged MCP primitives.

The build and reopen phases run in separate processes. Root, child and nested
child edits start unsaved; only native IPC saves the project. The second
process checks that the same connectivity survives a fresh native load.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from itertools import pairwise
from pathlib import Path
from typing import Any

from fastmcp import Client

from kicad_flow.server import mcp


async def exercise_ports(client: Client[Any], directory: Path, phase: str) -> None:
    """Verify actual sheet-port connectivity without design-wide global labels."""
    root = directory / "ports" / "ports.kicad_sch"
    child = root.parent / "child.kicad_sch"

    async def call(tool: str, path: Path, **args: Any) -> dict[str, Any]:
        data: dict[str, Any] = (
            await client.call_tool(tool, {"path": str(path), **args})
        ).data
        assert data.get("ok"), (tool, data)
        return data

    if phase == "build":
        await call("new_sheet", root)
        made = await call(
            "add_sheets",
            root,
            sheets=[
                {
                    "name": "Child",
                    "filename": "child.kicad_sch",
                    "x": 101.6,
                    "y": 76.2,
                    "ports": [
                        {"name": name, "kind": "input"}
                        for name in ("SIG_IO", "RETURN_IO")
                    ],
                }
            ],
        )
        box = made["sheets"][0]
        await call("new_sheet", child, instance_path=box["instance_path"])
        for path, ref, rotation in ((root, "R11", 90), (child, "R12", 0)):
            part = (
                await call(
                    "add_components",
                    path,
                    parts=[
                        {
                            "lib_id": "Device:R",
                            "ref": ref,
                            "x": 50.8,
                            "y": 76.2 if path == root else 50.8,
                            "rotation": rotation,
                        }
                    ],
                )
            )["parts"][0]
            pins = sorted(part["pins"], key=lambda pin: pin["number"])
            if path == root:
                routes = [
                    [
                        (pins[0]["x"], pins[0]["y"]),
                        (46.99, 69.85),
                        (93.98, 69.85),
                        (93.98, box["pins"][0]["y"]),
                        (box["pins"][0]["x"], box["pins"][0]["y"]),
                    ],
                    [
                        (pins[1]["x"], pins[1]["y"]),
                        (86.36, 76.2),
                        (86.36, box["pins"][1]["y"]),
                        (box["pins"][1]["x"], box["pins"][1]["y"]),
                    ],
                ]
            else:
                routes = [
                    [
                        (p["x"], p["y"]),
                        (p["x"], p["y"] + (-7.62 if i == 0 else 7.62)),
                        (p["x"] - 20.32, p["y"] + (-7.62 if i == 0 else 7.62)),
                    ]
                    for i, p in enumerate(pins)
                ]
            wires = [
                {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
                for route in routes
                for a, b in pairwise(route)
            ]
            for offset in range(0, len(wires), 5):
                await call("add_wires", path, wires=wires[offset : offset + 5])
            if path == child:
                await call(
                    "add_labels",
                    path,
                    labels=[
                        {
                            "x": route[-1][0],
                            "y": route[-1][1],
                            "text": name,
                            "kind": "hierarchical",
                        }
                        for route, name in zip(
                            routes, ("SIG_IO", "RETURN_IO"), strict=True
                        )
                    ],
                )
        await call("save_sheet", root)
    nets = await call("list_nets", root)
    groups = {
        tuple(sorted((p["ref"], p["pin"]) for p in n["pins"])) for n in nets["nets"]
    }
    assert groups == {
        (("R11", "1"), ("R12", "1")),
        (("R11", "2"), ("R12", "2")),
    }, nets
    moved = await call(
        "move_sheets",
        root,
        moves=[
            {
                "name": "Child",
                "x": 101.6,
                "y": 76.2,
            }
        ],
    )
    assert all(
        p["kind"] == "input" and p["orientation"] == 180.0
        for p in moved["sheets"][0]["pins"]
    ), moved


async def exercise(directory: Path, phase: str) -> None:
    """Build or reopen a portable hierarchy, using MCP for every CAD operation."""
    root = directory / "project" / "controller.kicad_sch"
    child = root.parent / "blocks" / "power.kicad_sch"
    nested = child.parent / "nested" / "sense.kicad_sch"
    sibling = root.parent / "io.kicad_sch"
    async with Client(mcp) as client:

        async def call(tool: str, path: Path, **args: Any) -> dict[str, Any]:
            result = await client.call_tool(tool, {"path": str(path), **args})
            data: dict[str, Any] = result.data
            assert data.get("ok"), (tool, data)
            return data

        if phase == "build":
            await call("new_sheet", root, title="Native IPC hierarchy")
            boxes = await call(
                "add_sheets",
                root,
                sheets=[
                    {
                        "name": "Power",
                        "filename": "blocks/power.kicad_sch",
                        "x": 101.6,
                        "y": 76.2,
                    },
                    {
                        "name": "IO",
                        "filename": "io.kicad_sch",
                        "x": 152.4,
                        "y": 76.2,
                    },
                ],
            )
            await call(
                "new_sheet",
                sibling,
                title="IO",
                instance_path=(boxes["sheets"][1]["instance_path"]),
            )
            await call(
                "new_sheet",
                child,
                title="Power",
                instance_path=(boxes["sheets"][0]["instance_path"]),
            )
            boxes = await call(
                "add_sheets",
                child,
                sheets=[
                    {
                        "name": "Sense",
                        "filename": "nested/sense.kicad_sch",
                        "x": 101.6,
                        "y": 76.2,
                    }
                ],
            )
            await call(
                "new_sheet",
                nested,
                title="Sense",
                instance_path=(boxes["sheets"][0]["instance_path"]),
            )
            for path, ref in (
                (root, "R1"),
                (child, "R2"),
                (nested, "R3"),
                (sibling, "R4"),
            ):
                made = await call(
                    "add_components",
                    path,
                    parts=[
                        {
                            "lib_id": "Device:R",
                            "ref": ref,
                            "value": "10k",
                            "x": 50.8,
                            "y": 50.8,
                        }
                    ],
                )
                pins = made["parts"][0]["pins"]
                ends = [
                    {"x": p["x"] - 20.32, "y": p["y"] + (-7.62 if index == 0 else 7.62)}
                    for index, p in enumerate(pins)
                ]
                wires = []
                for pin, end in zip(pins, ends, strict=True):
                    wires.extend(
                        [
                            {
                                "x1": pin["x"],
                                "y1": pin["y"],
                                "x2": pin["x"],
                                "y2": end["y"],
                            },
                            {
                                "x1": pin["x"],
                                "y1": end["y"],
                                "x2": end["x"],
                                "y2": end["y"],
                            },
                        ]
                    )
                await call("add_wires", path, wires=wires)
                await call(
                    "add_labels",
                    path,
                    labels=[
                        {
                            "x": p["x"],
                            "y": p["y"],
                            "text": name,
                            "kind": "global",
                        }
                        for p, name in zip(ends, ("COMMON", "RETURN"), strict=True)
                    ],
                )
            refused = (
                await client.call_tool(
                    "move_components",
                    {
                        "path": str(root),
                        "moves": [
                            {"ref": "R1", "x": 63.5, "y": 63.5},
                            {"ref": "MISSING", "x": 76.2, "y": 76.2},
                        ],
                    },
                )
            ).data
            assert refused["ok"] is False and refused["applied_count"] == 0
            before_scene = await call("inspect_schematic_scene", root)
            native_refusal = (
                await client.call_tool(
                    "add_sheets",
                    {
                        "path": str(root),
                        "sheets": [
                            {
                                "name": "Staged",
                                "filename": "staged.kicad_sch",
                                "x": 76.2,
                                "y": 101.6,
                            },
                            {
                                "name": "Recursive",
                                "filename": root.as_posix(),
                                "x": 127.0,
                                "y": 101.6,
                            },
                        ],
                    },
                )
            ).data
            assert not native_refusal["ok"], native_refusal
            assert native_refusal["applied_count"] == 0
            assert native_refusal["index"] == 1
            assert "recursive" in native_refusal["error"], native_refusal
            after_scene = await call("inspect_schematic_scene", root)
            assert before_scene["objects"] == after_scene["objects"]
            first_refusal = (
                await client.call_tool(
                    "add_sheets",
                    {
                        "path": str(root),
                        "sheets": [
                            {
                                "name": "RecursiveFirst",
                                "filename": root.as_posix(),
                                "x": 76.2,
                                "y": 101.6,
                            },
                            {
                                "name": "Unapplied",
                                "filename": "unapplied.kicad_sch",
                                "x": 127.0,
                                "y": 101.6,
                            },
                        ],
                    },
                )
            ).data
            assert not first_refusal["ok"] and first_refusal["index"] == 0
            assert first_refusal["applied_count"] == 0
            parts = (await call("list_components", root))["parts"]
            assert len(parts) == 1, parts
            assert parts[0]["x"] == 50.8 and parts[0]["y"] == 50.8
            assert not root.exists(), "Build should still be unsaved"
            diagnostic = directory / "diagnostic.kicad_sch"
            await call("new_sheet", diagnostic)
            made = await call(
                "add_components",
                diagnostic,
                parts=[
                    {
                        "lib_id": "Device:R",
                        "ref": "R99",
                        "x": 50.8,
                        "y": 50.8,
                    }
                ],
            )
            expected = {(p["number"], p["x"], p["y"]) for p in made["parts"][0]["pins"]}
            checked = await call("check_sheet", diagnostic)
            found = {
                (f["pin"], f["x"], f["y"])
                for f in checked["findings"]
                if f.get("ref") == "R99" and f["kind"] == "pin_not_connected"
            }
            assert found == expected, checked
        nets = await call("list_nets", root)
        pins_by_net = {
            net["name"]: sorted((pin["ref"], pin["pin"]) for pin in net["pins"])
            for net in nets["nets"]
        }
        assert pins_by_net == {
            "COMMON": [("R1", "1"), ("R2", "1"), ("R3", "1"), ("R4", "1")],
            "RETURN": [("R1", "2"), ("R2", "2"), ("R3", "2"), ("R4", "2")],
        }, pins_by_net
        for path, expected in ((child, {"R2", "R3"}), (nested, {"R3"})):
            local = await call("list_nets", path)
            refs = {pin["ref"] for net in local["nets"] for pin in net["pins"]}
            assert refs == expected, (path, local)
            if path == child:
                image = await call(
                    "render_schematic",
                    path,
                    output_dir=str(directory / "child"),
                    save=False,
                    dpi=100,
                )
                assert image["count"] == 2, image
        if phase == "build":
            await call("save_sheet", root)
        for path in (root, child, nested, sibling):
            assert path.is_file(), path
            parts = await call("list_components", path)
            assert parts["count"] == 1
        images = await call(
            "render_schematic",
            root,
            output_dir=str(directory / phase),
            dpi=100,
            save=False,
        )
        assert images["count"] == 4, images
        print(
            json.dumps(
                {"phase": phase, "nets": pins_by_net, "images": images["images"]}
            ),
            flush=True,
        )
        await exercise_ports(client, directory, phase)
    text = root.read_text(encoding="utf-8")
    assert '"blocks/power.kicad_sch"' in text
    assert str(directory) not in text and directory.as_posix() not in text


def main() -> None:
    """Run build, fresh reopen and a relocated-project reopen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("build", "reopen"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.phase:
        assert args.directory is not None
        asyncio.run(exercise(args.directory.resolve(), args.phase))
        return
    output = Path("out/schematic-ipc").resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    environment = {**os.environ, "KICAD_FLOW_AUTOSAVE": "0", "KICAD_FLOW_MONITOR": "0"}
    for phase in ("build", "reopen"):
        subprocess.run(
            [
                sys.executable,
                __file__,
                "--phase",
                phase,
                "--directory",
                str(directory),
            ],
            env=environment,
            check=True,
        )
        assert not list(directory.rglob("*.lck")), "Owned native session leaked locks"
    relocated = directory.with_name(directory.name + "-relocated")
    assert directory.resolve().parent == output
    assert relocated.resolve().parent == output
    directory.rename(relocated)
    subprocess.run(
        [
            sys.executable,
            __file__,
            "--phase",
            "reopen",
            "--directory",
            str(relocated),
        ],
        env=environment,
        check=True,
    )
    print(
        "PASS: native IPC hierarchy, atomic rollback, reopen and relocation: "
        f"{relocated}"
    )


if __name__ == "__main__":
    main()
