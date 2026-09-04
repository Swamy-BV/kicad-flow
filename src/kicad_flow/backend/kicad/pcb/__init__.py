"""KiCad's ``.kicad_pcb`` behind :class:`kicad_flow.pcb.api.Board`.

Nothing above the contract imports this. A caller reaches it through
:func:`kicad_flow.backend.create_board` and :func:`load_board`, which are
typed as the ABC.
"""

from __future__ import annotations

from .board import KiCadBoard, create, load

__all__ = ["KiCadBoard", "create", "load"]
