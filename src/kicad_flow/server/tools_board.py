"""MCP tools: the board primitives, one tool per primitive.

The board counterpart of :mod:`.tools_schematic`, and the same shape. Every
repeatable write takes a typed list and returns what it made -- notably, every
placement returns the **pad positions**, so the next call can route to them
without a lookup and without repeating the rotation arithmetic.

There is no autoplacer, no router, no via stitcher and no fanout. Those existed
and decided things a caller could not override. What is left cannot decide
anything: it puts a footprint where it is told and reports where the pads
landed.

Boards are held open in memory, keyed by path, so a session is a sequence of
small calls rather than a re-parse each time. `save_board` writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..backend import create_board, load_board
from ..pcb.api import Board
from ..pcb.types import (
    BoardLimits,
    BoardRule,
    Constraint,
    NetClass,
    NetClassAssignment,
    Stackup,
    StackupLayer,
)
from . import _meta
from ._app import mcp

#: Boards currently open, by absolute path.
_OPEN: dict[str, Board] = {}


def _key(path: str) -> str:
    """The dictionary key for a board path."""
    return str(Path(path).resolve())


def _board(path: str) -> Board:
    """The open board for *path*, loading it from disk if need be."""
    key = _key(path)
    if key not in _OPEN:
        if not Path(path).is_file():
            raise LookupError(
                f"no file at {path}. An existing .kicad_pcb "
                f"reopens by itself -- just name it. Use `new_board` only to "
                f"create one, which OVERWRITES whatever is there.")
        _OPEN[key] = load_board(path)
    return _OPEN[key]


def _fail(exc: Exception) -> dict[str, Any]:
    """A refusal that says what went wrong."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


_ERRORS = (LookupError, ValueError, OSError, RuntimeError)


def _partial(exc: Exception, index: int, key: str,
             done: list[Any]) -> dict[str, Any]:
    """A refusal that identifies the failed item and prior applied results."""
    return {**_fail(exc), "index": index, key: done}


class _GraphicBase(BaseModel):
    """Fields shared by every outline and silkscreen primitive."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["Edge.Cuts", "F.SilkS", "B.SilkS"] = Field(
        description="Board outline or front/back silkscreen layer.")
    width: float = Field(default=0.1, description="Stroke width in mm.")


class LineGraphic(_GraphicBase):
    """One straight graphical segment."""

    kind: Literal["line"]
    x1: float
    y1: float
    x2: float
    y2: float


class ArcGraphic(_GraphicBase):
    """One unambiguous circular arc through start, mid and end."""

    kind: Literal["arc"]
    x1: float
    y1: float
    xm: float
    ym: float
    x2: float
    y2: float


class CircleGraphic(_GraphicBase):
    """One circle by centre and radius."""

    kind: Literal["circle"]
    x: float
    y: float
    radius: float
    fill: bool = False


class RectangleGraphic(_GraphicBase):
    """One axis-aligned rectangle by opposite corners."""

    kind: Literal["rectangle"]
    x1: float
    y1: float
    x2: float
    y2: float
    fill: bool = False


class PolygonGraphic(_GraphicBase):
    """One closed polygon."""

    kind: Literal["polygon"]
    points: list[list[float]] = Field(
        description="Closed polygon as [[x, y], ...], with at least 3 points.")
    fill: bool = False


GraphicSpec = Annotated[
    LineGraphic | ArcGraphic | CircleGraphic | RectangleGraphic | PolygonGraphic,
    Field(discriminator="kind"),
]


class GraphicMove(BaseModel):
    """One graphical primitive and the offset to apply."""

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(description="Identity returned by add/list_graphics.")
    dx: float = Field(description="Horizontal offset in mm.")
    dy: float = Field(description="Vertical offset in mm.")


class NewFootprint(BaseModel):
    """One footprint placement."""

    fp_id: str = Field(description="Library footprint id.")
    ref: str = Field(description="Reference designator, e.g. R1.")
    x: float = Field(description="Footprint origin X in mm.")
    y: float = Field(description="Footprint origin Y in mm.")
    rotation: float = Field(default=0.0, description="Any angle in degrees.")
    side: str = Field(default="F", description="F or B.")
    value: str = Field(default="", description="Value field.")


class FootprintMove(BaseModel):
    """One footprint's new absolute position."""

    ref: str = Field(description="Reference designator to move.")
    x: float = Field(description="New absolute footprint-origin X in mm.")
    y: float = Field(description="New absolute footprint-origin Y in mm.")


