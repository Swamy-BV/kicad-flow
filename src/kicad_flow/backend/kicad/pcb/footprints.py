"""Footprint definitions, placement, fields and pad assignments."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from kicad_flow.pcb.types import (
    Footprint,
    FootprintDef,
    Pad,
    Point,
)

from .._sexpr import Node, Sym
from . import library as _fplib
from ._constants import (
    _SIDES,
)
from ._geometry import (
    _courtyard_geometry,
    _fabrication_geometry,
    _origin_for_anchor,
    _pad_on_board,
)
from ._nodes import (
    _f,
    _flip_layer,
    _net_name,
    _node,
    _refresh_uuids,
    _set,
    _text,
    _toggle_mirror,
    _turn_pads,
    _uid,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def find_footprints(
    self: KiCadBoard, query: str, limit: int = 20
) -> list[FootprintDef]:
    """Library footprints whose ``Library:Footprint`` id contains *query*."""
    out: list[FootprintDef] = []
    for fp_id in _fplib.search(query, limit=limit, project_dir=self._path.parent):
        try:
            out.append(self.footprint_def(fp_id))
        except (LookupError, ValueError):
            continue
    return out


def footprint_def(self: KiCadBoard, fp_id: str) -> FootprintDef:
    """One library footprint, with its pads at the footprint origin."""
    tree = self._load_def(fp_id)
    pads = tuple(
        self._pad_of(node, Point(0.0, 0.0), 0.0) for node in tree.get_all("pad")
    )
    width, height, cx, cy = _fplib.courtyard_box(tree)
    courtyard_polygon = tuple(
        Point(x, y)
        for x, y in (
            (cx - width / 2, cy - height / 2),
            (cx + width / 2, cy - height / 2),
            (cx + width / 2, cy + height / 2),
            (cx - width / 2, cy + height / 2),
        )
    )
    fabrication, fabrication_status = _fabrication_geometry(tree, Point(0.0, 0.0), 0.0)
    return FootprintDef(
        fp_id=fp_id,
        description=_text(tree.get("descr")),
        pads=pads,
        courtyard=(width, height),
        courtyard_center=Point(cx, cy),
        courtyard_polygon=courtyard_polygon,
        bbox=_fplib.bbox(tree),
        has_pth=any(p.through_hole for p in pads),
        fabrication_polygon=fabrication,
        fabrication_status=fabrication_status,
    )


def _load_def(self: KiCadBoard, fp_id: str) -> Node:
    """Load and cache a library footprint's tree."""
    if fp_id not in self._defs:
        self._defs[fp_id] = _fplib.load(fp_id, self._path.parent)
    return self._defs[fp_id]


def place(
    self: KiCadBoard,
    fp_id: str,
    ref: str,
    x: float,
    y: float,
    *,
    anchor: str = "origin",
    rotation: float = 0.0,
    side: str = "F",
    value: str = "",
) -> Footprint:
    """Put *fp_id* on the board at ``(x, y)`` as *ref*."""
    if side not in _SIDES:
        raise ValueError(f"side must be 'F' or 'B', not {side!r}")
    if self._find(ref) is not None:
        raise ValueError(f"{ref} is already on the board")
    node = copy.deepcopy(self._load_def(fp_id))
    # A library footprint node is `(footprint "Name" ...)`; on a board it
    # is `(footprint "Lib:Name" ...)` and carries a position and a side.
    node.items[1] = fp_id
    if side == "B":
        self._mirror(node)
    origin = _origin_for_anchor(node, x, y, rotation, anchor)
    self._set_child(node, "layer", ["F.Cu" if side == "F" else "B.Cu"])
    self._set_child(node, "at", [origin.x, origin.y, rotation % 360.0])
    _turn_pads(node, rotation % 360.0)
    _refresh_uuids(node)
    if node.get("uuid") is None:
        node.items.append(_node("uuid", [_uid()]))
    self._set_property(node, "Reference", ref)
    self._set_property(node, "Value", value or fp_id.split(":")[-1])
    for pad in node.get_all("pad"):
        if pad.get("uuid") is None:
            pad.items.append(_node("uuid", [_uid()]))
    self._tree.items.append(node)
    return self.footprint(ref)


def _mirror(node: Node) -> None:
    """Flip a footprint's stored geometry to the other side of the board.

    This is what the file records, and why :func:`_pad_on_board` applies
    no side of its own: a back-side footprint carries coordinates that are
    ALREADY mirrored. Negate X on everything it draws, negate the angles,
    and move every layer to its opposite -- a pad left on ``F.Cu`` under a
    part on the back is a pad on the wrong side, which routes cleanly and
    connects nothing.
    """
    for kind in (
        "pad",
        "fp_line",
        "fp_rect",
        "fp_poly",
        "fp_circle",
        "fp_arc",
        "fp_text",
        "property",
    ):
        for shape in node.get_all(kind):
            for corner in ("at", "start", "end", "center", "mid"):
                point = shape.get(corner)
                if point is not None and len(point.items) >= 2:
                    _set(point, 0, -_f(point, 0))
                    if len(point.items) >= 4:
                        _set(point, 2, (-_f(point, 2)) % 360.0)
            pts = shape.get("pts")
            for xy in pts.get_all("xy") if pts is not None else []:
                _set(xy, 0, -_f(xy, 0))
            for holder in (shape.get("layers"), shape.get("layer")):
                if holder is None:
                    continue
                holder.items = [holder.items[0]] + [
                    _flip_layer(str(x)) for x in holder.items[1:]
                ]
            if kind in ("fp_text", "property"):
                _toggle_mirror(shape)


