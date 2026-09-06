"""KiCad project netclasses and custom rules, kept beside one board.

The public board contract names routing policy without naming KiCad storage.
This module is the storage boundary: netclasses live in project JSON and
conditional rules live in the sibling ``.kicad_dru`` document.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from kicad_flow.pcb.types import (
    BoardLimits,
    BoardRule,
    Constraint,
    NetClass,
    NetClassAssignment,
)

from .._sexpr import Node, Sym, dumps, loads
from ..cli import cli

_LIMIT_KEYS = {
    "min_clearance": "min_clearance",
    "min_track_width": "min_track_width",
    "min_via_diameter": "min_via_diameter",
    "min_via_drill": "min_through_hole_diameter",
    "min_annular_width": "min_via_annular_width",
    "min_hole_clearance": "min_hole_clearance",
    "min_hole_to_hole": "min_hole_to_hole",
    "min_copper_edge_clearance": "min_copper_edge_clearance",
    "min_silk_clearance": "min_silk_clearance",
    "min_text_height": "min_text_height",
    "min_text_thickness": "min_text_thickness",
    "min_groove_width": "min_groove_width",
    "solder_mask_to_copper_clearance": "solder_mask_to_copper_clearance",
}

_DIMENSIONS = (
    "clearance", "track_width", "via_diameter", "via_drill",
    "microvia_diameter", "microvia_drill", "diff_pair_width",
    "diff_pair_gap", "diff_pair_via_gap",
)
_NUMERIC_CONSTRAINTS = {
    "annular_width",
    "clearance",
    "connection_width",
    "courtyard_clearance",
    "creepage",
    "diff_pair_gap",
    "diff_pair_uncoupled",
    "edge_clearance",
    "hole_clearance",
    "hole_size",
    "length",
    "physical_clearance",
    "physical_hole_clearance",
    "silk_clearance",
    "skew",
    "thermal_relief_gap",
    "thermal_spoke_width",
    "track_width",
    "via_diameter",
}
_DEFAULT_CLASS: dict[str, Any] = {
    "name": "Default",
    "clearance": 0.2,
    "track_width": 0.2,
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "microvia_diameter": 0.3,
    "microvia_drill": 0.1,
    "diff_pair_width": 0.2,
    "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25,
    "bus_width": 12,
    "line_style": 0,
    "pcb_color": "rgba(0, 0, 0, 0.000)",
    "priority": 2147483647,
    "schematic_color": "rgba(0, 0, 0, 0.000)",
    "wire_width": 6,
}
_CLASS_DISPLAY: dict[str, Any] = {
    "bus_width": 12,
    "line_style": 0,
    "pcb_color": "rgba(0, 0, 0, 0.000)",
    "priority": 0,
    "schematic_color": "rgba(0, 0, 0, 0.000)",
    "wire_width": 6,
}


def _project_path(board: Path) -> Path:
    return board.with_suffix(".kicad_pro")


def _rules_path(board: Path) -> Path:
    return board.with_suffix(".kicad_dru")


def _read_project(board: Path) -> dict[str, Any]:
    path = _project_path(board)
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"project root must be an object: {path}")
        return loaded
    return {
        "meta": {"filename": path.name, "version": 3},
        "net_settings": {
            "classes": [dict(_DEFAULT_CLASS)],
            "meta": {"version": 5},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [],
        },
    }


def _net_settings(project: dict[str, Any]) -> dict[str, Any]:
    settings = project.setdefault("net_settings", {})
    if not isinstance(settings, dict):
        raise ValueError("project net_settings must be an object")
    settings.setdefault("meta", {"version": 5})
    settings.setdefault("net_colors", None)
    settings.setdefault("netclass_patterns", [])
    return settings


def _design_rules(project: dict[str, Any]) -> dict[str, Any]:
    settings = project.setdefault("board", {}).setdefault("design_settings", {})
    if not isinstance(settings, dict):
        raise ValueError("project board.design_settings must be an object")
    rules = settings.setdefault("rules", {})
    if not isinstance(rules, dict):
        raise ValueError("project board.design_settings.rules must be an object")
    return rules


def limits(board: Path) -> BoardLimits:
    """Board-wide numeric limits stored in the project document."""
    rules = _design_rules(_read_project(board))
    values: dict[str, float | None] = {}
    for name, key in _LIMIT_KEYS.items():
        value = rules.get(key)
        values[name] = float(value) if isinstance(value, (int, float)) else None
    return BoardLimits(**values)


def set_limits(board: Path, supplied: BoardLimits) -> BoardLimits:
    """Update only explicitly supplied board-wide limits atomically."""
    values = supplied.as_dict()
    if any(value < 0 for value in values.values()):
        raise ValueError("board limits cannot be negative")
    project = _read_project(board)
    rules = _design_rules(project)
    for name, value in values.items():
        key = _LIMIT_KEYS.get(name)
        if key is not None:
            rules[key] = value
    text = json.dumps(project, indent=2, ensure_ascii=False) + "\n"
    _validate_sidecars(board, project=text)
    _atomic_text(_project_path(board), text)
    return limits(board)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.writing")
    try:
        scratch.write_text(text, encoding="utf-8")
        os.replace(scratch, path)
    except Exception:
        scratch.unlink(missing_ok=True)
        raise


def _validate_sidecars(board: Path, *, project: str | None = None,
                       rules: str | None = None) -> None:
    """Have KiCad load proposed sidecars without touching the real project."""
    if not board.is_file():
        return
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        copied = root / board.name
        shutil.copyfile(board, copied)
        pro = _project_path(board)
        dru = _rules_path(board)
        if project is not None:
            (root / pro.name).write_text(project, encoding="utf-8")
        elif pro.is_file():
            shutil.copyfile(pro, root / pro.name)
        if rules is not None:
            (root / dru.name).write_text(rules, encoding="utf-8")
        elif dru.is_file():
            shutil.copyfile(dru, root / dru.name)
        cli.drc(copied)


def _class_from(raw: dict[str, Any]) -> NetClass:
    values: dict[str, Any] = {"name": str(raw.get("name", ""))}
    for name in _DIMENSIONS:
        value = raw.get(name)
        values[name] = float(value) if isinstance(value, (int, float)) else None
    return NetClass(**values)


def net_classes(board: Path) -> list[NetClass]:
    """Every netclass in the board's project."""
    raw = _net_settings(_read_project(board)).get("classes") or []
    if not isinstance(raw, list):
        raise ValueError("project net_settings.classes must be a list")
    return [_class_from(item) for item in raw if isinstance(item, dict)]


