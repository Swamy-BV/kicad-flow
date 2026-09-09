"""KiCad's ``.kicad_sch`` behind :class:`kicad_flow.schematic.api.Sheet`.

Nothing above the contract imports this. A caller reaches it through
:func:`kicad_flow.schematic.create` and :func:`kicad_flow.schematic.load`,
which are typed as the ABC, so binding to the return value binds to the
interface rather than to this backend.
"""

from __future__ import annotations

from .sheet import PAPER, KiCadSheet, create, load

__all__ = ["PAPER", "KiCadSheet", "create", "load"]
