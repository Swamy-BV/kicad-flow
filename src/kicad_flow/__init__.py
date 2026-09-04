"""kicad-flow: an MCP server for authoring KiCad schematics and boards.

:mod:`kicad_flow.schematic` and :mod:`kicad_flow.pcb` are the contracts -- an
ABC each, naming no file format and no tool. Everything that knows what a KiCad
file *is* lives under :mod:`kicad_flow.backend`, which is where you ask for a
sheet or a board to work on::

    from kicad_flow.backend import create, load, create_board, load_board

Neither contract package imports a backend, so the interface can be read and
type-checked without one.
"""

from __future__ import annotations

__version__ = "0.1.0"
