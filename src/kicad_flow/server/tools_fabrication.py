"""MCP tools selecting fabrication facts without exposing a CAD backend."""

from __future__ import annotations

from typing import Any

from ..pcb.types import BoardLimits
from ..providers import FabricationSelection, fabrication_provider
from . import _meta
from ._app import mcp
from ._fabrication import profile_findings, read_profile, write_profile
from .tools_board import _board

_ERRORS = (LookupError, ValueError, OSError, RuntimeError)


def _fail(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_fabrication_profile(
    path: str,
    provider: str = "jlcpcb",
    board_type: str = "rigid_fr4",
    material: str = "FR-4",
    outer_copper_oz: float = 1.0,
    inner_copper_oz: float | None = None,
    finish: str = "ENIG",
    soldermask_color: str = "green",
    outline_process: str = "routed",
    impedance_control: bool = False,
    tier: str = "recommended",
) -> dict[str, Any]:
    """Resolve and apply an explicit manufacturing profile before layout.

    Layer count and thickness are facts read from the board. Every process
    choice remains an argument; none is guessed from nearby geometry. The
    operation changes limits and project metadata, never board geometry.
    """
    try:
        board = _board(path)
        profile = fabrication_provider(provider).resolve(FabricationSelection(
            board_type=board_type,
            layers=len(board.layers),
            thickness=board.thickness,
            material=material,
            outer_copper_oz=outer_copper_oz,
            inner_copper_oz=inner_copper_oz,
            finish=finish,
            soldermask_color=soldermask_color,
            outline_process=outline_process,
            impedance_control=impedance_control,
            tier=tier,
        ))
        payload = profile.as_dict()
        incompatibilities = profile_findings(board, payload)
        if incompatibilities:
            raise ValueError(incompatibilities[0].message)
        board.set_limits(BoardLimits(**profile.limits.as_dict()))
        stored = write_profile(path, payload)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "profile_path": str(stored), "profile": payload}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_fabrication_profile(path: str) -> dict[str, Any]:
    """Read the active project-local manufacturing profile."""
    try:
        profile = read_profile(path)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "active": profile is not None, "profile": profile}


__all__ = ["get_fabrication_profile", "set_fabrication_profile"]
