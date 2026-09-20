"""Small initialization instructions; detailed workflows are fetched on demand."""

from __future__ import annotations

from ..limits import BATCH_LIMIT
from .documentation import DOCUMENTATION
from .parts import PARTS
from .pcb import PCB
from .schematic import SCHEMATIC

# Some clients prefix these instructions to every discovered tool description.
# Keep them small; never concatenate the workflow bodies here.
INSTRUCTIONS = (
    "KiCadFlow edits and inspects KiCad designs. For substantial work, read only "
    "the relevant workflow once via get_workflow(topic) or "
    "kicad-flow://workflows/{topic}: schematic, pcb, parts, documentation. "
    "Reuse it while the task is unchanged; simple lookups need no workflow.\n"
    f"At most {BATCH_LIMIT} items per operation list, including placements and "
    "batch.ops. Split larger work into calls; inspect each reply. Geometry "
    "vertices and returned inventories are not operation batches.\n"
    "Coordinates are millimetres. The caller chooses parts and geometry. Use "
    "returned pin/pad positions; place components before wiring. For staged PCB "
    "transfer, continue remaining_refs until complete=true.\n"
    "ok=true means the call ran, not that the design passes. Verify net membership, "
    "ERC/DRC and renders before completion. Circuit Context supplies engineering "
    "guidance; query CAD tools for actual design state."
)

WORKFLOWS = {
    "schematic": SCHEMATIC,
    "pcb": PCB,
    "parts": PARTS,
    "documentation": DOCUMENTATION,
}

__all__ = ["DOCUMENTATION", "INSTRUCTIONS", "PARTS", "PCB", "SCHEMATIC", "WORKFLOWS"]
