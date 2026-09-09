"""The FastMCP instance the tool modules hang off.

Separate from ``__init__`` only to break a cycle: ``tools_schematic`` and
``tools_board`` need ``mcp`` to decorate with, and ``__init__`` imports both of
them to register their tools.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .instructions import INSTRUCTIONS

mcp = FastMCP(
    name="kicad-flow",
    instructions=INSTRUCTIONS,
)

__all__ = ["mcp"]
