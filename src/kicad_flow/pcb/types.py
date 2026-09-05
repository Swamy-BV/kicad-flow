"""The nouns the board API deals in: a point, a pad, a footprint, a track.

Every one is a plain frozen dataclass with millimetre floats. Nothing here
knows what a board is *for* -- there is no block, no band, no placement, no
score. Those are conclusions, and drawing them is the caller's job.

These do not reuse :mod:`kicad_flow.schematic.types`, deliberately. The two
contracts describe different things and should be readable apart; a board
:class:`Finding` carries a layer and a pad where a schematic one carries a
sheet and a pin, and coupling them would make one package's change the other's
problem for the sake of five shared lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Point:
    """A position on the board, in millimetres from the page origin."""

    x: float
    y: float

    def as_dict(self) -> dict[str, float]:
        """``{"x", "y"}``, rounded to the 3 decimals KiCad files carry."""
        return {"x": round(self.x, 3), "y": round(self.y, 3)}


@dataclass(frozen=True)
class Graphic:
    """One non-copper board shape, identified for later editing.

    ``points`` carry the primitive's defining geometry in order: line and
    rectangle corners; arc start, mid and end; circle centre and rim; or all
    polygon vertices. This is geometry, not KiCad syntax.
    """

    uuid: str
    kind: str
    layer: str
    points: tuple[Point, ...]
    width: float = 0.1
    fill: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The complete, round-trippable shape as JSON."""
        return {"uuid": self.uuid, "kind": self.kind, "layer": self.layer,
                "points": [p.as_dict() for p in self.points],
                "width": self.width, "fill": self.fill}


@dataclass(frozen=True)
class Pad:
    """One pad of a placed footprint, at its position **on the board**.

    *at* is where copper must arrive to connect. It already accounts for the
    footprint's position, rotation and side, so a caller never repeats that
    arithmetic -- getting it wrong is the classic way to draw a track that
    looks right and connects nothing.

    *layers* is every copper layer the pad is reachable from: one for an SMD
    pad, all of them for a plated through-hole. A track on a layer the pad is
    not on does not connect to it, and nothing on the board says so.
    """

    number: str
    at: Point
    size: tuple[float, float]
    layers: tuple[str, ...]
    net: str = ""
    drill: float = 0.0
    kind: str = "smd"  # smd, pth, npth

    @property
    def through_hole(self) -> bool:
        """Whether the pad is drilled, and so reachable from every layer."""
        return self.kind in ("pth", "npth")

    def as_dict(self) -> dict[str, Any]:
        """The pad as JSON, with *at* flattened to ``x``/``y``."""
        return {"number": self.number, "x": round(self.at.x, 3),
                "y": round(self.at.y, 3), "width": self.size[0],
                "height": self.size[1], "layers": list(self.layers),
                "net": self.net, "drill": self.drill, "kind": self.kind}


@dataclass(frozen=True)
class FootprintDef:
    """What a library footprint offers, before it is placed anywhere.

    Pad positions here are offsets from the footprint's own origin, unrotated
    -- useful for sizing, not for routing. Route to :class:`Footprint` pads
    instead.
    """

    fp_id: str
    description: str
    pads: tuple[Pad, ...]
    #: ``(width, height)`` of the courtyard -- the room the part actually
    #: needs, which is not its bounding box and not its pad extent.
    courtyard: tuple[float, float]
    bbox: tuple[float, float]
    has_pth: bool

    def as_dict(self) -> dict[str, Any]:
        """The footprint definition as JSON."""
        return {"fp_id": self.fp_id, "description": self.description,
                "courtyard": list(self.courtyard), "bbox": list(self.bbox),
                "has_pth": self.has_pth, "pad_count": len(self.pads),
                "pads": [p.as_dict() for p in self.pads]}


@dataclass(frozen=True)
class Footprint:
    """A footprint placed on the board, with its pads already resolved."""

    ref: str
    fp_id: str
    value: str
    at: Point
    rotation: float
    #: ``"F"`` front or ``"B"`` back. A part on the back is MIRRORED, so its
    #: pads run the other way and its silkscreen reads reversed.
    side: str
    pads: tuple[Pad, ...]
    #: ``(width, height)`` of the courtyard as placed -- already turned with
    #: the part, so a 90-degree rotation swaps them.
    courtyard: tuple[float, float]
    #: Offset from the footprint ORIGIN to the courtyard centre. The origin can
    #: sit on pad 1 rather than in the middle, so a caller placing by centre
    #: needs this and cannot derive it.
    courtyard_offset: Point
    uuid: str = ""

    def pad(self, number: str) -> Pad | None:
        """The pad with this number, or None."""
        for p in self.pads:
            if p.number == number:
                return p
        return None

    def as_dict(self) -> dict[str, Any]:
        """The footprint as JSON, pads included."""
        return {"ref": self.ref, "fp_id": self.fp_id, "value": self.value,
                "x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "rotation": self.rotation, "side": self.side,
                "courtyard": list(self.courtyard),
                "courtyard_offset": self.courtyard_offset.as_dict(),
                "uuid": self.uuid,
                "pads": [p.as_dict() for p in self.pads]}


