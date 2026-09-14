"""Persistent net-class patterns shared by the schematic and PCB editors."""

from __future__ import annotations

import json
from pathlib import Path

from kicad_flow.pcb.types import NetClassPattern

from . import project


def read(board: Path) -> list[NetClassPattern]:
    """Read patterns in their stored order."""
    settings = project._net_settings(project._read_project(board))
    raw = settings.get("netclass_patterns") or []
    if not isinstance(raw, list):
        raise ValueError("project netclass_patterns must be a list")
    return [NetClassPattern(str(p["pattern"]), str(p["netclass"]))
            for p in raw if isinstance(p, dict)
            and "pattern" in p and "netclass" in p]


def replace(board: Path, patterns: tuple[NetClassPattern, ...]
            ) -> list[NetClassPattern]:
    """Validate and atomically replace the complete pattern list."""
    known = {item.name for item in project.net_classes(board)}
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(patterns):
        if not item.pattern.strip() or not item.net_class.strip():
            raise ValueError(f"patterns[{index}]: empty pattern or net_class")
        if item.net_class not in known:
            raise LookupError(f"patterns[{index}]: unknown netclass {item.net_class!r}")
        key = (item.pattern, item.net_class)
        if key in seen:
            raise ValueError(f"patterns[{index}]: duplicate pattern/class pair")
        seen.add(key)
    data = project._read_project(board)
    settings = project._net_settings(data)
    settings["netclass_patterns"] = [
        {"pattern": item.pattern, "netclass": item.net_class} for item in patterns
    ]
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    project._validate_sidecars(board, project=text)
    project._atomic_text(project._project_path(board), text)
    return list(patterns)
