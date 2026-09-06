"""Persistence and checks for provider-neutral fabrication profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..pcb.api import Board
from ..pcb.types import Finding


def profile_path(board: str | Path) -> Path:
    """The project-local profile sidecar for one board."""
    return Path(board).with_suffix(".kicad-flow.json")


def read_profile(board: str | Path) -> dict[str, Any] | None:
    """Read the active profile, or None when no provider was selected."""
    path = profile_path(board)
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"fabrication profile root must be an object: {path}")
    profile = loaded.get("fabrication_profile")
    if profile is None:
        return None
    if not isinstance(profile, dict):
        raise ValueError(f"fabrication_profile must be an object: {path}")
    return profile


def write_profile(board: str | Path, profile: dict[str, Any]) -> Path:
    """Atomically store a resolved profile while preserving other metadata."""
    path = profile_path(board)
    root: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"fabrication profile root must be an object: {path}")
        root = loaded
    root["fabrication_profile"] = profile
    text = json.dumps(root, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.writing")
    try:
        scratch.write_text(text, encoding="utf-8")
        os.replace(scratch, path)
    except Exception:
        scratch.unlink(missing_ok=True)
        raise
    return path


def profile_findings(board: Board, profile: dict[str, Any]) -> list[Finding]:
    """Check active-profile facts that native electrical DRC cannot express."""
    out: list[Finding] = []
    selection = profile.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("fabrication profile has no valid selection")
    selected_layers = int(selection.get("layers", 0))
    if len(board.layers) != selected_layers:
        out.append(Finding(
            "error", "provider_layer_count",
            f"board has {len(board.layers)} layers; active provider profile "
            f"was resolved for {selected_layers}",
        ))
    selected_thickness = float(selection.get("thickness", 0.0))
    if abs(board.thickness - selected_thickness) > 0.001:
        out.append(Finding(
            "error", "provider_board_thickness",
            f"board thickness is {board.thickness:g} mm; active provider "
            f"profile was resolved for {selected_thickness:g} mm",
        ))
    width, height = board.size
    if width == 0.0 or height == 0.0:
        return out
    minimum = profile.get("minimum_size")
    maximum = profile.get("maximum_size")
    if not (
        isinstance(minimum, list) and len(minimum) == 2
        and isinstance(maximum, list) and len(maximum) == 2
    ):
        raise ValueError("fabrication profile has invalid size bounds")
    min_w, min_h = float(minimum[0]), float(minimum[1])
    max_w, max_h = float(maximum[0]), float(maximum[1])
    if width < min_w or height < min_h:
        out.append(Finding(
            "error", "provider_board_size",
            f"board is {width:g} x {height:g} mm; provider minimum is "
            f"{min_w:g} x {min_h:g} mm",
        ))
    elif width > max_w or height > max_h:
        out.append(Finding(
            "error", "provider_board_size",
            f"board is {width:g} x {height:g} mm; provider maximum is "
            f"{max_w:g} x {max_h:g} mm",
        ))
    return out


__all__ = ["profile_findings", "profile_path", "read_profile", "write_profile"]
