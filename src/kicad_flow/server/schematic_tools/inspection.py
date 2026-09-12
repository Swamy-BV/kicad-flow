"""Schematic MCP tools for inspection."""

from __future__ import annotations

from typing import Any

from ...schematic import PartPlacement, Point, SceneBounds
from .. import _meta
from .._app import mcp
from .models import (
    NewPart,
)
from .session import (
    _fail,
    _key,
    _sheet,
)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def inspect_schematic_scene(
    path: str,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    since: str = "",
    max_objects: int = 2000,
    detail: str = "full",
    max_bytes: int = 2000000,
) -> dict[str, Any]:
    """Inspect schematic geometry without images, saving or electrical inference.

    Omit all rectangle coordinates for the sheet, or supply all four in mm.
    Coordinates snap to the schematic grid. Select by bounds intersection;
    return whole objects, not clipped fragments. A parent_id may be outside
    the region. Child-sheet boxes name their files; inspect each child path
    explicitly rather than loading the entire project into the model context.

    Objects have stable IDs, bounds, exact connection anchors and properties.
    Text bounds and spatial conflicts are conservative estimates. This is a
    geometry view, not a replacement for list_nets or check_sheet; no electrical
    connectivity is inferred from overlapping bounds or matching coordinates.

    Pass the returned revision as since with the SAME path and rectangle to
    receive only added/changed objects and findings, plus removed ID lists.
    Apply deltas by ID; replace the local view on mode=full. An expired, unknown
    or different-scope cursor returns a full reset, never a partial snapshot.
    Each client keeps its own cursor. A region delta can remove an object that
    moved outside the region without deleting it from the design.

    max_objects limits the selected observation; exceeding it is an explicit
    refusal, not silent truncation. Existing editing tools still take references
    and coordinates as documented; scene IDs do not add another write API.

    detail=compact flattens properties and uses [x,y] coordinate pairs while
    retaining every object. detail=conflicts returns only objects implicated
    in findings for focused repair; it is NOT an obstacle map for new wiring.
    Object/finding kind counts describe the whole selected region in each mode.
    max_bytes bounds the JSON reply; changing detail resets the delta scope.
    """
    from ...schematic import snap
    from ..scene import history

    try:
        if not 1000 <= max_bytes <= 2000000:
            raise ValueError("max_bytes must be 1000..2000000")
        values = (x1, y1, x2, y2)
        region = None
        if any(value is not None for value in values):
            if x1 is None or y1 is None or x2 is None or y2 is None:
                raise ValueError("supply all of x1, y1, x2, y2 or omit all four")
            import math

            if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                raise ValueError("region coordinates must be finite")
            if x1 > x2 or y1 > y2:
                raise ValueError("rectangle requires x1 <= x2 and y1 <= y2")
            region = SceneBounds(snap(x1), snap(y1), snap(x2), snap(y2))
        sheet = _sheet(path)
        scene = sheet.scene(region, max_objects=max_objects)
        return history.observe(
            _key(path), scene, since, detail=detail, max_bytes=max_bytes
        )
    except (LookupError, ValueError, OSError) as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def what_is_at(path: str, x: float, y: float, radius: float = 0.01) -> dict[str, Any]:
    """What meets at a point: pins, wire ends and labels.

    The one query worth having while drawing, because it answers the only
    question that matters -- *is this actually connected?* A wire drawn to
    where a pin looked like it was reports one thing here, not two.
    """
    try:
        return {"ok": True, **_sheet(path).at(x, y, radius)}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_nets(path: str) -> dict[str, Any]:
    """What the sheet ACTUALLY connects -- not what you think you drew.

    Read back from KiCad itself. A schematic can be a valid file that opens
    and renders correctly while its wires join nothing, and this is the only
    call that will tell you. Check it after wiring, before believing a sheet
    is done.

    Returns:
        ``{ok, count, nets: [{name, count, pins: [{ref, pin, name}]}]}``.
    """
    try:
        nets = _sheet(path).nets()
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True, "count": len(nets), "nets": [n.as_dict() for n in nets]}


def _findings_result(found: list[Any]) -> dict[str, Any]:
    """Summarize an inspection without confusing execution with quality."""
    errors = sum(1 for item in found if item.severity == "error")
    warnings = sum(1 for item in found if item.severity == "warning")
    kind_counts: dict[str, int] = {}
    for item in found:
        kind_counts[item.kind] = kind_counts.get(item.kind, 0) + 1
    return {
        "ok": True,
        "clean": not found,
        "errors": errors,
        "warnings": warnings,
        "kind_counts": dict(sorted(kind_counts.items())),
        "findings": [item.as_dict() for item in found],
    }


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def check_sheet(path: str) -> dict[str, Any]:
    """Every rule violation, named by part and pin.

    Runs the electrical rule check and maps each violation from a position
    back to the pin that sits there, so a finding reads ``U1.20 VCCIO is not
    driven`` rather than ``something at (175.26, 93.98)``.

    On a design of more than one page, run this on the ROOT: it walks the
    whole hierarchy, and each finding carries the `sheet` it is on. Child
    pages are indexed too, so a violation at a known pin retains its `ref` and
    pin number instead of degrading to coordinates alone.

    ``ok`` means the inspection ran. ``clean`` means it found neither errors
    nor warnings; callers must not treat those states as interchangeable.

    Returns:
        ``{ok, clean, errors, warnings, kind_counts, findings:
        [{severity, kind, message, sheet, ref, pin, x, y}]}``.
    """
    try:
        found = _sheet(path).check()
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return _findings_result(found)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def check_sheet_layout(path: str) -> dict[str, Any]:
    """Find potential graphical collisions on every page of a schematic.

    KiCad ERC does not report a value crossed by a wire, labels printed over
    each other, or notes colliding with component fields. This checks visible
    text, symbol and child-sheet bounds conservatively without changing them.
    Symbol bounds include pin extents. Each finding
    names both objects and its sheet position; render to confirm it, and the
    caller still decides which object to move.

    Run this alongside `check_sheet`, then render and LOOK AT the result.
    Geometry catches intersections, not subjective readability such as poor
    signal flow or excessive crowding.

    ``ok`` means the geometry inspection ran. ``clean`` means it found no
    possible collision. The caller may waive a warning after rendering it,
    but cannot mistake a populated findings list for success.

    Returns:
        ``{ok, clean, errors, warnings, kind_counts, findings:
        [{severity, kind, message, sheet, first, second, x, y}]}``.
    """
    try:
        found = _sheet(path).check_layout()
    except (LookupError, OSError, ValueError) as exc:
        return _fail(exc)
    return _findings_result(found)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def measure_schematic_placement(path: str, parts: list[NewPart]) -> dict[str, Any]:
    """Measure a complete caller-decided placement without changing the sheet.

    Supply the same list intended for `add_components`. The backend resolves
    exact symbol geometry, rotations, mirrors, fields and pin positions on an
    in-memory clone, then reports overlaps and drawable-page violations. It
    never chooses, spreads, moves or repairs a component.

    Use this before placing a functional block. If `clean` is false, revise the
    complete coordinate list and measure again; when it is true, pass the same
    list to `add_components`, then wire only to the pins that call returns.
    """
    try:
        sheet = _sheet(path)
        measured = sheet.measure_placement(
            tuple(
                PartPlacement(
                    lib_id=item.lib_id,
                    ref=item.ref,
                    at=Point(item.x, item.y),
                    value=item.value,
                    rotation=item.rotation,
                    mirror=item.mirror,
                    unit=item.unit,
                )
                for item in parts
            )
        )
    except (LookupError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, **measured.as_dict()}