class FootprintTurn(BaseModel):
    """One footprint's new absolute rotation."""

    ref: str = Field(description="Reference designator to rotate.")
    rotation: float = Field(description="New absolute angle in degrees.")


class FootprintFlip(BaseModel):
    """One footprint's requested board side."""

    ref: str = Field(description="Reference designator to flip.")
    side: str = Field(description="F or B.")


class PadNet(BaseModel):
    """One pad-to-net assignment."""

    ref: str = Field(description="Reference designator containing the pad.")
    pad: str = Field(description="Pad number or name.")
    net: str = Field(description="Exact net name to assign.")


class FootprintFieldValue(BaseModel):
    """One footprint field value."""

    ref: str = Field(description="Reference designator containing the field.")
    name: str = Field(description="Field name, e.g. Value or LCSC.")
    value: str = Field(description="New field value.")


class FootprintFieldShift(BaseModel):
    """One footprint field placement."""

    ref: str = Field(description="Reference designator containing the field.")
    name: str = Field(description="Field name, e.g. Reference.")
    dx: float = Field(description="X offset from the footprint origin in mm.")
    dy: float = Field(description="Y offset from the footprint origin in mm.")
    rotation: float | None = Field(
        default=None, description="Absolute text angle, or preserve when omitted.")
    layer: str = Field(
        default="", description="New layer, or preserve when empty.")
    hide: bool | None = Field(
        default=None, description="Visibility override, or preserve when omitted.")


class NewTrack(BaseModel):
    """One straight copper segment."""

    x1: float = Field(description="Start X in mm.")
    y1: float = Field(description="Start Y in mm.")
    x2: float = Field(description="End X in mm.")
    y2: float = Field(description="End Y in mm.")
    layer: str = Field(description="Copper layer name.")
    width: float = Field(description="Track width in mm.")
    net: str = Field(default="", description="Exact net name, or empty for none.")


class NewVia(BaseModel):
    """One plated through-via."""

    x: float = Field(description="Centre X in mm.")
    y: float = Field(description="Centre Y in mm.")
    net: str = Field(default="", description="Exact net name, or empty for none.")
    diameter: float = Field(default=0.6, description="Finished diameter in mm.")
    drill: float = Field(default=0.3, description="Drill diameter in mm.")


class NewZone(BaseModel):
    """One copper pour or keep-out polygon."""

    points: list[list[float]] = Field(
        default_factory=list,
        description="Explicit polygon as [[x, y], ...], with at least 3 points.")
    boundary: Literal["points", "board_outline"] = Field(
        default="points",
        description="Use explicit points or derive the boundary from Edge.Cuts.")
    inset: float | None = Field(
        default=None,
        description="Required inward offset in mm for a board_outline boundary.")
    max_error: float = Field(
        default=0.02,
        description="Maximum curve-to-polygon chord error in mm.")
    layer: str = Field(description="Copper layer name.")
    net: str = Field(default="", description="Pour net, or empty for no net.")
    clearance: float = Field(default=0.0, description="Clearance in mm.")
    forbids: list[str] = Field(
        default_factory=list,
        description="For a keep-out: tracks, vias, pads, pours, footprints.")

    @model_validator(mode="after")
    def valid_boundary(self) -> NewZone:
        """Require exactly one explicit, unambiguous boundary source."""
        if self.boundary == "points":
            if len(self.points) < 3:
                raise ValueError("a points boundary needs at least 3 points")
            if self.inset is not None:
                raise ValueError("inset is only valid for a board_outline boundary")
        else:
            if self.points:
                raise ValueError("board_outline boundary cannot also supply points")
            if self.inset is None:
                raise ValueError("board_outline boundary requires an explicit inset")
            if self.inset < 0:
                raise ValueError("board_outline inset cannot be negative")
        if self.max_error <= 0:
            raise ValueError("max_error must be positive")
        return self


class NewBoardText(BaseModel):
    """One text item on a board layer."""

    x: float = Field(description="Anchor X in mm.")
    y: float = Field(description="Anchor Y in mm.")
    text: str = Field(description="Literal text; newlines are preserved.")
    layer: str = Field(description="Board layer name, e.g. F.SilkS.")
    size: float = Field(default=1.0, description="Text height and width in mm.")
    rotation: float = Field(default=0.0, description="Angle in degrees.")
    mirror: bool = Field(default=False, description="Mirror the text.")


