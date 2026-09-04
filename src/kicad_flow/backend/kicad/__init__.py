"""The KiCad backend: the only code that knows KiCad's files and tools.

Six things live here that read as neutral infrastructure and are not. Four are
**private**, because nothing outside this package has any business touching a
KiCad file directly:

* :mod:`_sexpr` -- KiCad's dialect of S-expressions, not a general reader.
* :mod:`_library` -- loading and flattening a ``.kicad_sym``.
* :mod:`_fileio` -- writing a KiCad tree atomically.
* :mod:`_build` -- helpers for constructing nodes.

Two are not, only because the board side has no interface yet and needs them:

* :mod:`cli` -- finding and running ``kicad-cli``.
* :mod:`render` -- ``sch/pcb export pdf`` plus rasterising the result.

Both become private too once ``pcb/`` has an ABC to hide behind.

They used to sit in ``core/``, ``lib/`` and ``interchange/``, where nothing
said they were tool-specific. They are all of them KiCad-shaped, and a second
backend would want none of them.

``_fileio`` and ``_build`` have no caller inside this package at all -- their
only user is ``fab/bom.py``, from outside. That is not a sign they are shared
infrastructure; it is ARCH-1 and SCH-7 in BUGS.md, visible in the import line.

:mod:`schematic` implements :class:`kicad_flow.schematic.api.Sheet`. There is
no ``pcb`` package here yet: the board code still lives at
``kicad_flow.pcb`` with no interface over it.
"""

from __future__ import annotations

from .pcb import KiCadBoard
from .pcb import create as create_board
from .pcb import load as load_board
from .schematic import PAPER, KiCadSheet, create, load

__all__ = ["PAPER", "KiCadBoard", "KiCadSheet", "create",
           "create_board", "load", "load_board"]
