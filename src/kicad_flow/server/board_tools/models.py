"""PCB validated request models for MCP list writes."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """One list item whose unknown or non-finite fields are refused."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _GraphicBase(_StrictModel):
    """Fields shared by every outline and silkscreen primitive."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["Edge.Cuts", "F.SilkS", "B.SilkS"] = Field(
        description="Board outline or front/back silkscreen layer."
    )
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
        description="Closed polygon as [[x, y], ...], with at least 3 points."
    )
    fill: bool = False


GraphicSpec = Annotated[
    LineGraphic | ArcGraphic | CircleGraphic | RectangleGraphic | PolygonGraphic,
    Field(discriminator="kind"),
]


class GraphicMove(_StrictModel):
    """One graphical primitive and the offset to apply."""

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(description="Identity returned by add/list_graphics.")
    dx: float = Field(description="Horizontal offset in mm.")
    dy: float = Field(description="Vertical offset in mm.")


class NewFootprint(_StrictModel):
    """One footprint placement."""

    fp_id: str = Field(description="Library footprint id.")
    ref: str = Field(description="Reference designator, e.g. R1.")
    x: float = Field(description="Anchor X in mm.")
    y: float = Field(description="Anchor Y in mm.")
    anchor: Literal["origin", "courtyard_center"] = Field(
        default="origin",
        description="Whether x/y names the library origin or physical centre.",
    )
    rotation: float = Field(default=0.0, description="Any angle in degrees.")
    side: str = Field(default="F", description="F or B.")
    value: str = Field(default="", description="Value field.")


class FootprintMove(_StrictModel):
    """One footprint's new absolute position."""

    ref: str = Field(description="Reference designator to move.")
    x: float = Field(description="New absolute anchor X in mm.")
    y: float = Field(description="New absolute anchor Y in mm.")
    anchor: Literal["origin", "courtyard_center"] = "origin"


class PlacementCandidate(FootprintMove):
    """A tentative footprint pose to measure without applying it."""

    rotation: float | None = Field(
        default=None, description="Tentative angle, or preserve when omitted."
    )
    side: Literal["F", "B"] | None = Field(
        default=None, description="Tentative side, or preserve when omitted."
    )


class FootprintTurn(_StrictModel):
    """One footprint's new absolute rotation."""

    ref: str = Field(description="Reference designator to rotate.")
    rotation: float = Field(description="New absolute angle in degrees.")


class FootprintFlip(_StrictModel):
    """One footprint's requested board side."""

    ref: str = Field(description="Reference designator to flip.")
    side: str = Field(description="F or B.")


class PadNet(_StrictModel):
    """One pad-to-net assignment."""

    ref: str = Field(description="Reference designator containing the pad.")
    pad: str = Field(description="Pad number or name.")
    net: str = Field(description="Exact net name to assign.")


class FootprintFieldValue(_StrictModel):
    """One footprint field value."""

    ref: str = Field(description="Reference designator containing the field.")
    name: str = Field(description="Field name, e.g. Value or LCSC.")
    value: str = Field(description="New field value.")


class FootprintFieldShift(_StrictModel):
    """One footprint field placement."""

    ref: str = Field(description="Reference designator containing the field.")
    name: str = Field(description="Field name, e.g. Reference.")
    dx: float = Field(description="X offset from the footprint origin in mm.")
    dy: float = Field(description="Y offset from the footprint origin in mm.")
    rotation: float | None = Field(
        default=None, description="Absolute text angle, or preserve when omitted."
    )
    layer: str = Field(default="", description="New layer, or preserve when empty.")
    hide: bool | None = Field(
        default=None, description="Visibility override, or preserve when omitted."
    )


class NewTrack(_StrictModel):
    """One straight copper segment."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x1: float = Field(description="Start X in mm.")
    y1: float = Field(description="Start Y in mm.")
    x2: float = Field(description="End X in mm.")
    y2: float = Field(description="End Y in mm.")
    layer: str = Field(description="Copper layer name.")
    width: float = Field(gt=0, description="Positive track width in mm.")
    net: str = Field(default="", description="Exact net name, or empty for none.")

    @model_validator(mode="after")
    def has_length(self) -> NewTrack:
        """Reject copper with no geometric extent before a batch mutates."""
        if self.x1 == self.x2 and self.y1 == self.y2:
            raise ValueError("a track must have two different endpoints")
        return self


class NewVia(_StrictModel):
    """One explicitly typed plated via."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x: float = Field(description="Centre X in mm.")
    y: float = Field(description="Centre Y in mm.")
    net: str = Field(default="", description="Exact net name, or empty for none.")
    diameter: float = Field(
        default=0.6, gt=0, description="Positive finished diameter in mm."
    )
    drill: float = Field(
        default=0.3, gt=0, description="Positive drill diameter in mm."
    )
    layers: tuple[str, str] = Field(
        default=("F.Cu", "B.Cu"),
        description="Explicit start and end copper layers.",
    )
    kind: Literal["through", "blind_buried", "microvia"] = "through"

    @model_validator(mode="after")
    def valid_ring(self) -> NewVia:
        """A plated via needs copper outside its drill."""
        if self.drill >= self.diameter:
            raise ValueError("via drill must be smaller than its diameter")
        if self.layers[0] == self.layers[1]:
            raise ValueError("via layers must be different")
        return self


