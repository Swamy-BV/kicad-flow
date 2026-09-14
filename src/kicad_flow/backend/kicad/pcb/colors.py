"""Translate explicit hex colors to and from KiCad project color strings."""

from __future__ import annotations

import re

_HEX = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?")
_RGBA = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
    r"(?:\s*,\s*(\d*\.?\d+))?\s*\)"
)


def to_project(value: str) -> str:
    """Encode hex RGB/RGBA; empty means the unspecified color sentinel."""
    if value == "":
        return "rgba(0, 0, 0, 0.000)"
    if not _HEX.fullmatch(value):
        raise ValueError("color must be #RRGGBB, #RRGGBBAA, or empty to clear")
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    alpha = int(value[7:9], 16) / 255 if len(value) == 9 else 1.0
    if alpha == 0:
        return "rgba(0, 0, 0, 0.000)"
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def from_project(value: object) -> str | None:
    """Read canonical hex; transparent is empty and missing is None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("project color must be a string")
    if value == "" or _HEX.fullmatch(value):
        return from_project(to_project(value))
    match = _RGBA.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported project color: {value!r}")
    r, g, b = (int(match[i]) for i in (1, 2, 3))
    alpha = float(match[4]) if match[4] is not None else 1.0
    if any(c > 255 for c in (r, g, b)) or not 0 <= alpha <= 1:
        raise ValueError(f"project color channels out of range: {value!r}")
    if alpha == 0:
        return ""
    rgb = f"#{r:02X}{g:02X}{b:02X}"
    return rgb if alpha == 1 else f"{rgb}{round(alpha * 255):02X}"
