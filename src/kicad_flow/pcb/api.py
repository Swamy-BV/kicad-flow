"""The whole board contract: primitives, and nothing above them.

This interface is the only thing the MCP server should talk to. It names no
file format and no tool -- ``KiCadBoard`` is one implementation and another
could serve the same calls.

**What is deliberately absent.** There is no autoplacer, no router, no via
stitcher, no silkscreen cleaner, no fanout. Those all existed and were removed,
for the reason the schematic ones were: each decided something -- where a part
goes, how a track runs, which pads get a via, where a reference sits -- and a
caller who disagreed had no way to say so, because the decision was inside the
algorithm rather than in the call. What is left cannot decide anything: it puts
a footprint where it is told and reports where the pads landed.

The one service it does provide is **arithmetic**: :meth:`Board.pad` gives the
board position of a pad with the footprint's rotation and side already applied.
That is not a decision, it is a fact about the geometry, and it is the fact a
caller most needs and most easily gets wrong -- a track drawn to the position a
pad would have had if the part were unrotated looks connected and is not.

So the division is the same one the schematic side settled on: **this layer
knows where things are, the caller decides where they should be.**

**A board differs from a sheet in two ways worth stating.** It has layers, so
a position is not enough -- copper on the wrong layer connects nothing. And a
part can be on the BACK, where it is mirrored: its pads run the other way and
its silkscreen reads reversed. Both are in every signature that needs them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .types import (
    BoardLimits,
    BoardRule,
    Connection,
    Finding,
    Footprint,
    FootprintDef,
    Graphic,
    Net,
    NetClass,
    NetClassAssignment,
    Point,
    Stackup,
    Track,
    Via,
    Zone,
)


class Board(ABC):
    """One board, open for editing.

    Every mutator returns what it made, so a caller can chain without a
    lookup, and every position argument is in millimetres. Nothing is written
    to disk until :meth:`save`.
    """

    # -- the board itself -------------------------------------------------

    @property
    @abstractmethod
    def path(self) -> Path:
        """Where this board will be written."""

    @property
    @abstractmethod
    def size(self) -> tuple[float, float]:
        """The outline's ``(width, height)`` in mm, or ``(0, 0)`` if undrawn."""

    @property
    @abstractmethod
    def layers(self) -> tuple[str, ...]:
        """Every copper layer, front to back: ``("F.Cu", "In1.Cu", "B.Cu")``.

        Copper goes on one of these and nowhere else. A track named onto a
        layer that does not exist is a silent no-op in some tools and an error
        in others, so the caller wants the real list.
        """

    @abstractmethod
    def set_layers(self, count: int) -> tuple[str, ...]:
        """Set the copper layer count and return the new layers.

        Do this BEFORE routing. Changing it afterwards invalidates the route:
        an inner-layer track on a layer that no longer exists does not move,
        it disappears.
        """

    @abstractmethod
    def save(self, *, validate: bool = False) -> Path:
        """Write the board to disk and return its path.

        When *validate* is true, the implementation must prove that its native
        application can load the serialized result before replacing the
        destination. Rule violations do not make a structurally valid design
        unsavable.
        """

    @abstractmethod
    def set_stackup(self, stackup: Stackup) -> Stackup:
        """Replace the physical stackup and return what the board records.

        Layer order, materials and dimensions are caller decisions. The
        implementation verifies that the copper entries agree with the
        board's actual copper-layer table and updates total board thickness.
        """

    @abstractmethod
    def stackup(self) -> Stackup:
        """The board's physical layer construction and fabrication options."""

    @property
    @abstractmethod
    def thickness(self) -> float:
        """The finished board thickness stated by the design, in mm."""

    @abstractmethod
    def set_limits(self, limits: BoardLimits) -> BoardLimits:
        """Set explicitly supplied board-wide manufacturing limits."""

    @abstractmethod
    def limits(self) -> BoardLimits:
        """Read every supported board-wide manufacturing limit."""

    @abstractmethod
    def set_net_classes(self, classes: tuple[NetClass, ...]) -> list[NetClass]:
        """Create or replace named routing classes and return those classes."""

    @abstractmethod
    def net_classes(self) -> list[NetClass]:
        """Every routing class stored with the board's project."""

    @abstractmethod
    def assign_net_classes(
            self, assignments: tuple[NetClassAssignment, ...]
    ) -> list[NetClassAssignment]:
        """Replace each named net's netclass memberships and return them."""

    @abstractmethod
    def net_class_assignments(self) -> list[NetClassAssignment]:
        """Every explicit net-to-netclass assignment in the project."""

    @abstractmethod
    def set_rules(self, rules: tuple[BoardRule, ...]) -> list[BoardRule]:
        """Create or replace named conditional design rules."""

    @abstractmethod
    def rules(self) -> list[BoardRule]:
        """Every supported numeric custom design rule for the board."""

    @abstractmethod
    def graphic(self, kind: str, points: list[tuple[float, float]], *,
                layer: str, width: float = 0.1,
                fill: bool = False) -> Graphic:
        """Draw one non-copper shape and return its identity and geometry.

        Supported primitives are ``line``, ``arc``, ``circle``, ``rectangle``
        and ``polygon``. Their defining points are respectively 2, 3, 2, 2,
        and 3 or more. Layers are ``Edge.Cuts``, ``F.SilkS`` and ``B.SilkS``.

        A complex outline is caller-supplied lines and arcs whose endpoints
        meet. A closed circle, rectangle or polygon is one contour. Nothing
        here decides whether a contour is an outside edge or an internal
        cutout.
        """

    @abstractmethod
    def graphics(self, layer: str = "") -> list[Graphic]:
        """Every non-copper shape, optionally restricted to one layer."""

    @abstractmethod
    def outline_polygon(self, *, inset: float, max_error: float) -> tuple[Point, ...]:
        """The single outside board contour as polygon points.

        Curves are approximated so their maximum chord error is no greater
        than *max_error*.  *inset* moves the contour inward.  Both values are
        explicit because the required copper-to-edge distance and geometric
        fidelity are caller decisions; interpreting the outline is a fact the
        backend must provide.

        Raises:
            ValueError: If the outline is open, ambiguous, collapses under the
                inset, or either numeric argument is invalid.
        """

    @abstractmethod
    def move_graphic(self, uuid: str, dx: float, dy: float) -> Graphic:
        """Shift one shape by an offset and return its new geometry."""

    @abstractmethod
    def remove_graphic(self, uuid: str) -> None:
        """Remove one shape by the UUID returned when it was drawn."""

    # -- the library ------------------------------------------------------

    @abstractmethod
    def find_footprints(self, query: str, limit: int = 20) -> list[FootprintDef]:
        """Library footprints matching *query*, by ``Library:Footprint`` id."""

    @abstractmethod
    def footprint_def(self, fp_id: str) -> FootprintDef:
        """One library footprint, with its pads at the footprint origin.

        Ask this BEFORE placing, to know how much room a part needs. The
        courtyard is the number that matters -- not the bounding box, which
        includes silkscreen, and not the pad extent, which excludes the body.

        Raises:
            LookupError: If no library supplies *fp_id*.
        """

    # -- parts ------------------------------------------------------------

    @abstractmethod
    def place(self, fp_id: str, ref: str, x: float, y: float, *,
              rotation: float = 0.0, side: str = "F",
              value: str = "") -> Footprint:
        """Put *fp_id* on the board at ``(x, y)`` as *ref*.

        The returned footprint carries its pads at their **board** positions,
        so the next call can route to them without any further arithmetic.

        ``(x, y)`` is the footprint's ORIGIN, which may sit on pad 1 rather
        than in the middle of the part. Use the returned
        *courtyard_offset* to place by centre instead.

        Raises:
            LookupError: If no library supplies *fp_id*.
            ValueError: If *ref* is already on the board, or *side* is not
                ``"F"`` or ``"B"``.
        """

    @abstractmethod
    def move(self, ref: str, x: float, y: float) -> Footprint:
        """Move a placed footprint. Pads move with it; copper does not."""

    @abstractmethod
    def rotate(self, ref: str, rotation: float) -> Footprint:
        """Set a placed footprint's rotation in degrees.

        Any angle: a board is not on a 90-degree grid the way a schematic is,
        and a connector at 45 degrees is ordinary.
        """

    @abstractmethod
    def flip(self, ref: str, side: str) -> Footprint:
        """Put a footprint on ``"F"`` or ``"B"``.

        Flipping MIRRORS the part: its pads run the other way. A caller that
        keeps the old pad positions after a flip routes to where they were.
        """

    @abstractmethod
    def remove(self, ref: str) -> None:
        """Take a footprint off the board."""

    @abstractmethod
    def footprints(self) -> list[Footprint]:
        """Every placed footprint, in reference order."""

    @abstractmethod
    def footprint(self, ref: str) -> Footprint:
        """One placed footprint.

        Raises:
            LookupError: If *ref* is not on the board.
        """

    @abstractmethod
    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a footprint, by name."""

    @abstractmethod
    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a footprint's fields and return all of them.

        ``Reference`` and ``Value`` are the two KiCad draws on silkscreen;
        ``Datasheet``, ``LCSC`` and any custom name work the same way and are
        carried for the fab rather than printed.
        """

    @abstractmethod
    def move_field(self, ref: str, name: str, dx: float, dy: float, *,
                   rotation: float | None = None, layer: str = "",
                   hide: bool | None = None) -> Point:
        """Move a footprint's field, relative to the footprint's position.

        A designator is placed by the LIBRARY, which cannot know what ends up
        beside it: on a dense board they land on their own part, on a
        neighbour, or on a pad. Rotating a part turns its designator with it,
        so a row of parts at four angles gets four differently-slanted labels
        over the top of them.

        *dx*/*dy* are an offset from the footprint, so a field follows its
        part when the part moves. *layer* moves it between silkscreen and
        fab; *hide* takes it off the board without deleting it, which is what
        a 0402 usually wants.

        Returns where the field now sits on the board.
        """

    @abstractmethod
    def set_net(self, ref: str, pad: str, net: str) -> str:
        """Put *ref*'s *pad* on *net*, and return the net.

        A library footprint carries no nets -- it is a land pattern, not a
        circuit -- so a board built by placing parts has none either, and
        without them it is geometry: :meth:`nets` is empty, :meth:`unrouted`
        has nothing to compare, a pour connects to nothing, and DRC reports
        every track as shorting an unnamed net to a named one.

        Which pad is on which net is a FACT that comes from the schematic,
        not a board decision. The caller reads it from there -- `Sheet.nets()`
        gives exactly ``(ref, pad, net)`` -- and applies it here. That is why
        this takes one pad rather than a netlist file: the two contracts
        compose, and neither has to know about the other's format.

        Raises:
            LookupError: If the footprint or the pad does not exist.
        """

    @abstractmethod
    def pad(self, ref: str, pad: str) -> Point:
        """Where *ref*'s *pad* is on the board -- the point to route to.

        This is the call that makes the rest usable: it applies the
        footprint's rotation and side so the caller never has to.

        Raises:
            LookupError: If the footprint or the pad does not exist.
        """

    # -- copper -----------------------------------------------------------

    @abstractmethod
    def track(self, x1: float, y1: float, x2: float, y2: float, *,
              layer: str, width: float, net: str = "") -> Track:
        """Lay one straight copper segment and return it.

        One segment on one layer. A corner is two calls and a layer change is
        a via -- deliberately, because where a track turns and where it
        changes layer are routing decisions and belong to the caller.
        """

    @abstractmethod
    def via(self, x: float, y: float, *, net: str = "",
            diameter: float = 0.6, drill: float = 0.3,
            layers: tuple[str, str] = ("F.Cu", "B.Cu")) -> Via:
        """Drill a plated via joining *layers* and return it."""

    @abstractmethod
    def zone(self, points: list[tuple[float, float]], *, layer: str,
             net: str = "", clearance: float = 0.0,
             forbids: tuple[str, ...] = ()) -> Zone:
        """Pour copper inside *points* on *layer*, tied to *net*.

        With *forbids* it is a keep-out instead: a region that refuses
        ``tracks``, ``vias``, ``pads``, ``pours`` or ``footprints``.

        A pour is not filled until :meth:`refill`. An unfilled zone is an
        outline that connects nothing and renders as almost nothing.
        """

    @abstractmethod
    def refill(self) -> int:
        """Recompute every pour against the copper as it now stands.

        Returns the number filled. Tracks laid after a pour do not update it,
        so a board looks poured while the fill still hugs the old routing.
        """

    @abstractmethod
    def text(self, x: float, y: float, text: str, *, layer: str,
             size: float = 1.0, rotation: float = 0.0,
             mirror: bool = False) -> Point:
        """Put text on a layer -- a legend, a fab note, a designator."""

    @abstractmethod
    def remove_copper(self, *, net: str = "", layer: str = "",
                      tracks: bool = True, vias: bool = True) -> int:
        """Delete copper, filtered by net and layer, and say how much went.

        The undo for routing. Filters are AND-ed and an empty one matches
        everything, so calling this with no arguments strips the board.
        """

    # -- reading back -----------------------------------------------------

    @abstractmethod
    def tracks(self) -> list[Track]:
        """Every copper segment on the board."""

    @abstractmethod
    def vias(self) -> list[Via]:
        """Every via on the board."""

    @abstractmethod
    def zones(self) -> list[Zone]:
        """Every pour and keep-out."""

    @abstractmethod
    def nets(self) -> list[Net]:
        """What the board is MEANT to connect, from its netlist.

        Intent, not fact. Whether copper actually joins these pads is
        :meth:`unrouted`, and the two disagreeing is the normal state of a
        board mid-layout.
        """

    @abstractmethod
    def unrouted(self) -> list[Connection]:
        """Every pair of pads on a net with no copper between them.

        The work remaining, named rather than counted. A count says how much
        is left; this says which, so a caller can route one.
        """

    @abstractmethod
    def check(self) -> list[Finding]:
        """Every rule violation, named by part and pad.

        The tool reports positions. A position means nothing until it is
        looked up against every pad on the board, so this does that lookup and
        returns findings a caller can act on: ``R1.2 clearance to U1.7``
        rather than ``something at (25.46, 10.45)``.
        """

    @abstractmethod
    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What is at a point: which pads, track ends, vias and zones meet.

        The one query worth having while routing, because it answers the only
        question that matters -- *is this actually connected?*
        """

    @abstractmethod
    def render(self, output_file: str | Path, *, side: str = "top",
               width: int = 1200, height: int = 1200,
               quality: str = "basic", background: str = "opaque",
               zoom: float = 1.0, rotate: str = "",
               perspective: bool = False, floor: bool = False,
               pan: str = "", pivot: str = "") -> Path:
        """Render the board in 3D and return the PNG or JPEG image.

        The other half of :meth:`check`. That one has the rule answer and
        cannot see a part 6 mm from where you put it, a block of passives
        piled in one corner, or an outline that renders as one piece and would
        mill as three. Camera placement and appearance are caller decisions;
        this method passes them to the backend renderer unchanged.
        """


__all__ = ["Board"]
