"""The KiCad backend: the only code that knows KiCad's files and tools.

Private, because nothing outside this package has any business touching a
KiCad file directly:

* :mod:`_sexpr` -- KiCad's dialect of S-expressions, not a general reader.
* :mod:`_library` -- loading and flattening a ``.kicad_sym``.
* :mod:`_fileio` -- writing a KiCad tree atomically.

Public, because a caller legitimately needs to run the tool and look at the
result -- the monitor uses both:

* :mod:`cli` -- finding and running ``kicad-cli``.
* :mod:`render` -- ``sch/pcb export pdf`` plus rasterising the result.

They used to sit in ``core/``, ``lib/`` and ``interchange/``, where nothing
said they were tool-specific. They are all of them KiCad-shaped, and a second
backend would want none of them.

:mod:`schematic` implements :class:`kicad_flow.schematic.api.Sheet` and
:mod:`pcb` implements :class:`kicad_flow.pcb.api.Board`. Nothing above this
package imports it and it imports nothing above -- measured, both directions.
"""

from __future__ import annotations

from .pcb import KiCadBoard
from .pcb import create as create_board
from .pcb import load as load_board
from .schematic import PAPER, KiCadSheet, create, load

__all__ = ["PAPER", "KiCadBoard", "KiCadSheet", "create",
           "create_board", "load", "load_board"]
