"""Typed KiCadSheet facade, file lifecycle and shared state."""

from __future__ import annotations

import contextlib
import copy
import os
from collections.abc import Iterator
from pathlib import Path

from kicad_flow.schematic.api import GRID, Sheet
from kicad_flow.schematic.types import (
    Finding,
    Label,
    LayoutFinding,
    Net,
    Part,
    PartPlacement,
    PlacementBounds,
    PlacementMeasurement,
    Point,
    SceneBounds,
    SceneSnapshot,
    SheetRef,
    SymbolDef,
)

from .. import _library as library
from .._sexpr import Node, dumps, loads
from . import components as _components
from . import connections as _connections
from . import fields as _fields
from . import hierarchy as _hierarchy
from . import placement as _placement
from . import symbols as _symbols
from . import validation as _validation
from ._geometry import (
    _VisibleText,
)
from ._nodes import (
    _MARGIN,
    PAPER,
    _f,
    _node,
    _text,
    _uid_from,
    _walk,
)


class KiCadSheet(Sheet):
    """A ``.kicad_sch`` file, edited through the primitive API."""

    def __init__(
        self, path: Path, tree: Node, paper: str, instance_path: str = ""
    ) -> None:
        """Wrap an already-parsed sheet. Use :func:`create` or :func:`load`."""
        self._path = Path(path)
        self._tree = tree
        self._paper = paper
        self._instance_path = instance_path
        self._defs: dict[str, library.LibrarySymbol] = {}
        # Every identifier already in the file. A derived uuid is checked
        # against it, so two identical things -- two wires between the same
        # pair of points -- cannot end up sharing one.
        self._uids: set[str] = {
            _text(node.get("uuid"))
            for node in _walk(tree)
            if node.get("uuid") is not None
        }

    def _uid_for(self, key: str) -> str:
        """A stable uuid for *key*, made unique if that key repeats."""
        n = 1
        while True:
            candidate = _uid_from(key if n == 1 else f"{key}#{n}")
            if candidate not in self._uids:
                self._uids.add(candidate)
                return candidate
            n += 1

    @property
    def uuid(self) -> str:
        """This sheet's own identifier."""
        return _text(self._tree.get("uuid"))

    @property
    def _where(self) -> str:
        """The instance path parts on this sheet record.

        A sheet opened on its own is the root and its parts live at ``/<own
        uuid>``. A sheet that is a child of another was told its place when it
        was created, and its parts live at ``/<root>/<sheet symbol>`` -- get
        this wrong and the child's parts annotate against the wrong sheet, so
        their nets never merge into the design.
        """
        return self._instance_path or ("/" + self.uuid)

    @property
    def path(self) -> Path:
        """Where this sheet will be written."""
        return self._path

    @property
    def size(self) -> tuple[float, float]:
        """The drawable ``(width, height)`` inside the title block."""
        w, h = PAPER.get(self._paper, PAPER["A4"])
        return (w - 2 * _MARGIN, h - 2 * _MARGIN)

    def add_sheet(
        self,
        name: str,
        filename: str,
        x: float,
        y: float,
        *,
        width: float = 38.1,
        height: float = 25.4,
        ports: tuple[tuple[str, str], ...] = (),
    ) -> SheetRef:
        """Put a child sheet on this one, and return where its ports landed."""
        return _hierarchy.add_sheet(
            self, name, filename, x, y, width=width, height=height, ports=ports
        )

    def save(self, *, validate: bool = False) -> Path:
        """Write the sheet to disk and return its path.

        Written to a neighbouring temp file and moved into place, so the sheet
        is never half a file. That matters more than it used to: with autosave
        on, this runs after every write rather than once at the end, and
        something else may be reading -- the monitor renders the file the
        moment it changes, and KiCad may have it open.

        If *validate* is true, KiCad parses the complete temporary file before
        it replaces the last known-good destination. ERC findings are allowed;
        failure to load or parse is not.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self._path.with_name(f".{self._path.stem}.writing{self._path.suffix}")
        try:
            scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            if validate:
                from ..cli import cli

                cli.erc(scratch)
            os.replace(scratch, self._path)  # atomic on the same volume
        except Exception:
            scratch.unlink(missing_ok=True)
            raise
        return self._path

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Rollback the in-memory tree and UUID registry when a write fails."""
        tree = copy.deepcopy(self._tree)
        uids = set(self._uids)
        try:
            yield
        except Exception:
            self._tree = tree
            self._uids = uids
            raise

    def find_symbols(self, query: str, limit: int = 20) -> list[SymbolDef]:
        """Library symbols whose ``Library:Symbol`` id contains *query*."""
        return _symbols.find_symbols(self, query, limit)

    def symbol(self, lib_id: str, *, unit: int = 1) -> SymbolDef:
        """One unit of a symbol, with its pins at the symbol origin."""
        return _symbols.symbol(self, lib_id, unit=unit)

    @staticmethod
    def _prop_of(definition: Node, name: str) -> Node | None:
        """The ``(property "<name>" ...)`` node of a symbol, if it has one."""
        for prop in definition.get_all("property"):
            if _text(prop) == name:
                return prop
        return None

    def _load(self, lib_id: str) -> library.LibrarySymbol:
        """Load and cache a library symbol."""
        if lib_id not in self._defs:
            self._defs[lib_id] = library.load_symbol(lib_id, self._path.parent)
        return self._defs[lib_id]

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
        """Put one unit of *lib_id* on the sheet at ``(x, y)`` as *ref*."""
        return _components.place(
            self,
            lib_id,
            ref,
            x,
            y,
            value=value,
            rotation=rotation,
            mirror=mirror,
            unit=unit,
        )

    def measure_placement(
        self, placements: tuple[PartPlacement, ...]
    ) -> PlacementMeasurement:
        """Apply explicit poses to an in-memory clone and measure the result."""
        return _placement.measure_placement(self, placements)

    def _placement_bounds(self) -> list[PlacementBounds]:
        """Symbol and visible-field rectangles in sheet coordinates."""
        return _placement._placement_bounds(self)

    def _placement_findings(
        self, bounds: tuple[PlacementBounds, ...]
    ) -> list[LayoutFinding]:
        """Conservative overlap and page-boundary facts for placement bounds."""
        return _placement._placement_findings(self, bounds)

    def _instances(self, ref: str, unit: int = 1) -> Node:
        """The ``(instances ...)`` block tying a symbol to this sheet."""
        return _components._instances(self, ref, unit)

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
        """One ``(property ...)`` node on a placed symbol."""
        return _fields._property(self, name, value, x, y, hide=hide, justify=justify)

    def _layout_fields(
        self, node: Node, lib_id: str, rotation: float, mirror: str = "", unit: int = 1
    ) -> None:
        """Put a part's Reference and Value where no wire is going to be."""
        return _fields._layout_fields(self, node, lib_id, rotation, mirror, unit)

    @staticmethod
    def _justify(prop: Node, justify: str) -> None:
        """Set or clear a field's justification, leaving the rest alone."""
        return _fields._justify(prop, justify)

    def _ensure_lib_symbol(self, lib_id: str, sym: library.LibrarySymbol) -> None:
        """Copy a symbol's definition into the sheet's ``lib_symbols``."""
        return _components._ensure_lib_symbol(self, lib_id, sym)

    def _find(self, ref: str, unit: int = 1) -> Node | None:
        """The ``(symbol ...)`` node placed as *ref*'s *unit*, if any."""
        for node in self._tree.get_all("symbol"):
            if node.get("lib_id") is None:
                continue
            prop = self._prop_of(node, "Reference")
            if (
                prop is not None
                and _text(prop, 1) == ref
                and int(_f(node.get("unit"), 0, 1)) == unit
            ):
                return node
        return None

    def _require(self, ref: str, unit: int = 1) -> Node:
        """The node for *ref*'s *unit*, or a :class:`LookupError`."""
        node = self._find(ref, unit)
        if node is None:
            raise LookupError(f"{ref} unit {unit} is not on the sheet")
        return node

    def part(self, ref: str, *, unit: int = 1) -> Part:
        """One placed unit, with its pins at sheet positions."""
        return _components.part(self, ref, unit=unit)

    def _as_part(self, node: Node, ref: str) -> Part:
        """Build a :class:`Part` from a placed ``(symbol ...)`` node."""
        return _components._as_part(self, node, ref)

    def parts(self) -> list[Part]:
        """Every placed part, in reference order."""
        return _components.parts(self)

    def move(self, ref: str, x: float, y: float, *, unit: int = 1) -> Part:
        """Move a placed part. Its pins move with it."""
        return _components.move(self, ref, x, y, unit=unit)

    def rotate(self, ref: str, rotation: float, *, unit: int = 1) -> Part:
        """Set a placed part's rotation in degrees."""
        return _components.rotate(self, ref, rotation, unit=unit)

    def mirror(self, ref: str, axis: str, *, unit: int = 1) -> Part:
        """Mirror a placed part about ``"x"``, ``"y"`` or ``""`` for neither."""
        return _components.mirror(self, ref, axis, unit=unit)

    def remove(self, ref: str, *, unit: int = 1) -> None:
        """Take one placed unit off the sheet."""
        return _components.remove(self, ref, unit=unit)

    def _at_point(self, kinds: tuple[str, ...], x: float, y: float) -> list[Node]:
        """Every node of these kinds whose ``at`` is this snapped point."""
        return _connections._at_point(self, kinds, x, y)

    def _wires_between(self, x1: float, y1: float, x2: float, y2: float) -> list[Node]:
        """Wire nodes joining these two snapped points, either way round."""
        return _connections._wires_between(self, x1, y1, x2, y2)

    def remove_wire(self, x1: float, y1: float, x2: float, y2: float) -> int:
        """Delete wires running between these two points."""
        return _connections.remove_wire(self, x1, y1, x2, y2)

    def move_wire(
        self, x1: float, y1: float, x2: float, y2: float, dx: float, dy: float
    ) -> int:
        """Shift wires between these points by ``(dx, dy)``."""
        return _connections.move_wire(self, x1, y1, x2, y2, dx, dy)

    def remove_label(self, x: float, y: float) -> int:
        """Delete labels at this point, of any kind."""
        return _connections.remove_label(self, x, y)

    def _label_from_node(self, node: Node) -> Label:
        """Describe one stored label without exposing its S-expression."""
        return _connections._label_from_node(self, node)

    def _label_node(self, uuid: str) -> Node:
        """The label carrying *uuid*, or a useful refusal."""
        return _connections._label_node(self, uuid)

    def remove_label_by_id(self, uuid: str) -> None:
        """Delete exactly one label by stable identity."""
        return _connections.remove_label_by_id(self, uuid)

    def move_label(self, x: float, y: float, dx: float, dy: float) -> int:
        """Shift labels at this point by ``(dx, dy)``."""
        return _connections.move_label(self, x, y, dx, dy)

    def move_label_by_id(self, uuid: str, dx: float, dy: float) -> Label:
        """Shift exactly one label by stable identity."""
        return _connections.move_label_by_id(self, uuid, dx, dy)

    def rotate_label(self, x: float, y: float, rotation: float) -> int:
        """Turn labels at this point."""
        return _connections.rotate_label(self, x, y, rotation)

    def rotate_label_by_id(self, uuid: str, rotation: float) -> Label:
        """Turn exactly one label by stable identity."""
        return _connections.rotate_label_by_id(self, uuid, rotation)

    def remove_junction(self, x: float, y: float) -> int:
        """Delete junctions at this point."""
        return _connections.remove_junction(self, x, y)

    def remove_no_connect(self, x: float, y: float) -> int:
        """Delete no-connect marks at this point."""
        return _connections.remove_no_connect(self, x, y)

    def _sheet_node(self, name: str) -> Node:
        """The child-sheet box called *name*."""
        return _hierarchy._sheet_node(self, name)

    def _sheet_ref(self, node: Node) -> SheetRef:
        """Describe a child-sheet box that is already on the sheet."""
        return _hierarchy._sheet_ref(self, node)

    def move_sheet(self, name: str, x: float, y: float) -> SheetRef:
        """Move a child-sheet box, and say where its ports ended up."""
        return _hierarchy.move_sheet(self, name, x, y)

    def remove_sheet(self, name: str) -> None:
        """Take a child-sheet box off this sheet. The child FILE is left."""
        return _hierarchy.remove_sheet(self, name)

    def remove_field(self, ref: str, name: str, *, unit: int = 1) -> dict[str, str]:
        """Delete a field from a part and return the fields it has left."""
        return _fields.remove_field(self, ref, name, unit=unit)

    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a part's fields and return all of them."""
        return _fields.set_field(self, ref, name, value)

    def move_field(
        self,
        ref: str,
        name: str,
        dx: float,
        dy: float,
        *,
        rotation: float | None = None,
        justify: str = "",
    ) -> Point:
        """Move a field relative to its part, and return where it landed."""
        return _fields.move_field(
            self, ref, name, dx, dy, rotation=rotation, justify=justify
        )

    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a part, by name."""
        return _fields.fields(self, ref)

    def pin(self, ref: str, pin: str) -> Point:
        """Where *ref*'s *pin* is on the sheet -- the point to wire to."""
        return _components.pin(self, ref, pin)

    def wire(self, x1: float, y1: float, x2: float, y2: float) -> list[Point]:
        """Draw one straight wire segment and return its ends."""
        return _connections.wire(self, x1, y1, x2, y2)

    def junction(self, x: float, y: float) -> Point:
        """Mark a point where crossing wires connect."""
        return _connections.junction(self, x, y)

    def label(
        self,
        x: float,
        y: float,
        text: str,
        *,
        kind: str = "local",
        rotation: float = 0.0,
        justify: str = "left",
    ) -> Label:
        """Attach a net name at a point."""
        return _connections.label(
            self, x, y, text, kind=kind, rotation=rotation, justify=justify
        )

    def power(self, x: float, y: float, net: str, *, rotation: float = 0.0) -> Part:
        """Place a power symbol for *net* and return it."""
        return _components.power(self, x, y, net, rotation=rotation)

    def power_flag(self, x: float, y: float, *, rotation: float = 0.0) -> Part:
        """Place a PWR_FLAG, which tells ERC a net is driven."""
        return _components.power_flag(self, x, y, rotation=rotation)

    def _next_hash_ref(self, prefix: str) -> str:
        """The next free ``#PWR0001``-style reference."""
        return _components._next_hash_ref(self, prefix)

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 1.27,
        rotation: float = 0.0,
        bold: bool = False,
        justify: str = "left",
    ) -> Point:
        """Write a note on the sheet. It connects nothing and ERC ignores it."""
        return _connections.text(
            self, x, y, text, size=size, rotation=rotation, bold=bold, justify=justify
        )

    def no_connect(self, x: float, y: float) -> Point:
        """Mark a pin deliberately unconnected."""
        return _connections.no_connect(self, x, y)

    def scene(
        self, region: SceneBounds | None = None, *, max_objects: int = 2000
    ) -> SceneSnapshot:
        """Return spatial objects and conservative bounds without rendering."""
        from .scene import snapshot

        return snapshot(self, region, max_objects=max_objects)

    def wires(self) -> list[tuple[Point, Point]]:
        """Every wire segment on the sheet."""
        return _connections.wires(self)

    def labels(self) -> list[Label]:
        """Every label, including stable identity for later editing."""
        return _connections.labels(self)

    @contextlib.contextmanager
    def _scratch(self) -> Iterator[Path]:
        """The sheet as a file on disk, for a tool that needs a path.

        Written BESIDE the real file, not in a temp directory. The sheet in
        memory may be ahead of what is on disk, so a copy is needed -- but a
        root sheet names its children by relative filename, and a copy off in
        a temp directory cannot see them. That produced a design reporting
        eight unmatched ports and no nets at all, which reads exactly like a
        wiring mistake and was not one.
        """
        scratch = self._path.parent / f".{self._path.stem}.scratch.kicad_sch"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        try:
            yield scratch
        finally:
            scratch.unlink(missing_ok=True)

    def nets(self) -> list[Net]:
        """What this sheet actually connects, from KiCad's own netlist."""
        return _validation.nets(self)

    def check(self) -> list[Finding]:
        """Every violation, mapped from a position back to a part and pin."""
        return _validation.check(self)

    def _visible_text(self) -> list[_VisibleText]:
        """Fields, labels and notes visible on this page."""
        return _validation._visible_text(self)

    def _check_layout_page(self, page: str) -> list[LayoutFinding]:
        """Graphical findings for this page, without following child sheets."""
        return _validation._check_layout_page(self, page)

    def check_layout(self) -> list[LayoutFinding]:
        """Return potential graphical collisions across this hierarchy."""
        return _validation.check_layout(self)

    def render(
        self,
        *,
        output_dir: Path | None = None,
        dpi: int = 150,
        black_and_white: bool = False,
        pages: str | None = None,
    ) -> list[Path]:
        """Render the sheet to PNG, one per page, via kicad-cli."""
        from .. import render as _render

        return _render.export_png(
            self._path,
            output_dir=output_dir,
            dpi=dpi,
            black_and_white=black_and_white,
            pages=pages,
        )

    def next_ref(self, prefix: str) -> str:
        """The next unused reference with this prefix."""
        used = set()
        for part in self.parts():
            if part.ref.startswith(prefix):
                tail = part.ref[len(prefix) :]
                if tail.isdigit():
                    used.add(int(tail))
        n = 1
        while n in used:
            n += 1
        return f"{prefix}{n}"

    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What meets at a point: pins, wire ends and labels."""

        def near(px: float, py: float) -> bool:
            return abs(px - x) <= radius and abs(py - y) <= radius

        pins = [
            {"ref": part.ref, "pin": pin.number, "name": pin.name}
            for part in self.parts()
            for pin in part.pins
            if near(pin.at.x, pin.at.y)
        ]
        ends = sum(1 for a, b in self.wires() if near(a.x, a.y) or near(b.x, b.y))
        labels = [lab.as_dict() for lab in self.labels() if near(lab.at.x, lab.at.y)]
        return {
            "x": round(x, 3),
            "y": round(y, 3),
            "pins": pins,
            "wire_ends": ends,
            "labels": labels,
            "connected": len(pins) + ends + len(labels) > 1,
        }


def create(
    path: str | Path, *, paper: str = "A4", title: str = "", instance_path: str = ""
) -> Sheet:
    """Make a new, empty sheet and return it open for editing.

    Typed as the abstract :class:`~kicad_flow.schematic.api.Sheet`, not as the
    concrete class, so a caller who takes their type from the return value
    binds to the interface rather than to this backend.
    """
    if paper not in PAPER:
        raise ValueError(
            f"paper must be A4 or A3, not {paper!r}. There is deliberately "
            f"nothing larger: a page too big for A3 is one nobody reads. Put "
            f"the overflow on another sheet with add_sheets instead."
        )
    tree = _node(
        "kicad_sch",
        [
            _node("version", [20250114]),
            _node("generator", ["kicad_flow"]),
            _node("generator_version", ["10.0"]),
            _node("uuid", [_uid_from(f"file:{Path(path).name}")]),
            _node("paper", [paper]),
            _node("lib_symbols", []),
            _node(
                "sheet_instances",
                [
                    _node("path", ["/", _node("page", ["1"])]),
                ],
            ),
        ],
    )
    if title:
        tree.items.insert(
            5,
            _node(
                "title_block",
                [
                    _node("title", [title]),
                ],
            ),
        )
    return KiCadSheet(Path(path), tree, paper, instance_path)


def load(path: str | Path, *, instance_path: str = "") -> Sheet:
    """Open an existing sheet.

    *instance_path* matters only for a child in a hierarchy; see
    :meth:`KiCadSheet._where`.
    """
    file = Path(path)
    tree = loads(file.read_text(encoding="utf-8"))
    return KiCadSheet(file, tree, _text(tree.get("paper"), 0, "A4"), instance_path)


__all__ = ["GRID", "PAPER", "KiCadSheet", "create", "load"]
