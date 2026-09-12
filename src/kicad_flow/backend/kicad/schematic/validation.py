"""Native netlists, electrical checks and hierarchy layout findings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kicad_flow.schematic.types import (
    Finding,
    LayoutFinding,
    Net,
    NetPin,
    Point,
)

from .._sexpr import loads
from ._geometry import (
    _Box,
    _hidden,
    _on_segment,
    _segment_crosses_box,
    _text_box,
    _VisibleText,
)
from ._nodes import (
    _LABEL_NODE,
    _f,
    _text,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def nets(self: KiCadSheet) -> list[Net]:
    """What this sheet actually connects, from KiCad's own netlist."""
    # Imported here, not at module scope: the package root imports both
    # this and the interchange layer, and one of the two has to go second.
    from . import netlist as _netlist

    with self._scratch() as scratch:
        tree = _netlist.export_netlist(scratch)
    found: list[Net] = []
    table = tree.get("nets")
    for node in table.get_all("net") if table is not None else []:
        pins = tuple(
            NetPin(
                ref=_text(item.get("ref")),
                pin=_text(item.get("pin")),
                name=_text(item.get("pinfunction")),
            )
            for item in node.get_all("node")
        )
        found.append(Net(name=_text(node.get("name")), pins=pins))
    return sorted(found, key=lambda n: n.name)


def check(self: KiCadSheet) -> list[Finding]:
    """Every violation, mapped from a position back to a part and pin."""
    from ..cli import cli as _kicad
    from .sheet import KiCadSheet

    where_by_page: dict[str, dict[tuple[float, float], tuple[str, str]]] = {}
    seen: set[Path] = set()

    def index(sheet: KiCadSheet, page: str) -> None:
        """Index every page in the same hierarchy KiCad checks."""
        resolved = sheet.path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        where: dict[tuple[float, float], tuple[str, str]] = {}
        for part in sheet.parts():
            where[(round(part.at.x, 2), round(part.at.y, 2))] = (part.ref, "")
            for pin in part.pins:
                key = (round(pin.at.x, 2), round(pin.at.y, 2))
                where[key] = (part.ref, pin.number)
        where_by_page[page] = where
        for child in sheet._tree.get_all("sheet"):
            name = _text(sheet._prop_of(child, "Sheetname"), 1, "Sheet")
            filename = _text(sheet._prop_of(child, "Sheetfile"), 1)
            if not filename:
                continue
            child_path = sheet.path.parent / filename
            tree = loads(child_path.read_text(encoding="utf-8"))
            child_sheet = KiCadSheet(
                child_path, tree, _text(tree.get("paper"), 0, "A4")
            )
            child_page = f"{page.rstrip('/')}/{name}/"
            index(child_sheet, child_page)

    index(self, "/")

    with self._scratch() as scratch:
        data = _kicad.erc(scratch)

    out: list[Finding] = []
    for sheet in data.get("sheets", []):
        page = str(sheet.get("path", "/"))
        for violation in sheet.get("violations", []):
            items = violation.get("items") or [{}]
            for item in items:
                pos = item.get("pos") or {}
                # KiCad reports ERC positions in hundredths of a
                # millimetre's worth of inches -- x100 puts them back in mm.
                at = Point(
                    round(float(pos.get("x", 0.0)) * 100, 3),
                    round(float(pos.get("y", 0.0)) * 100, 3),
                )
                ref, number = where_by_page.get(page, {}).get(
                    (round(at.x, 2), round(at.y, 2)), ("", "")
                )
                out.append(
                    Finding(
                        severity=str(violation.get("severity", "error")),
                        kind=str(violation.get("type", "")),
                        message=str(violation.get("description", "")),
                        ref=ref,
                        pin=number,
                        sheet=page,
                        at=at,
                    )
                )
    return out


