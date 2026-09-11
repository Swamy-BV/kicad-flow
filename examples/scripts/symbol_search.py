"""Check library search through MCP with an empty generated-library cache.

The child process has a deadline and its own temporary directory. This does
not clear the operating system's disk cache or alter installed libraries.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


async def search() -> None:
    """Exercise qualified, broad, missing and repeated searches through MCP."""
    from fastmcp import Client

    from kicad_flow.server import mcp

    timings = []
    progress: list[tuple[float, str | None]] = []

    async def update(value: float, total: float | None, message: str | None) -> None:
        progress.append((value, message))

    cache = Path(tempfile.gettempdir()) / "kicad_flow_symbols"
    assert not cache.exists(), "Worker must start with an empty generated cache"
    expected_libraries: set[str] = set()
    async with Client(mcp, progress_handler=update) as client:
        for query in ("Device:R", "STM32F405", "USB_C",
                      "NoSuchSymbol_72618", "Device:R"):
            start = time.perf_counter()
            arguments = {"query": query, "limit": 3}
            if not timings:
                result, concurrent = await asyncio.gather(
                    client.call_tool("find_symbol", arguments),
                    client.call_tool("find_symbol", arguments),
                )
                assert result.data == concurrent.data
            else:
                result = await client.call_tool("find_symbol", arguments)
            assert result.data["ok"], result.data
            symbols = result.data["symbols"]
            assert bool(symbols) == (query != "NoSuchSymbol_72618"), result.data
            assert len(symbols) <= 3
            for symbol in symbols:
                assert query.lower() in symbol["lib_id"].lower()
                assert symbol["pins"] > 0
                expected_libraries.add(symbol["lib_id"].split(":", 1)[0])
            # A lookup must not rebuild unrelated libraries as a side effect.
            assert {p.stem for p in cache.glob("*.kicad_sym")} <= expected_libraries
            row = {"query": query, "seconds": round(time.perf_counter() - start, 3),
                   "ids": [symbol["lib_id"] for symbol in symbols]}
            timings.append(row)
            print(json.dumps(row), flush=True)
        zero = await client.call_tool("find_symbol", {"query": "Device:R", "limit": 0})
        assert zero.data == {"ok": True, "symbols": []}
        assert any(value == 0 and "find_symbol" in (message or "")
                   for value, message in progress)
        assert any(value == 1 and "find_symbol" in (message or "")
                   for value, message in progress)
    assert timings[0]["ids"] == timings[-1]["ids"]
    print("PASS: concurrent empty-cache search, matching-only rebuilds, limits "
          "and progress")


def main() -> None:
    """Run a fresh server process with a bounded wall-clock budget."""
    if "--worker" in sys.argv:
        asyncio.run(search())
        return
    output = Path("out/symbol-search").resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="run-", dir=output)
    env = {**os.environ, "TEMP": scratch, "TMP": scratch, "TMPDIR": scratch,
           "KICAD_FLOW_ACTIVITY": str(Path(scratch) / "activity.jsonl")}
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
                   env=env, check=True, timeout=60)


if __name__ == "__main__":
    main()
