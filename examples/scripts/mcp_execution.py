"""Exercise dispatch and concurrency; compare direct and search MCP payloads.

Run from the repository root. This is a deterministic client benchmark, not
an evaluation of model reasoning, tokens, or schematic aesthetics.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.server.transforms.search import RegexSearchTransform

from kicad_flow.server import mcp
from kicad_flow.server.execution import DesignAccessMiddleware

OUT = Path("out/mcp-execution").resolve()


async def concurrency() -> None:
    """Drive blocking workers through MCP and observe simultaneous execution."""
    server = FastMCP("concurrency probe")
    server.add_middleware(DesignAccessMiddleware())
    mutex = threading.Lock()
    active = 0
    peak = 0
    entered = threading.Event()
    seen: list[str] = []

    @server.tool
    def probe(path: str) -> dict[str, Any]:
        """Hold a worker briefly so overlapping requests can be observed."""
        nonlocal active, peak
        with mutex:
            active += 1
            peak = max(peak, active)
            seen.append(path)
            entered.set()
        try:
            time.sleep(0.08)
            return {"ok": True, "path": path}
        finally:
            with mutex:
                active -= 1

    server.tool(probe, name="list_nets")
    async with Client(server) as client:
        async def pair(first: str, second: str, tool: str = "probe") -> int:
            nonlocal peak
            peak = 0
            await asyncio.gather(
                client.call_tool(tool, {"path": first}),
                client.call_tool("probe", {"path": second}),
            )
            return peak

        same = await pair(str(OUT / "a/one"), str(OUT / "a/two"))
        separate = await pair(str(OUT / "a/one"), str(OUT / "b/two"))
        hierarchy = await pair(str(OUT / "a/one"), str(OUT / "b/two"),
                               "list_nets")
        assert (same, separate, hierarchy) == (1, 2, 1)
        entered.clear()
        peak = 0
        running = asyncio.create_task(client.call_tool(
            "probe", {"path": str(OUT / "cancel/active")}))
        async def started() -> None:
            while not entered.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(started(), 2)
        waiting_path = str(OUT / "cancel/waiting")
        waiting = asyncio.create_task(client.call_tool("probe", {"path": waiting_path}))
        await asyncio.sleep(0.01)
        waiting.cancel()
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        with contextlib.suppress(asyncio.CancelledError):
            await running
        await client.call_tool("probe", {"path": str(OUT / "cancel/next")})
        assert waiting_path not in seen and peak == 1 and active == 0
        print(f"Concurrent workers: same project={same}, separate={separate}, "
              f"hierarchy={hierarchy}; cancellation retained the active claim")


async def dispatch() -> None:
    """Check native validation, partial-batch persistence and activity records."""
    log = OUT / "dispatch/logs/mcp.jsonl"
    offset = log.stat().st_size if log.exists() else 0
    updates: list[float] = []

    async def progress(value: float, total: float | None,
                       message: str | None) -> None:
        updates.append(value)

    path = str(OUT / "dispatch/sheet.kicad_sch")
    async with Client(mcp, progress_handler=progress) as client:
        assert (await client.call_tool("new_sheet", {"path": path})).data["ok"]
        invalid = {"path": path, "parts": [{"ref": "R1", "typo": 1}]}
        direct = await client.call_tool("add_components", invalid,
                                        raise_on_error=False)
        result = await client.call_tool("batch", {"ops": [
            {"tool": "add_texts", "args": {"path": path, "notes": [
                {"x": 25.4, "y": 25.4, "text": "persisted before refusal"}]}},
            {"tool": "add_components", "args": invalid},
            {"tool": "add_texts", "args": {"path": path, "notes": [
                {"x": 25.4, "y": 50.8, "text": "must not run"}]}},
        ]})
        assert direct.is_error and not result.data["ok"]
        assert result.data["ran"] == 2 and result.data["failed"][0]["index"] == 1
        saved = Path(path).read_text()
        assert "persisted before refusal" in saved and "must not run" not in saved
        assert 2 in updates and 3 not in updates
        malformed = await client.call_tool("batch", {"ops": None},
                                            raise_on_error=False)
        assert malformed.is_error
    with log.open("rb") as stream:
        stream.seek(offset)
        rows = [json.loads(line) for line in stream.read().splitlines()]
    assert sum(row["tool"] == "add_texts" for row in rows) == 1
    assert sum(row["tool"] == "add_components" for row in rows) == 2
    print("Dispatch: direct/batch validation, partial autosave, stop-on-error, "
          "progress and single activity records passed")


async def benchmark(search: bool) -> dict[str, Any]:
    """Measure schemas and JSON exchange size for an identical small circuit."""
    if search:
        mcp.add_transform(RegexSearchTransform(max_results=8))
    mode = "search" if search else "direct"
    path = str(OUT / mode / "sheet.kicad_sch")
    payload = 0
    calls = 0
    updates: list[tuple[float, str | None]] = []

    async def progress(value: float, total: float | None,
                       message: str | None) -> None:
        updates.append((value, message))

    def count(value: Any) -> None:
        nonlocal payload
        payload += len(json.dumps(value, separators=(",", ":")).encode())

    def reply(result: Any) -> None:
        count({"content": [part.model_dump(mode="json") for part in result.content],
               "structuredContent": result.structured_content,
               "_meta": result.meta, "isError": result.is_error})

    started = time.perf_counter()
    async with Client(mcp, progress_handler=progress) as client:
        catalog = await client.list_tools()
        count([tool.model_dump(mode="json") for tool in catalog])
        catalog_bytes = payload
        assert all("ctx" not in t.input_schema.get("properties", {}) for t in catalog)

        async def call(name: str, **args: Any) -> dict[str, Any]:
            nonlocal calls
            if search:
                request = {"pattern": "^" + name + " "}
                found = await client.call_tool("search_tools", request)
                count({"name": "search_tools", "arguments": request})
                reply(found)
                assert name in str(found.content), (name, found)
                calls += 1
            tool = "call_tool" if search else name
            request = {"name": name, "arguments": args} if search else args
            count({"name": tool, "arguments": request})
            result = await client.call_tool(tool, request)
            reply(result)
            calls += 1
            assert result.data["ok"], result.data
            return result.data

        await call("new_sheet", path=path)
        placed = await call("add_components", path=path, parts=[
            {"lib_id": "Device:R", "ref": "R1", "x": 50.8, "y": 50.8},
            {"lib_id": "Device:C", "ref": "C1", "x": 76.2, "y": 50.8},
        ])
        a, b = [part["pins"][0] for part in placed["parts"]]
        await call("add_wires", path=path, wires=[{
            "x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"]}])
        scene = await call("inspect_schematic_scene", path=path)
        nets = await call("list_nets", path=path)
        assert any(value == 1 and "list_nets" in (message or "")
                   for value, message in updates), updates
        signature = sorted(sorted((pin["ref"], pin["pin"]) for pin in net["pins"])
                           for net in nets["nets"])
    return {"mode": mode, "advertised_tools": len(catalog), "calls": calls,
            "catalog_bytes": catalog_bytes, "exchange_bytes": payload,
            "seconds": round(time.perf_counter() - started, 3),
            "objects": scene["object_count"], "connectivity": signature}


async def main() -> None:
    """Run the probes and write a reproducible comparison under out/."""
    OUT.mkdir(parents=True, exist_ok=True)
    await concurrency()
    await dispatch()
    direct = await benchmark(False)
    search = await benchmark(True)
    assert direct["connectivity"] == search["connectivity"]
    assert direct["objects"] == search["objects"]
    results = [direct, search]
    (OUT / "benchmark.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

