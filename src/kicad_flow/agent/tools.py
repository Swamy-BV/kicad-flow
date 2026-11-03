"""Expose the existing MCP catalogue to the dashboard's local agent."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any

from fastmcp import Client

# These are file destinations or project inputs, not library identifiers/URLs.
_PATHS = frozenset(
    {
        "path",
        "schematic_path",
        "board_path",
        "project_dir",
        "output_file",
        "output_dir",
        "output_zip",
        "dsn_path",
        "ses_path",
        "jar_path",
        "session_file",
        "source_file",
        "source_path",
        "bom_file",
        "placements_file",
        "bundle_path",
        "asset_path",
        "library_dir",
        "database_path",
    }
)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "kicad_view_image",
        "description": "Inspect a PNG, JPEG or WebP inside the selected project. "
        "Use after an MCP render to check the actual visual output.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "kicad_search",
        "description": "Find existing KiCadFlow MCP tools and exact argument schemas. "
        "Search before calling unfamiliar tools. Geometry and CAD "
        "writes must use these tools.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "kicad_call",
        "description": "Call one existing KiCadFlow MCP primitive by its exact name "
        "and arguments. Use kicad_search for schemas. At most five "
        "items per operation list by default. All project paths "
        "must stay inside the selected workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
            "required": ["name", "arguments"],
            "additionalProperties": False,
        },
    },
]


def project_arguments(value: Any, root: Path, key: str = "") -> Any:
    """Resolve explicit project paths and refuse escapes before any MCP call."""
    if isinstance(value, dict):
        return {k: project_arguments(v, root, k) for k, v in value.items()}
    if isinstance(value, list):
        return [
            project_arguments(v, root, "path" if key == "files" else key) for v in value
        ]
    if key in _PATHS and isinstance(value, str) and value:
        path = Path(value)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"{key} must stay inside the selected project")
        return str(resolved)
    if key == "java_path" and value not in ("java", "java.exe"):
        raise ValueError("Use the installed Java command for routing")
    return value


async def _invoke(
    tool: str, arguments: dict[str, Any], root: Path, read_only: bool
) -> dict[str, Any]:
    from kicad_flow.server import mcp

    async with Client(mcp) as client:
        if tool == "kicad_search":
            query = str(arguments.get("query", "")).lower()
            words = re.findall(r"[a-z0-9_]+", query)
            catalogue = await client.list_tools()
            ranked = sorted(
                catalogue,
                key=lambda t: (
                    t.name.lower() == query,
                    sum(w in t.name.lower() for w in words),
                    sum(w in (t.description or "").lower() for w in words),
                ),
                reverse=True,
            )
            found = [
                t
                for t in ranked
                if any(
                    w in (t.name + " " + (t.description or "")).lower() for w in words
                )
            ][:5]
            data: Any = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "outputSchema": t.output_schema,
                }
                for t in found
            ]
            return _reply(data, True)
        if tool != "kicad_call":
            raise ValueError("Unknown dashboard tool")
        name = arguments.get("name")
        supplied = arguments.get("arguments")
        if not isinstance(name, str) or not isinstance(supplied, dict):
            raise ValueError("Provide a tool name and an arguments object")
        if read_only:
            catalogue = await client.list_tools()
            target = next((t for t in catalogue if t.name == name), None)
            if target is None or not getattr(
                target.annotations, "read_only_hint", False
            ):
                raise ValueError(
                    "Ask mode allows inspection only; select Edit for writes"
                )
        supplied = project_arguments(supplied, root)
        if name == "add_sheets":
            parent = Path(supplied["path"]).parent
            for child in supplied.get("sheets", []):
                filename = child.get("filename", "")
                if filename and not (parent / filename).resolve().is_relative_to(root):
                    raise ValueError("Child sheet filenames must stay in the project")
        result = await client.call_tool(name, supplied, raise_on_error=False)
        data = result.structured_content
        if data is None:
            data = [c.text for c in result.content if c.type == "text"]
        success = not result.is_error and not (
            isinstance(data, dict) and data.get("ok") is False
        )
        reply = _reply(data, success)
        for content in result.content:
            if content.type == "image":
                reply["contentItems"].append(
                    {
                        "type": "inputImage",
                        "imageUrl": f"data:{content.mimeType};base64,{content.data}",
                    }
                )
        return reply


def _reply(data: Any, success: bool) -> dict[str, Any]:
    return {
        "success": success,
        "contentItems": [
            {
                "type": "inputText",
                "text": json.dumps(data, ensure_ascii=True),
            }
        ],
    }


def invoke(
    tool: str, arguments: dict[str, Any], root: Path, *, read_only: bool = False
) -> dict[str, Any]:
    """Invoke tools from a worker, returning exactly one model representation."""
    try:
        if tool == "kicad_view_image":
            path = Path(project_arguments(arguments, root)["path"])
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(path.suffix.lower())
            if not mime or path.stat().st_size > 10_000_000:
                raise ValueError(
                    "Use a PNG, JPEG or WebP up to 10 MB; render at lower DPI"
                )
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputImage",
                        "imageUrl": f"data:{mime};base64,{data}",
                    }
                ],
            }
        return asyncio.run(_invoke(tool, arguments, root, read_only))
    except Exception as exc:
        return _reply({"ok": False, "error": str(exc)}, False)
