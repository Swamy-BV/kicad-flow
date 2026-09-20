"""Read-only, on-demand workflows for resource-capable and tool-only clients."""

from __future__ import annotations

from typing import Literal

from . import _meta
from ._app import mcp
from .instructions import WORKFLOWS


@mcp.tool(tags={"documentation", "inspect"}, annotations=_meta.READ)
def get_workflow(
    topic: Literal["schematic", "pcb", "parts", "documentation"],
) -> str:
    """Read one KiCadFlow workflow; reuse it until the task changes.

    Tool-only fallback for kicad-flow://workflows/{topic}. Fetch only the
    relevant topic for substantial work; ordinary lookups need no workflow.
    """
    return WORKFLOWS[topic]


@mcp.resource(
    "kicad-flow://workflows/schematic", mime_type="text/plain",
    description="Schematic placement, wiring, hierarchy and verification workflow.",
)
def schematic_workflow() -> str:
    """Read the schematic workflow without loading other topics."""
    return WORKFLOWS["schematic"]


@mcp.resource(
    "kicad-flow://workflows/pcb", mime_type="text/plain",
    description="PCB placement, routing, fabrication and verification workflow.",
)
def pcb_workflow() -> str:
    """Read the PCB workflow without loading other topics."""
    return WORKFLOWS["pcb"]


@mcp.resource(
    "kicad-flow://workflows/parts", mime_type="text/plain",
    description="Part catalogue selection and project-local CAD asset workflow.",
)
def parts_workflow() -> str:
    """Read the parts workflow without loading other topics."""
    return WORKFLOWS["parts"]


@mcp.resource(
    "kicad-flow://workflows/documentation", mime_type="text/plain",
    description="Project requirements, datasheet evidence and decision records.",
)
def documentation_workflow() -> str:
    """Read the engineering record workflow without loading other topics."""
    return WORKFLOWS["documentation"]
