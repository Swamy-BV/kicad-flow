"""Exercise native companion attachment and live MCP edits without a model login.

The example leaves its disposable project open in KiCad for visual inspection.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import Client


async def exercise(root: Path, build: bool) -> None:
    """Use the unmodified MCP primitives for fixture creation and live edits."""
    from kicad_flow.server import mcp

    sch = str(root / "companion.kicad_sch")
    pcb = str(root / "companion.kicad_pcb")
    async with Client(mcp) as client:

        async def call(name: str, **arguments: Any) -> dict[str, Any]:
            result = await client.call_tool(name, arguments)
            data = result.structured_content
            assert isinstance(data, dict) and data.get("ok") is not False, data
            return data

        if build:
            await call("new_sheet", path=sch)
            await call(
                "add_components",
                path=sch,
                parts=[
                    {
                        "ref": "R1",
                        "lib_id": "Device:R",
                        "value": "10k",
                        "x": 76.2,
                        "y": 76.2,
                    }
                ],
            )
            await call(
                "set_fields",
                path=sch,
                fields=[
                    {
                        "ref": "R1",
                        "name": "Footprint",
                        "value": "Resistor_SMD:R_0603_1608Metric",
                    }
                ],
            )
            await call("save_sheet", path=sch)
            await call(
                "update_board_from_schematic",
                schematic_path=sch,
                board_path=pcb,
                placements=[{"ref": "R1", "x": 20, "y": 12.5}],
            )
            await call(
                "add_graphics",
                path=pcb,
                graphics=[
                    {
                        "kind": "rectangle",
                        "layer": "Edge.Cuts",
                        "x1": 0,
                        "y1": 0,
                        "x2": 40,
                        "y2": 25,
                    }
                ],
            )
            await call("save_board", path=pcb)
        else:
            assert (await call("get_component", path=sch, ref="R1"))["value"] == "10k"
            before = await call("get_footprint", path=pcb, ref="R1")
            await call(
                "set_fields",
                path=sch,
                fields=[
                    {
                        "ref": "R1",
                        "name": "Value",
                        "value": "22k",
                    }
                ],
            )
            await call(
                "update_board_from_schematic", schematic_path=sch, board_path=pcb
            )
            after = await call("get_footprint", path=pcb, ref="R1")
            assert after["value"] == "22k", after
            assert all(after[k] == before[k] for k in ("x", "y", "rotation", "side"))
            assert (await call("get_component", path=sch, ref="R1"))["value"] == "22k"


def main() -> None:
    """Build headless, then attach and edit through actual visible GUI IPC."""
    if len(sys.argv) == 3 and sys.argv[1] == "--build":
        asyncio.run(exercise(Path(sys.argv[2]), True))
        return
    from kicad_flow.agent import AgentSession
    from kicad_flow.agent.client import AgentClient
    from kicad_flow.agent.companion import CompanionService
    from kicad_flow.backend import DesktopProject

    output = Path("out/companion-ipc").resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--build", str(root)],
        env={
            **os.environ,
            "KICAD_FLOW_MONITOR": "0",
            "KICAD_FLOW_SCHEMATIC_MODE": "headless",
            "KICAD_FLOW_PCB_MODE": "headless",
        },
        check=True,
        timeout=90,
    )
    os.environ["KICAD_FLOW_SCHEMATIC_MODE"] = "gui"
    os.environ["KICAD_FLOW_PCB_MODE"] = "gui"
    os.environ["KICAD_FLOW_MONITOR"] = "0"
    os.environ["KICAD_FLOW_COMPANION_STATE"] = str(root / "companion.json")
    desktop = DesktopProject()
    session = AgentSession(desktop.open, desktop.status)
    focused: list[bool] = []
    service = CompanionService(session, lambda: focused.append(True))
    try:
        client = AgentClient()
        client.ensure_running()
        assert client.port == service.server.server_port
        client.action("project", path=str(root / "companion.kicad_pro"))
        assert client.state()["live_editor"] and not client.state()["connected"]
        assert len(client.state()["editor"]["documents"]) == 2
        client.action("focus")
        assert focused == [True]
        assert desktop.status()["ready"], (
            "KiCad is connected but busy. Finish its startup dialogs or active "
            "commands, then run the live example again."
        )
        asyncio.run(exercise(root, False))
        connections = dict(desktop.connections)
        desktop.close()
        assert desktop.open(root / "companion.kicad_pro")["documents"]
        assert desktop.connections == connections
        assert desktop.status()["ready"]
    finally:
        service.close()
        session.close()
        desktop.close()
    print(
        json.dumps(
            {
                "ok": True,
                "directory": str(root),
                "editors": 2,
                "schematic_value": "22k",
                "pcb_value": "22k",
            }
        )
    )


if __name__ == "__main__":
    main()
