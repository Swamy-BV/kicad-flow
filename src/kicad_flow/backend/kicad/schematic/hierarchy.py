"""Child sheet creation, ports and hierarchy editing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kicad_flow.schematic.api import snap
from kicad_flow.schematic.types import (
    Pin,
    Point,
    SheetRef,
)

from .._sexpr import Node, Sym
from ._nodes import (
    _f,
    _node,
    _set,
    _text,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def add_sheet(
    self: KiCadSheet,
    name: str,
    filename: str,
    x: float,
    y: float,
    *,
    width: float = 38.1,
    height: float = 25.4,
    ports: tuple[tuple[str, str], ...] = (),
) -> SheetRef:
    """Put a child sheet on this one, and return where its ports landed."""
    at = Point(snap(x), snap(y))
    uid = self._uid_for(f"sheet:{name}:{filename}")
    node = _node(
        "sheet",
        [
            _node("at", [at.x, at.y]),
            _node("size", [snap(width), snap(height)]),
            _node("stroke", [_node("width", [0]), _node("type", [Sym("solid")])]),
            _node("fill", [_node("color", [0, 0, 0, 0])]),
            _node("uuid", [uid]),
            self._property("Sheetname", name, at.x, at.y - 1.27),
            self._property("Sheetfile", filename, at.x, at.y + snap(height) + 1.27),
        ],
    )
    pins = []
    for index, (port, kind) in enumerate(ports):
        py = at.y + snap(2.54 + index * 2.54)
        node.items.append(
            _node(
                "pin",
                [
                    port,
                    Sym(kind),
                    _node("at", [at.x, py, 180]),
                    _node(
                        "effects",
                        [
                            _node("font", [_node("size", [1.27, 1.27])]),
                            _node("justify", [Sym("right")]),
                        ],
                    ),
                    _node("uuid", [self._uid_for(f"port:{name}:{port}")]),
                ],
            )
        )
        pins.append(
            Pin(
                number=port,
                name=port,
                at=Point(at.x, py),
                orientation=180.0,
                kind=kind,
                length=0.0,
            )
        )
    # A page number per sheet, so the design has an order.
    table = self._tree.get("sheet_instances")
    page = len(table.get_all("path")) + 1 if table is not None else 2
    node.items.append(
        _node(
            "instances",
            [
                _node(
                    "project",
                    [
                        "",
                        _node(
                            "path",
                            [
                                self._where,
                                _node("page", [str(page)]),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    if table is not None:
        table.items.append(
            _node(
                "path",
                [
                    "/" + uid,
                    _node("page", [str(page)]),
                ],
            )
        )
    self._tree.items.append(node)
    return SheetRef(
        name=name,
        filename=filename,
        at=at,
        size=(snap(width), snap(height)),
        uuid=uid,
        instance_path=f"{self._where}/{uid}",
        pins=tuple(pins),
    )


def _sheet_node(self: KiCadSheet, name: str) -> Node:
    """The child-sheet box called *name*."""
    for node in self._tree.get_all("sheet"):
        if _text(self._prop_of(node, "Sheetname"), 1) == name:
            return node
    have = (
        ", ".join(
            _text(self._prop_of(n, "Sheetname"), 1) for n in self._tree.get_all("sheet")
        )
        or "none"
    )
    raise LookupError(f"no child sheet named {name!r}; this sheet has {have}")


def _sheet_ref(self: KiCadSheet, node: Node) -> SheetRef:
    """Describe a child-sheet box that is already on the sheet."""
    at = node.get("at")
    size = node.get("size")
    pins = tuple(
        Pin(
            number=_text(p),
            name=_text(p),
            orientation=180.0,
            kind=_text(p.get("pin")) or "passive",
            length=0.0,
            at=Point(_f(p.get("at"), 0), _f(p.get("at"), 1)),
        )
        for p in node.get_all("pin")
    )
    uid = _text(node.get("uuid"))
    return SheetRef(
        name=_text(self._prop_of(node, "Sheetname"), 1),
        filename=_text(self._prop_of(node, "Sheetfile"), 1),
        at=Point(_f(at, 0), _f(at, 1)),
        size=(_f(size, 0), _f(size, 1)),
        uuid=uid,
        instance_path=f"{self._where}/{uid}",
        pins=pins,
    )


def move_sheet(self: KiCadSheet, name: str, x: float, y: float) -> SheetRef:
    """Move a child-sheet box, and say where its ports ended up."""
    node = self._sheet_node(name)
    at = node.get("at")
    if at is None:
        raise LookupError(f"child sheet {name!r} has no position")
    was = Point(_f(at, 0), _f(at, 1))
    now = Point(snap(x), snap(y))
    dx, dy = now.x - was.x, now.y - was.y
    _set(at, 0, now.x)
    _set(at, 1, now.y)
    # The box's own text and every port move with it: a port is placed on
    # the box edge, so leaving them behind detaches the page's interface.
    for child in list(node.get_all("property")) + list(node.get_all("pin")):
        pat = child.get("at")
        if pat is not None:
            _set(pat, 0, snap(_f(pat, 0) + dx))
            _set(pat, 1, snap(_f(pat, 1) + dy))
    return self._sheet_ref(node)


def remove_sheet(self: KiCadSheet, name: str) -> None:
    """Take a child-sheet box off this sheet. The child FILE is left."""
    self._tree.items.remove(self._sheet_node(name))