def set_net_classes(board: Path,
                    classes: tuple[NetClass, ...]) -> list[NetClass]:
    """Upsert a complete typed list of netclasses atomically."""
    names = [item.name for item in classes]
    if any(not name.strip() for name in names):
        raise ValueError("netclass name cannot be empty")
    if len(set(names)) != len(names):
        raise ValueError("netclass names must be unique within one call")
    for item in classes:
        values = [getattr(item, name) for name in _DIMENSIONS]
        if any(value is not None and value < 0 for value in values):
            raise ValueError(f"netclass {item.name!r} has a negative dimension")
        if (item.via_diameter is not None and item.via_drill is not None
                and item.via_drill > item.via_diameter):
            raise ValueError(f"netclass {item.name!r} via drill exceeds diameter")
        if (item.microvia_diameter is not None
                and item.microvia_drill is not None
                and item.microvia_drill > item.microvia_diameter):
            raise ValueError(f"netclass {item.name!r} microvia drill exceeds "
                             "diameter")

    project = _read_project(board)
    settings = _net_settings(project)
    raw = settings.setdefault("classes", [dict(_DEFAULT_CLASS)])
    if not isinstance(raw, list):
        raise ValueError("project net_settings.classes must be a list")
    by_name = {str(item.get("name", "")): item for item in raw
               if isinstance(item, dict)}
    for item in classes:
        current = by_name.get(item.name)
        if current is None:
            current = dict(_DEFAULT_CLASS if item.name == "Default"
                           else _CLASS_DISPLAY)
            current["name"] = item.name
            raw.append(current)
            by_name[item.name] = current
        for name in _DIMENSIONS:
            value = getattr(item, name)
            if value is not None:
                current[name] = value
    text = json.dumps(project, indent=2, ensure_ascii=False) + "\n"
    _validate_sidecars(board, project=text)
    _atomic_text(_project_path(board), text)
    return [_class_from(by_name[name]) for name in names]


def assignments(board: Path) -> list[NetClassAssignment]:
    """Every explicit netclass assignment."""
    raw = _net_settings(_read_project(board)).get("netclass_assignments") or {}
    if not isinstance(raw, dict):
        raise ValueError("project netclass_assignments must be an object")
    return [NetClassAssignment(str(net), str(name))
            for net, names in raw.items() if isinstance(names, list)
            for name in names]


def assign(board: Path, items: tuple[NetClassAssignment, ...]
           ) -> list[NetClassAssignment]:
    """Replace the mentioned nets' memberships and preserve every other net."""
    if any(not item.net.strip() or not item.net_class.strip() for item in items):
        raise ValueError("assignment net and net_class cannot be empty")
    known = {item.name for item in net_classes(board)}
    missing = sorted({item.net_class for item in items} - known)
    if missing:
        raise LookupError(f"unknown netclass(es): {', '.join(missing)}")
    project = _read_project(board)
    settings = _net_settings(project)
    raw = settings.get("netclass_assignments") or {}
    if not isinstance(raw, dict):
        raise ValueError("project netclass_assignments must be an object")
    grouped: dict[str, list[str]] = {}
    for item in items:
        grouped.setdefault(item.net, [])
        if item.net_class not in grouped[item.net]:
            grouped[item.net].append(item.net_class)
    raw.update(grouped)
    settings["netclass_assignments"] = raw or None
    text = json.dumps(project, indent=2, ensure_ascii=False) + "\n"
    _validate_sidecars(board, project=text)
    _atomic_text(_project_path(board), text)
    return [NetClassAssignment(net, name) for net, names in grouped.items()
            for name in names]


