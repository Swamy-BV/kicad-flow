"""Launch a compact native chat companion beside the KiCad editors."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    """Open the native companion and the explicitly selected KiCad project."""
    from kicad_flow.agent.client import AgentClient
    from kicad_flow.agent.companion import endpoint

    parser = argparse.ArgumentParser(description="KiCadFlow live KiCad companion")
    parser.add_argument(
        "project", nargs="?", help="KiCad project, schematic or PCB file"
    )
    args = parser.parse_args()
    existing = endpoint()
    if existing:
        client = AgentClient(existing[0])
        try:
            state = client.state()
            if state.get("instance") == existing[1] and state.get("live_editor"):
                if args.project:
                    client.action("project", path=str(Path(args.project).resolve()))
                client.action("focus")
                return 0
        except (OSError, RuntimeError):
            pass
    os.environ["KICAD_FLOW_SCHEMATIC_MODE"] = "gui"
    os.environ["KICAD_FLOW_PCB_MODE"] = "gui"
    os.environ["KICAD_FLOW_MONITOR"] = "0"
    try:
        from .window import CompanionWindow

        window = CompanionWindow(args.project)
        window.run()
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
