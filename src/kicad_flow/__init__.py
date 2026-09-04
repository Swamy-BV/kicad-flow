"""kicad-flow: an MCP server for authoring KiCad schematics and boards.

The schematic side is :mod:`kicad_flow.schematic` -- a small set of primitives
(place a part, ask where a pin is, draw a wire) behind an interface that names
no tool. There is deliberately nothing above it: no autoplacer, no router, no
floorplanner. Those existed, and each one decided something a caller had no way
to override. Deciding is the caller's job now.

Everything that knows what a KiCad file *is* lives under
:mod:`kicad_flow.backend`. Ask it for a sheet::

    import kicad_flow
    sheet = kicad_flow.create("board.kicad_sch")   # typed as Sheet

``create`` and ``load`` are resolved on first use rather than on import, so
``import kicad_flow.schematic`` reads the contract without loading a backend,
finding ``kicad-cli`` or parsing a symbol library. Someone writing a second
backend should be able to read the interface without running the first one.

The board side (:mod:`kicad_flow.pcb`) still carries its own placement and
routing, and has not been through the same treatment.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.2.0"

__all__ = ["create", "load"]


def __getattr__(name: str) -> Any:
    """Resolve the backend factories lazily. See the module docstring."""
    if name in __all__:
        from . import backend

        return getattr(backend, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
