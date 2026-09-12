"""Schematic pin transforms, text bounds and spatial intersections."""

from __future__ import annotations

import math
from dataclasses import dataclass

from kicad_flow.schematic.types import (
    Point,
)

from .._sexpr import Node
from ._nodes import (
    _atom,
    _f,
    _text,
)


@dataclass(frozen=True)
class _Box:
    """An axis-aligned visible extent in sheet millimetres."""

    left: float
    top: float
    right: float
    bottom: float

    def overlaps(self, other: _Box, clearance: float = 0.05) -> bool:
        """Whether this box has positive area in common with *other*."""
        return (
            min(self.right, other.right) - max(self.left, other.left) > clearance
            and min(self.bottom, other.bottom) - max(self.top, other.top) > clearance
        )

    def meeting(self, other: _Box) -> Point:
        """Centre of the common area, for locating a finding."""
        return Point(
            (max(self.left, other.left) + min(self.right, other.right)) / 2,
            (max(self.top, other.top) + min(self.bottom, other.bottom)) / 2,
        )


@dataclass(frozen=True)
class _VisibleText:
    """One rendered text item and the geometry needed to inspect it."""

    kind: str
    name: str
    box: _Box
    at: Point
    attaches: bool = False


def _hidden(effects: Node | None) -> bool:
    """Whether KiCad marks an item's effects hidden."""
    if effects is None:
        return False
    hide = effects.get("hide")
    return hide is not None and _text(hide, 0, "yes") != "no"


def _text_width(text: str, size: float) -> float:
    """Conservative width of KiCad stroke text in millimetres.

    KiCad's default font is proportional. Character classes avoid the much
    larger false boxes produced by treating ``III`` like ``WWW`` while
    retaining a small margin for stroke width. Rendering remains the final
    authority; KiCad exposes no schematic text-collision check or font metrics.
    """
    narrow = " .,:;!|'ijlI1()[]{}"
    wide = "MW@%&#QO"
    units = sum(0.34 if c in narrow else 0.9 if c in wide else 0.62 for c in text)
    return max(size * 0.35, size * units + size * 0.12)


def _text_box(
    node: Node, content: str | None = None, *, rotation_offset: float = 0.0
) -> _Box:
    """Visible text bounds from an item's ``at`` and ``effects`` nodes."""
    at = node.get("at")
    effects = node.get("effects")
    font = effects.get("font") if effects is not None else None
    dimensions = font.get("size") if font is not None else None
    size = max(_f(dimensions, 0, 1.27), _f(dimensions, 1, 1.27))
    lines = (content if content is not None else _text(node)).splitlines() or [""]
    width = max(_text_width(line, size) for line in lines)
    height = size * (1.0 + 1.35 * (len(lines) - 1))
    justify = effects.get("justify") if effects is not None else None
    sides = (
        {_atom(justify, i) for i in range(len(justify.items) - 1)}
        if justify is not None
        else set()
    )
    x, y = _f(at, 0), _f(at, 1)
    if "left" in sides:
        x0, x1 = x, x + width
    elif "right" in sides:
        x0, x1 = x - width, x
    else:
        x0, x1 = x - width / 2, x + width / 2
    if "bottom" in sides:
        y0, y1 = y - height, y
    elif "top" in sides:
        y0, y1 = y, y + height
    else:
        y0, y1 = y - height / 2, y + height / 2

    angle = math.radians((_f(at, 2) + rotation_offset) % 360.0)
    cos, sin = math.cos(angle), math.sin(angle)
    corners = []
    for px, py in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
        dx, dy = px - x, py - y
        corners.append((x + dx * cos + dy * sin, y - dx * sin + dy * cos))
    return _Box(
        min(p[0] for p in corners),
        min(p[1] for p in corners),
        max(p[0] for p in corners),
        max(p[1] for p in corners),
    )


