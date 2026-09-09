"""Shared tags + annotations for MCP tools (tiering + read-only hints).

Every ``@mcp.tool`` carries a **phase** tag (``schematic``/``pcb``) and a
**tier** tag: ``primary`` for the tools to reach for first, ``inspect`` for
read-only queries and checks.

There is no ``advanced`` tier and no ``fab`` phase. ``advanced`` meant a
low-level escape hatch with a batch/auto equivalent to prefer instead; those
equivalents were the deciders, and with them gone both surfaces are primitives
all the way down, so there is no golden path to hide anything in favour of.

Annotations are the standard MCP hints; passed as plain dicts (FastMCP accepts a
dict for ``annotations=``).
"""

from __future__ import annotations

# MCP annotation hints.
READ: dict[str, bool] = {"read_only_hint": True, "idempotent_hint": True}
WRITE: dict[str, bool] = {"read_only_hint": False}
DESTRUCTIVE: dict[str, bool] = {"read_only_hint": False, "destructive_hint": True}

# Phase x tier tag sets.
SCH_PRIMARY = {"schematic", "primary"}
SCH_INSPECT = {"schematic", "inspect"}
PCB_PRIMARY = {"pcb", "primary"}
PCB_INSPECT = {"pcb", "inspect"}
PARTS_PRIMARY = {"parts", "primary"}
PARTS_INSPECT = {"parts", "inspect"}
