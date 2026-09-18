"""Internal structural view of the KiCadSheet facade used by helpers."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from kicad_flow.schematic.types import (
    Label,
    LayoutFinding,
    Part,
    PlacementBounds,
    Point,
    SheetRef,
    SymbolDef,
)

from .. import _library as library
from .._sexpr import Node
from ._geometry import _VisibleText


class SheetState(Protocol):
    """Typed dependency shared by the facade and its helpers."""

    _path: Path
    _tree: Node
    _defs: dict[str, library.LibrarySymbol]
    _paper: str
    _instance_path: str

    def _copy(self) -> SheetState:
        pass

    def _open_child(self, path: Path) -> SheetState:
        pass

    def _placement_bounds(self) -> list[PlacementBounds]:
        pass

    def _placement_findings(
        self, bounds: tuple[PlacementBounds, ...]
    ) -> list[LayoutFinding]:
        pass

    @property
    def size(self) -> tuple[float, float]:
        pass

    def _as_part(self, node: Node, ref: str) -> Part:
        pass

    def _at_point(self, kinds: tuple[str, ...], x: float, y: float) -> list[Node]:
        pass

    def _ensure_lib_symbol(self, lib_id: str, sym: library.LibrarySymbol) -> None:
        pass

    def _find(self, ref: str, unit: int = 1) -> Node | None:
        pass

    def _instances(self, ref: str, unit: int = 1) -> Node:
        pass

    @staticmethod
    def _justify(prop: Node, justify: str) -> None:
        pass

    def _label_from_node(self, node: Node) -> Label:
        pass

    def _label_node(self, uuid: str) -> Node:
        pass

    def _layout_fields(
        self, node: Node, lib_id: str, rotation: float, mirror: str = "", unit: int = 1
    ) -> None:
        pass

    def _load(self, lib_id: str) -> library.LibrarySymbol:
        pass

    def _next_hash_ref(self, prefix: str) -> str:
        pass

    @staticmethod
    def _prop_of(definition: Node, name: str) -> Node | None:
        pass

    def _property(
        self,
        name: str,
        value: str,
        x: float,
        y: float,
        *,
        hide: bool = False,
        justify: str = "",
    ) -> Node:
        pass

    def _require(self, ref: str, unit: int = 1) -> Node:
        pass

    def _scratch(self) -> AbstractContextManager[Path]:
        pass

    def _check_layout_page(self, page: str) -> list[LayoutFinding]:
        pass

    @property
    def path(self) -> Path:
        pass

    @property
    def uuid(self) -> str:
        pass

    def _sheet_node(self, name: str) -> Node:
        pass

    def _sheet_ref(self, node: Node) -> SheetRef:
        pass

    def _uid_for(self, key: str) -> str:
        pass

    def _visible_text(self) -> list[_VisibleText]:
        pass

    @property
    def _where(self) -> str:
        pass

    def _wires_between(self, x1: float, y1: float, x2: float, y2: float) -> list[Node]:
        pass

    def fields(self, ref: str) -> dict[str, str]:
        pass

    def part(self, ref: str, *, unit: int = 1) -> Part:
        pass

    def parts(self) -> list[Part]:
        pass

    def place(
        self,
        lib_id: str,
        ref: str,
        x: float,
        y: float,
        *,
        value: str = "",
        rotation: float = 0.0,
        mirror: str = "",
        unit: int = 1,
    ) -> Part:
        pass

    def symbol(self, lib_id: str, *, unit: int = 1) -> SymbolDef:
        pass

    def wires(self) -> list[tuple[Point, Point]]:
        pass