def _segment_crosses_box(a: Point, b: Point, box: _Box) -> bool:
    """Whether a segment enters a box's interior rather than touching its edge."""
    inset = 0.08
    left, right = box.left + inset, box.right - inset
    top, bottom = box.top + inset, box.bottom - inset
    if left >= right or top >= bottom:
        return False
    dx, dy = b.x - a.x, b.y - a.y
    low, high = 0.0, 1.0
    for p, q in (
        (-dx, a.x - left),
        (dx, right - a.x),
        (-dy, a.y - top),
        (dy, bottom - a.y),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            low = max(low, ratio)
        else:
            high = min(high, ratio)
        if low > high:
            return False
    return True


def _on_segment(point: Point, a: Point, b: Point, tolerance: float = 0.01) -> bool:
    """Whether *point* lies on the finite segment *a*--*b*."""
    cross = (point.x - a.x) * (b.y - a.y) - (point.y - a.y) * (b.x - a.x)
    if abs(cross) > tolerance:
        return False
    return (
        min(a.x, b.x) - tolerance <= point.x <= max(a.x, b.x) + tolerance
        and min(a.y, b.y) - tolerance <= point.y <= max(a.y, b.y) + tolerance
    )


_QUARTER_TURNS = (0.0, 90.0, 180.0, 270.0)


def _quarter_turn(rotation: float) -> float:
    """*rotation* normalised to 0/90/180/270, or a :class:`ValueError`.

    KiCad turns a symbol in quarter turns and nothing else. Any other angle
    writes a file it will not open -- measured, 45 and 30 both give "Failed to
    load schematic" while 90 and 180 are fine. This used to be accepted: the
    call returned pin positions computed off the angle, reported success, and
    left the fault for whatever opened the file next. It also put those pins
    off the 1.27 mm grid, which the rest of the module guarantees they are on.

    Only symbols are restricted. A label or a field takes any angle and KiCad
    loads it, so `label` and `move_field` do not go through here.
    """
    turned = rotation % 360.0
    if turned not in _QUARTER_TURNS:
        raise ValueError(f"rotation must be 0, 90, 180 or 270, not {rotation!r}")
    return turned


def _pin_on_sheet(
    px: float, py: float, pangle: float, at: Point, rotation: float, mirror: str
) -> tuple[Point, float]:
    """Where a pin lands once its part is placed, rotated and mirrored.

    This is the arithmetic every caller would otherwise repeat and quietly get
    wrong. Three things have to line up:

    1. **The Y axis flips.** A library draws with Y increasing *upward*; a
       sheet has Y increasing *downward*. So a pin at library ``y = +3.81``
       sits 3.81 mm **above** the part's origin on the sheet.
    2. **Rotation is counter-clockwise on screen**, which -- with Y already
       flipped -- is the ordinary rotation matrix applied to the flipped offset.
    3. **Mirroring happens after rotation**, negating one axis of the offset,
       and it also reverses the direction the pin points.

    Returns the sheet point and the direction the pin points, in degrees with
    0 = right and 90 = up, so a caller knows which way to leave.
    """
    dx, dy = px, -py  # (1) into sheet space
    theta = math.radians(rotation % 360.0)
    cos, sin = math.cos(theta), math.sin(theta)
    rx = dx * cos + dy * sin  # (2) counter-clockwise, Y already down
    ry = -dx * sin + dy * cos
    angle = (pangle + rotation) % 360.0
    if mirror == "y":  # (3) mirrored about the vertical axis
        rx, angle = -rx, (180.0 - angle) % 360.0
    elif mirror == "x":
        ry, angle = -ry, (-angle) % 360.0
    return Point(at.x + rx, at.y + ry), angle


def _field_angle(rotation: float) -> float:
    """The text angle a field needs so it reads horizontally.

    KiCad ADDS the symbol's rotation to its field text -- but only for the
    quarter turns. At 0 and 180 it leaves the text upright on its own, so
    compensating there turns it upside down instead. Measured by rendering a
    row of resistors at all four rotations, both ways, and looking at it.
    """
    return (-rotation) % 360.0 if rotation % 180 else 0.0