class StackupLayerSpec(BaseModel):
    """One explicitly ordered physical or surface stackup layer."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Layer name, e.g. F.Cu or dielectric 1.")
    kind: str = Field(description="Layer type, e.g. copper, core or prepreg.")
    thickness: float | None = Field(
        default=None, description="Layer thickness in mm when applicable.")
    material: str = Field(default="", description="Laminate/material name.")
    epsilon_r: float | None = Field(
        default=None, description="Relative dielectric constant.")
    loss_tangent: float | None = Field(
        default=None, description="Dielectric loss tangent.")
    color: str = Field(default="", description="Optional mask/silkscreen color.")


class NetClassSpec(BaseModel):
    """One named collection of routing dimensions."""

    model_config = ConfigDict(extra="forbid")

    name: str
    clearance: float | None = None
    track_width: float | None = None
    via_diameter: float | None = None
    via_drill: float | None = None
    microvia_diameter: float | None = None
    microvia_drill: float | None = None
    diff_pair_width: float | None = None
    diff_pair_gap: float | None = None
    diff_pair_via_gap: float | None = None


class NetClassAssignmentSpec(BaseModel):
    """One net's membership in a netclass."""

    model_config = ConfigDict(extra="forbid")

    net: str
    net_class: str


class NumericConstraintSpec(BaseModel):
    """One millimetre-valued DRC constraint."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        description="KiCad constraint name, e.g. track_width, skew or length.")
    min: float | None = None
    opt: float | None = None
    max: float | None = None


class BoardRuleSpec(BaseModel):
    """One named custom DRC rule."""

    model_config = ConfigDict(extra="forbid")

    name: str
    condition: str = Field(description="DRC condition selecting rule objects.")
    constraints: list[NumericConstraintSpec]
    layer: str = Field(
        default="", description="Optional board layer, outer or inner selector.")


# -- the board ------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def new_board(path: str, layers: int = 2,
              thickness: float = 1.6) -> dict[str, Any]:
    """Start a new board and open it for editing.

    Nothing is written until `save_board`. Set the layer count HERE: changing
    it after routing invalidates the route, because an inner-layer track on a
    layer that no longer exists does not move, it disappears.

    Args:
        path: Where the board will be written.
        layers: Copper layers -- 2, 4, 6 or 8.
        thickness: Board thickness in mm.

    Returns:
        ``{ok, path, layers}``.
    """
    try:
        board = create_board(path, layers=layers, thickness=thickness)
    except _ERRORS as exc:
        return _fail(exc)
    _OPEN[_key(path)] = board
    return {"ok": True, "path": str(board.path), "layers": list(board.layers)}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def save_board(path: str) -> dict[str, Any]:
    """Write the open board to disk."""
    try:
        board = _board(path)
        written = board.save(validate=True)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "path": str(written),
            "footprints": len(board.footprints()),
            "tracks": len(board.tracks()), "vias": len(board.vias()),
            "zones": len(board.zones())}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_graphics(path: str, graphics: list[GraphicSpec]) -> dict[str, Any]:
    """Draw board outlines and front/back silkscreen art in order.

    Lines, arcs, circles, rectangles and polygons are geometric primitives,
    not shape generators: the caller supplies every coordinate. Join line and
    arc endpoints on ``Edge.Cuts`` to make a complex contour. Closed circles,
    rectangles and polygons make contours by themselves. KiCad decides which
    nested closed contours are cutouts; the API does not guess.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, shape in enumerate(graphics):
        try:
            if isinstance(shape, LineGraphic):
                points = [(shape.x1, shape.y1), (shape.x2, shape.y2)]
                fill = False
            elif isinstance(shape, ArcGraphic):
                points = [(shape.x1, shape.y1), (shape.xm, shape.ym),
                          (shape.x2, shape.y2)]
                fill = False
            elif isinstance(shape, CircleGraphic):
                points = [(shape.x, shape.y),
                          (shape.x + shape.radius, shape.y)]
                fill = shape.fill
            elif isinstance(shape, RectangleGraphic):
                points = [(shape.x1, shape.y1), (shape.x2, shape.y2)]
                fill = shape.fill
            else:
                points = [(p[0], p[1]) for p in shape.points]
                fill = shape.fill
            made = board.graphic(shape.kind, points, layer=shape.layer,
                                 width=shape.width, fill=fill)
        except (IndexError, *_ERRORS) as exc:
            return _partial(exc, i, "graphics", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "graphics": out,
            "size": list(board.size)}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_graphics(path: str, layer: str = "") -> dict[str, Any]:
    """List outline and silkscreen shapes, optionally on one layer."""
    try:
        found = _board(path).graphics(layer)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "graphics": [shape.as_dict() for shape in found]}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_graphics(path: str, moves: list[GraphicMove]) -> dict[str, Any]:
    """Shift graphical primitives by UUID; nothing else follows them."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, move in enumerate(moves):
        try:
            out.append(board.move_graphic(
                move.uuid, move.dx, move.dy).as_dict())
        except _ERRORS as exc:
            return _partial(exc, i, "moved", out)
    return {"ok": True, "count": len(out), "moved": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_graphics(path: str, uuids: list[str]) -> dict[str, Any]:
    """Remove graphical primitives by UUID in order."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[str] = []
    for i, uuid in enumerate(uuids):
        try:
            board.remove_graphic(uuid)
        except _ERRORS as exc:
            return _partial(exc, i, "removed", out)
        out.append(uuid)
    return {"ok": True, "count": len(out), "removed": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_layers(path: str, count: int) -> dict[str, Any]:
    """Set the copper layer count (2, 4, 6 or 8). Do this before routing."""
    try:
        return {"ok": True, "layers": list(_board(path).set_layers(count))}
    except _ERRORS as exc:
        return _fail(exc)


# -- the library ----------------------------------------------------------


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def find_footprint(query: str, limit: int = 20,
                   project_dir: str = "") -> dict[str, Any]:
    """Search the footprint libraries for a land pattern.

    Matched against ``Library:Footprint`` ids -- search by package or family,
    ``"0603"``, ``"LQFP-64"``, ``"PinHeader_1x06"``.
    """
    try:
        found = _blank(project_dir).find_footprints(query, limit=limit)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "footprints": [
        {"fp_id": f.fp_id, "courtyard": list(f.courtyard),
         "pads": len(f.pads), "has_pth": f.has_pth} for f in found]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def footprint_pads(fp_id: str, project_dir: str = "") -> dict[str, Any]:
    """A library footprint's pads and size, before it is placed anywhere.

    Use the COURTYARD to decide how much room to leave -- not the bounding
    box, which includes silkscreen, and not the pad extent, which excludes the
    body. For the positions to actually ROUTE to, place it and read the pads
    `place_footprints` returns.
    """
    try:
        found = _blank(project_dir).footprint_def(fp_id)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **found.as_dict()}


