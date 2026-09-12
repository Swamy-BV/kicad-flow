"""Read-only placement bounds and overlap measurements."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from kicad_flow.schematic.types import (
    LayoutFinding,
    PartPlacement,
    PlacementBounds,
    PlacementMeasurement,
    Point,
)

from ._geometry import (
    _Box,
    _hidden,
    _pin_on_sheet,
    _text_box,
)
from ._nodes import (
    _MARGIN,
    PAPER,
    _atom,
    _f,
    _text,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def measure_placement(
    self: KiCadSheet, placements: tuple[PartPlacement, ...]
) -> PlacementMeasurement:
    """Apply explicit poses to an in-memory clone and measure the result."""
    from .sheet import KiCadSheet

    clone = KiCadSheet(
        self._path, copy.deepcopy(self._tree), self._paper, self._instance_path
    )
    clone._defs = dict(self._defs)
    predicted = tuple(
        clone.place(
            item.lib_id,
            item.ref,
            item.at.x,
            item.at.y,
            value=item.value,
            rotation=item.rotation,
            mirror=item.mirror,
            unit=item.unit,
        )
        for item in placements
    )
    bounds = tuple(clone._placement_bounds())
    findings = tuple(clone._placement_findings(bounds))
    return PlacementMeasurement(
        parts=predicted, bounds=bounds, findings=findings, page_size=clone.size
    )


def _placement_bounds(self: KiCadSheet) -> list[PlacementBounds]:
    """Symbol and visible-field rectangles in sheet coordinates."""
    found: list[PlacementBounds] = []
    for node in self._tree.get_all("symbol"):
        lib_node = node.get("lib_id")
        if lib_node is None:
            continue
        lib_id = _text(lib_node)
        ref = _text(self._prop_of(node, "Reference"), 1, "?")
        unit = int(_f(node.get("unit"), 0, 1))
        at_node = node.get("at")
        at = Point(_f(at_node, 0), _f(at_node, 1))
        rotation = _f(at_node, 2)
        mirror_node = node.get("mirror")
        mirror = (_atom(mirror_node, 0) if mirror_node is not None else "") or ""
        left, bottom, right, top = self.symbol(lib_id, unit=unit).bounds
        corners = [
            _pin_on_sheet(x, y, 0.0, at, rotation, mirror)[0]
            for x in (left, right)
            for y in (bottom, top)
        ]
        body = _Box(
            min(point.x for point in corners),
            min(point.y for point in corners),
            max(point.x for point in corners),
            max(point.y for point in corners),
        )
        found.append(
            PlacementBounds(
                name=f"{ref}.{unit}",
                kind="symbol",
                ref=ref,
                unit=unit,
                left=body.left,
                top=body.top,
                right=body.right,
                bottom=body.bottom,
            )
        )
        for prop in node.get_all("property"):
            value = _text(prop, 1)
            effects = prop.get("effects")
            if not value or _hidden(effects):
                continue
            field = _text(prop)
            box = _text_box(prop, value, rotation_offset=rotation)
            found.append(
                PlacementBounds(
                    name=f"{ref}.{unit}.{field}",
                    kind="field",
                    ref=ref,
                    unit=unit,
                    left=box.left,
                    top=box.top,
                    right=box.right,
                    bottom=box.bottom,
                )
            )
    return found


def _placement_findings(
    self: KiCadSheet, bounds: tuple[PlacementBounds, ...]
) -> list[LayoutFinding]:
    """Conservative overlap and page-boundary facts for placement bounds."""
    findings: list[LayoutFinding] = []
    paper_width, paper_height = PAPER.get(self._paper, PAPER["A4"])
    left_limit = top_limit = _MARGIN
    right_limit = paper_width - _MARGIN
    bottom_limit = paper_height - _MARGIN

    def box(item: PlacementBounds) -> _Box:
        return _Box(item.left, item.top, item.right, item.bottom)

    for item in bounds:
        if (
            item.left >= left_limit
            and item.top >= top_limit
            and item.right <= right_limit
            and item.bottom <= bottom_limit
        ):
            continue
        findings.append(
            LayoutFinding(
                severity="warning",
                kind="page_bounds",
                message=f"{item.name} extends outside the drawable page",
                first=item.name,
                second="page",
                at=Point(
                    min(max((item.left + item.right) / 2, left_limit), right_limit),
                    min(max((item.top + item.bottom) / 2, top_limit), bottom_limit),
                ),
            )
        )

    for index, first in enumerate(bounds):
        for second in bounds[index + 1 :]:
            if (first.ref, first.unit) == (second.ref, second.unit):
                continue
            first_box, second_box = box(first), box(second)
            if not first_box.overlaps(second_box):
                continue
            kinds = "_".join(sorted((first.kind, second.kind)))
            findings.append(
                LayoutFinding(
                    severity="warning",
                    kind=f"{kinds}_overlap",
                    message=f"{first.name} overlaps {second.name}",
                    first=first.name,
                    second=second.name,
                    at=first_box.meeting(second_box),
                )
            )
    return findings
