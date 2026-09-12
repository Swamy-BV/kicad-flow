"""PCB open-document registry, file tools and atomic list writes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...backend import create_board, load_board
from ...pcb.api import Board
from .. import _meta
from .._app import mcp

_OPEN: dict[str, Board] = {}


def _key(path: str) -> str:
    """The dictionary key for a board path."""
    return os.path.normcase(str(Path(path).resolve()))


def _board(path: str) -> Board:
    """The open board for *path*, loading it from disk if need be."""
    key = _key(path)
    if key not in _OPEN:
        if not Path(path).is_file():
            raise LookupError(
                f"no file at {path}. An existing .kicad_pcb "
                f"reopens by itself -- just name it. Use `new_board` only to "
                f"create one, which OVERWRITES whatever is there."
            )
        _OPEN[key] = load_board(path)
    return _OPEN[key]


def _fail(exc: Exception) -> dict[str, Any]:
    """A refusal that says what went wrong."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


_ERRORS = (LookupError, ValueError, OSError, RuntimeError)


def _atomic_items(
    board: Board, items: list[Any], key: str, each: Any
) -> dict[str, Any]:
    """Apply scalar board primitives as one all-or-nothing list write."""
    out: list[Any] = []
    _failed_index = 0
    try:
        with board.transaction():
            for _failed_index, item in enumerate(items):
                out.append(each(board, item))
    except (IndexError, *_ERRORS) as exc:
        return {**_fail(exc), "index": _failed_index, "applied_count": 0, key: []}
    return {"ok": True, "count": len(out), key: out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def new_board(path: str, layers: int = 2, thickness: float = 1.6) -> dict[str, Any]:
    """Start a new board and open it for editing.

    Nothing is written until `save_board`. Set the layer count HERE: changing
    it after routing invalidates the route, because an inner-layer track on a
    layer that no longer exists does not move, it disappears.

    Args:
        path: Where the board will be written.
        layers: Copper layers -- 2, 4, 6 or 8.
        thickness: Board thickness in mm.

    Returns:
        ``{ok, path, layers}``.
    """
    try:
        board = create_board(path, layers=layers, thickness=thickness)
    except _ERRORS as exc:
        return _fail(exc)
    _OPEN[_key(path)] = board
    return {"ok": True, "path": str(board.path), "layers": list(board.layers)}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def save_board(path: str) -> dict[str, Any]:
    """Write the open board to disk."""
    try:
        board = _board(path)
        written = board.save(validate=True)
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "path": str(written),
        "footprints": len(board.footprints()),
        "tracks": len(board.tracks()),
        "vias": len(board.vias()),
        "zones": len(board.zones()),
    }


def _blank(project_dir: str = "") -> Board:
    """A throwaway board, for library queries that need no file."""
    directory = Path(project_dir).resolve() if project_dir else Path.cwd()
    return create_board(directory / "_query.kicad_pcb")
