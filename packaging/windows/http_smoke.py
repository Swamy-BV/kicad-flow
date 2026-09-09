"""Confirm that a running packaged server answers over HTTP MCP."""

from __future__ import annotations

import argparse
import asyncio

from fastmcp import Client


async def smoke(url: str) -> None:
    """Connect to *url* and inspect the registered MCP tools."""
    async with Client(url) as client:
        tools = await client.list_tools()
    print(f"PASS  HTTP MCP  {len(tools)} tools")


def main() -> None:
    """Read an optional endpoint and run the HTTP check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8471/mcp")
    args = parser.parse_args()
    asyncio.run(smoke(args.url))


if __name__ == "__main__":
    main()
