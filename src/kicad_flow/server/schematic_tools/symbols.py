"""Schematic MCP tools for symbols."""

from __future__ import annotations

from typing import Any

from .. import _meta
from .._app import mcp
from .session import (
    _blank,
    _fail,
)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def find_symbol(query: str, limit: int = 20, project_dir: str = "") -> dict[str, Any]:
    """Search the symbol libraries for a part.

    Args:
        query: Matched against ``Library:Symbol`` ids, e.g. ``"Device:R"``,
            ``"MCU_Espressif"``, ``"USB_C"``.
        limit: Most results to return.
        project_dir: Optional KiCad project directory whose local
            ``sym-lib-table`` should also be searched.

    Returns:
        ``{ok, symbols: [{lib_id, description, pins: n, width, height}]}``.
    """
    try:
        found = _blank(project_dir).find_symbols(query, limit=limit)
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {
        "ok": True,
        "symbols": [
            {
                "lib_id": s.lib_id,
                "description": s.description,
                "pins": len(s.pins),
                "width": s.width,
                "height": s.height,
                "power": s.power,
            }
            for s in found
        ],
    }


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def symbol_pins(lib_id: str, unit: int = 1, project_dir: str = "") -> dict[str, Any]:
    """The pins a symbol has, before it is placed anywhere.

    Use this to decide how to orient a part and how much room to leave. For
    the positions to actually WIRE to, place the part and read the pins that
    `add_component` returns -- those have the rotation applied.

    Args:
        lib_id: ``Library:Symbol``.
        unit: Which unit to report. A multi-unit symbol answers one at a
            time: reporting them together puts two units' pins at identical
            coordinates, which is a wrong netlist rather than a messy drawing.
        project_dir: Optional project containing the symbol's local library.

    Returns:
        ``{ok, lib_id, units, unit, width, height, pins: [...]}``.
    """
    try:
        sym = _blank(project_dir).symbol(lib_id, unit=unit)
    except LookupError as exc:
        return _fail(exc)
    return {
        "ok": True,
        "lib_id": sym.lib_id,
        "units": sym.units,
        "unit": sym.unit,
        "width": sym.width,
        "height": sym.height,
        "description": sym.description,
        "pins": [p.as_dict() for p in sym.pins],
    }
