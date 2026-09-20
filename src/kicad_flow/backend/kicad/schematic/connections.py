"""Schematic wires, labels, junctions, no-connects and drawn text."""

from __future__ import annotations

from kicad_flow.schematic.api import snap
from kicad_flow.schematic.types import (
    Label,
    Point,
    SheetText,
)

from .._sexpr import Node, Sym
from ._geometry import (
    _quarter_turn,
)
from ._nodes import (
    _LABEL_NODE,
    _f,
    _fmt,
    _node,
    _set,
    _text,
)
from ._state import SheetState


def _at_point(
    self: SheetState, kinds: tuple[str, ...], x: float, y: float
) -> list[Node]:
    """Every node of these kinds whose ``at`` is this snapped point."""
    # Coordinates returned through JSON carry KiCad's three decimal
    # places. A grid multiplication can retain a binary-float tail in
    # memory, so identity comparisons must use that same precision.
    want = (round(snap(x), 3), round(snap(y), 3))
    found = []
    for kind in kinds:
        for node in self._tree.get_all(kind):
            at = node.get("at")
            stored = (round(_f(at, 0), 3), round(_f(at, 1), 3))
            if at is not None and stored == want:
                found.append(node)
    return found


def _wires_between(
    self: SheetState, x1: float, y1: float, x2: float, y2: float
) -> list[Node]:
    """Wire nodes joining these two snapped points, either way round."""
    a = (round(snap(x1), 3), round(snap(y1), 3))
    b = (round(snap(x2), 3), round(snap(y2), 3))
    found = []
    for node in self._tree.get_all("wire"):
        pts = node.get("pts")
        xy = pts.get_all("xy") if pts is not None else []
        if len(xy) < 2:
            continue
        ends = (
            (round(_f(xy[0], 0), 3), round(_f(xy[0], 1), 3)),
            (round(_f(xy[1], 0), 3), round(_f(xy[1], 1), 3)),
        )
        if ends == (a, b) or ends == (b, a):
            found.append(node)
    return found


def remove_wire(self: SheetState, x1: float, y1: float, x2: float, y2: float) -> int:
    """Delete wires running between these two points."""
    found = self._wires_between(x1, y1, x2, y2)
    for node in found:
        self._tree.items.remove(node)
    return len(found)


def move_wire(
    self: SheetState, x1: float, y1: float, x2: float, y2: float, dx: float, dy: float
) -> int:
    """Shift wires between these points by ``(dx, dy)``."""
    found = self._wires_between(x1, y1, x2, y2)
    for node in found:
        pts = node.get("pts")
        for xy in pts.get_all("xy") if pts is not None else []:
            _set(xy, 0, snap(_f(xy, 0) + dx))
            _set(xy, 1, snap(_f(xy, 1) + dy))
    return len(found)


def remove_label(self: SheetState, x: float, y: float) -> int:
    """Delete labels at this point, of any kind."""
    found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
    for node in found:
        self._tree.items.remove(node)
    return len(found)


def _label_from_node(self: SheetState, node: Node) -> Label:
    """Describe one stored label without exposing its S-expression."""
    at = node.get("at")
    effects = node.get("effects")
    just = effects.get("justify") if effects is not None else None
    kind = next(
        (kind for kind, name in _LABEL_NODE.items() if name == node.name), "local"
    )
    return Label(
        uuid=_text(node.get("uuid")),
        text=_text(node),
        kind=kind,
        at=Point(_f(at, 0), _f(at, 1)),
        rotation=_f(at, 2),
        justify=_text(just) if just is not None else "",
    )


def _label_node(self: SheetState, uuid: str) -> Node:
    """The label carrying *uuid*, or a useful refusal."""
    for name in _LABEL_NODE.values():
        for node in self._tree.get_all(name):
            if _text(node.get("uuid")) == uuid:
                return node
    raise LookupError(f"no label with uuid {uuid!r}")


def remove_label_by_id(self: SheetState, uuid: str) -> None:
    """Delete exactly one label by stable identity."""
    self._tree.items.remove(self._label_node(uuid))


def move_label(self: SheetState, x: float, y: float, dx: float, dy: float) -> int:
    """Shift labels at this point by ``(dx, dy)``."""
    found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
    for node in found:
        at = node.get("at")
        if at is None:  # _at_point cannot return one
            continue
        _set(at, 0, snap(_f(at, 0) + dx))
        _set(at, 1, snap(_f(at, 1) + dy))
    return len(found)


def move_label_by_id(self: SheetState, uuid: str, dx: float, dy: float) -> Label:
    """Shift exactly one label by stable identity."""
    node = self._label_node(uuid)
    at = node.get("at")
    if at is None:
        raise LookupError(f"label {uuid!r} has no position")
    _set(at, 0, snap(_f(at, 0) + dx))
    _set(at, 1, snap(_f(at, 1) + dy))
    return self._label_from_node(node)


def rotate_label(self: SheetState, x: float, y: float, rotation: float) -> int:
    """Turn labels at this point."""
    turn = _quarter_turn(rotation)
    found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
    for node in found:
        at = node.get("at")
        if at is None:  # _at_point cannot return one
            continue
        if len(at.items) > 3:
            _set(at, 2, turn)
        else:
            at.items.append(Sym(_fmt(turn)))
    return len(found)


