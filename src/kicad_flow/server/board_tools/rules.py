"""PCB MCP tools for rules."""

from __future__ import annotations

from typing import Any

from ...pcb.types import (
    BoardLimits,
    BoardRule,
    Constraint,
    NetClass,
    NetClassAssignment,
    Stackup,
    StackupLayer,
)
from .. import _meta
from .._app import mcp
from .models import (
    BoardRuleSpec,
    NetClassAssignmentSpec,
    NetClassSpec,
    StackupLayerSpec,
)
from .session import (
    _ERRORS,
    _board,
    _fail,
)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_layers(path: str, count: int) -> dict[str, Any]:
    """Set the copper layer count (2, 4, 6 or 8). Do this before routing."""
    try:
        return {"ok": True, "layers": list(_board(path).set_layers(count))}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_stackup(
    path: str,
    layers: list[StackupLayerSpec],
    copper_finish: str = "",
    dielectric_constraints: bool = False,
    edge_connector: str = "",
    castellated_pads: bool = False,
    edge_plating: bool = False,
) -> dict[str, Any]:
    """Set the board's complete ordered physical stackup.

    Copper entries must exactly match the layers reported by `new_board` or
    `set_board_layers`. Include every layer the manufacturer specifies:
    copper, dielectric core/prepreg and optional mask/silkscreen/paste layers.
    No impedance dimensions are inferred from materials or thicknesses.
    """
    try:
        made = _board(path).set_stackup(
            Stackup(
                layers=tuple(
                    StackupLayer(
                        name=item.name,
                        kind=item.kind,
                        thickness=item.thickness,
                        material=item.material,
                        epsilon_r=item.epsilon_r,
                        loss_tangent=item.loss_tangent,
                        color=item.color,
                    )
                    for item in layers
                ),
                copper_finish=copper_finish,
                dielectric_constraints=dielectric_constraints,
                edge_connector=edge_connector,
                castellated_pads=castellated_pads,
                edge_plating=edge_plating,
            )
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **made.as_dict()}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_stackup(path: str) -> dict[str, Any]:
    """Read the complete stackup currently stored in the board."""
    try:
        found = _board(path).stackup()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **found.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_limits(
    path: str,
    min_clearance: float | None = None,
    min_track_width: float | None = None,
    min_via_diameter: float | None = None,
    min_via_drill: float | None = None,
    min_annular_width: float | None = None,
    min_hole_clearance: float | None = None,
    min_hole_to_hole: float | None = None,
    min_copper_edge_clearance: float | None = None,
    min_silk_clearance: float | None = None,
    min_text_height: float | None = None,
    min_text_thickness: float | None = None,
    min_groove_width: float | None = None,
    solder_mask_to_copper_clearance: float | None = None,
    min_solder_mask_bridge: float | None = None,
) -> dict[str, Any]:
    """Set explicitly supplied board-wide manufacturing limits.

    Omitted values stay unchanged. These are provider-neutral physical limits,
    not routing decisions; all dimensions are millimetres.
    """
    try:
        made = _board(path).set_limits(
            BoardLimits(
                min_clearance=min_clearance,
                min_track_width=min_track_width,
                min_via_diameter=min_via_diameter,
                min_via_drill=min_via_drill,
                min_annular_width=min_annular_width,
                min_hole_clearance=min_hole_clearance,
                min_hole_to_hole=min_hole_to_hole,
                min_copper_edge_clearance=min_copper_edge_clearance,
                min_silk_clearance=min_silk_clearance,
                min_text_height=min_text_height,
                min_text_thickness=min_text_thickness,
                min_groove_width=min_groove_width,
                solder_mask_to_copper_clearance=solder_mask_to_copper_clearance,
                min_solder_mask_bridge=min_solder_mask_bridge,
            )
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "limits": made.as_dict()}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_board_limits(path: str) -> dict[str, Any]:
    """Read every supported board-wide manufacturing limit."""
    try:
        found = _board(path).limits()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "limits": found.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_net_classes(path: str, classes: list[NetClassSpec]) -> dict[str, Any]:
    """Create or update named routing classes without changing other classes.

    Omitted dimensions remain unchanged on an existing class. A newly created
    named class may omit dimensions and inherit the project's Default class.
    """
    try:
        made = _board(path).set_net_classes(
            tuple(NetClass(**item.model_dump()) for item in classes)
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(made),
        "classes": [item.as_dict() for item in made],
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_net_classes(path: str) -> dict[str, Any]:
    """List every routing class in the board's project."""
    try:
        found = _board(path).net_classes()
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "classes": [item.as_dict() for item in found],
    }


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def assign_net_classes(
    path: str, assignments: list[NetClassAssignmentSpec]
) -> dict[str, Any]:
    """Assign mentioned nets to classes, preserving all other assignments.

    Repeat a net with different classes to give it multiple memberships.
    Every referenced class must already exist.
    """
    try:
        made = _board(path).assign_net_classes(
            tuple(NetClassAssignment(item.net, item.net_class) for item in assignments)
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(made),
        "assignments": [item.as_dict() for item in made],
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_net_class_assignments(path: str) -> dict[str, Any]:
    """List every explicit net-to-netclass membership in the project."""
    try:
        found = _board(path).net_class_assignments()
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "assignments": [item.as_dict() for item in found],
    }


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_constraints(path: str, rules: list[BoardRuleSpec]) -> dict[str, Any]:
    """Create or replace named numeric custom DRC rules.

    Each condition explicitly selects the objects governed by its constraints.
    Bounds are millimetres. Supported examples include `track_width`,
    `diff_pair_gap`, `diff_pair_uncoupled`, `length`, `skew`, `clearance`,
    `hole_size` and `via_diameter`. Untouched rules and comments are preserved.
    """
    try:
        made = _board(path).set_rules(
            tuple(
                BoardRule(
                    name=rule.name,
                    condition=rule.condition,
                    layer=rule.layer,
                    constraints=tuple(
                        Constraint(
                            kind=item.kind,
                            minimum=item.min,
                            optimum=item.opt,
                            maximum=item.max,
                        )
                        for item in rule.constraints
                    ),
                )
                for rule in rules
            )
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(made), "rules": [item.as_dict() for item in made]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_constraints(path: str) -> dict[str, Any]:
    """List numeric custom DRC rules from the board's rules document."""
    try:
        found = _board(path).rules()
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "count": len(found),
        "rules": [item.as_dict() for item in found],
    }
