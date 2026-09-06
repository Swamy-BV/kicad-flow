"""The whole schematic contract: sixteen primitives, and nothing above them.

This interface is the only thing the MCP server talks to. It names no file
format and no tool -- :class:`~kicad_flow.backend.kicad.KiCadSheet` is one
implementation and another could serve the same calls.

**What is deliberately absent.** There is no autoplacer, no router, no
floorplanner, no block recovery, no design document, no legaliser. Those all
existed and were removed. Each one decided something -- where a part goes, which
way a wire runs, what belongs beside what -- and a caller that disagreed had no
way to say so, because the decision was inside the algorithm rather than in the
call. What is left cannot decide anything: it places a part where it is told and
reports where the pins landed.

The one service it does provide is **arithmetic**: :meth:`Sheet.pin` gives the
sheet position of a pin with the part's rotation and mirroring already applied.
That is not a decision, it is a fact about the geometry, and it is the fact a
caller most needs and most easily gets wrong -- a wire drawn to the position a
pin would have had if the part were unrotated looks connected and is not.

So the division is: **this layer knows where things are, the caller decides
where they should be.**
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .types import Finding, Label, Net, Part, Point, SheetRef, SymbolDef

#: KiCad's schematic grid. Every position a caller gives is snapped to it,
#: because a wire end and a pin that differ by a fraction of a millimetre are
#: not connected and nothing on the sheet says so.
GRID = 1.27


def snap(value: float, grid: float = GRID) -> float:
    """*value* moved to the nearest grid line."""
    return round(value / grid) * grid


class Sheet(ABC):
    """One schematic sheet, open for editing.

    Every mutator returns what it made, so a caller can chain without a
    lookup, and every position argument is in millimetres from the top-left.
    Nothing is written to disk until :meth:`save`.
    """

    # -- the sheet itself -------------------------------------------------

    @property
    @abstractmethod
    def path(self) -> Path:
        """Where this sheet will be written."""

    @property
    @abstractmethod
    def size(self) -> tuple[float, float]:
        """The drawable ``(width, height)`` in mm, inside the title block."""

    @property
    @abstractmethod
    def uuid(self) -> str:
        """This sheet's own identifier, needed to place it in a hierarchy."""

    @abstractmethod
    def add_sheet(self, name: str, filename: str, x: float, y: float, *,
                  width: float = 38.1, height: float = 25.4,
                  ports: tuple[tuple[str, str], ...] = ()) -> SheetRef:
        """Put a child sheet on this one, and return where its ports landed.

        A hierarchy is two halves that meet by NAME: a port here, and a
        hierarchical label of the same name inside the child. Nothing checks
        the pairing while you draw -- a port with no matching label is simply
        an unconnected pin, which is what `check` is for.

        Args:
            name: The sheet's name, shown above the box.
            filename: The child's file, e.g. ``"power.kicad_sch"``.
            x: Top-left corner of the box.
            y: Top-left corner of the box.
            width: Box width in mm.
            height: Box height in mm.
            ports: ``(name, kind)`` pairs, where kind is ``input``,
                ``output``, ``bidirectional``, ``tri_state`` or ``passive``.
                They are spread down the left edge on the grid; move them
                afterwards if that is not where they belong.

        Returns:
            A :class:`SheetRef`. Its *pins* carry sheet positions to wire to,
            and its *instance_path* is what the child must be created with.
        """

    @abstractmethod
    def save(self, *, validate: bool = False) -> Path:
        """Write the sheet to disk and return its path.

        When *validate* is true, the implementation must prove that its native
        application can load the serialized result before replacing the
        destination. Rule violations do not make a structurally valid design
        unsavable.
        """

    # -- the library ------------------------------------------------------

    @abstractmethod
    def find_symbols(self, query: str, limit: int = 20) -> list[SymbolDef]:
        """Library symbols matching *query*, by id, description or keyword."""

    @abstractmethod
    def symbol(self, lib_id: str, *, unit: int = 1) -> SymbolDef:
        """One unit of a symbol, with its pins at the symbol origin.

        A multi-unit symbol -- an LM358 is two op-amps plus a shared power
        unit -- is asked for a unit at a time. Reporting them all together
        puts two units' pins at identical coordinates, which is a wrong
        netlist rather than a messy drawing. ``SymbolDef.units`` says how
        many there are.

        Raises:
            LookupError: If no library supplies *lib_id*.
        """

    # -- parts ------------------------------------------------------------

    @abstractmethod
    def place(self, lib_id: str, ref: str, x: float, y: float, *,
              value: str = "", rotation: float = 0.0, mirror: str = "",
              unit: int = 1) -> Part:
        """Put one unit of *lib_id* on the sheet at ``(x, y)`` as *ref*.

        The returned part carries its pins at their **sheet** positions, so the
        next call can wire to them without any further arithmetic.

        Units of one part share a reference and are placed one call at a time,
        wherever each belongs on the sheet -- so ``(ref, unit)`` identifies a
        placed thing, and every call below that takes a *ref* takes a *unit*
        alongside it.

        *rotation* is a quarter turn -- 0, 90, 180 or 270. Anything else is
        refused, because KiCad will not open a sheet holding a symbol at any
        other angle, and a call that accepted one reported success and left
        the fault to whatever opened the file next.

        Raises:
            LookupError: If no library supplies *lib_id*.
            ValueError: If that unit of *ref* is already placed, or if
                *rotation* is not a quarter turn.
        """

    @abstractmethod
    def move(self, ref: str, x: float, y: float, *,
             unit: int = 1) -> Part:
        """Move a placed part to ``(x, y)``. Pins move with it."""

    @abstractmethod
    def rotate(self, ref: str, rotation: float, *,
               unit: int = 1) -> Part:
        """Set a placed part's rotation, in degrees.

        Raises:
            ValueError: If *rotation* is not 0, 90, 180 or 270. See `place`.
        """

    @abstractmethod
    def mirror(self, ref: str, axis: str, *, unit: int = 1) -> Part:
        """Mirror a placed part about ``"x"``, ``"y"``, or ``""`` for neither."""

    @abstractmethod
    def remove(self, ref: str, *, unit: int = 1) -> None:
        """Take a part off the sheet."""

    @abstractmethod
    def parts(self) -> list[Part]:
        """Every placed part, in reference order."""

    @abstractmethod
    def part(self, ref: str, *, unit: int = 1) -> Part:
        """One placed unit.

        Raises:
            LookupError: If that unit of *ref* is not on the sheet.
        """

    @abstractmethod
    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a part's fields and return all of them.

        ``Footprint`` is the field the board export reads; ``Value``,
        ``Datasheet``, ``MPN`` and any custom name work the same way. There is
        no separate footprint call because a footprint is not a special thing
        here -- it is a field with a name the next tool happens to read.
        """

    @abstractmethod
    def move_field(self, ref: str, name: str, dx: float, dy: float, *,
                   rotation: float | None = None,
                   justify: str = "") -> Point:
        """Move one of a part's fields, relative to the part's position.

        Fields are placed automatically when a part is placed or turned, on
        the side its wires do not leave from. That is a default, not a rule:
        on a crowded sheet two parts' labels can still meet, and only the
        caller knows which one should move.

        Args:
            ref: The part.
            name: Field name -- ``"Reference"``, ``"Value"``, or any other.
            dx: Offset from the part's own position, in mm.
            dy: Offset from the part's own position, in mm.
            rotation: Text angle; ``None`` leaves it alone.
            justify: ``"left"``, ``"right"``, or empty to centre.

        Returns:
            Where the field now sits on the sheet.
        """

    @abstractmethod
    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a part, by name."""

    @abstractmethod
    def pin(self, ref: str, pin: str) -> Point:
        """Where *ref*'s *pin* is on the sheet -- the point to wire to.

        *pin* may be its number or its name. This is the call that makes the
        rest usable: it applies the part's rotation and mirroring so the caller
        never has to.

        Raises:
            LookupError: If the part or the pin does not exist.
        """

    # -- connections ------------------------------------------------------

    @abstractmethod
    def wire(self, x1: float, y1: float, x2: float, y2: float) -> list[Point]:
        """Draw wire from one point to another and return its end points.

        One straight segment. A corner is two calls -- deliberately, because
        choosing where a wire turns is a drawing decision and belongs to the
        caller.
        """

    @abstractmethod
    def junction(self, x: float, y: float) -> Point:
        """Mark a point where crossing wires connect."""

    @abstractmethod
    def label(self, x: float, y: float, text: str, *, kind: str = "local",
              rotation: float = 0.0, justify: str = "left") -> Label:
        """Attach a net name at a point.

        Args:
            x: Position in mm.
            y: Position in mm.
            text: The net name.
            kind: ``"local"`` (this sheet), ``"global"`` (the whole design),
                or ``"hierarchical"`` (a port on this sheet's symbol).
            rotation: Degrees; any angle, unlike a part's. Only meaningful
                for a VERTICAL label (90, 270).
                A global label drawn horizontally renders identically at 0 and
                180 -- rotation turns the box with the text, so it cannot make
                the flag point the other way.
            justify: ``"left"`` or ``"right"``. THIS is what points a global
                or hierarchical label. ``right`` puts the flag's tip on the
                right and grows the box leftward, which is what a label on a
                part's LEFT-hand pins needs -- the wire arrives from the right
                and must not run through the text. ``left`` is the mirror of
                it, for a part's right-hand pins.

                A local label is text sitting on a wire rather than a box, so
                it also takes ``bottom`` and reads above the wire.
        """

    @abstractmethod
    def power(self, x: float, y: float, net: str, *,
              rotation: float = 0.0) -> Part:
        """Place a power symbol for *net* (``GND``, ``+3V3``, ...).

        Returns the placed part, whose single pin is where to wire to.

        Raises:
            ValueError: If *rotation* is not a quarter turn. See `place`.
        """

    @abstractmethod
    def power_flag(self, x: float, y: float, *, rotation: float = 0.0) -> Part:
        """Place a PWR_FLAG, which tells ERC a net is driven.

        Raises:
            ValueError: If *rotation* is not a quarter turn. See `place`.
        """

    @abstractmethod
    def no_connect(self, x: float, y: float) -> Point:
        """Mark a pin deliberately unconnected."""

    # -- reading back -----------------------------------------------------

    @abstractmethod
    def wires(self) -> list[tuple[Point, Point]]:
        """Every wire segment on the sheet."""

    @abstractmethod
    def labels(self) -> list[Label]:
        """Every label, including the stable identity used to edit it."""

    @abstractmethod
    def nets(self) -> list[Net]:
        """What this sheet ACTUALLY connects, read back from the tool.

        Not what the caller believes it drew. A schematic can be a valid file
        that opens and renders correctly while its wires join nothing, and
        nothing else here can tell the difference -- `at` answers a point at a
        time, and knowing which point to ask about is the hard part.

        Nets are returned in name order, power and named nets included.
        """

    @abstractmethod
    def check(self) -> list[Finding]:
        """Every rule violation on the sheet, named by part and pin.

        The tool reports positions. A position means nothing until it is
        looked up against every pin on the sheet, so this does that lookup and
        returns findings a caller can act on: ``U1.20 VCCIO is not driven``
        rather than ``something at (175.26, 93.98)``.
        """

    @abstractmethod
    def render(self, *, output_dir: Path | None = None, dpi: int = 150,
               black_and_white: bool = False,
               pages: str | None = None) -> list[Path]:
        """Draw the sheet, one image per page, and return the files.

        The other half of `check`. That one has the electrical answer and
        cannot see a label printed over a pin number, a power symbol through a
        net name, or a page that is correct and unreadable. Every readability
        fault found in this project was found by looking at this.

        Renders what is ON DISK, so `save` first. Args are ``output_dir``
        (default: beside the sheet), ``dpi``, ``black_and_white``, and
        ``pages`` as the backend's page selector or None for all.

        Raises:
            RuntimeError: If the drawing tool fails.
        """

    @abstractmethod
    def next_ref(self, prefix: str) -> str:
        """The next unused reference with this prefix, e.g. ``"R"`` -> ``"R7"``.

        This is all the annotation this API needs. `place` demands a reference
        and refuses a duplicate, so a sheet cannot end up unannotated or
        double-annotated the way an imported one can; what a caller actually
        wants is not to keep a counter.
        """

    @abstractmethod
    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What is at a point: which pins, wire ends and labels meet there.

        The one query worth having, because it answers the only question that
        matters while drawing -- *is this actually connected?*
        """

    @abstractmethod
    def text(self, x: float, y: float, text: str, *, size: float = 1.27,
             rotation: float = 0.0, bold: bool = False,
             justify: str = "left") -> Point:
        """Write a note on the sheet, and return where it landed.

        Plain text and nothing else: it names no net, joins nothing, and ERC
        never sees it. That is the difference from `label`, which looks the
        same on the page and is the thing that CONNECTS. Use this for the
        notes a reader needs and the netlist must not have -- a revision
        block, a derivation, "all VBAT caps 50 V".
        """

    # -- editing what is already drawn --------------------------------------
    #
    # A part is named by its ref. Nothing else on a sheet has a name, so a
    # wire, a label, a junction and a no-connect are addressed by WHERE THEY
    # ARE -- which is what `wires`, `labels` and the reply to the call that
    # drew them already report. Coordinates are snapped, so a point read back
    # from this layer matches exactly; one a caller worked out itself may not.
    # Every call below returns HOW MANY it found, so doing nothing cannot be
    # mistaken for success.

    @abstractmethod
    def remove_wire(self, x1: float, y1: float, x2: float, y2: float) -> int:
        """Delete wires running between these two points, and say how many.

        Either direction matches: a segment does not know which end was drawn
        first.
        """

    @abstractmethod
    def move_wire(self, x1: float, y1: float, x2: float, y2: float,
                  dx: float, dy: float) -> int:
        """Shift wires between these points by ``(dx, dy)``, and say how many.

        Both ends move together, so the segment keeps its length and its
        angle. A wire moved off a pin is no longer joined to it and nothing on
        the sheet says so -- `nets` is what says so.
        """

    @abstractmethod
    def remove_label(self, x: float, y: float) -> int:
        """Delete labels at this point, of any kind, and say how many."""

    @abstractmethod
    def remove_label_by_id(self, uuid: str) -> None:
        """Delete exactly one label by the identity returned when it was added."""

    @abstractmethod
    def move_label(self, x: float, y: float, dx: float, dy: float) -> int:
        """Shift labels at this point by ``(dx, dy)``, and say how many.

        A label names the net it TOUCHES. Move one off its wire and it names
        nothing, quietly.
        """

    @abstractmethod
    def move_label_by_id(self, uuid: str, dx: float, dy: float) -> Label:
        """Shift exactly one label by identity and return its new geometry."""

    @abstractmethod
    def rotate_label(self, x: float, y: float, rotation: float) -> int:
        """Turn labels at this point, and say how many.

        Rotation is meaningful at 90 and 270; a horizontal label reads the
        same at 0 and 180. Which way a global label POINTS is its
        justification, not its rotation.
        """

    @abstractmethod
    def rotate_label_by_id(self, uuid: str, rotation: float) -> Label:
        """Turn exactly one label by identity and return its new geometry."""

    @abstractmethod
    def remove_junction(self, x: float, y: float) -> int:
        """Delete junctions at this point, and say how many.

        Removing one separates wires that cross there into different nets.
        """

    @abstractmethod
    def remove_no_connect(self, x: float, y: float) -> int:
        """Delete no-connect marks at this point, and say how many.

        A no-connect suppresses an ERC error; taking one off lets a real fault
        be reported again.
        """

    @abstractmethod
    def move_sheet(self, name: str, x: float, y: float) -> SheetRef:
        """Move a child-sheet box, and say where its ports ended up.

        The box moves; the child file and its ``instance_path`` do not, so
        nothing downstream has to be rebuilt.

        Raises:
            LookupError: If no child sheet is named *name*.
        """

    @abstractmethod
    def remove_sheet(self, name: str) -> None:
        """Take a child-sheet box off this sheet.

        The child FILE is left alone. This removes the box that refers to it,
        so the design stops walking into that page.

        Raises:
            LookupError: If no child sheet is named *name*.
        """

    @abstractmethod
    def remove_field(self, ref: str, name: str, *,
                     unit: int = 1) -> dict[str, str]:
        """Delete a field from a part and return the fields it has left.

        Setting a field to an empty string is a different thing: it stays
        present and blank, KiCad keeps writing it, and a BOM still sees the
        column.

        Raises:
            LookupError: If the part or the field is not there.
        """


__all__ = ["GRID", "Sheet", "snap"]
