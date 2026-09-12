"""Placed symbols, instance bookkeeping and component transforms."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from kicad_flow.schematic.api import snap
from kicad_flow.schematic.types import (
    Part,
    Pin,
    Point,
)

from .. import _library as library
from .._sexpr import Node, Sym
from ._geometry import (
    _pin_on_sheet,
    _quarter_turn,
)
from ._nodes import (
    _f,
    _node,
    _set,
    _text,
)
from .symbols import (
    _symbol_pins,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def place(
    self: KiCadSheet,
    lib_id: str,
    ref: str,
    x: float,
    y: float,
    *,
    value: str = "",
    rotation: float = 0.0,
    mirror: str = "",
    unit: int = 1,
) -> Part:
    """Put one unit of *lib_id* on the sheet at ``(x, y)`` as *ref*."""
    rotation = _quarter_turn(rotation)
    if self._find(ref, unit) is not None:
        raise ValueError(f"{ref} unit {unit} is already on the sheet")
    sym = self._load(lib_id)
    self._ensure_lib_symbol(lib_id, sym)
    at = Point(snap(x), snap(y))
    node = _node(
        "symbol",
        [
            _node("lib_id", [lib_id]),
            _node("at", [at.x, at.y, rotation]),
            _node("unit", [unit]),
            _node("exclude_from_sim", [Sym("no")]),
            _node("in_bom", [Sym("yes")]),
            _node("on_board", [Sym("yes")]),
            _node("dnp", [Sym("no")]),
            _node("uuid", [self._uid_for(f"symbol:{ref}:{unit}")]),
        ],
    )
    if mirror in ("x", "y"):
        node.items.insert(3, _node("mirror", [Sym(mirror)]))
    node.items.append(
        self._property("Reference", ref, at.x, at.y, hide=ref.startswith("#"))
    )
    node.items.append(self._property("Value", value or sym.default_value, at.x, at.y))
    node.items.append(self._property("Footprint", "", at.x, at.y, hide=True))
    self._layout_fields(node, lib_id, rotation, mirror, unit)
    for pin in _symbol_pins(sym.definition, unit):
        node.items.append(
            _node(
                "pin",
                [pin[0], _node("uuid", [self._uid_for(f"pin:{ref}:{unit}:{pin[0]}")])],
            )
        )
    # Without this, KiCad does not consider the symbol annotated, and a
    # wire between two of its pins connects nothing -- see _instances.
    node.items.append(self._instances(ref, unit))
    self._tree.items.append(node)
    # The unit that was just placed, not unit 1: `part` defaults, and
    # returning the default here handed back another unit's pins.
    return self.part(ref, unit=unit)


def _instances(self: KiCadSheet, ref: str, unit: int = 1) -> Node:
    """The ``(instances ...)`` block tying a symbol to this sheet.

    KiCad 6 and later record a placed symbol's reference here as well as
    in its Reference property, and it is THIS that annotation reads. A
    symbol without it is unannotated: its pins do not join the nets they
    sit on, so a wire drawn between two pins connects nothing, ERC calls
    that wire dangling, and the net never reaches the netlist.

    It was invisible for a while because every net that had a label or a
    power symbol on it still appeared -- those carry their own identity.
    Only plain pin-to-pin wires vanished.
    """
    return _node(
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
                            _node("reference", [ref]),
                            _node("unit", [unit]),
                        ],
                    ),
                ],
            ),
        ],
    )


def _ensure_lib_symbol(
    self: KiCadSheet, lib_id: str, sym: library.LibrarySymbol
) -> None:
    """Copy a symbol's definition into the sheet's ``lib_symbols``."""
    table = self._tree.get("lib_symbols")
    if table is None:
        table = _node("lib_symbols", [])
        self._tree.items.insert(4, table)
    for existing in table.get_all("symbol"):
        if _text(existing) == lib_id:
            return
    # A copy: the loader shares its definitions between callers.
    table.items.append(copy.deepcopy(sym.definition))


def part(self: KiCadSheet, ref: str, *, unit: int = 1) -> Part:
    """One placed unit, with its pins at sheet positions."""
    return self._as_part(self._require(ref, unit), ref)


def _as_part(self: KiCadSheet, node: Node, ref: str) -> Part:
    """Build a :class:`Part` from a placed ``(symbol ...)`` node."""
    lib_id = _text(node.get("lib_id"))
    at_node = node.get("at")
    at = Point(_f(at_node, 0), _f(at_node, 1))
    rotation = _f(at_node, 2)
    mirror = _text(node.get("mirror")) if node.get("mirror") else ""
    unit = int(_f(node.get("unit"), 0, 1))
    value_prop = self._prop_of(node, "Value")
    pins = []
    for number, name, px, py, pang, kind, length in _symbol_pins(
        self._load(lib_id).definition, unit
    ):
        point, angle = _pin_on_sheet(px, py, pang, at, rotation, mirror)
        pins.append(
            Pin(
                number=number,
                name=name,
                at=point,
                orientation=angle,
                kind=kind,
                length=length,
            )
        )
    return Part(
        ref=ref,
        lib_id=lib_id,
        value=_text(value_prop, 1) if value_prop else "",
        at=at,
        rotation=rotation,
        mirror=mirror,
        unit=unit,
        pins=tuple(pins),
        uuid=_text(node.get("uuid")),
    )


def parts(self: KiCadSheet) -> list[Part]:
    """Every placed part, in reference order."""
    out = []
    for node in self._tree.get_all("symbol"):
        if node.get("lib_id") is None:
            continue
        prop = self._prop_of(node, "Reference")
        if prop is not None:
            out.append(self._as_part(node, _text(prop, 1)))
    return sorted(out, key=lambda p: p.ref)


def move(self: KiCadSheet, ref: str, x: float, y: float, *, unit: int = 1) -> Part:
    """Move a placed part. Its pins move with it."""
    node = self._require(ref, unit)
    at = node.get("at")
    if at is None:
        raise LookupError(f"{ref} has no position")
    dx, dy = snap(x) - _f(at, 0), snap(y) - _f(at, 1)
    _set(at, 0, snap(x))
    _set(at, 1, snap(y))
    for prop in node.get_all("property"):  # the fields ride along
        pat = prop.get("at")
        if pat is not None:
            _set(pat, 0, round(_f(pat, 0) + dx, 3))
            _set(pat, 1, round(_f(pat, 1) + dy, 3))
    return self.part(ref, unit=unit)


def rotate(self: KiCadSheet, ref: str, rotation: float, *, unit: int = 1) -> Part:
    """Set a placed part's rotation in degrees."""
    rotation = _quarter_turn(rotation)
    node = self._require(ref, unit)
    at = node.get("at")
    if at is None:
        raise LookupError(f"{ref} has no position")
    _set(at, 2, rotation)
    # The fields have to follow: a part turned on its side with its label
    # left where it was reads sideways and sits in the wrong place.
    self._layout_fields(
        node, _text(node.get("lib_id")), rotation, _text(node.get("mirror")), unit
    )
    return self.part(ref, unit=unit)


