"""Run a list of tool calls in one request.

A design is mostly repetition -- 180 wires on a flight controller, 480 on ten
LED digits -- and every one of those was a separate round trip. That is fine for
a script and expensive for an agent, where a call is a conversational turn.

This adds NO new capability. `batch` runs the same primitives, with the same
arguments, and returns what each one returned; there is nothing you can express
here that you could not express as N calls. It is a transport, not a surface.
That is why it is one tool rather than a batch variant of each primitive: the
primitives keep taking scalars and keep doing one thing.

**Placement and wiring cannot go in the same batch, and should not.** A wire is
drawn to a coordinate that `add_component` returns, so the caller has to see the
pins before it can compute the wire. The shape that works is two batches: place
everything, read the pins out of the reply, then draw everything. That is what a
careful caller does one call at a time anyway.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from . import _meta, tools_board, tools_schematic
from ._app import mcp


def _registry() -> dict[str, Any]:
    """Every primitive `batch` may call, by tool name."""
    out: dict[str, Any] = {}
    for module in (tools_schematic, tools_board):
        for name in module.__all__:
            fn = getattr(module, name)
            out[name] = getattr(fn, "fn", fn)
    return out


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def batch(
    ops: Annotated[list[dict[str, Any]], Field(
        description="Calls to run in order, each "
                    '`{"tool": "add_wire", "args": {"path": ..., "x1": ...}}`. '
                    "Any schematic or board tool except `batch` itself.")],
    stop_on_error: Annotated[bool, Field(
        description="Stop at the first refusal (default), or run the rest and "
                    "report every failure. Stop for a chain where a later call "
                    "depends on an earlier one; continue for independent work "
                    "you want a full report on.")] = True,
) -> dict[str, Any]:
    """Run several tool calls in one request, in order.

    Same primitives, same arguments, same replies -- one round trip instead of
    N. Use it for the repetitive half of a design: all the wires on a sheet,
    all the pads on a net, all the tracks of a bus.

    **Two batches, not one.** `add_component` and `place_footprint` return the
    pin and pad positions that later calls must aim at, so place first, read
    the reply, then send the wires or tracks as a second batch. A wire drawn to
    a coordinate you guessed instead of one the server reported looks connected
    and is not.

    Args:
        ops: The calls, in order.
        stop_on_error: Stop at the first refusal, or run everything and report.

    Returns:
        `results`, one entry per op that ran, in order -- each exactly what
        that tool would have returned on its own. `failed` lists the index,
        tool and error of every op that refused, so a failure is locatable
        without matching replies up by hand.
    """
    known = _registry()
    results: list[Any] = []
    failed: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or "tool" not in op:
            failed.append({"index": i, "tool": "", "error":
                           'each op needs a "tool" key and an "args" object'})
            if stop_on_error:
                break
            continue
        name = str(op["tool"])
        fn = known.get(name)
        if fn is None:
            failed.append({"index": i, "tool": name, "error":
                           f"no tool {name!r}" + (" (batch cannot call itself)"
                                                  if name == "batch" else "")})
            if stop_on_error:
                break
            continue
        try:
            got = fn(**(op.get("args") or {}))
        except TypeError as exc:          # wrong or missing arguments
            got = {"ok": False, "error": f"TypeError: {exc}"}
        results.append(got)
        if isinstance(got, dict) and got.get("ok") is not True:
            failed.append({"index": i, "tool": name,
                           "error": got.get("error", "refused")})
            if stop_on_error:
                break
    return {"ok": not failed, "count": len(ops), "ran": len(results),
            "results": results, "failed": failed}


__all__ = ["batch"]
