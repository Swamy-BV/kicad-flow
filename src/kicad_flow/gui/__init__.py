"""Launch the separate project GUI using the shared local agent service."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from kicad_flow.agent.client import DEFAULT_PORT, AgentClient


def main() -> int:
    """Open the agent workspace, preserving existing MCP and monitor ports."""
    parser = argparse.ArgumentParser(description="KiCadFlow agent workspace")
    parser.add_argument("project", nargs="?", help="project folder or KiCad file")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-open", action="store_true", help="print the workspace URL"
    )
    args = parser.parse_args()
    try:
        client = AgentClient(args.port)
        client.ensure_running()
        if args.project:
            client.action(
                "project", path=str(Path(args.project).expanduser().resolve())
            )
        url = client.url + "/workspace"
        print(f"KiCadFlow workspace: {url}")
        if not args.no_open:
            webbrowser.open(url)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
