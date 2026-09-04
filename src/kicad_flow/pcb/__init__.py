"""The board contract, and the arithmetic that goes with it.

:class:`~kicad_flow.pcb.api.Board` is an ABC naming no file format and no tool.
This package imports no backend, so it can be read and type-checked without
one; ask :mod:`kicad_flow.backend` for a board to work on::

    from kicad_flow.backend import create_board, load_board

There is nothing above these primitives. No autoplacer, no router, no via
stitcher, no fanout, no silkscreen cleaner. Each of those decided something the
caller could not override -- where a part goes, how a track runs, which pads
get a via -- and deciding is the caller's job now.

Nothing is kept beside the contract either. This package is `api.py` and
`types.py` and no more, which is what :mod:`kicad_flow.schematic` already was.
The via sizer, the impedance solver and the fab-process tables that used to sit
here had no caller; the footprint-library resolution that did has moved down
into the backend, where knowing what an ``fp-lib-table`` is belongs.
"""

from __future__ import annotations

from .api import Board
from .types import (
    Connection,
    Finding,
    Footprint,
    FootprintDef,
    Net,
    NetPad,
    Pad,
    Point,
    Track,
    Via,
    Zone,
)

__all__ = ["Board", "Connection", "Finding", "Footprint", "FootprintDef",
           "Net", "NetPad", "Pad", "Point", "Track", "Via", "Zone"]
