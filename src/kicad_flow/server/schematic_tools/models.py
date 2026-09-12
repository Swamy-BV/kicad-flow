"""Schematic validated request models for MCP list writes.

Pydantic validates the complete list before a tool runs. Backend transactions
then roll back the entire write if any item fails, preserving its error index.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """One fully checked list element; unknown keys are never ignored."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


QuarterTurn = Literal[0, 90, 180, 270]


class NewPart(_StrictModel):
    """One part for `add_components`."""

    lib_id: str = Field(description="Library symbol, e.g. 'Device:R'.")
    ref: str = Field(description="Reference, e.g. 'R1'. Must be unused.")
    x: float = Field(description="Position in mm; snapped to the 1.27 grid.")
    y: float = Field(description="Position in mm.")
    value: str = Field(default="", description="Shown value, e.g. '10k'.")
    rotation: QuarterTurn = Field(default=0, description="0, 90, 180 or 270.")
    mirror: Literal["", "x", "y"] = Field(default="", description="'x', 'y', or empty.")
    unit: int = Field(default=1, ge=1, description="Unit of a multi-unit symbol.")


class Segment(_StrictModel):
    """One wire for `add_wires`, from one point to another."""

    x1: float = Field(description="Start, in mm; snapped to the grid.")
    y1: float = Field(description="Start, in mm.")
    x2: float = Field(description="End, in mm.")
    y2: float = Field(description="End, in mm.")

    @model_validator(mode="after")
    def has_length(self) -> Segment:
        """Reject a segment that cannot connect two different points."""
        if self.x1 == self.x2 and self.y1 == self.y2:
            raise ValueError("a wire must have two different endpoints")
        return self


class Spot(_StrictModel):
    """One point, for `add_junctions` and `add_no_connects`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")


class LabelTarget(_StrictModel):
    """One label selected by stable UUID, or legacy snapped position."""

    uuid: str = Field(default="", description="Identity returned by add/list_labels.")
    x: float | None = Field(
        default=None, description="Legacy position in mm when UUID is empty."
    )
    y: float | None = Field(
        default=None, description="Legacy position in mm when UUID is empty."
    )

    @model_validator(mode="after")
    def valid_target(self) -> LabelTarget:
        """Require UUID alone or a complete coordinate pair."""
        if self.uuid:
            if self.x is not None or self.y is not None:
                raise ValueError("select a label by uuid or position, not both")
        elif self.x is None or self.y is None:
            raise ValueError("a label target needs uuid or both x and y")
        return self


class NewLabel(_StrictModel):
    """One label for `add_labels`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    text: str = Field(
        description="The net name. Do not use labels for GND or "
        "+3V3; place those rails with add_power."
    )
    kind: Literal["local", "global", "hierarchical"] = Field(
        default="local",
        description="'local', 'global' or 'hierarchical'. Use "
        "global only for an intentionally design-wide signal.",
    )
    rotation: float = Field(
        default=0.0,
        description="Local, global and "
        "hierarchical labels can be vertical at 90 or 270; "
        "horizontal reads the same at 0 and 180.",
    )
    justify: Literal["left", "right", "bottom"] = Field(
        default="left",
        description="Text growth direction: "
        "'left' grows rightward and 'right' grows leftward. "
        "Set this explicitly for local labels: use 'right' "
        "on left-side pins and 'left' on right-side pins.",
    )


class NewPower(_StrictModel):
    """One power symbol for `add_power`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    net: str = Field(
        description="Rail name, especially 'GND' or '+3V3'. Use "
        "this power symbol instead of a label with that name."
    )
    rotation: QuarterTurn = Field(default=0, description="0, 90, 180 or 270.")


class NewFlag(_StrictModel):
    """One PWR_FLAG for `add_power_flags`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    rotation: QuarterTurn = Field(default=0, description="0, 90, 180 or 270.")


class NewSheetPort(_StrictModel):
    """One explicitly directed hierarchical-sheet port."""

    name: str
    kind: Literal["input", "output", "bidirectional", "tri_state", "passive"]


