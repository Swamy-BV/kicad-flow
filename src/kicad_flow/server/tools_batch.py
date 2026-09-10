"""Run a list of tool calls in one request.

A design is mostly repetition -- 180 wires on a flight controller, 480 on ten
LED digits -- and every one of those was a separate round trip. That is fine for
a script and expensive for an agent, where a call is a conversational turn.

This adds NO new capability. `batch` runs the same primitives, with the same
arguments, and returns what each one returned; there is nothing you can express
here that you could not express as N calls. It is a transport for mixing
different operations. Repeatable writes already take typed lists.

**Placement and wiring cannot go in the same request, and should not.** A wire
is drawn to a coordinate that `add_components` returns, so the caller has to see
the pins before it can compute the wire. Place everything, read the pins out of
the reply, then draw everything.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context
from fastmcp.exceptions import DisabledError, FastMCPError, NotFoundError
from pydantic import Field, ValidationError

from . import _meta, tools_board, tools_schematic
from ._app import mcp
from .activity import record_nested_tool


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
async def batch(
    ctx: Context,
    ops: Annotated[list[dict[str, Any]], Field(
        description="Calls to run in order, each "
                    '`{"tool": "save_board", "args": {"path": ...}}`. '
                    "Any schematic or board tool except `batch` itself.")],
    stop_on_error: Annotated[bool, Field(
        description="Stop at the first refusal (default), or run the rest and "
                    "report every failure. Stop for a chain where a later call "
                    "depends on an earlier one; continue for independent work "
                    "you want a full report on.")] = True,
) -> dict[str, Any]:
    """Run several tool calls in one request, in order.

    Same primitives, same arguments, same replies -- one round trip instead of
    N. Repeatable writes already accept typed lists; use this when one request
    needs several different, independent operations.

    The activity log and monitor show each enclosed primitive under its own
    tool name. The `batch` wrapper is transport only and is not shown when its
    operations can be recorded individually.

    **Two requests, not one.** `add_components` and `place_footprints` return the
    pin and pad positions that later calls must aim at, so place first, read
    the reply, then send the wires or tracks as a second typed list call. A wire
    drawn to a coordinate you guessed instead of one the server reported looks
    connected and is not.

    Args:
        ctx: MCP request context, injected by the server.
        ops: The calls, in order.
        stop_on_error: Stop at the first refusal, or run everything and report.

    Returns:
        `results`, one entry per op that ran, in order -- each exactly what
        that tool would have returned on its own. `failed` lists the index,
        tool and error of every op that refused, so a failure is locatable
        without matching replies up by hand.
    """
    known = set(tools_schematic.__all__) | set(tools_board.__all__)
    await ctx.report_progress(0, len(ops), "Starting batch")
    results: list[Any] = []
    failed: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or "tool" not in op:
            error = 'each op needs a "tool" key and an "args" object'
            failed.append({"index": i, "tool": "", "error": error})
            record_nested_tool("invalid_batch_op", {},
                               {"ok": False, "error": error}, 0.0)
            await ctx.report_progress(i + 1, len(ops), f"Rejected operation {i + 1}")
            if stop_on_error:
                break
            continue
        name = str(op["tool"])
        arguments = op.get("args", {})
        if not isinstance(arguments, dict):
            error = 'each op needs an "args" object'
            failed.append({"index": i, "tool": name, "error": error})
            record_nested_tool(name, {}, {"ok": False, "error": error}, 0.0)
            await ctx.report_progress(i + 1, len(ops), f"Rejected operation {i + 1}")
            if stop_on_error:
                break
            continue
        if name not in known:
            error = f"no tool {name!r}" + (
                " (batch cannot call itself)" if name == "batch" else ""
            )
            failed.append({"index": i, "tool": name, "error": error})
            record_nested_tool(name, arguments,
                               {"ok": False, "error": error}, 0.0)
            await ctx.report_progress(i + 1, len(ops), f"Rejected operation {i + 1}")
            if stop_on_error:
                break
            continue
        try:
            result = await mcp.call_tool(name, arguments)
            got = result.structured_content
        except (TypeError, ValidationError, FastMCPError,
                NotFoundError, DisabledError) as exc:
            got = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        await ctx.report_progress(i + 1, len(ops), f"Finished operation {i + 1}")
        results.append(got)
        if isinstance(got, dict) and got.get("ok") is not True:
            failed.append({"index": i, "tool": name,
                           "error": got.get("error", "refused")})
            if stop_on_error:
                break
    return {"ok": not failed, "count": len(ops), "ran": len(results),
            "results": results, "failed": failed}


__all__ = ["batch"]
