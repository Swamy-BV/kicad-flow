"""Schematic component field construction, placement and editing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kicad_flow.schematic.types import (
    Point,
)

from .._sexpr import Node, Sym
from ._geometry import (
    _field_angle,
    _pin_on_sheet,
)
from ._nodes import (
    _f,
    _node,
    _set,
    _text,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def _property(
    self: KiCadSheet,
    name: str,
    value: str,
    x: float,
    y: float,
    *,
    hide: bool = False,
    justify: str = "",
) -> Node:
    """One ``(property ...)`` node on a placed symbol."""
    effects: list[Any] = [_node("font", [_node("size", [1.27, 1.27])])]
    if justify:
        effects.append(_node("justify", [Sym(justify)]))
    if hide:
        effects.append(_node("hide", [Sym("yes")]))
    return _node(
        "property",
        [
            name,
            value,
            _node("at", [round(x, 3), round(y, 3), 0]),
            _node("effects", effects),
        ],
    )


def _layout_fields(
    self: KiCadSheet,
    node: Node,
    lib_id: str,
    rotation: float,
    mirror: str = "",
    unit: int = 1,
) -> None:
    """Put a part's Reference and Value where no wire is going to be.

    Which side depends on how the part is oriented, because that is where
    its wires leave from:

    * **Pins leaving top and bottom** -- a capacitor, a resistor standing
      up -- get their labels stacked to the RIGHT. Above and below is
      exactly where the wires run, and a reference 1.27 mm over a
      capacitor sits on the wire climbing to the rail.
    * **Pins leaving left and right** -- a regulator, a fuse lying down --
      get Reference above and Value below, which is clear for the same
      reason.

    This is what KiCad itself does, and for the same reason. A caller who
    wants otherwise moves the field: see :meth:`move_field`.
    """
    at = node.get("at")
    if at is None:
        return
    cx, cy = _f(at, 0), _f(at, 1)
    left, bottom, right, top = self.symbol(lib_id, unit=unit).bounds
    # Take the box's four corners through the same transform the pins get,
    # so the answer is where the part actually sits on the sheet. A symbol
    # is NOT centred on its origin -- a USB-C receptacle's body hangs
    # 22.86 mm below it, and treating it as centred put the value label
    # exactly on the ground pin.
    here = Point(cx, cy)
    corners = [
        _pin_on_sheet(x, y, 0.0, here, rotation, mirror)[0]
        for x in (left, right)
        for y in (bottom, top)
    ]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    angle = _field_angle(rotation)

    # Which way the wires will leave, from the pins themselves rather than
    # from the rotation: a symbol can be drawn either way round, and it is
    # the pin directions that decide where a wire goes.
    pins = self._as_part(node, "?").pins
    upright = sum(1 for p in pins if p.orientation % 180 == 90)
    if pins and upright * 2 >= len(pins):
        places = (
            ("Reference", max(xs) - cx + 1.27, -1.27),
            ("Value", max(xs) - cx + 1.27, 1.27),
        )
        justify = "left"
    else:
        places = (
            ("Reference", 0.0, min(ys) - cy - 2.54),
            ("Value", 0.0, max(ys) - cy + 2.54),
        )
        justify = ""

    for name, dx, dy in places:
        prop = self._prop_of(node, name)
        if prop is None:
            continue
        pat = prop.get("at")
        if pat is None:
            continue
        _set(pat, 0, round(cx + dx, 3))
        _set(pat, 1, round(cy + dy, 3))
        _set(pat, 2, angle)
        self._justify(prop, justify)


def _justify(prop: Node, justify: str) -> None:
    """Set or clear a field's justification, leaving the rest alone."""
    effects = prop.get("effects")
    if effects is None:
        return
    existing = effects.get("justify")
    if existing is not None:
        effects.items.remove(existing)
    if justify:
        effects.items.append(_node("justify", [Sym(justify)]))


def remove_field(
    self: KiCadSheet, ref: str, name: str, *, unit: int = 1
) -> dict[str, str]:
    """Delete a field from a part and return the fields it has left."""
    node = self._require(ref, unit)
    prop = self._prop_of(node, name)
    if prop is None:
        raise LookupError(f"{ref} has no field {name!r}")
    node.items.remove(prop)
    return self.fields(ref)


def set_field(self: KiCadSheet, ref: str, name: str, value: str) -> dict[str, str]:
    """Set one of a part's fields and return all of them."""
    node = self._require(ref)
    prop = self._prop_of(node, name)
    if prop is None:
        at = node.get("at")
        node.items.append(self._property(name, value, _f(at, 0), _f(at, 1), hide=True))
    else:
        prop.items[2] = value
    return self.fields(ref)


def move_field(
    self: KiCadSheet,
    ref: str,
    name: str,
    dx: float,
    dy: float,
    *,
    rotation: float | None = None,
    justify: str = "",
) -> Point:
    """Move a field relative to its part, and return where it landed."""
    node = self._require(ref)
    prop = self._prop_of(node, name)
    if prop is None:
        have = ", ".join(self.fields(ref))
        raise LookupError(f"{ref} has no field {name!r}; it has {have}")
    at = node.get("at")
    pat = prop.get("at")
    if at is None or pat is None:
        raise LookupError(f"{ref}.{name} has no position")
    where = Point(round(_f(at, 0) + dx, 3), round(_f(at, 1) + dy, 3))
    _set(pat, 0, where.x)
    _set(pat, 1, where.y)
    if rotation is not None:
        _set(pat, 2, rotation % 360.0)
    self._justify(prop, justify)
    return where


def fields(self: KiCadSheet, ref: str) -> dict[str, str]:
    """Every field on a part, by name."""
    node = self._require(ref)
    return {_text(prop, 0): _text(prop, 1) for prop in node.get_all("property")}