def rotate_label_by_id(self: SheetState, uuid: str, rotation: float) -> Label:
    """Turn exactly one label by stable identity."""
    node = self._label_node(uuid)
    at = node.get("at")
    if at is None:
        raise LookupError(f"label {uuid!r} has no position")
    turn = _quarter_turn(rotation)
    if len(at.items) > 3:
        _set(at, 2, turn)
    else:
        at.items.append(Sym(_fmt(turn)))
    return self._label_from_node(node)


def remove_junction(self: SheetState, x: float, y: float) -> int:
    """Delete junctions at this point."""
    found = self._at_point(("junction",), x, y)
    for node in found:
        self._tree.items.remove(node)
    return len(found)


def remove_no_connect(self: SheetState, x: float, y: float) -> int:
    """Delete no-connect marks at this point."""
    found = self._at_point(("no_connect",), x, y)
    for node in found:
        self._tree.items.remove(node)
    return len(found)


def wire(self: SheetState, x1: float, y1: float, x2: float, y2: float) -> list[Point]:
    """Draw one straight wire segment and return its ends."""
    a, b = Point(snap(x1), snap(y1)), Point(snap(x2), snap(y2))
    self._tree.items.append(
        _node(
            "wire",
            [
                _node("pts", [_node("xy", [a.x, a.y]), _node("xy", [b.x, b.y])]),
                _node("stroke", [_node("width", [0]), _node("type", [Sym("default")])]),
                _node("uuid", [self._uid_for(f"wire:{a.x},{a.y}:{b.x},{b.y}")]),
            ],
        )
    )
    return [a, b]


def junction(self: SheetState, x: float, y: float) -> Point:
    """Mark a point where crossing wires connect."""
    at = Point(snap(x), snap(y))
    self._tree.items.append(
        _node(
            "junction",
            [
                _node("at", [at.x, at.y]),
                _node("diameter", [0]),
                _node("color", [0, 0, 0, 0]),
                _node("uuid", [self._uid_for(f"junction:{at.x},{at.y}")]),
            ],
        )
    )
    return at


def label(
    self: SheetState,
    x: float,
    y: float,
    text: str,
    *,
    kind: str = "local",
    rotation: float = 0.0,
    justify: str = "left",
) -> Label:
    """Attach a net name at a point."""
    if kind not in _LABEL_NODE:
        raise ValueError(f"label kind must be one of {list(_LABEL_NODE)}")
    if justify not in ("left", "right"):
        raise ValueError(f"justify must be 'left' or 'right', not {justify!r}")
    at = Point(snap(x), snap(y))
    # A LOCAL label is text sitting on a wire, so its baseline goes at the
    # anchor and it reads above: `bottom` as well as a side.
    #
    # A GLOBAL or HIERARCHICAL label is a flag drawn AROUND its text, and
    # the side is what points it: `right` puts the tip on the right and
    # grows the box leftward. `bottom` on one of these pins the baseline
    # to the anchor instead of centring the text in the flag, which is why
    # every one of them was drawn with its name riding out of the top of
    # its own box.
    sides = [Sym(justify)]
    if kind == "local":
        sides.append(Sym("bottom"))
    uid = self._uid_for(f"label:{kind}:{text}:{at.x},{at.y}")
    node = _node(
        _LABEL_NODE[kind],
        [
            text,
            _node("at", [at.x, at.y, rotation % 360.0]),
            _node(
                "effects",
                [
                    _node("font", [_node("size", [1.27, 1.27])]),
                    _node("justify", sides),
                ],
            ),
            _node("uuid", [uid]),
        ],
    )
    if kind != "local":
        # After the text, not before it: items[0] is the node's own name,
        # so index 1 is the label's text. Putting the shape there gives
        # `(hierarchical_label (shape input) "VBUS" ...)`, which KiCad
        # parses without complaint and then does not match to a sheet pin.
        node.items.insert(2, _node("shape", [Sym("input")]))
    self._tree.items.append(node)
    return self._label_from_node(node)


def text(
    self: SheetState,
    x: float,
    y: float,
    text: str,
    *,
    size: float = 1.27,
    rotation: float = 0.0,
    bold: bool = False,
    justify: str = "left",
) -> SheetText:
    """Write a note on the sheet. It connects nothing and ERC ignores it."""
    if size <= 0:
        raise ValueError("text size must be positive")
    if justify not in {"left", "center", "right"}:
        raise ValueError("text justify must be 'left', 'center' or 'right'")
    at = Point(snap(x), snap(y))
    font = [_node("size", [size, size]), _node("thickness", [size * 0.2])]
    if bold:
        font.append(_node("bold", [Sym("yes")]))
    sides = [Sym("bottom")]
    if justify != "center":
        sides.insert(0, Sym(justify))
    node = _node(
        "text",
        [
            text,
            _node("exclude_from_sim", [Sym("no")]),
            _node("at", [at.x, at.y, rotation % 360.0]),
            _node(
                "effects",
                [
                    _node("font", font),
                    # `bottom` pins the baseline to the anchor. Without it KiCad
                    # centres the block on the point, so a multi-line note grows
                    # upward off the page instead of downward from where it was put.
                    _node("justify", sides),
                ],
            ),
            _node("uuid", [self._uid_for(f"text:{text}:{at.x},{at.y}")]),
        ],
    )
    self._tree.items.append(node)
    return _text_from_node(node)