def _blank(project_dir: str = "") -> Board:
    """A throwaway board, for library queries that need no file."""
    directory = Path(project_dir).resolve() if project_dir else Path.cwd()
    return create_board(directory / "_query.kicad_pcb")


# -- parts ----------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def place_footprints(path: str,
                     footprints: list[NewFootprint]) -> dict[str, Any]:
    """Place footprints on the board in order.

    **The returned pads are the point of this call.** Each carries the board
    position to route to, with rotation and side already applied.

    ``(x, y)`` is the footprint's ORIGIN, which on many parts sits at pad 1
    rather than in the middle. The reply's `courtyard_offset` is the vector
    from the origin to the courtyard centre -- add it to place by centre.

    Args:
        path: The open board.
        footprints: Placements. Rotation may be any angle; side is F or B.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(footprints):
        try:
            made = board.place(p.fp_id, p.ref, p.x, p.y,
                               rotation=p.rotation, side=p.side, value=p.value)
        except _ERRORS as exc:
            return _partial(exc, i, "footprints", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "footprints": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_stackup(path: str, layers: list[StackupLayerSpec],
                copper_finish: str = "", dielectric_constraints: bool = False,
                edge_connector: str = "", castellated_pads: bool = False,
                edge_plating: bool = False) -> dict[str, Any]:
    """Set the board's complete ordered physical stackup.

    Copper entries must exactly match the layers reported by `new_board` or
    `set_board_layers`. Include every layer the manufacturer specifies:
    copper, dielectric core/prepreg and optional mask/silkscreen/paste layers.
    No impedance dimensions are inferred from materials or thicknesses.
    """
    try:
        made = _board(path).set_stackup(Stackup(
            layers=tuple(StackupLayer(
                name=item.name, kind=item.kind, thickness=item.thickness,
                material=item.material, epsilon_r=item.epsilon_r,
                loss_tangent=item.loss_tangent, color=item.color)
                for item in layers),
            copper_finish=copper_finish,
            dielectric_constraints=dielectric_constraints,
            edge_connector=edge_connector,
            castellated_pads=castellated_pads,
            edge_plating=edge_plating,
        ))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **made.as_dict()}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_stackup(path: str) -> dict[str, Any]:
    """Read the complete stackup currently stored in the board."""
    try:
        found = _board(path).stackup()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **found.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_limits(
    path: str,
    min_clearance: float | None = None,
    min_track_width: float | None = None,
    min_via_diameter: float | None = None,
    min_via_drill: float | None = None,
    min_annular_width: float | None = None,
    min_hole_clearance: float | None = None,
    min_hole_to_hole: float | None = None,
    min_copper_edge_clearance: float | None = None,
    min_silk_clearance: float | None = None,
    min_text_height: float | None = None,
    min_text_thickness: float | None = None,
    min_groove_width: float | None = None,
    solder_mask_to_copper_clearance: float | None = None,
    min_solder_mask_bridge: float | None = None,
) -> dict[str, Any]:
    """Set explicitly supplied board-wide manufacturing limits.

    Omitted values stay unchanged. These are provider-neutral physical limits,
    not routing decisions; all dimensions are millimetres.
    """
    try:
        made = _board(path).set_limits(BoardLimits(
            min_clearance=min_clearance,
            min_track_width=min_track_width,
            min_via_diameter=min_via_diameter,
            min_via_drill=min_via_drill,
            min_annular_width=min_annular_width,
            min_hole_clearance=min_hole_clearance,
            min_hole_to_hole=min_hole_to_hole,
            min_copper_edge_clearance=min_copper_edge_clearance,
            min_silk_clearance=min_silk_clearance,
            min_text_height=min_text_height,
            min_text_thickness=min_text_thickness,
            min_groove_width=min_groove_width,
            solder_mask_to_copper_clearance=solder_mask_to_copper_clearance,
            min_solder_mask_bridge=min_solder_mask_bridge,
        ))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "limits": made.as_dict()}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_board_limits(path: str) -> dict[str, Any]:
    """Read every supported board-wide manufacturing limit."""
    try:
        found = _board(path).limits()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "limits": found.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_net_classes(path: str, classes: list[NetClassSpec]) -> dict[str, Any]:
    """Create or update named routing classes without changing other classes.

    Omitted dimensions remain unchanged on an existing class. A newly created
    named class may omit dimensions and inherit the project's Default class.
    """
    try:
        made = _board(path).set_net_classes(tuple(NetClass(**item.model_dump())
                                                  for item in classes))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(made),
            "classes": [item.as_dict() for item in made]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_net_classes(path: str) -> dict[str, Any]:
    """List every routing class in the board's project."""
    try:
        found = _board(path).net_classes()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "classes": [item.as_dict() for item in found]}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def assign_net_classes(path: str,
                       assignments: list[NetClassAssignmentSpec]
                       ) -> dict[str, Any]:
    """Assign mentioned nets to classes, preserving all other assignments.

    Repeat a net with different classes to give it multiple memberships.
    Every referenced class must already exist.
    """
    try:
        made = _board(path).assign_net_classes(tuple(
            NetClassAssignment(item.net, item.net_class)
            for item in assignments))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(made),
            "assignments": [item.as_dict() for item in made]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_net_class_assignments(path: str) -> dict[str, Any]:
    """List every explicit net-to-netclass membership in the project."""
    try:
        found = _board(path).net_class_assignments()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "assignments": [item.as_dict() for item in found]}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_constraints(path: str, rules: list[BoardRuleSpec]
                          ) -> dict[str, Any]:
    """Create or replace named numeric custom DRC rules.

    Each condition explicitly selects the objects governed by its constraints.
    Bounds are millimetres. Supported examples include `track_width`,
    `diff_pair_gap`, `diff_pair_uncoupled`, `length`, `skew`, `clearance`,
    `hole_size` and `via_diameter`. Untouched rules and comments are preserved.
    """
    try:
        made = _board(path).set_rules(tuple(BoardRule(
            name=rule.name, condition=rule.condition, layer=rule.layer,
            constraints=tuple(Constraint(
                kind=item.kind, minimum=item.min, optimum=item.opt,
                maximum=item.max) for item in rule.constraints))
            for rule in rules))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(made),
            "rules": [item.as_dict() for item in made]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_constraints(path: str) -> dict[str, Any]:
    """List numeric custom DRC rules from the board's rules document."""
    try:
        found = _board(path).rules()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "rules": [item.as_dict() for item in found]}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprints(path: str,
                    moves: list[FootprintMove]) -> dict[str, Any]:
    """Move footprints. Their pads move with them; copper does not."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, move in enumerate(moves):
        try:
            out.append(board.move(move.ref, move.x, move.y).as_dict())
        except _ERRORS as exc:
            return _partial(exc, i, "moved", out)
    return {"ok": True, "count": len(out), "moved": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def rotate_footprints(path: str,
                      turns: list[FootprintTurn]) -> dict[str, Any]:
    """Turn footprints to absolute rotations. Any angle is valid."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, turn in enumerate(turns):
        try:
            out.append(board.rotate(turn.ref, turn.rotation).as_dict())
        except _ERRORS as exc:
            return _partial(exc, i, "turned", out)
    return {"ok": True, "count": len(out), "turned": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def flip_footprints(path: str,
                    flips: list[FootprintFlip]) -> dict[str, Any]:
    """Put footprints on requested ``"F"`` or ``"B"`` sides.

    Flipping MIRRORS it: the pads run the other way. Anything you routed to
    the old pad positions now goes nowhere -- read them back.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, flip in enumerate(flips):
        try:
            out.append(board.flip(flip.ref, flip.side).as_dict())
        except _ERRORS as exc:
            return _partial(exc, i, "flipped", out)
    return {"ok": True, "count": len(out), "flipped": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def remove_footprints(path: str, refs: list[str]) -> dict[str, Any]:
    """Take footprints off the board in order."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[str] = []
    for i, ref in enumerate(refs):
        try:
            board.remove(ref)
        except _ERRORS as exc:
            return _partial(exc, i, "removed", out)
        out.append(ref)
    return {"ok": True, "count": len(out), "removed": out}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint(path: str, ref: str) -> dict[str, Any]:
    """One placed footprint and its pad positions."""
    try:
        return {"ok": True, **_board(path).footprint(ref).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_footprints(path: str, with_pads: bool = False) -> dict[str, Any]:
    """Every footprint on the board.

    Args:
        path: The open board.
        with_pads: Include every pad position. Off by default -- on a full
            board that is most of the reply.
    """
    try:
        parts = _board(path).footprints()
    except _ERRORS as exc:
        return _fail(exc)
    out = []
    for p in parts:
        d = p.as_dict()
        if not with_pads:
            d["pads"] = len(p.pads)
        out.append(d)
    return {"ok": True, "count": len(out), "footprints": out}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_pad(path: str, ref: str, pad: str) -> dict[str, Any]:
    """Where one pad is on the board -- the point to route to.

    This is the call that makes the rest usable: it applies the footprint's
    rotation and side so you never have to. A track drawn to where a pad
    would have been unrotated looks connected and is not.
    """
    try:
        point = _board(path).pad(ref, pad)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "pad": pad, **point.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_pad_nets(path: str, pads: list[PadNet]) -> dict[str, Any]:
    """Put pads on nets in order.

    A library footprint carries no nets -- it is a land pattern, not a
    circuit. Without them the board is geometry: nothing is unrouted because
    nothing is connected, a plane joins nothing, and DRC calls every track a
    short.

    Which pad is on which net is a fact the SCHEMATIC holds. Read it with
    `list_nets` on the sheet and apply it here, one pad at a time.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, str]] = []
    for i, pad in enumerate(pads):
        try:
            net = board.set_net(pad.ref, pad.pad, pad.net)
        except _ERRORS as exc:
            return _partial(exc, i, "pads", out)
        out.append({"ref": pad.ref, "pad": pad.pad, "net": net})
    return {"ok": True, "count": len(out), "pads": out}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a footprint, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _board(path).fields(ref)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_footprint_fields(path: str,
                         fields: list[FootprintFieldValue]) -> dict[str, Any]:
    """Set footprint fields, e.g. ``Value`` or ``LCSC``."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, field in enumerate(fields):
        try:
            values = board.set_field(field.ref, field.name, field.value)
        except _ERRORS as exc:
            return _partial(exc, i, "fields", out)
        out.append({"ref": field.ref, "fields": values})
    return {"ok": True, "count": len(out), "fields": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprint_fields(
        path: str, moves: list[FootprintFieldShift]) -> dict[str, Any]:
    """Move footprint fields relative to their footprints.

    The library places these and cannot know what ends up beside them. On a
    dense board they land on their own part, a neighbour, or a pad -- and
    turning a part turns its designator with it, so a row of parts at
    different angles gets a row of differently-slanted labels over the top of
    them.

    On fine-pitch passives the reference is wider than the part it names;
    `hide=true` takes it off the silkscreen without losing it.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, move in enumerate(moves):
        try:
            at = board.move_field(
                move.ref, move.name, move.dx, move.dy,
                rotation=move.rotation, layer=move.layer, hide=move.hide)
        except _ERRORS as exc:
            return _partial(exc, i, "moved", out)
        out.append({"ref": move.ref, "field": move.name, **at.as_dict()})
    return {"ok": True, "count": len(out), "moved": out}


# -- copper ---------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_tracks(path: str, tracks: list[NewTrack]) -> dict[str, Any]:
    """Lay straight copper segments in order.

    A corner is two list items and a layer change is a via. That is deliberate:
    where a track turns and where it changes layer are routing decisions.

    Copper on the wrong layer connects nothing, so *layer* is required --
    `new_board` reports which exist.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, track in enumerate(tracks):
        try:
            made = board.track(
                track.x1, track.y1, track.x2, track.y2,
                layer=track.layer, width=track.width, net=track.net)
        except _ERRORS as exc:
            return _partial(exc, i, "tracks", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "tracks": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_vias(path: str, vias: list[NewVia]) -> dict[str, Any]:
    """Drill plated through-vias joining front to back."""
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, via in enumerate(vias):
        try:
            made = board.via(via.x, via.y, net=via.net,
                             diameter=via.diameter, drill=via.drill)
        except _ERRORS as exc:
            return _partial(exc, i, "vias", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "vias": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_zones(path: str, zones: list[NewZone]) -> dict[str, Any]:
    """Add copper pours or keep-out polygons in order.

    A zone can use caller-supplied polygon *points*, or an explicit
    ``boundary="board_outline"`` with an inward *inset*. Curved Edge.Cuts are
    converted to the polygon KiCad zones actually store, within *max_error*;
    the generated points are returned for inspection.

    With *forbids* it is a KEEP-OUT instead -- a region refusing any of
    ``tracks``, ``vias``, ``pads``, ``pours``, ``footprints``.

    A pour is not filled until `refill_zones`. An unfilled zone is an outline
    that connects nothing and renders as almost nothing.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, zone in enumerate(zones):
        try:
            if zone.boundary == "board_outline":
                assert zone.inset is not None
                boundary = board.outline_polygon(
                    inset=zone.inset, max_error=zone.max_error
                )
                points = [(point.x, point.y) for point in boundary]
            else:
                points = [(point[0], point[1]) for point in zone.points]
            made = board.zone(
                points, layer=zone.layer,
                net=zone.net, clearance=zone.clearance,
                forbids=tuple(zone.forbids))
        except (IndexError, *_ERRORS) as exc:
            return _partial(exc, i, "zones", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "zones": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def refill_zones(path: str) -> dict[str, Any]:
    """Recompute every pour against the copper as it now stands.

    Tracks laid after a pour do not update it, so a board looks poured while
    the fill still hugs routing that has moved.
    """
    try:
        return {"ok": True, "filled": _board(path).refill()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_board_texts(path: str,
                    texts: list[NewBoardText]) -> dict[str, Any]:
    """Put texts on board layers -- legends, fab notes, part markings.

    Back-side silkscreen wants ``mirror=true`` or it reads reversed.
    """
    try:
        board = _board(path)
    except _ERRORS as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, note in enumerate(texts):
        try:
            at = board.text(
                note.x, note.y, note.text, layer=note.layer, size=note.size,
                rotation=note.rotation, mirror=note.mirror)
        except _ERRORS as exc:
            return _partial(exc, i, "texts", out)
        out.append({"text": note.text, "layer": note.layer, **at.as_dict()})
    return {"ok": True, "count": len(out), "texts": out}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_copper(path: str, net: str = "", layer: str = "",
                  tracks: bool = True, vias: bool = True) -> dict[str, Any]:
    """Delete copper, filtered by net and layer, and say how much went.

    The undo for routing. Filters are AND-ed and an empty one matches
    everything, so calling this with no arguments strips the board.
    """
    try:
        gone = _board(path).remove_copper(net=net, layer=layer, tracks=tracks,
                                          vias=vias)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "removed": gone}


# -- reading back ---------------------------------------------------------


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_copper(path: str) -> dict[str, Any]:
    """Every track, via and zone on the board."""
    try:
        board = _board(path)
        return {"ok": True,
                "tracks": [t.as_dict() for t in board.tracks()],
                "vias": [v.as_dict() for v in board.vias()],
                "zones": [z.as_dict() for z in board.zones()]}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_nets(path: str) -> dict[str, Any]:
    """What the board is MEANT to connect, from its own pads.

    Intent, not fact. Whether copper actually joins these pads is
    `unrouted_connections`, and the two disagreeing is the normal state of a
    board mid-layout.
    """
    try:
        nets = _board(path).nets()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(nets),
            "nets": [n.as_dict() for n in nets]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def unrouted_connections(path: str, limit: int = 40) -> dict[str, Any]:
    """Every pair of pads on a net with no copper between them.

    The work remaining, NAMED rather than counted, nearest first -- so you can
    route one. A filled plane counts as copper, so pads on a poured net are
    not reported.
    """
    try:
        found = sorted(_board(path).unrouted(), key=lambda c: c.distance)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "connections": [c.as_dict() for c in found[:limit]]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def check_board(path: str) -> dict[str, Any]:
    """Every design-rule violation, named by part and pad.

    Runs DRC and maps each violation from a position back to the pad that
    sits there, so a finding reads ``R1.2 clearance to U1.7`` rather than
    ``something at (25.46, 10.45)``.
    """
    try:
        board = _board(path)
        found = board.check()
        from ._fabrication import profile_findings, read_profile

        profile = read_profile(path)
        if profile is not None:
            found.extend(profile_findings(board, profile))
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True,
            "errors": sum(1 for f in found if f.severity == "error"),
            "warnings": sum(1 for f in found if f.severity == "warning"),
            "findings": [f.as_dict() for f in found]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def what_is_on_board(path: str, x: float, y: float,
                     radius: float = 0.01) -> dict[str, Any]:
    """What is at a point: pads, track ends and vias.

    The one query worth having while routing -- *is this actually connected?*
    """
    try:
        return {"ok": True, **_board(path).at(x, y, radius)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.WRITE)
def render_board(path: str, output_file: str, side: str = "top",
                 width: int = 1200, height: int = 1200,
                 quality: str = "basic", background: str = "opaque",
                 zoom: float = 1.0, rotate: str = "",
                 perspective: bool = False, floor: bool = False,
                 pan: str = "", pivot: str = "") -> dict[str, Any]:
    """Render the board in 3D to a PNG or JPEG you can actually look at.

    LOOK AT THE BOARD. `check_board` has the rule answer and cannot see a
    part 6 mm from where you put it, a designator printed over a pad, a block
    of passives piled in one corner, or an outline that renders as one piece
    and would mill as three. It saves the board first, so the picture is of
    what you have drawn.

    Render BOTH sides -- half the parts are usually on the back, and the
    bottom view is MIRRORED, so left and right swap.

    Args:
        path: The open board.
        output_file: Destination ending in ``.png``, ``.jpg`` or ``.jpeg``.
        side: ``top``, ``bottom``, ``left``, ``right``, ``front`` or ``back``.
        width: Image width in pixels.
        height: Image height in pixels.
        quality: ``basic``, ``high``, ``user`` or ``job_settings``.
        background: ``opaque``, ``transparent`` or ``default``.
        zoom: Camera zoom; 1 fits the board.
        rotate: Board rotation as ``X,Y,Z`` degrees; ``-30,0,25`` isometric.
        perspective: Use perspective instead of orthographic projection.
        floor: Include a floor, shadows and post-processing.
        pan: Camera translation as ``X,Y,Z``.
        pivot: Orbit pivot as ``X,Y,Z`` centimetres from board centre.
    """
    try:
        board = _board(path)
        image = board.render(
            output_file, side=side, width=width, height=height,
            quality=quality, background=background, zoom=zoom, rotate=rotate,
            perspective=perspective, floor=floor, pan=pan, pivot=pivot)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "board": str(board.path), "image": str(image),
            "side": side, "quality": quality, "perspective": perspective}


__all__ = [
    "add_board_texts",
    "add_graphics",
    "add_tracks",
    "add_vias",
    "add_zones",
    "assign_net_classes",
    "check_board",
    "find_footprint",
    "flip_footprints",
    "footprint_pads",
    "get_board_limits",
    "get_footprint",
    "get_footprint_fields",
    "get_pad",
    "get_stackup",
    "list_board_constraints",
    "list_board_nets",
    "list_copper",
    "list_footprints",
    "list_graphics",
    "list_net_class_assignments",
    "list_net_classes",
    "move_footprint_fields",
    "move_footprints",
    "move_graphics",
    "new_board",
    "place_footprints",
    "refill_zones",
    "remove_copper",
    "remove_footprints",
    "remove_graphics",
    "render_board",
    "rotate_footprints",
    "save_board",
    "set_board_constraints",
    "set_board_layers",
    "set_board_limits",
    "set_footprint_fields",
    "set_net_classes",
    "set_pad_nets",
    "set_stackup",
    "unrouted_connections",
    "what_is_on_board",
]