def _set_property(self: KiCadBoard, node: Node, name: str, value: str) -> None:
    """Set a footprint property, adding it if absent."""
    prop = self._prop_of(node, name)
    if prop is None:
        node.items.append(
            _node(
                "property",
                [
                    name,
                    value,
                    _node("at", [0, 0, 0]),
                    # A new custom property is BOM/fabrication metadata, not
                    # artwork.  Reference and Value already exist and retain the
                    # library's visibility; callers can explicitly expose a
                    # custom property with move_field(hide=False, layer=...).
                    _node("layer", ["F.Fab"]),
                    _node("hide", [Sym("yes")]),
                    _node("uuid", [_uid()]),
                ],
            )
        )
    else:
        _set(prop, 1, value)


def _find(self: KiCadBoard, ref: str) -> Node | None:
    """The ``(footprint ...)`` node placed as *ref*, if any."""
    for node in self._tree.get_all("footprint"):
        prop = self._prop_of(node, "Reference")
        if prop is not None and _text(prop, 1) == ref:
            return node
    return None


def _require(self: KiCadBoard, ref: str) -> Node:
    """The node for *ref*, or a :class:`LookupError`."""
    node = self._find(ref)
    if node is None:
        raise LookupError(f"{ref} is not on the board")
    return node


def move(
    self: KiCadBoard, ref: str, x: float, y: float, *, anchor: str = "origin"
) -> Footprint:
    """Move a footprint by origin or courtyard centre; copper stays."""
    node = self._require(ref)
    at = node.get("at")
    if at is None:
        raise LookupError(f"{ref} has no position")
    origin = _origin_for_anchor(node, x, y, _f(at, 2), anchor)
    _set(at, 0, origin.x)
    _set(at, 1, origin.y)
    return self.footprint(ref)


def rotate(self: KiCadBoard, ref: str, rotation: float) -> Footprint:
    """Set a placed footprint's rotation in degrees."""
    node = self._require(ref)
    at = node.get("at")
    if at is None:
        raise LookupError(f"{ref} has no position")
    was = _f(at, 2) if len(at.items) > 3 else 0.0
    _set(at, 2, rotation % 360.0)
    _turn_pads(node, (rotation - was) % 360.0)
    return self.footprint(ref)


def flip(self: KiCadBoard, ref: str, side: str) -> Footprint:
    """Put a footprint on ``"F"`` or ``"B"``."""
    if side not in _SIDES:
        raise ValueError(f"side must be 'F' or 'B', not {side!r}")
    node = self._require(ref)
    now = "B" if _text(node.get("layer")).startswith("B.") else "F"
    if now != side:
        self._mirror(node)
        self._set_child(node, "layer", ["F.Cu" if side == "F" else "B.Cu"])
    return self.footprint(ref)


def remove(self: KiCadBoard, ref: str) -> None:
    """Take a footprint off the board."""
    self._tree.items.remove(self._require(ref))


def footprints(self: KiCadBoard) -> list[Footprint]:
    """Every placed footprint, in reference order."""
    out = []
    for node in self._tree.get_all("footprint"):
        prop = self._prop_of(node, "Reference")
        if prop is not None:
            out.append(self._as_footprint(node, _text(prop, 1)))
    return sorted(out, key=lambda f: f.ref)


def footprint(self: KiCadBoard, ref: str) -> Footprint:
    """One placed footprint, with its pads at board positions."""
    return self._as_footprint(self._require(ref), ref)


def _as_footprint(self: KiCadBoard, node: Node, ref: str) -> Footprint:
    """Build a :class:`Footprint` from a placed ``(footprint ...)``."""
    at_node = node.get("at")
    at = Point(_f(at_node, 0), _f(at_node, 1))
    rotation = _f(at_node, 2)
    side = "B" if _text(node.get("layer")).startswith("B.") else "F"
    value = self._prop_of(node, "Value")
    pads = tuple(self._pad_of(p, at, rotation) for p in node.get_all("pad"))
    centre, offset, polygon, courtyard = _courtyard_geometry(node, at, rotation)
    fabrication, fabrication_status = _fabrication_geometry(node, at, rotation)
    return Footprint(
        ref=ref,
        fp_id=_text(node),
        value=_text(value, 1) if value else "",
        at=at,
        rotation=rotation,
        side=side,
        pads=pads,
        courtyard=courtyard,
        courtyard_offset=offset,
        courtyard_center=centre,
        courtyard_polygon=polygon,
        uuid=_text(node.get("uuid")),
        fabrication_polygon=fabrication,
        fabrication_status=fabrication_status,
    )


