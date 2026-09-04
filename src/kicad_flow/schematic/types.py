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


__all__ = ["Finding", "Net", "NetPin", "Part", "Pin", "Point", "SheetRef",
           "SymbolDef"]
