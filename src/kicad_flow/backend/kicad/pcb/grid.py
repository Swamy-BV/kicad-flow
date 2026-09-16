"""KiCadFlow's footprint placement grid, stored beside a KiCad board."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .project import _atomic_text

DEFAULT_SPACING_MM = 0.25


def _path(board: Path) -> Path:
    # Share the existing project-local KiCadFlow metadata file with the
    # fabrication profile, whose writer preserves unrelated keys.
    return board.with_suffix(".kicad-flow.json")


def _read(board: Path) -> dict[str, Any]:
    path = _path(board)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"grid settings root must be an object: {path}")
    return data


def spacing(board: Path) -> float:
    """Read the selected spacing, or the recommended default for old boards."""
    value = _read(board).get("placement_grid_mm", DEFAULT_SPACING_MM)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("placement_grid_mm must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("placement_grid_mm must be a positive finite number")
    return result


def set_spacing(board: Path, value: float) -> float:
    """Persist an explicit spacing without touching KiCad geometry."""
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise ValueError("placement grid spacing must be positive and finite")
    settings = _read(board)
    settings["placement_grid_mm"] = float(value)
    _atomic_text(_path(board), json.dumps(settings, indent=2) + "\n")
    return spacing(board)