@dataclass(frozen=True)
class Track:
    """One copper segment on one layer."""

    start: Point
    end: Point
    layer: str
    width: float
    net: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The track as JSON."""
        return {"start": self.start.as_dict(), "end": self.end.as_dict(),
                "layer": self.layer, "width": self.width, "net": self.net}


@dataclass(frozen=True)
class Via:
    """A plated hole joining layers, on a net."""

    at: Point
    diameter: float
    drill: float
    net: str = ""
    layers: tuple[str, str] = ("F.Cu", "B.Cu")

    def as_dict(self) -> dict[str, Any]:
        """The via as JSON."""
        return {"x": round(self.at.x, 3), "y": round(self.at.y, 3),
                "diameter": self.diameter, "drill": self.drill,
                "net": self.net, "layers": list(self.layers)}


@dataclass(frozen=True)
class Zone:
    """A copper pour on one layer, tied to a net."""

    net: str
    layer: str
    points: tuple[Point, ...]
    filled: bool = False
    #: What the pour refuses to contain, for a keep-out: ``tracks``, ``vias``,
    #: ``pads``, ``pours``, ``footprints``. Empty for an ordinary pour.
    forbids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The zone as JSON."""
        return {"net": self.net, "layer": self.layer, "filled": self.filled,
                "forbids": list(self.forbids),
                "points": [p.as_dict() for p in self.points]}


@dataclass(frozen=True)
class NetPad:
    """One pad sitting on a net."""

    ref: str
    pad: str

    def as_dict(self) -> dict[str, str]:
        """The pad as JSON."""
        return {"ref": self.ref, "pad": self.pad}


@dataclass(frozen=True)
class Net:
    """A set of pads that are meant to be one thing electrically.

    On a board this is INTENT, not fact: the netlist says these pads belong
    together, and whether copper actually joins them is what
    :meth:`Board.unrouted` answers.
    """

    name: str
    pads: tuple[NetPad, ...]

    def as_dict(self) -> dict[str, Any]:
        """The net as JSON."""
        return {"name": self.name, "count": len(self.pads),
                "pads": [p.as_dict() for p in self.pads]}


@dataclass(frozen=True)
class Connection:
    """Two pads on one net with no copper between them.

    The board's remaining work, named. A count of unrouted connections says
    how much is left; this says which, so a caller can route one.
    """

    net: str
    a: NetPad
    b: NetPad
    distance: float

    def as_dict(self) -> dict[str, Any]:
        """The connection as JSON."""
        return {"net": self.net, "from": self.a.as_dict(),
                "to": self.b.as_dict(), "distance": round(self.distance, 3)}


@dataclass(frozen=True)
class Finding:
    """Something wrong with a board, said in terms of parts rather than mm.

    *ref* and *pad* are what makes this worth having. The underlying tool
    reports a position; a position has to be looked up against every pad on
    the board before it means anything.
    """

    severity: str          # "error", "warning" or "exclusion"
    kind: str              # e.g. "clearance", "unconnected_items"
    message: str
    ref: str = ""
    pad: str = ""
    layer: str = ""
    at: Point | None = None
    #: The other side of a two-item violation -- a clearance error is always
    #: between two things, and naming one of them is half an answer.
    other_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The finding as JSON."""
        out: dict[str, Any] = {"severity": self.severity, "kind": self.kind,
                               "message": self.message}
        for name, value in (("ref", self.ref), ("pad", self.pad),
                            ("layer", self.layer),
                            ("other_ref", self.other_ref)):
            if value:
                out[name] = value
        if self.at is not None:
            out.update(self.at.as_dict())
        return out


__all__ = ["Connection", "Finding", "Footprint", "FootprintDef", "Net",
           "NetPad", "Pad", "Point", "Track", "Via", "Zone"]
