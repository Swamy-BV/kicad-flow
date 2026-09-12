"""KiCad board node access, identifiers and stored transforms."""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from .._sexpr import Node, Sym


def _uid() -> str:
    """A fresh UUID, as KiCad writes them."""
    return str(_uuid.uuid4())


def _refresh_uuids(node: Node) -> None:
    """Give every object in a copied library tree its own identity.

    Library footprints carry stable UUIDs for their pads and graphics. Those
    IDs identify the library definition; multiple placed instances cannot
    reuse them. KiCad otherwise reports a real violation against an arbitrary
    sibling instance sharing the child UUID, so its reference and coordinates
    disagree with the geometry that actually failed.
    """
    for item in node.items:
        if not isinstance(item, Node):
            continue
        if item.name in {"uuid", "tstamp"}:
            item.items[1:] = [_uid()]
        else:
            _refresh_uuids(item)


def _fmt(value: float) -> str:
    """A number as KiCad writes it: no trailing zeros, no exponent."""
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _toggle_mirror(shape: Node) -> None:
    """Add or remove ``(justify mirror)`` on a text node's effects.

    Text on a back layer has to carry this flag or it reads backwards on the
    finished board -- and nothing says so: the file parses, KiCad opens it, and
    the fab plots silkscreen you cannot read. Measured on a bare board with
    four 0402 resistors placed ``side="B"``: without this, DRC reported 12
    ``nonmirrored_text_on_back_layer`` warnings, three per part; with it, none.

    Toggled rather than set, because :meth:`KiCadBoard._mirror` runs on the way
    back to the front as well.
    """
    effects = shape.get("effects")
    if effects is None:
        effects = _node("effects", [_node("justify", [Sym("mirror")])])
        shape.items.append(effects)
        return
    justify = effects.get("justify")
    if justify is None:
        effects.items.append(_node("justify", [Sym("mirror")]))
        return
    if any(str(x) == "mirror" for x in justify.items[1:]):
        justify.items = [justify.items[0]] + [
            x for x in justify.items[1:] if str(x) != "mirror"
        ]
        if len(justify.items) == 1:  # nothing left to say
            effects.items.remove(justify)
    else:
        justify.items.append(Sym("mirror"))


def _node(name: str, atoms: list[Any] | None = None) -> Node:
    """``(name atom ...)``, with the quoting KiCad expects."""
    items: list[Node | Sym | str] = [Sym(name)]
    for atom in atoms or []:
        if isinstance(atom, bool):
            items.append(Sym("yes" if atom else "no"))
        elif isinstance(atom, (int, float)):
            items.append(Sym(_fmt(float(atom))))
        elif isinstance(atom, Node):
            items.append(atom)
        else:
            items.append(atom)
    return Node(items)


def _atom(node: Node | None, index: int) -> str | None:
    """The *index*-th atom AFTER the node name, as text."""
    if node is None or len(node.items) <= index + 1:
        return None
    return str(node.items[index + 1])


def _f(node: Node | None, index: int, default: float = 0.0) -> float:
    """Float value at *index*, or *default*."""
    raw = _atom(node, index)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _text(node: Node | None, index: int = 0, default: str = "") -> str:
    """String value at *index*, or *default*."""
    raw = _atom(node, index)
    return default if raw is None else raw


def _set(node: Node, index: int, value: Any) -> None:
    """Write the *index*-th atom after the node name."""
    while len(node.items) <= index + 1:
        node.items.append(Sym("0"))
    node.items[index + 1] = (
        Sym(_fmt(float(value))) if isinstance(value, (int, float)) else value
    )


def _net_name(node: Node | None) -> str:
    """The net a ``(net ...)`` names, in either the new or the old form.

    KiCad 10 writes ``(net "GND")``. Before that it was ``(net 3 "GND")``,
    an index and a name, and a board written by an older KiCad still reads
    that way -- so take the LAST atom, which is the name either way.
    """
    if node is None or len(node.items) < 2:
        return ""
    return str(node.items[-1])


def _turn_pads(node: Node, by: float) -> None:
    """Add *by* degrees to every pad's own ``at`` angle.

    KiCad rotates a pad's POSITION from the footprint's angle but takes the
    pad's SHAPE orientation from the pad's own ``(at x y angle)``. Leave the
    angle off and a turned part keeps axis-aligned pads: measured on a
    SOT-23-6 at 90 degrees, its own pads overlapped at 0.0000 mm and DRC
    returned 4 clearance and 4 solder-mask errors, all of the part against
    itself. Writing the angle cleared all eight.

    Added rather than set, because a footprint may already give a pad an angle
    of its own, and that is relative to the footprint.
    """
    if not by % 360.0:
        return
    for pad in node.get_all("pad"):
        at = pad.get("at")
        if at is None:
            continue
        if len(at.items) > 3:
            _set(at, 2, (_f(at, 2) + by) % 360.0)
        else:
            at.items.append(Sym(_fmt(by % 360.0)))


def _flip_layer(name: str) -> str:
    """The same layer on the other side of the board."""
    if name.startswith("F."):
        return "B." + name[2:]
    if name.startswith("B."):
        return "F." + name[2:]
    return name
