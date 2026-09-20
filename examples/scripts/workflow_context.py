"""Measure MCP instruction size and exercise on-demand workflow discovery."""

from __future__ import annotations

import asyncio
import json

from fastmcp import Client
from fastmcp.server.transforms.search import RegexSearchTransform

from kicad_flow.server import mcp
from kicad_flow.server.instructions import WORKFLOWS
from kicad_flow.server.limits import BATCH_LIMIT


async def main() -> None:
    """Keep startup small while retaining every workflow through both surfaces."""
    async with Client(mcp) as client:
        instructions = client.instructions or ""
        assert 0 < len(instructions) <= 1100
        assert f"At most {BATCH_LIMIT} items" in instructions
        assert "get_workflow" in instructions
        tools = await client.list_tools()
        descriptions = [t.description or "" for t in tools]
        assert sum(map(len, descriptions)) < 40000
        workflow = next(t for t in tools if t.name == "get_workflow")
        assert set(workflow.input_schema["properties"]["topic"]["enum"]) == (
            set(WORKFLOWS)
        )
        assert workflow.annotations is not None
        assert workflow.annotations.read_only_hint
        placement = next(t for t in tools if t.name == "update_board_from_schematic")
        assert any(v.get("maxItems") == BATCH_LIMIT for v in
                   placement.input_schema["properties"]["placements"]["anyOf"])
        resources = {str(r.uri): r for r in await client.list_resources()}
        sizes = {}
        for topic, expected in WORKFLOWS.items():
            assert expected not in instructions
            assert all(expected not in d for d in descriptions)
            uri = f"kicad-flow://workflows/{topic}"
            assert uri in resources
            fetched = await client.read_resource(uri)
            assert len(fetched) == 1 and fetched[0].text == expected
            result = await client.call_tool("get_workflow", {"topic": topic})
            assert not result.is_error
            assert len(result.content) == 1 and result.content[0].text == expected
            sizes[topic] = len(expected)
        invalid = await client.call_tool("get_workflow", {"topic": "all"},
                                         raise_on_error=False)
        assert invalid.is_error
        print(json.dumps({
            "instruction_chars": len(instructions),
            "tool_count": len(tools),
            "tool_description_chars": sum(map(len, descriptions)),
            "workflow_chars": sizes,
            "batch_limit": BATCH_LIMIT,
        }, indent=2))
    # Older MCP clients receive the same short initialization instructions.
    async with Client(mcp, mode="legacy") as client:
        initialized = await client.initialize()
        assert initialized.instructions == instructions
        assert len(await client.list_resources()) == len(WORKFLOWS)
    # Tool-only discovery still works when the server's search transform is used.
    mcp.add_transform(RegexSearchTransform(max_results=8))
    async with Client(mcp) as client:
        result = await client.call_tool("call_tool", {
            "name": "get_workflow", "arguments": {"topic": "parts"},
        })
        assert not result.is_error and result.content[0].text == WORKFLOWS["parts"]
    print("PASS: compact initialization, resource/tool parity, bounded topic "
          "selection, placement limit and search dispatch")


if __name__ == "__main__":
    asyncio.run(main())