class NewZone(_StrictModel):
    """One copper pour or keep-out polygon."""

    points: list[list[float]] = Field(
        default_factory=list,
        description="Explicit polygon as [[x, y], ...], with at least 3 points.",
    )
    boundary: Literal["points", "board_outline"] = Field(
        default="points",
        description="Use explicit points or derive the boundary from Edge.Cuts.",
    )
    inset: float | None = Field(
        default=None,
        description="Required inward offset in mm for a board_outline boundary.",
    )
    max_error: float = Field(
        default=0.02, description="Maximum curve-to-polygon chord error in mm."
    )
    layer: str = Field(description="Copper layer name.")
    net: str = Field(default="", description="Pour net, or empty for no net.")
    clearance: float = Field(default=0.5, ge=0, description="Clearance in mm.")
    pad_connection: Literal["thermal", "solid", "none"] = Field(
        default="thermal",
        description="How same-net pads join the pour.",
    )
    min_thickness: float = Field(default=0.25, gt=0)
    thermal_gap: float = Field(default=0.5, gt=0)
    thermal_spoke_width: float = Field(default=0.5, gt=0)
    priority: int = Field(default=0, ge=0)
    island_removal: Literal["always", "never", "area"] = "always"
    min_island_area: float = Field(default=0.0, ge=0)
    forbids: list[str] = Field(
        default_factory=list,
        description="For a keep-out: tracks, vias, pads, pours, footprints.",
    )

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
        if self.island_removal == "area" and self.min_island_area <= 0:
            raise ValueError(
                "min_island_area must be positive when island_removal='area'"
            )
        return self


class NetPairSpec(_StrictModel):
    """Two explicitly named nets whose authored lengths should be compared."""

    model_config = ConfigDict(extra="forbid")

    first: str
    second: str


class NewBoardText(_StrictModel):
    """One text item on a board layer."""

    x: float = Field(description="Anchor X in mm.")
    y: float = Field(description="Anchor Y in mm.")
    text: str = Field(description="Literal text; newlines are preserved.")
    layer: str = Field(description="Board layer name, e.g. F.SilkS.")
    size: float = Field(default=1.0, description="Text height and width in mm.")
    rotation: float = Field(default=0.0, description="Angle in degrees.")
    mirror: bool = Field(default=False, description="Mirror the text.")


class StackupLayerSpec(_StrictModel):
    """One explicitly ordered physical or surface stackup layer."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Layer name, e.g. F.Cu or dielectric 1.")
    kind: str = Field(description="Layer type, e.g. copper, core or prepreg.")
    thickness: float | None = Field(
        default=None, description="Layer thickness in mm when applicable."
    )
    material: str = Field(default="", description="Laminate/material name.")
    epsilon_r: float | None = Field(
        default=None, description="Relative dielectric constant."
    )
    loss_tangent: float | None = Field(
        default=None, description="Dielectric loss tangent."
    )
    color: str = Field(default="", description="Optional mask/silkscreen color.")


class NetClassSpec(_StrictModel):
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


class NetClassAssignmentSpec(_StrictModel):
    """One net's membership in a netclass."""

    model_config = ConfigDict(extra="forbid")

    net: str
    net_class: str


class NumericConstraintSpec(_StrictModel):
    """One millimetre-valued DRC constraint."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        description="KiCad constraint name, e.g. track_width, skew or length."
    )
    min: float | None = None
    opt: float | None = None
    max: float | None = None


class BoardRuleSpec(_StrictModel):
    """One named custom DRC rule."""

    model_config = ConfigDict(extra="forbid")

    name: str
    condition: str = Field(description="DRC condition selecting rule objects.")
    constraints: list[NumericConstraintSpec]
    layer: str = Field(
        default="", description="Optional board layer, outer or inner selector."
    )