def _forms(text: str) -> list[tuple[int, int, Node]]:
    """Top-level S-expression spans, ignoring ``#`` comments."""
    out: list[tuple[int, int, Node]] = []
    depth = 0
    start = -1
    quoted = False
    escaped = False
    comment = False
    for index, char in enumerate(text):
        if comment:
            if char in "\r\n":
                comment = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == "#":
            comment = True
        elif char == '"':
            quoted = True
        elif char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced ')' in rules file")
            if depth == 0 and start >= 0:
                end = index + 1
                out.append((start, end, loads(text[start:end])))
                start = -1
    if quoted or depth:
        raise ValueError("unterminated string or expression in rules file")
    return out


def _atom(node: Node | None, index: int = 0) -> str:
    if node is None or len(node.items) <= index + 1:
        return ""
    return str(node.items[index + 1])


def _millimetres(node: Node | None) -> float | None:
    raw = _atom(node)
    if not raw:
        return None
    if raw.endswith("mm"):
        raw = raw[:-2]
    try:
        return float(raw)
    except ValueError:
        return None


def _rule_from(node: Node) -> BoardRule | None:
    if node.name != "rule":
        return None
    constraints = []
    for item in node.get_all("constraint"):
        constraint = Constraint(
            kind=_atom(item),
            minimum=_millimetres(item.get("min")),
            optimum=_millimetres(item.get("opt")),
            maximum=_millimetres(item.get("max")),
        )
        if any(value is not None for value in (
                constraint.minimum, constraint.optimum, constraint.maximum)):
            constraints.append(constraint)
    return BoardRule(
        name=_atom(node), condition=_atom(node.get("condition")),
        constraints=tuple(constraints), layer=_atom(node.get("layer")),
    )


def rules(board: Path) -> list[BoardRule]:
    """Every numeric rule in the custom rules document."""
    path = _rules_path(board)
    if not path.is_file():
        return []
    return [rule for _, _, node in _forms(path.read_text(encoding="utf-8"))
            if (rule := _rule_from(node)) is not None]


def _constraint_node(item: Constraint) -> Node:
    children: list[Node | Sym | str] = [Sym("constraint"), Sym(item.kind)]
    for name, value in (("min", item.minimum), ("opt", item.optimum),
                        ("max", item.maximum)):
        if value is not None:
            children.append(Node([Sym(name), Sym(f"{value:g}mm")]))
    return Node(children)


def _rule_node(rule: BoardRule) -> Node:
    items: list[Node | Sym | str] = [Sym("rule"), rule.name]
    if rule.layer:
        layer: Sym | str = (Sym(rule.layer) if rule.layer in ("outer", "inner")
                            else rule.layer)
        items.append(Node([Sym("layer"), layer]))
    items.extend(_constraint_node(item) for item in rule.constraints)
    items.append(Node([Sym("condition"), rule.condition]))
    return Node(items)


def set_rules(board: Path, items: tuple[BoardRule, ...]) -> list[BoardRule]:
    """Upsert named rules while preserving untouched source and comments."""
    names = [item.name for item in items]
    if any(not item.name.strip() or not item.condition.strip() for item in items):
        raise ValueError("rule name and condition cannot be empty")
    if len(set(names)) != len(names):
        raise ValueError("rule names must be unique within one call")
    for rule in items:
        if not rule.constraints:
            raise ValueError(f"rule {rule.name!r} needs at least one constraint")
        for item in rule.constraints:
            if item.kind not in _NUMERIC_CONSTRAINTS:
                raise ValueError(
                    f"unsupported numeric constraint {item.kind!r}; expected "
                    f"one of {sorted(_NUMERIC_CONSTRAINTS)}"
                )
            values = (item.minimum, item.optimum, item.maximum)
            if all(value is None for value in values):
                raise ValueError(f"constraint {item.kind!r} needs a bound")
            if any(value is not None and value < 0 for value in values):
                raise ValueError(f"constraint {item.kind!r} has a negative bound")
            stated = [value for value in values if value is not None]
            if stated != sorted(stated):
                raise ValueError(f"constraint {item.kind!r} bounds are out of order")

    path = _rules_path(board)
    text = path.read_text(encoding="utf-8") if path.is_file() else "(version 1)\n"
    forms = _forms(text)
    existing = {_atom(node): (start, end) for start, end, node in forms
                if node.name == "rule"}
    additions = []
    replacements = []
    for rule in items:
        rendered = dumps(_rule_node(rule))
        if rule.name in existing:
            start, end = existing[rule.name]
            replacements.append((start, end, rendered))
        else:
            additions.append(rendered)
    for start, end, rendered in sorted(replacements, reverse=True):
        text = text[:start] + rendered + text[end:]
    if additions:
        text = text.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
    _forms(text)  # prove our own complete document parses before KiCad sees it
    _validate_sidecars(board, rules=text)
    _atomic_text(path, text)
    return list(items)


__all__ = ["assign", "assignments", "limits", "net_classes", "rules",
           "set_limits", "set_net_classes", "set_rules"]