class NewSheetBox(_StrictModel):
    """One child-sheet box for `add_sheets`."""

    name: str = Field(description="Sheet name, shown above the box.")
    filename: str = Field(description="Child file, e.g. 'power.kicad_sch'.")
    x: float = Field(description="Top-left corner, in mm.")
    y: float = Field(description="Top-left corner, in mm.")
    width: float = Field(default=38.1, gt=0, description="Box width in mm.")
    height: float = Field(default=25.4, gt=0, description="Box height in mm.")
    ports: list[NewSheetPort] = Field(
        default_factory=list, description='[{"name": "SENSE", "kind": "input"}, ...].'
    )


class PartMove(_StrictModel):
    """One absolute move for `move_components`."""

    ref: str = Field(description="Reference to move.")
    x: float = Field(description="New position in mm; snapped to the grid.")
    y: float = Field(description="New position in mm.")
    unit: int = Field(default=1, ge=1, description="Unit of a multi-unit symbol.")


class PartTurn(_StrictModel):
    """One rotation for `rotate_components`."""

    ref: str = Field(description="Reference to turn.")
    rotation: QuarterTurn = Field(description="0, 90, 180 or 270.")
    unit: int = Field(default=1, ge=1, description="Unit of a multi-unit symbol.")


class PartFlip(_StrictModel):
    """One mirroring for `mirror_components`."""

    ref: str = Field(description="Reference to mirror.")
    axis: Literal["", "x", "y"] = Field(description="'x', 'y', or empty to clear it.")
    unit: int = Field(default=1, ge=1, description="Unit of a multi-unit symbol.")


class FieldValue(_StrictModel):
    """One field to set, for `set_fields`."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, e.g. 'Footprint'.")
    value: str = Field(description="The value to write.")


class FieldShift(_StrictModel):
    """One field to move, for `move_fields`."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, e.g. 'Reference'.")
    dx: float = Field(description="Offset from the part's position, in mm.")
    dy: float = Field(description="Offset from the part's position, in mm.")
    rotation: float | None = Field(
        default=None, description="Absolute text angle, or null."
    )
    justify: Literal["", "left", "right"] = Field(
        default="", description="'left', 'right' or empty."
    )


class SheetNote(_StrictModel):
    """One note for `add_texts`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(
        description="Position in mm. This is the text's BASELINE,"
        " so a note grows downward from here."
    )
    text: str = Field(description="The note. Newlines are kept.")
    size: float = Field(
        default=1.27,
        gt=0,
        description="Text height in mm. 1.27 matches a label; 2.54 reads as a heading.",
    )
    rotation: float = Field(default=0.0, description="Degrees. Any angle.")
    bold: bool = Field(default=False, description="Bold, for a heading.")
    justify: Literal["left", "right", "center"] = Field(
        default="left", description="'left', 'right' or 'center'."
    )


class WireEnds(_StrictModel):
    """One wire, named by the two points it runs between."""

    x1: float = Field(description="One end, in mm; snapped to the grid.")
    y1: float = Field(description="One end, in mm.")
    x2: float = Field(description="The other end, in mm.")
    y2: float = Field(description="The other end, in mm.")


class WireShift(WireEnds):
    """One wire to shift, and by how much."""

    dx: float = Field(description="Offset in mm. Both ends move together.")
    dy: float = Field(description="Offset in mm.")


class LabelShift(LabelTarget):
    """One label, and how far to move it."""

    dx: float = Field(description="Offset in mm.")
    dy: float = Field(description="Offset in mm.")


class LabelTurn(LabelTarget):
    """One label, and the angle to turn it to."""

    rotation: float = Field(
        description="Degrees. 90 or 270 for a vertical "
        "label; horizontal reads the same at 0 and 180."
    )


class SheetMove(_StrictModel):
    """One child-sheet box to move."""

    name: str = Field(description="The sheet name shown above the box.")
    x: float = Field(description="New top-left corner, in mm.")
    y: float = Field(description="New top-left corner, in mm.")


class FieldRef(_StrictModel):
    """One field to delete."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, for example MPN.")
    unit: int = Field(default=1, ge=1, description="Unit of a multi-unit symbol.")
