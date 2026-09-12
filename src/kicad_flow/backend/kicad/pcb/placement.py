"""Read-only measurements of current and proposed footprint placement."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from kicad_flow.pcb.types import (
    Footprint,
    PlacementEdge,
    PlacementMeasurement,
    PlacementNetLength,
    PlacementOverlap,
    PlacementProposal,
    Point,
)

from ._geometry import (
    _minimum_tree_length,
    _point_in_polygon,
    _point_on_segment,
    _polygon_area,
    _polygon_intersection,
    _segment_distance,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def measure_placement(
    self: KiCadBoard,
    proposals: tuple[PlacementProposal, ...] = (),
    *,
    edge_clearance: float = 0.0,
    edge_exempt_refs: tuple[str, ...] = (),
) -> PlacementMeasurement:
    """Measure current or proposed poses without modifying this board."""
    from .board import KiCadBoard

    if edge_clearance < 0:
        raise ValueError("edge_clearance cannot be negative")
    for ref in edge_exempt_refs:
        self._require(ref)
    target = self
    if proposals:
        target = KiCadBoard(self._path, copy.deepcopy(self._tree))
        target._defs = self._defs
        seen: set[str] = set()
        for proposal in proposals:
            if proposal.ref in seen:
                raise ValueError(f"duplicate placement proposal for {proposal.ref}")
            seen.add(proposal.ref)
            if proposal.side is not None:
                target.flip(proposal.ref, proposal.side)
            if proposal.rotation is not None:
                target.rotate(proposal.ref, proposal.rotation)
            target.move(
                proposal.ref,
                proposal.x,
                proposal.y,
                anchor=proposal.anchor,
            )
    return target._measure_current_placement(edge_clearance, edge_exempt_refs)


def _measure_current_placement(
    self: KiCadBoard, edge_clearance: float, edge_exempt_refs: tuple[str, ...]
) -> PlacementMeasurement:
    """Measure the current tree; caller choices have already been applied."""
    footprints = self.footprints()
    outline = self.outline_polygon(inset=0.0, max_error=0.02)
    outline_list = list(outline)
    board_area = _polygon_area(outline_list)
    courtyard_area = sum(_polygon_area(part.courtyard_polygon) for part in footprints)
    front_courtyard_area = sum(
        _polygon_area(part.courtyard_polygon) for part in footprints if part.side == "F"
    )
    back_courtyard_area = sum(
        _polygon_area(part.courtyard_polygon) for part in footprints if part.side == "B"
    )

    overlaps: list[PlacementOverlap] = []
    for i, first in enumerate(footprints):
        for second in footprints[i + 1 :]:
            if first.side != second.side:
                continue
            area = _polygon_area(
                _polygon_intersection(first.courtyard_polygon, second.courtyard_polygon)
            )
            if area > 1e-6:
                overlaps.append(
                    PlacementOverlap(first.ref, second.ref, first.side, area)
                )

    edge_violations: list[PlacementEdge] = []
    edge_exceptions: list[PlacementEdge] = []
    clearances: list[float] = []
    outline_pairs = [
        (point, outline[(i + 1) % len(outline)]) for i, point in enumerate(outline)
    ]
    for part in footprints:
        outside = any(
            not self._inside_or_on_outline(point, outline_list)
            for point in part.courtyard_polygon
        )
        edges = [
            (point, part.courtyard_polygon[(i + 1) % len(part.courtyard_polygon)])
            for i, point in enumerate(part.courtyard_polygon)
        ]
        clearance = min(
            _segment_distance(a, b, c, d) for a, b in edges for c, d in outline_pairs
        )
        clearances.append(clearance)
        if outside or clearance + 1e-9 < edge_clearance:
            findings = (
                edge_exceptions if part.ref in edge_exempt_refs else edge_violations
            )
            findings.append(PlacementEdge(part.ref, clearance, outside))

    net_lengths = self._placement_net_lengths(footprints)
    ratio = courtyard_area / board_area if board_area else 0.0
    front_ratio = front_courtyard_area / board_area if board_area else 0.0
    back_ratio = back_courtyard_area / board_area if board_area else 0.0
    return PlacementMeasurement(
        footprint_count=len(footprints),
        board_area=board_area,
        courtyard_area=courtyard_area,
        courtyard_area_ratio=ratio,
        front_courtyard_area=front_courtyard_area,
        back_courtyard_area=back_courtyard_area,
        front_courtyard_area_ratio=front_ratio,
        back_courtyard_area_ratio=back_ratio,
        maximum_side_area_ratio=max(front_ratio, back_ratio),
        minimum_edge_clearance=min(clearances) if clearances else None,
        required_edge_clearance=edge_clearance,
        overlaps=tuple(overlaps),
        edge_violations=tuple(edge_violations),
        edge_exceptions=tuple(edge_exceptions),
        edge_exempt_refs=tuple(sorted(set(edge_exempt_refs))),
        connection_length=sum(item.length for item in net_lengths),
        net_lengths=tuple(
            sorted(net_lengths, key=lambda item: (-item.length, item.net))
        ),
    )


def _inside_or_on_outline(point: Point, outline: list[Point]) -> bool:
    """Whether a point is inside or on the sampled board outline."""
    if any(
        _point_on_segment(point, edge, outline[(i + 1) % len(outline)])
        for i, edge in enumerate(outline)
    ):
        return True
    return _point_in_polygon((point.x, point.y), [(item.x, item.y) for item in outline])


def _placement_net_lengths(
    self: KiCadBoard, footprints: list[Footprint]
) -> list[PlacementNetLength]:
    """Euclidean minimum-spanning-tree length for every multi-pad net."""
    pads = {(part.ref, pad.number): pad.at for part in footprints for pad in part.pads}
    out: list[PlacementNetLength] = []
    for net in self.nets():
        points = [pads[(pad.ref, pad.pad)] for pad in net.pads]
        if len(points) < 2:
            continue
        out.append(
            PlacementNetLength(net.name, len(points), _minimum_tree_length(points))
        )
    return out
