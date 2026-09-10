"""The nouns the API deals in: a point, a pin, a part, a symbol.

Every one is a plain frozen dataclass with millimetre floats. Nothing here
knows what a schematic is *for* -- there is no net, no block, no rank, no
group. Those are conclusions, and drawing them is the caller's job.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Point:
    """A position on the sheet, in millimetres from the top-left corner."""

    x: float
    y: float

    def as_dict(self) -> dict[str, float]:
        """``{"x", "y"}``, rounded to the 3 decimals KiCad files carry."""
        return {"x": round(self.x, 3), "y": round(self.y, 3)}


@dataclass(frozen=True)
class Label:
    """One net label, with stable identity for later editing."""

    uuid: str
    text: str
    kind: str
    at: Point
    rotation: float
    justify: str

    def as_dict(self) -> dict[str, Any]:
        """The complete label as JSON, with its position flattened."""
        return {"uuid": self.uuid, "text": self.text, "kind": self.kind,
                "x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "rotation": self.rotation, "justify": self.justify}


@dataclass(frozen=True)
class Pin:
    """One pin of a placed part, at its position **on the sheet**.

    *at* is where a wire must end to connect. It already accounts for the
    part's position, rotation and mirroring, so a caller never repeats that
    arithmetic -- getting it wrong is the classic way to draw a schematic that
    looks right and is not connected.

    *orientation* is the direction the pin points, in degrees, 0 = right,
    90 = up. A wire should leave along it.
    """

    number: str
    name: str
    at: Point
    orientation: float
    kind: str  # passive, input, output, bidirectional, power_in, ...
    length: float

    def as_dict(self) -> dict[str, Any]:
        """The pin as JSON, with *at* flattened to ``x``/``y``."""
        return {"number": self.number, "name": self.name,
                "x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "orientation": self.orientation, "kind": self.kind,
                "length": self.length}


@dataclass(frozen=True)
class Part:
    """A symbol placed on the sheet, with its pins already resolved."""

    ref: str
    lib_id: str
    value: str
    at: Point
    rotation: float
    mirror: str  # "", "x" or "y"
    #: Which unit of a multi-unit symbol this is. An LM358 is three units:
    #: one op-amp, the other op-amp, and the shared power pins. Each is placed
    #: separately and they share a reference.
    unit: int
    pins: tuple[Pin, ...]
    uuid: str

    def pin(self, number: str) -> Pin | None:
        """The pin with this number, by number **or** by name."""
        for p in self.pins:
            if p.number == number or p.name == number:
                return p
        return None

    def as_dict(self) -> dict[str, Any]:
        """The part as JSON, pins included."""
        return {"ref": self.ref, "lib_id": self.lib_id, "value": self.value,
                "x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "rotation": self.rotation, "mirror": self.mirror,
                "unit": self.unit, "uuid": self.uuid,
                "pins": [p.as_dict() for p in self.pins]}


@dataclass(frozen=True)
class PartPlacement:
    """One caller-decided symbol pose to inspect without placing it."""

    lib_id: str
    ref: str
    at: Point
    value: str = ""
    rotation: float = 0.0
    mirror: str = ""
    unit: int = 1


@dataclass(frozen=True)
class PlacementBounds:
    """Axis-aligned sheet bounds for one predicted visible object."""

    name: str
    kind: str
    ref: str
    unit: int
    left: float
    top: float
    right: float
    bottom: float

    def as_dict(self) -> dict[str, Any]:
        """The visible extent as JSON."""
        return {"name": self.name, "kind": self.kind, "ref": self.ref,
                "unit": self.unit, "left": round(self.left, 3),
                "top": round(self.top, 3), "right": round(self.right, 3),
                "bottom": round(self.bottom, 3)}


@dataclass(frozen=True)
class PlacementMeasurement:
    """Facts about a proposed placement, computed without changing the sheet."""

    parts: tuple[Part, ...]
    bounds: tuple[PlacementBounds, ...]
    findings: tuple[LayoutFinding, ...]
    page_size: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        """The complete non-mutating measurement as JSON."""
        overlap_count = sum(1 for item in self.findings
                            if item.kind.endswith("_overlap"))
        page_violation_count = sum(1 for item in self.findings
                                   if item.kind == "page_bounds")
        return {"clean": not self.findings, "part_count": len(self.parts),
                "parts": [part.as_dict() for part in self.parts],
                "bounds": [item.as_dict() for item in self.bounds],
                "overlap_count": overlap_count,
                "page_violation_count": page_violation_count,
                "page_size": [self.page_size[0], self.page_size[1]],
                "findings": [item.as_dict() for item in self.findings]}


@dataclass(frozen=True)
class SymbolDef:
    """What a library symbol offers, before it is placed anywhere.

    Pin positions here are offsets from the symbol's own origin, unrotated --
    useful for sizing, not for wiring. Wire to :class:`Part` pins instead.
    """

    lib_id: str
    description: str
    keywords: str
    #: How many units the symbol has. Above 1, *pins* covers only the unit
    #: asked for -- reporting them all at once puts two units' pins at the
    #: same coordinates, which is a wrong netlist rather than a messy one.
    units: int
    unit: int
    pins: tuple[Pin, ...]
    width: float
    height: float
    #: ``(left, bottom, right, top)`` in the symbol's own space, where Y runs
    #: UP. Not the same as width/height around the origin: a connector's body
    #: hangs well below its origin, and treating it as centred puts its value
    #: label straight through a pin.
    bounds: tuple[float, float, float, float]
    power: bool

    def as_dict(self) -> dict[str, Any]:
        """The symbol as JSON."""
        d = asdict(self)
        d["pins"] = [p.as_dict() for p in self.pins]
        return d


@dataclass(frozen=True)
class NetPin:
    """One pin sitting on a net."""

    ref: str
    pin: str
    name: str

    def as_dict(self) -> dict[str, str]:
        """The pin as JSON."""
        return {"ref": self.ref, "pin": self.pin, "name": self.name}


@dataclass(frozen=True)
class Net:
    """A set of pins that are electrically one thing.

    This is what the sheet ACTUALLY connects, read back from the tool rather
    than from what the caller believes it drew. The distinction is not
    academic: a schematic can be a valid file, open, and render correctly
    while its wires join nothing.
    """

    name: str
    pins: tuple[NetPin, ...]

    def as_dict(self) -> dict[str, Any]:
        """The net as JSON."""
        return {"name": self.name, "count": len(self.pins),
                "pins": [p.as_dict() for p in self.pins]}


@dataclass(frozen=True)
class Finding:
    """Something wrong with a sheet, said in terms of parts rather than mm.

    *ref* and *pin* are what makes this worth having. The underlying tool
    reports a position; a position has to be looked up against every pin on
    the sheet before it means anything, and doing that by hand is how an
    afternoon goes.
    """

    severity: str          # "error" or "warning"
    kind: str              # e.g. "power_pin_not_driven", "wire_dangling"
    message: str
    ref: str = ""
    pin: str = ""
    #: Which page, in a design of more than one -- ``"/"`` for the root,
    #: ``"/Power/"`` for a child. Without it a hierarchical finding says what
    #: is wrong and gives no way to find it.
    sheet: str = "/"
    at: Point | None = None

    def as_dict(self) -> dict[str, Any]:
        """The finding as JSON."""
        out: dict[str, Any] = {"severity": self.severity, "kind": self.kind,
                               "message": self.message, "sheet": self.sheet}
        if self.ref:
            out["ref"] = self.ref
        if self.pin:
            out["pin"] = self.pin
        if self.at is not None:
            out.update(self.at.as_dict())
        return out


@dataclass(frozen=True)
class LayoutFinding:
    """A potential graphical collision on a schematic sheet.

    Unlike :class:`Finding`, this is not an electrical-rule result.  *first*
    and *second* name the two visible objects whose geometry intersects, so a
    caller can choose which one to move without the inspection layer making a
    layout decision for it.
    """

    severity: str
    kind: str
    message: str
    first: str
    second: str
    sheet: str = "/"
    at: Point | None = None

    def as_dict(self) -> dict[str, Any]:
        """The finding as JSON, with its position flattened."""
        out: dict[str, Any] = {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "sheet": self.sheet,
            "first": self.first,
            "second": self.second,
        }
        if self.at is not None:
            out.update(self.at.as_dict())
        return out


@dataclass(frozen=True)
class SheetRef:
    """A child sheet, as it appears on its parent.

    *pins* are the sheet's ports at their positions ON THE PARENT, ready to
    wire to. Each one pairs with a hierarchical label of the same name inside
    the child -- that pairing, by name, is the whole of how the two sheets
    connect.
    """

    name: str
    filename: str
    at: Point
    size: tuple[float, float]
    uuid: str
    #: The instance path a symbol inside the child must record, e.g.
    #: ``/<root-uuid>/<this-sheet-uuid>``. Pass it to `create` when making the
    #: child, or the child's parts are annotated against the wrong sheet and
    #: their nets do not merge into the design.
    instance_path: str
    pins: tuple[Pin, ...]

    def as_dict(self) -> dict[str, Any]:
        """The sheet as JSON."""
        return {"name": self.name, "filename": self.filename,
                "x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "width": self.size[0], "height": self.size[1],
                "uuid": self.uuid, "instance_path": self.instance_path,
                "pins": [p.as_dict() for p in self.pins]}


@dataclass(frozen=True)
class SceneBounds:
    """An axis-aligned rectangle in sheet coordinates, including its edges."""

    left: float
    top: float
    right: float
    bottom: float

    def intersects(self, other: SceneBounds) -> bool:
        """Whether the rectangles share any point."""
        return (self.left <= other.right and other.left <= self.right
                and self.top <= other.bottom and other.top <= self.bottom)

    def as_list(self) -> list[float]:
        """Return left, top, right and bottom in millimetres."""
        return [round(v, 3) for v in (
            self.left, self.top, self.right, self.bottom)]


@dataclass(frozen=True)
class SceneObject:
    """One addressable geometric object; bounds are not electrical evidence."""

    id: str
    kind: str
    bounds: SceneBounds
    at: Point
    parent_id: str = ""
    properties: tuple[tuple[str, str | float | int | bool], ...] = ()
    points: tuple[Point, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return geometry and scalar properties without image data."""
        out: dict[str, Any] = {
            "id": self.id, "kind": self.kind,
            "bounds": self.bounds.as_list(), **self.at.as_dict(),
            "properties": dict(self.properties),
        }
        if self.parent_id:
            out["parent_id"] = self.parent_id
        if self.points:
            out["points"] = [p.as_dict() for p in self.points]
        return out


@dataclass(frozen=True)
class SceneFinding:
    """A conservative spatial conflict naming stable scene object IDs."""

    id: str
    kind: str
    objects: tuple[str, ...]
    at: Point

    def as_dict(self) -> dict[str, Any]:
        """Return an actionable geometric finding, not an ERC result."""
        return {"id": self.id, "kind": self.kind,
                "objects": list(self.objects), **self.at.as_dict(),
                "severity": "warning"}


@dataclass(frozen=True)
class SceneSnapshot:
    """A complete observation of one sheet or explicitly selected region."""

    sheet_id: str
    revision: str
    page: SceneBounds
    region: SceneBounds | None
    objects: tuple[SceneObject, ...]
    findings: tuple[SceneFinding, ...]
    unsupported: tuple[str, ...] = ()


__all__ = ["Finding", "Label", "LayoutFinding", "Net", "NetPin", "Part",
           "PartPlacement", "Pin", "PlacementBounds", "PlacementMeasurement",
           "Point", "SceneBounds", "SceneFinding", "SceneObject", "SceneSnapshot",
           "SheetRef", "SymbolDef"]