def _visible_text(self: KiCadSheet) -> list[_VisibleText]:
    """Fields, labels and notes visible on this page."""
    out: list[_VisibleText] = []
    for node in self._tree.get_all("symbol"):
        if node.get("lib_id") is None:
            continue
        ref_prop = self._prop_of(node, "Reference")
        ref = _text(ref_prop, 1, "?")
        unit = int(_f(node.get("unit"), 0, 1))
        symbol_at = node.get("at")
        symbol_rotation = _f(symbol_at, 2)
        for prop in node.get_all("property"):
            value = _text(prop, 1)
            effects = prop.get("effects")
            if not value or _hidden(effects):
                continue
            field = _text(prop)
            at_node = prop.get("at")
            out.append(
                _VisibleText(
                    kind="field",
                    name=f"{ref}.{unit}.{field} {value!r}",
                    box=_text_box(prop, value, rotation_offset=symbol_rotation),
                    at=Point(_f(at_node, 0), _f(at_node, 1)),
                )
            )

    for kind, node_name in _LABEL_NODE.items():
        for node in self._tree.get_all(node_name):
            effects = node.get("effects")
            if _hidden(effects):
                continue
            value = _text(node)
            at_node = node.get("at")
            uid = _text(node.get("uuid"))[-8:]
            out.append(
                _VisibleText(
                    kind="label",
                    name=f"{kind} label {value!r} [{uid}]",
                    box=_text_box(node),
                    at=Point(_f(at_node, 0), _f(at_node, 1)),
                    attaches=True,
                )
            )

    for node in self._tree.get_all("text"):
        effects = node.get("effects")
        value = _text(node)
        if not value or _hidden(effects):
            continue
        at_node = node.get("at")
        shown = value.replace("\n", " ")
        if len(shown) > 32:
            shown = shown[:29] + "..."
        uid = _text(node.get("uuid"))[-8:]
        out.append(
            _VisibleText(
                kind="text",
                name=f"text {shown!r} [{uid}]",
                box=_text_box(node),
                at=Point(_f(at_node, 0), _f(at_node, 1)),
            )
        )
    return out


def _check_layout_page(self: KiCadSheet, page: str) -> list[LayoutFinding]:
    """Graphical findings for this page, without following child sheets."""
    from .scene import body_findings

    findings = body_findings(self, page)
    texts = self._visible_text()
    wires: list[tuple[Point, Point]] = []
    seen_wires: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for a, b in self.wires():
        end_a, end_b = (a.x, a.y), (b.x, b.y)
        ends = (end_a, end_b) if end_a <= end_b else (end_b, end_a)
        if ends in seen_wires:
            continue
        seen_wires.add(ends)
        wires.append((a, b))

    for item in texts:
        for a, b in wires:
            # A label touching the wire it names is required geometry, not
            # a collision.  The text box still catches every other wire.
            if item.attaches and _on_segment(item.at, a, b):
                continue
            if not _segment_crosses_box(a, b, item.box):
                continue
            wire = f"wire ({a.x:g},{a.y:g})-({b.x:g},{b.y:g})"
            findings.append(
                LayoutFinding(
                    severity="warning",
                    kind=f"{item.kind}_wire_overlap",
                    message=f"{item.name} is crossed by {wire}",
                    first=item.name,
                    second=wire,
                    sheet=page,
                    at=item.box.meeting(
                        _Box(
                            min(a.x, b.x),
                            min(a.y, b.y),
                            max(a.x, b.x),
                            max(a.y, b.y),
                        )
                    ),
                )
            )

    for index, first in enumerate(texts):
        for second in texts[index + 1 :]:
            if not first.box.overlaps(second.box):
                continue
            kinds = "_".join(sorted((first.kind, second.kind)))
            findings.append(
                LayoutFinding(
                    severity="warning",
                    kind=f"{kinds}_overlap",
                    message=f"{first.name} overlaps {second.name}",
                    first=first.name,
                    second=second.name,
                    sheet=page,
                    at=first.box.meeting(second.box),
                )
            )
    return findings


def check_layout(self: KiCadSheet) -> list[LayoutFinding]:
    """Return potential graphical collisions across this hierarchy."""
    from .sheet import KiCadSheet

    findings: list[LayoutFinding] = []
    seen: set[Path] = set()

    def visit(sheet: KiCadSheet, page: str) -> None:
        resolved = sheet.path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        findings.extend(sheet._check_layout_page(page))
        for child in sheet._tree.get_all("sheet"):
            name_prop = sheet._prop_of(child, "Sheetname")
            file_prop = sheet._prop_of(child, "Sheetfile")
            name = _text(name_prop, 1, "Sheet")
            filename = _text(file_prop, 1)
            if not filename:
                continue
            child_path = sheet.path.parent / filename
            tree = loads(child_path.read_text(encoding="utf-8"))
            child_sheet = KiCadSheet(
                child_path, tree, _text(tree.get("paper"), 0, "A4")
            )
            child_page = f"{page.rstrip('/')}/{name}/"
            visit(child_sheet, child_page)

    visit(self, "/")
    return findings