def _text_from_node(node: Node) -> SheetText:
    """Describe one root-level note without exposing its S-expression."""
    at = node.get("at")
    effects = node.get("effects")
    font = effects.get("font") if effects is not None else None
    size = font.get("size") if font is not None else None
    justify_node = effects.get("justify") if effects is not None else None
    sides = {
        _text(justify_node, index)
        for index in range(max(0, len(justify_node.items) - 1))
    } if justify_node is not None else set()
    justify = next((side for side in ("left", "right") if side in sides), "center")
    return SheetText(
        uuid=_text(node.get("uuid")),
        text=_text(node),
        at=Point(_f(at, 0), _f(at, 1)),
        size=_f(size, 0, 1.27),
        rotation=_f(at, 2),
        bold=font is not None and font.get("bold") is not None,
        justify=justify,
    )


def texts(self: SheetState) -> list[SheetText]:
    """Every root-level note, in file order."""
    return [
        _text_from_node(item)
        for item in self._tree.items
        if isinstance(item, Node) and item.name == "text"
    ]


def _text_node(self: SheetState, uuid: str) -> Node:
    """The root note carrying *uuid*, or a useful refusal."""
    for item in self._tree.items:
        if (
            isinstance(item, Node)
            and item.name == "text"
            and _text(item.get("uuid")) == uuid
        ):
            return item
    raise LookupError(f"no schematic text with uuid {uuid!r}")


def update_text(
    self: SheetState,
    uuid: str,
    *,
    x: float | None = None,
    y: float | None = None,
    text: str | None = None,
    size: float | None = None,
    rotation: float | None = None,
    bold: bool | None = None,
    justify: str | None = None,
) -> SheetText:
    """Update explicit properties of one note."""
    if size is not None and size <= 0:
        raise ValueError("text size must be positive")
    if justify is not None and justify not in {"left", "center", "right"}:
        raise ValueError("text justify must be 'left', 'center' or 'right'")
    node = _text_node(self, uuid)
    if text is not None:
        node.items[1] = text
    at = node.get("at")
    if at is None:
        raise LookupError(f"schematic text {uuid!r} has no position")
    if x is not None:
        _set(at, 0, snap(x))
    if y is not None:
        _set(at, 1, snap(y))
    if rotation is not None:
        _set(at, 2, rotation % 360.0)
    effects = node.get("effects")
    font = effects.get("font") if effects is not None else None
    if effects is None or font is None:
        raise LookupError(f"schematic text {uuid!r} has no font effects")
    if size is not None:
        size_node = font.get("size")
        if size_node is None:
            raise LookupError(f"schematic text {uuid!r} has no font size")
        _set(size_node, 0, size)
        _set(size_node, 1, size)
        thickness = font.get("thickness")
        if thickness is None:
            font.items.append(_node("thickness", [size * 0.2]))
        else:
            _set(thickness, 0, size * 0.2)
    if bold is not None:
        font.items = [
            item
            for item in font.items
            if not (isinstance(item, Node) and item.name == "bold")
        ]
        if bold:
            font.items.append(_node("bold", [Sym("yes")]))
    if justify is not None:
        current = effects.get("justify")
        sides = [Sym("bottom")]
        if justify != "center":
            sides.insert(0, Sym(justify))
        replacement = _node("justify", sides)
        if current is None:
            effects.items.append(replacement)
        else:
            effects.items[effects.items.index(current)] = replacement
    return _text_from_node(node)


def remove_text(self: SheetState, uuid: str) -> None:
    """Remove one root-level note by UUID."""
    self._tree.items.remove(_text_node(self, uuid))


def no_connect(self: SheetState, x: float, y: float) -> Point:
    """Mark a pin deliberately unconnected."""
    at = Point(snap(x), snap(y))
    self._tree.items.append(
        _node(
            "no_connect",
            [
                _node("at", [at.x, at.y]),
                _node("uuid", [self._uid_for(f"noconnect:{at.x},{at.y}")]),
            ],
        )
    )
    return at


def wires(self: SheetState) -> list[tuple[Point, Point]]:
    """Every wire segment on the sheet."""
    out = []
    for node in self._tree.get_all("wire"):
        pts = node.get("pts")
        if pts is None:
            continue
        xy = pts.get_all("xy")
        if len(xy) >= 2:
            out.append(
                (Point(_f(xy[0], 0), _f(xy[0], 1)), Point(_f(xy[1], 0), _f(xy[1], 1)))
            )
    return out


def labels(self: SheetState) -> list[Label]:
    """Every label, including stable identity for later editing."""
    out: list[Label] = []
    for name in _LABEL_NODE.values():
        for node in self._tree.get_all(name):
            out.append(self._label_from_node(node))
    return out
