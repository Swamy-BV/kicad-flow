"""Exercise configured MCP batch boundaries without changing existing examples."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.server.transforms.search import RegexSearchTransform

from kicad_flow.server import mcp
from kicad_flow.server.limits import BATCH_LIMIT


async def main() -> None:
    """Check schemas, boundary successes, atomic refusals and nested dispatch."""
    root = Path("out/batch-limits").resolve()
    root.mkdir(parents=True, exist_ok=True)
    sch = str(root / "boundary.kicad_sch")
    pcb = str(root / "boundary.kicad_pcb")
    async with Client(mcp) as client:

        async def call(name: str, **args: Any) -> Any:
            data = (await client.call_tool(name, args)).data
            assert data["ok"], data
            return data

        catalog = await client.list_tools()
        schemas = {tool.name: tool.input_schema for tool in catalog}
        structural_lists = {
            ("measure_placement", "edge_exempt_refs"),
            ("query_board_region", "layers"),
            ("set_stackup", "layers"),
            ("get_manufacturing_requirements", "assembly_sides"),
            ("export_gerbers", "assembly_sides"),
            ("export_bom", "assembly_sides"),
            ("export_placements", "assembly_sides"),
            ("check_manufacturing_package", "files"),
            ("check_manufacturing_package", "assembly_sides"),
            ("archive_manufacturing_files", "files"),
        }
        limited = []
        for tool in catalog:
            for field, schema in tool.input_schema.get("properties", {}).items():
                variants = [schema, *schema.get("anyOf", [])]
                for variant in variants:
                    if variant.get("type") == "array" and "maxItems" not in variant:
                        assert (tool.name, field) in structural_lists, (
                            tool.name,
                            field,
                        )
                if any("maxItems" in variant for variant in variants):
                    assert any(v.get("maxItems") == BATCH_LIMIT for v in variants)
                    limited.append((tool.name, field))
        assert ("batch", "ops") in limited
        assert ("add_components", "parts") in limited
        assert ("move_footprints", "refs") in limited
        assert "maxItems" not in schemas["set_stackup"]["properties"]["layers"]
        assert (
            "maxItems"
            not in schemas["add_zones"]["properties"]["zones"]["items"]["properties"][
                "points"
            ]
        )
        await call("new_sheet", path=sch)
        await call("new_board", path=pcb)
        parts = [
            {"ref": f"R{i + 1}", "lib_id": "Device:R", "x": 25.4 + i * 10.16, "y": 25.4}
            for i in range(BATCH_LIMIT)
        ]
        notes = [
            {"text": f"Note {i + 1}", "x": i * 5, "y": 5, "layer": "F.SilkS"}
            for i in range(BATCH_LIMIT)
        ]
        assert (
            len((await call("add_components", path=sch, parts=parts))["parts"])
            == BATCH_LIMIT
        )
        assert (
            len((await call("add_board_texts", path=pcb, texts=notes))["texts"])
            == BATCH_LIMIT
        )
        before = {path: Path(path).read_bytes() for path in (sch, pcb)}
        for tool, args in (
            ("add_components", {"path": sch, "parts": [*parts, parts[0]]}),
            ("move_components", {"path": sch, "refs": ["R1"] * (BATCH_LIMIT + 1)}),
            ("add_board_texts", {"path": pcb, "texts": [*notes, notes[0]]}),
            ("check_board", {"path": pcb, "tracks": [{}] * (BATCH_LIMIT + 1)}),
            (
                "batch",
                {
                    "ops": [{"tool": "list_components", "args": {"path": sch}}]
                    * (BATCH_LIMIT + 1)
                },
            ),
        ):
            result = await client.call_tool(tool, args, raise_on_error=False)
            assert result.is_error, (tool, result)
        if BATCH_LIMIT >= 2:
            for stop in (True, False):
                result = await client.call_tool(
                    "batch",
                    {
                        "stop_on_error": stop,
                        "ops": [
                            {
                                "tool": "add_board_texts",
                                "args": {"path": pcb, "texts": [notes[0]]},
                            },
                            {
                                "tool": "add_components",
                                "args": {"path": sch, "parts": [*parts, parts[0]]},
                            },
                        ],
                    },
                    raise_on_error=False,
                )
                assert result.is_error, result
                assert "No batch operations were run" in str(result.content)
        for path, data in before.items():
            assert Path(path).read_bytes() == data, path
        assert len((await call("list_components", path=sch))["parts"]) == BATCH_LIMIT
        assert len((await call("list_board_texts", path=pcb))["texts"]) == BATCH_LIMIT
        await call("batch", ops=[])
        await call(
            "batch",
            ops=[{"tool": "list_components", "args": {"path": sch}}] * BATCH_LIMIT,
        )
        # A separate request can add more items; inventories are never truncated.
        await call(
            "add_components",
            path=sch,
            parts=[{"ref": "R_EXTRA", "lib_id": "Device:R", "x": 25.4, "y": 50.8}],
        )
        assert (
            len((await call("list_components", path=sch))["parts"]) == BATCH_LIMIT + 1
        )
    mcp.add_transform(RegexSearchTransform(max_results=8))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "call_tool",
            {
                "name": "add_components",
                "arguments": {"path": sch, "parts": [*parts, parts[0]]},
            },
            raise_on_error=False,
        )
        assert result.is_error, result
        if BATCH_LIMIT >= 2:
            before_search = Path(pcb).read_bytes()
            result = await client.call_tool(
                "call_tool",
                {
                    "name": "batch",
                    "arguments": {
                        "ops": [
                            {
                                "tool": "add_board_texts",
                                "args": {"path": pcb, "texts": [notes[0]]},
                            },
                            {
                                "tool": "add_components",
                                "args": {"path": sch, "parts": [*parts, parts[0]]},
                            },
                        ]
                    },
                },
                raise_on_error=False,
            )
            assert result.is_error, result
            assert Path(pcb).read_bytes() == before_search
    print(
        f"Limit {BATCH_LIMIT}: {len(limited)} bounded schema arguments; "
        "schematic/PCB boundaries, unchanged files on refusal, batch preflight, "
        "full inventories and search dispatch passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
