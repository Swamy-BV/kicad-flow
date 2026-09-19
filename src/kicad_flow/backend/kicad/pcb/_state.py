"""Internal structural view of the KiCadBoard facade used by helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from kicad_flow.pcb.types import (
    BoardLimits,
    BoardText,
    Finding,
    Footprint,
    FootprintDef,
    Graphic,
    Net,
    NetConnectivity,
    Pad,
    PlacementMeasurement,
    PlacementNetLength,
    Point,
    Stackup,
    Track,
    Via,
    Zone,
)

from .._sexpr import Node


class BoardState(Protocol):
    """Typed dependency shared by the facade and its helpers."""

    _path: Path
    _tree: Node
    _defs: dict[str, Node]

    def _copy_to(self, path: Path) -> BoardState:
        pass

    def _accept_disk_revision(self) -> None:
        pass

    def _as_footprint(self, node: Node, ref: str) -> Footprint:
        pass

    def _find(self, ref: str) -> Node | None:
        pass

    def _graphic_from_node(self, node: Node) -> Graphic:
        pass

    def _graphic_node(self, uuid: str) -> Node:
        pass

    def _text_from_node(self, node: Node) -> BoardText:
        pass

    def _text_node(self, uuid: str) -> Node:
        pass

    @staticmethod
    def _inside_or_on_outline(point: Point, outline: list[Point]) -> bool:
        pass

    def _load_def(self, fp_id: str) -> Node:
        pass

    @staticmethod
    def _mirror(node: Node) -> None:
        pass

    def _pad_copper_layers(self, pad: Pad) -> tuple[str, ...]:
        pass

    def _pad_of(self, node: Node, at: Point, rotation: float) -> Pad:
        pass

    def _placement_net_lengths(
        self, footprints: list[Footprint]
    ) -> list[PlacementNetLength]:
        pass

    def _measure_current_placement(
        self, edge_clearance: float, edge_exempt_refs: tuple[str, ...]
    ) -> PlacementMeasurement:
        pass

    @staticmethod
    def _prop_of(node: Node, name: str) -> Node | None:
        pass

    def _require(self, ref: str) -> Node:
        pass

    def _require_layer(self, name: str) -> None:
        pass

    def _routing_findings(self) -> list[Finding]:
        pass

    @staticmethod
    def _set_child(node: Node, name: str, atoms: list[Any]) -> None:
        pass

    def _set_property(self, node: Node, name: str, value: str) -> None:
        pass

    def _via_copper_layers(self, via: Via) -> tuple[str, ...]:
        pass

    def connectivity(self, nets: tuple[str, ...] = ()) -> list[NetConnectivity]:
        pass

    def check(self, *, schematic_parity: bool = False) -> list[Finding]:
        pass

    def fields(self, ref: str) -> dict[str, str]:
        pass

    def footprint(self, ref: str) -> Footprint:
        pass

    def footprint_def(self, fp_id: str) -> FootprintDef:
        pass

    def footprints(self) -> list[Footprint]:
        pass

    def flip(self, ref: str, side: str) -> Footprint:
        pass

    def graphics(self, layer: str = "") -> list[Graphic]:
        pass

    def texts(self, layer: str = "") -> list[BoardText]:
        pass

    @property
    def layers(self) -> tuple[str, ...]:
        pass

    def limits(self) -> BoardLimits:
        pass

    def move(
        self, ref: str, x: float, y: float, *, anchor: str = "origin"
    ) -> Footprint:
        pass

    def nets(self) -> list[Net]:
        pass

    def outline_polygon(self, *, inset: float, max_error: float) -> tuple[Point, ...]:
        pass

    def rotate(self, ref: str, rotation: float) -> Footprint:
        pass

    @property
    def path(self) -> Path:
        pass

    def save(self, *, validate: bool = False) -> Path:
        pass

    def refill(self) -> int:
        pass

    def stackup(self) -> Stackup:
        pass

    @property
    def thickness(self) -> float:
        pass

    def tracks(self) -> list[Track]:
        pass

    def track(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        layer: str,
        width: float,
        net: str = "",
    ) -> Track:
        pass

    def via(
        self,
        x: float,
        y: float,
        *,
        net: str = "",
        diameter: float = 0.6,
        drill: float = 0.3,
        layers: tuple[str, str] = ("F.Cu", "B.Cu"),
        kind: str = "through",
    ) -> Via:
        pass

    def zone(
        self,
        points: list[tuple[float, float]],
        *,
        layer: str,
        net: str = "",
        clearance: float = 0.5,
        pad_connection: str = "thermal",
        min_thickness: float = 0.25,
        thermal_gap: float = 0.5,
        thermal_spoke_width: float = 0.5,
        priority: int = 0,
        island_removal: str = "always",
        min_island_area: float = 0.0,
        forbids: tuple[str, ...] = (),
    ) -> Zone:
        pass

    def vias(self) -> list[Via]:
        pass

    def zones(self) -> list[Zone]:
        pass