def _pad_of(self: KiCadBoard, node: Node, at: Point, rotation: float) -> Pad:
    """Build a :class:`Pad` from a ``(pad ...)`` node."""
    pat = node.get("at")
    size = node.get("size")
    layers = node.get("layers")
    kind = _text(node, 1, "smd")
    drill = node.get("drill")
    return Pad(
        number=_text(node),
        at=_pad_on_board(_f(pat, 0), _f(pat, 1), at, rotation),
        size=(_f(size, 0), _f(size, 1)),
        layers=tuple(str(x) for x in (layers.items[1:] if layers else [])),
        net=_net_name(node.get("net")),
        drill=_f(drill, 0) if drill is not None else 0.0,
        kind="pth"
        if kind == "thru_hole"
        else ("npth" if kind == "np_thru_hole" else "smd"),
        shape=_text(node, 2, "unknown"),
        rotation=_f(pat, 2) % 360.0,
        corner_ratio=_f(node.get("roundrect_rratio"), 0),
        uuid=_text(node.get("uuid")),
        geometry_supported=(
            _text(node, 2) in {"rect", "circle", "oval", "roundrect"}
            and not any(
                node.get(key) is not None
                for key in ("offset", "chamfer", "chamfer_ratio", "padstack")
            )
            and not (
                drill is not None
                and (drill.get("offset") is not None or _text(drill, 0) == "oval")
            )
        ),
    )


def fields(self: KiCadBoard, ref: str) -> dict[str, str]:
    """Every field on a footprint, by name."""
    node = self._require(ref)
    return {_text(p, 0): _text(p, 1) for p in node.get_all("property")}


def set_field(self: KiCadBoard, ref: str, name: str, value: str) -> dict[str, str]:
    """Set one of a footprint's fields and return all of them."""
    self._set_property(self._require(ref), name, value)
    return self.fields(ref)


def move_field(
    self: KiCadBoard,
    ref: str,
    name: str,
    dx: float,
    dy: float,
    *,
    rotation: float | None = None,
    layer: str = "",
    hide: bool | None = None,
) -> Point:
    """Move a footprint's field, relative to the footprint's position."""
    node = self._require(ref)
    prop = self._prop_of(node, name)
    if prop is None:
        have = ", ".join(self.fields(ref))
        raise LookupError(f"{ref} has no field {name!r}; it has {have}")
    at = node.get("at")
    pat = prop.get("at")
    if at is None or pat is None:
        raise LookupError(f"{ref}.{name} has no position")
    # KiCad stores a footprint field's position as an offset from the
    # footprint, so this is what a caller means by "move it 2 mm left" and
    # the field keeps following its part.
    _set(pat, 0, round(float(dx), 6))
    _set(pat, 1, round(float(dy), 6))
    if rotation is not None:
        _set(pat, 2, float(rotation) % 360.0)
    if layer:
        self._set_child(prop, "layer", [layer])
    if hide is not None:
        existing = prop.get("hide")
        if existing is not None:
            prop.items.remove(existing)
        prop.items.append(_node("hide", [Sym("yes" if hide else "no")]))
    return _pad_on_board(float(dx), float(dy), Point(_f(at, 0), _f(at, 1)), _f(at, 2))


def set_net(self: KiCadBoard, ref: str, pad: str, net: str) -> str:
    """Put EVERY pad of *ref* numbered *pad* on *net*, and return the net.

    A pad number is not unique. A USB-C receptacle carries four shield lugs
    all numbered ``SH``; an SOT-223 numbers its tab ``2`` along with a lead.
    They are one electrical pin and the schematic names them once, so
    setting the first and leaving the rest floating is silently wrong --
    the unnetted copper reads as an island and DRC calls it unconnected.
    """
    node = self._require(ref)
    hit = 0
    for candidate in node.get_all("pad"):
        if _text(candidate) == pad:
            existing = candidate.get("net")
            if existing is not None:
                candidate.items.remove(existing)
            candidate.items.append(_node("net", [net]))
            hit += 1
    if hit:
        return net
    have = ", ".join(_text(p) for p in node.get_all("pad"))
    raise LookupError(f"{ref} has no pad {pad!r}; it has {have}")


def pad(self: KiCadBoard, ref: str, pad: str) -> Point:
    """Where *ref*'s *pad* is on the board -- the point to route to."""
    part = self.footprint(ref)
    found = part.pad(pad)
    if found is None:
        have = ", ".join(p.number for p in part.pads)
        raise LookupError(f"{ref} has no pad {pad!r}; it has {have}")
    return found.at