def mirror(self: KiCadSheet, ref: str, axis: str, *, unit: int = 1) -> Part:
    """Mirror a placed part about ``"x"``, ``"y"`` or ``""`` for neither."""
    if axis not in ("", "x", "y"):
        raise ValueError(f"mirror axis must be '', 'x' or 'y', not {axis!r}")
    node = self._require(ref, unit)
    existing = node.get("mirror")
    if existing is not None:
        node.items.remove(existing)
    if axis:
        node.items.insert(3, _node("mirror", [Sym(axis)]))
    return self.part(ref, unit=unit)


def remove(self: KiCadSheet, ref: str, *, unit: int = 1) -> None:
    """Take one placed unit off the sheet."""
    self._tree.items.remove(self._require(ref, unit))


def pin(self: KiCadSheet, ref: str, pin: str) -> Point:
    """Where *ref*'s *pin* is on the sheet -- the point to wire to."""
    part = self.part(ref)
    found = part.pin(pin)
    if found is None:
        have = ", ".join(p.number for p in part.pins)
        raise LookupError(f"{ref} has no pin {pin!r}; it has {have}")
    return found.at


def power(
    self: KiCadSheet, x: float, y: float, net: str, *, rotation: float = 0.0
) -> Part:
    """Place a power symbol for *net* and return it."""
    return self.place(
        f"power:{net}", self._next_hash_ref("#PWR"), x, y, value=net, rotation=rotation
    )


def power_flag(self: KiCadSheet, x: float, y: float, *, rotation: float = 0.0) -> Part:
    """Place a PWR_FLAG, which tells ERC a net is driven."""
    return self.place(
        "power:PWR_FLAG",
        self._next_hash_ref("#FLG"),
        x,
        y,
        value="PWR_FLAG",
        rotation=rotation,
    )


def _next_hash_ref(self: KiCadSheet, prefix: str) -> str:
    """The next free ``#PWR0001``-style reference."""
    used = {p.ref for p in self.parts() if p.ref.startswith(prefix)}
    n = 1
    while f"{prefix}{n:04d}" in used:
        n += 1
    return f"{prefix}{n:04d}"
