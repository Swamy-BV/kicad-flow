"""Primitive schematic editing: the contract, and the nouns it deals in.

:class:`~kicad_flow.schematic.api.Sheet` is an ABC naming no file format and no
tool. This package imports no backend, so it can be read and type-checked
without one; ask :mod:`kicad_flow.backend` for a sheet to work on::

    from kicad_flow.backend import create, load   # or kicad_flow.create

There is nothing above these primitives. No autoplacer, no router, no
floorplanner, no design document. Each of those decided something the caller
could not override; deciding is the caller's job now. What is left reports
facts -- where a pin is, what meets at a point -- and does what it is told.
"""

from __future__ import annotations

from .api import GRID, Sheet, snap
from .types import Finding, Label, Net, NetPin, Part, Pin, Point, SheetRef, SymbolDef

__all__ = ["GRID", "Finding", "Label", "Net", "NetPin", "Part", "Pin", "Point",
           "Sheet", "SheetRef", "SymbolDef", "snap"]
