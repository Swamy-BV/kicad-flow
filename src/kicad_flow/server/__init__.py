"""FastMCP server exposing KiCad schematic + PCB tools.

Start it yourself (the monitor comes up with it)::

    python -m kicad_flow.server                 # stdio transport (host spawns it)
    python -m kicad_flow.server --http          # HTTP on :8471 (you start it)

With ``--http`` the server listens on a port, so you run it manually in a
terminal and point the MCP host at the URL instead of having it spawn the
process -- e.g. in a cowork/Copilot config::

    "kicad-flow": { "type": "http", "url": "http://127.0.0.1:8471/mcp" }
"""

from __future__ import annotations

from . import (  # noqa: F401  (register)
    tools_batch,
    tools_board,
    tools_schematic,
)
from ._app import mcp
from .activity import ActivityMiddleware
from .autosave import AutosaveMiddleware
from .autosave import AutosaveMiddleware

# Log every tool call to the activity JSONL so the live monitor can show what
# the agent is doing (best-effort; a logging failure never affects a tool call).
mcp.add_middleware(ActivityMiddleware())

# Put every change on disk as it happens, so the monitor has something
# current to render. A sheet used to reach disk only on `save_sheet`,
# which is why the preview sat still while a design was being built.
mcp.add_middleware(AutosaveMiddleware())

# Put every change on disk as it happens, so the monitor has something current
# to render. A sheet used to reach disk only on `save_sheet`, which is why the
# preview sat still while a design was being built.
mcp.add_middleware(AutosaveMiddleware())


def _monitor_port() -> int | None:
    """Resolve the monitor port from ``KICAD_FLOW_MONITOR``; None = disabled.

    Unset or a truthy word (``1``/``on``/``true``/``yes``) -> the default port.
    ``0``/``off``/``false``/``no``/empty -> disabled. A real port number -> that
    port. (The flag ``1`` must NOT be read as "port 1" -- that bug pointed the
    auto-start at an unbindable port, so the monitor silently never came up.)
    """
    import os

    from kicad_flow import monitor

    setting = os.environ.get("KICAD_FLOW_MONITOR", "on").strip().lower()
    if setting in ("0", "off", "false", "no", ""):
        return None
    if setting.isdigit() and int(setting) > 1:
        return int(setting)
    return monitor.DEFAULT_PORT


def _maybe_start_monitor() -> None:
    """Bring up the live monitor alongside the server, unless disabled.

    Called from :func:`main`, so it fires whenever you start the server (either
    transport). Idempotent, non-blocking, and failure is swallowed so it can
    never affect the MCP server. Set ``KICAD_FLOW_MONITOR=0`` to disable, or a
    port number to move it (default 8472). The test suite disables it (see
    ``tests/conftest.py``) so it never spawns a server.
    """
    import contextlib
    import sys

    port = _monitor_port()
    if port is None:
        return
    with contextlib.suppress(Exception):
        from kicad_flow import monitor

        url = monitor.ensure_running(port=port)
        print(f"kicad-flow monitor: {url}", file=sys.stderr)


def main() -> None:
    """Run the MCP server (stdio by default, or ``--http`` on a port)."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="kicad_flow.server", description="kicad-flow MCP server"
    )
    ap.add_argument(
        "--http",
        action="store_true",
        help="serve MCP over HTTP on a port (default: stdio)",
    )
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    ap.add_argument("--port", type=int, default=8471, help="HTTP port (with --http)")
    args = ap.parse_args()

    _maybe_start_monitor()
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


__all__ = ["main", "mcp"]
