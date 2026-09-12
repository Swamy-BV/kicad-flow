"""Extract a spatial scene from one sheet without rasterization or net inference."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import quote

from kicad_flow.schematic.types import (
    LayoutFinding,
    Point,
    SceneBounds,
    SceneFinding,
    SceneObject,
    SceneSnapshot,
)

from .._sexpr import Node
from ._geometry import (
    _Box,
    _hidden,
    _on_segment,
    _pin_on_sheet,
    _segment_crosses_box,
    _text_box,
)
from ._nodes import _LABEL_NODE, PAPER, _f, _text

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def _bounds(points: tuple[Point, ...]) -> SceneBounds:
    return SceneBounds(min(p.x for p in points), min(p.y for p in points),
                       max(p.x for p in points), max(p.y for p in points))


def _box(bounds: SceneBounds) -> _Box:
    return _Box(bounds.left, bounds.top, bounds.right, bounds.bottom)


def _identity(node: Node, kind: str) -> str:
    uid = _text(node.get("uuid"))
    if not uid:
        raise ValueError(f"cannot assign a stable scene ID to {kind} without UUID")
    return f"{kind}:{uid}"


def _text_object(node: Node, identity: str, kind: str, *,
                 parent: str = "", rotation: float = 0.0,
                 value: str | None = None, field: str = "") -> SceneObject:
    content = _text(node) if value is None else value
    box = _text_box(node, content, rotation_offset=rotation)
    at = node.get("at")
    effects = node.get("effects")
    font = effects.get("font") if effects else None
    size = font.get("size") if font else None
    return SceneObject(
        identity, kind, SceneBounds(box.left, box.top, box.right, box.bottom),
        Point(_f(at, 0), _f(at, 1)), parent,
        (("text", content), ("rotation", (_f(at, 2) + rotation) % 360),
         ("field", field), ("bounds_accuracy", "estimated"),
         ("size", _f(size, 0, 1.27))),
    )


def _objects(sheet: KiCadSheet) -> Iterator[SceneObject]:
    """Read each root object once; symbol definitions remain backend-cached."""
    labels = {value: key for key, value in _LABEL_NODE.items()}
    for node in sheet._tree.items:
        if not isinstance(node, Node):
            continue
        kind = node.name
        if kind == "symbol" and node.get("lib_id"):
            identity = _identity(node, "symbol")
            ref = _text(sheet._prop_of(node, "Reference"), 1)
            part = sheet._as_part(node, ref)
            symbol = sheet.symbol(part.lib_id, unit=part.unit)
            left, bottom, right, top = symbol.bounds
            corners = tuple(_pin_on_sheet(x, y, 0, part.at,
                                         part.rotation, part.mirror)[0]
                            for x in (left, right) for y in (bottom, top))
            yield SceneObject(
                identity, "symbol", _bounds(corners), part.at,
                properties=(("ref", ref), ("unit", part.unit),
                            ("lib_id", part.lib_id), ("value", part.value),
                            ("rotation", part.rotation), ("mirror", part.mirror)),
            )
            seen: dict[str, int] = {}
            for pin in part.pins:
                index = seen.get(pin.number, 0)
                seen[pin.number] = index + 1
                angle = math.radians(pin.orientation)
                end = Point(pin.at.x + pin.length * math.cos(angle),
                            pin.at.y - pin.length * math.sin(angle))
                yield SceneObject(
                    f"{identity}/pin/{quote(pin.number, safe='')}/{index}",
                    "pin", _bounds((pin.at, end)), pin.at, identity,
                    (("ref", ref), ("unit", part.unit), ("number", pin.number),
                     ("name", pin.name), ("electrical_type", pin.kind),
                     ("orientation", pin.orientation), ("length", pin.length)),
                    (pin.at, end),
                )
            for prop in node.get_all("property"):
                field, value = _text(prop), _text(prop, 1)
                if value and not _hidden(prop.get("effects")):
                    yield _text_object(
                        prop, f"{identity}/field/{quote(field, safe='')}",
                        "field", parent=identity, rotation=part.rotation,
                        value=value, field=field,
                    )
        elif kind in labels or kind == "text":
            if not _hidden(node.get("effects")):
                scene_kind = "label" if kind in labels else "text"
                obj = _text_object(node, _identity(node, scene_kind), scene_kind)
                if kind in labels:
                    obj = SceneObject(
                        obj.id, obj.kind, obj.bounds, obj.at,
                        properties=(*obj.properties, ("label_kind", labels[kind])),
                    )
                yield obj
        elif kind in {"wire", "bus", "polyline"}:
            pts = node.get("pts")
            points = tuple(Point(_f(p, 0), _f(p, 1))
                           for p in pts.get_all("xy")) if pts else ()
            if points:
                stroke = node.get("stroke")
                width = _f(stroke.get("width"), 0) if stroke else 0.0
                yield SceneObject(_identity(node, kind), kind, _bounds(points),
                                  points[0], properties=(("width", width),),
                                  points=points)
        elif kind in {"junction", "no_connect", "bus_entry"}:
            at, size = node.get("at"), node.get("size")
            point = Point(_f(at, 0), _f(at, 1))
            end = Point(point.x + _f(size, 0), point.y + _f(size, 1))
            radius = (_f(node.get("diameter"), 0, 0.9) / 2
                      if kind == "junction" else 0.635)
            points = (point, end) if kind == "bus_entry" else ()
            bounds = _bounds(points) if points else SceneBounds(
                point.x - radius, point.y - radius,
                point.x + radius, point.y + radius)
            yield SceneObject(_identity(node, kind), kind, bounds, point,
                              points=points)
        elif kind == "sheet":
            identity = _identity(node, kind)
            child = sheet._sheet_ref(node)
            yield SceneObject(
                identity, kind, SceneBounds(child.at.x, child.at.y,
                                           child.at.x + child.size[0],
                                           child.at.y + child.size[1]), child.at,
                properties=(("name", child.name), ("filename", child.filename),
                            ("instance_path", child.instance_path)),
            )
            for port in node.get_all("pin"):
                at = port.get("at")
                point = Point(_f(at, 0), _f(at, 1))
                yield SceneObject(
                    _identity(port, "sheet_pin"), "sheet_pin",
                    SceneBounds(point.x, point.y, point.x, point.y),
                    point, identity, (("name", _text(port)),
                                      ("orientation", _f(at, 2))),
                )
            for prop in node.get_all("property"):
                field, value = _text(prop), _text(prop, 1)
                if value and not _hidden(prop.get("effects")):
                    yield _text_object(
                        prop, f"{identity}/field/{quote(field, safe='')}",
                        "field", parent=identity, value=value, field=field,
                    )


def _findings(objects: list[SceneObject]) -> tuple[SceneFinding, ...]:
    """Sweep bounds for body/text overlaps and text crossed by wires."""
    texts = {"field", "label", "text"}
    bodies = {"symbol", "sheet"}
    relevant = texts | bodies | {"wire"}
    active: list[SceneObject] = []
    findings: list[SceneFinding] = []
    for obj in sorted((o for o in objects if o.kind in relevant),
                      key=lambda o: (o.bounds.left, o.id)):
        active = [o for o in active if o.bounds.right >= obj.bounds.left]
        for other in active:
            if not obj.bounds.intersects(other.bounds):
                continue
            if (obj.parent_id or obj.id) == (other.parent_id or other.id):
                continue
            pair = {obj.kind, other.kind}
            if "wire" in pair:
                wire, text = (obj, other) if obj.kind == "wire" else (other, obj)
                if text.kind not in texts or len(wire.points) != 2:
                    continue
                a, b = wire.points
                if text.kind == "label" and _on_segment(text.at, a, b):
                    continue
                if not _segment_crosses_box(a, b, _box(text.bounds)):
                    continue
                kind = "text_wire_overlap"
            else:
                if not _box(obj.bounds).overlaps(_box(other.bounds)):
                    continue
                kind = "body_overlap" if pair <= bodies else "text_overlap"
            ids = tuple(sorted((obj.id, other.id)))
            identity = hashlib.sha256(json.dumps((kind, ids)).encode()).hexdigest()
            findings.append(SceneFinding(identity, kind, ids,
                                          _box(obj.bounds).meeting(_box(other.bounds))))
        active.append(obj)
    return tuple(sorted(findings, key=lambda f: f.id))


def body_findings(sheet: KiCadSheet, page: str) -> list[LayoutFinding]:
    """Share scene body collisions with the hierarchy layout checker.

    Text/text and text/wire checks remain in the layout checker. These bounds
    include pin extents and are conservative, just like the scene observation.
    """
    objects = {obj.id: obj for obj in _objects(sheet)}

    def name(obj: SceneObject) -> str:
        properties = dict(obj.properties)
        parent = objects.get(obj.parent_id or "", obj)
        owner = dict(parent.properties)
        ref = owner.get("ref", owner.get("name", ""))
        field = properties.get("field", "")
        text = properties.get("text", "")
        unit = f"unit {owner['unit']}" if "unit" in owner else ""
        detail = " ".join(str(value) for value in (obj.kind, ref, unit, field, text)
                          if value != "")
        return f"{detail} [{obj.id}]"

    out: list[LayoutFinding] = []
    for finding in _findings(list(objects.values())):
        first, second = (objects[identity] for identity in finding.objects)
        if not {first.kind, second.kind} & {"symbol", "sheet"}:
            continue
        kind = "_".join(sorted((first.kind, second.kind))) + "_overlap"
        first_name, second_name = name(first), name(second)
        out.append(LayoutFinding(
            severity="warning", kind=kind,
            message=f"{first_name} overlaps {second_name} (conservative bounds)",
            first=first_name, second=second_name, sheet=page, at=finding.at,
        ))
    return out


def snapshot(sheet: KiCadSheet, region: SceneBounds | None, *,
             max_objects: int) -> SceneSnapshot:
    """Extract a bounded, content-addressed spatial observation of one sheet."""
    if max_objects < 1 or max_objects > 10000:
        raise ValueError("max_objects must be between 1 and 10000")
    if region is not None:
        values = (region.left, region.top, region.right, region.bottom)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("region coordinates must be finite")
        if region.left > region.right or region.top > region.bottom:
            raise ValueError("region must have left <= right and top <= bottom")
    objects: list[SceneObject] = []
    for obj in _objects(sheet):
        if region is not None and not obj.bounds.intersects(region):
            continue
        objects.append(obj)
        if len(objects) > max_objects:
            raise ValueError("scene exceeds max_objects; select a smaller region "
                             "or explicitly increase max_objects")
    objects.sort(key=lambda obj: obj.id)
    if len({obj.id for obj in objects}) != len(objects):
        raise ValueError("duplicate scene IDs; source object identities are ambiguous")
    supported = {"symbol", "wire", "bus", "polyline", "junction", "no_connect",
                 "bus_entry", "sheet", "text", *_LABEL_NODE.values()}
    metadata = {"version", "generator", "generator_version", "uuid", "paper",
                "title_block", "lib_symbols", "sheet_instances", "embedded_fonts",
                "embedded_files", "symbol_instances", "bus_alias"}
    unsupported = tuple(sorted({n.name for n in sheet._tree.items
                                if isinstance(n, Node)
                                and n.name not in supported | metadata}))
    width, height = PAPER[sheet._paper]
    page = SceneBounds(0, 0, width, height)
    content = {"sheet": sheet.uuid, "page": page.as_list(),
               "objects": [o.as_dict() for o in objects],
               "unsupported": unsupported}
    revision = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    return SceneSnapshot(sheet.uuid, revision, page, region, tuple(objects),
                         _findings(objects), unsupported)
