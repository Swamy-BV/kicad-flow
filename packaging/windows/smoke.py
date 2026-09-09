"""Exercise the frozen executable over its real stdio MCP transport."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from kicad_flow.server import mcp


async def smoke(executable: Path) -> None:
    """Connect, inspect registration, and create one schematic through MCP."""
    async with Client(mcp) as source_client:
        source_tools = await source_client.list_tools()
    expected_names = {tool.name for tool in source_tools}

    environment = dict(os.environ)
    environment["KICAD_FLOW_MONITOR"] = "0"
    transport = StdioTransport(command=str(executable), args=[], env=environment)
    async with Client(transport) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        if names != expected_names:
            missing = sorted(expected_names - names)
            extra = sorted(names - expected_names)
            raise RuntimeError(
                "packaged tool registration differs from source: "
                f"missing={missing}, extra={extra}"
            )
        required = {
            "new_sheet",
            "new_board",
            "get_fabrication_capabilities",
            "get_parts_provider_status",
        }
        if missing := required - names:
            raise RuntimeError(f"packaged executable omitted tools: {sorted(missing)}")

        with tempfile.TemporaryDirectory(prefix="kicad-flow-release-") as tmp:
            target = Path(tmp) / "smoke.kicad_sch"
            response = await client.call_tool("new_sheet", {"path": str(target)})
            data: Any = response.data if hasattr(response, "data") else response
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise RuntimeError(f"new_sheet failed through packaged MCP: {data}")
            if not target.is_file():
                raise RuntimeError("packaged new_sheet reported success without a file")

    print(f"PASS  stdio MCP  {len(names)} tools; schematic write succeeded")


def main() -> None:
    """Parse the executable path and run the asynchronous smoke check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    asyncio.run(smoke(args.executable.resolve()))


if __name__ == "__main__":
    main()
