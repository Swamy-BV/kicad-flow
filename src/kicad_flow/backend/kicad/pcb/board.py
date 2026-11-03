"""Typed KiCadBoard facade, file lifecycle and shared state."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import math
import tempfile
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kicad_flow.pcb.api import Board
from kicad_flow.pcb.routing import RoutePath
from kicad_flow.pcb.types import (
    BoardLimits,
    BoardRule,
    BoardText,
    Connection,
    Constraint,
    Finding,
    Footprint,
    FootprintDef,
    Graphic,
    Net,
    NetClass,
    NetClassAssignment,
    NetClassPattern,
    NetConnectivity,
    Pad,
    PlacementMeasurement,
    PlacementNetLength,
    PlacementProposal,
    Point,
    RouteMetric,
    Stackup,
    TextBounds,
    Track,
    Via,
    Zone,
)

from .. import render as _render
from .._fileio import replace_file
from .._sexpr import Node, Sym, dumps, loads
from . import connectivity as _connectivity
from . import copper as _copper
from . import footprints as _footprints
from . import graphics as _graphics
from . import grid as _grid
from . import inspection as _inspection
from . import native_types as _native
from . import placement as _placement
from . import routing_exchange as _routing_exchange
from . import settings as _settings
from . import validation as _validation
from ._constants import (
    _copper_names,
)
from ._geometry import (
    _arc_extrema,
)
from ._nodes import (
    _node,
    _text,
    _uid,
)
from .ipc import Document, edited, observed, unavailable


class KiCadBoard(Board):
    """A ``.kicad_pcb`` file, edited through the primitive API."""

    def __init__(self, path: Path, tree: Node, ipc: Document | None = None) -> None:
        """Wrap an already-parsed board. Use :func:`create` or :func:`load`."""
        self._path = Path(path)
        self._ipc = ipc
        if ipc is not None:
            self._finalizer = weakref.finalize(self, ipc.close)
        self._tree = tree
        self._defs: dict[str, Node] = {}
        self._disk_revision = self._file_revision()

    def _refresh(self) -> None:
        """Refresh the read-only model from the actual native document."""
        if self._ipc is not None:
            self._tree = loads(self._ipc.snapshot())

    def _file_revision(self) -> str | None:
        """Hash the exact board bytes, or report that no file exists."""
        if not self._path.is_file():
            return None
        return hashlib.sha256(self._path.read_bytes()).hexdigest()

    def _accept_disk_revision(self) -> None:
        """Record a disk change performed synchronously by this board object."""
        self._disk_revision = self._file_revision()

    def _copy_to(self, path: Path) -> KiCadBoard:
        """An isolated in-memory copy for read-only candidate checks."""
        if self._ipc is not None:
            self._ipc.copy_to(path)
            document = Document(path, create=False, mode="headless")
            return KiCadBoard(path, loads(document.snapshot()), document)
        duplicate = KiCadBoard(path, copy.deepcopy(self._tree))
        duplicate._defs = self._defs
        return duplicate

    @property
    def path(self) -> Path:
        """Where this board will be written."""
        return self._path

    @property
    @observed
    def size(self) -> tuple[float, float]:
        """The outline's ``(width, height)``, or ``(0, 0)`` if undrawn."""
        points: list[Point] = []
        for graphic in self.graphics("Edge.Cuts"):
            if graphic.kind == "circle":
                centre, rim = graphic.points
                radius = math.hypot(rim.x - centre.x, rim.y - centre.y)
                points.extend(
                    [
                        Point(centre.x - radius, centre.y - radius),
                        Point(centre.x + radius, centre.y + radius),
                    ]
                )
            elif graphic.kind == "arc":
                points.extend(_arc_extrema(graphic.points))
            else:
                points.extend(graphic.points)
        if not points:
            return (0.0, 0.0)
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))

    @property
    @observed
    def layers(self) -> tuple[str, ...]:
        """Every copper layer, front to back."""
        table = self._tree.get("layers")
        out = []
        for entry in table.items[1:] if table is not None else []:
            if isinstance(entry, Node) and _text(entry, 1) == "signal":
                out.append(_text(entry, 0))
        return tuple(out)

    def set_layers(self, count: int) -> tuple[str, ...]:
        """Set the copper layer count and return the new layers."""
        names = _copper_names(count)
        if self._ipc is not None:
            self._ipc.rpc.call("set_layers", count=count)
            self._refresh()
            return names
        table = self._tree.get("layers")
        if table is None:
            raise LookupError("board has no layer table")
        # Numbering is KiCad's: F.Cu is 0, B.Cu is 2, inner layers run from 4.
        ids = {"F.Cu": 0, "B.Cu": 2}
        for index, name in enumerate(names[1:-1], start=1):
            ids[name] = 2 + 2 * index
        keep = [
            e
            for e in table.items[1:]
            if not (isinstance(e, Node) and _text(e, 1) == "signal")
        ]
        table.items = (
            [table.items[0]]
            + [_node(str(ids[n]), [n, Sym("signal")]) for n in names]
            + keep
        )
        return names

    def placement_grid(self) -> float:
        """Read KiCadFlow's footprint-origin spacing."""
        return _grid.spacing(self._path)

    def set_placement_grid(self, spacing: float) -> float:
        """Persist an explicit spacing without modifying copper or pads."""
        return _grid.set_spacing(self._path, spacing)

    def save(self, *, validate: bool = False) -> Path:
        """Atomically write the board and optionally prove KiCad can load it."""
        if self._ipc is not None:
            return self._ipc.save()
        self.assert_current()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self._path.with_name(f".{self._path.stem}.writing{self._path.suffix}")
        try:
            scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            if validate:
                from ..cli import cli

                cli.drc(scratch)
            self.assert_current()
            replace_file(scratch, self._path)  # atomic on the same volume
            self._accept_disk_revision()
        except Exception:
            scratch.unlink(missing_ok=True)
            raise
        return self._path

    def assert_current(self) -> None:
        """Refuse to overwrite a board changed outside this open object."""
        if self._ipc is not None:
            self._ipc.assert_current()
            return
        current = self._file_revision()
        if current != self._disk_revision:
            raise RuntimeError(
                f"board changed on disk since it was opened: {self._path}. "
                "Call reload_board to use the external version or close_board "
                "to discard this cached board."
            )

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Rollback the in-memory board if a composed primitive write fails."""
        if self._ipc is not None:
            with self._ipc.transaction():
                yield
            self._refresh()
            return
        tree = copy.deepcopy(self._tree)
        definitions = copy.deepcopy(self._defs)
        try:
            yield
        except Exception:
            self._tree = tree
            self._defs = definitions
            raise

    def set_stackup(self, stackup: Stackup) -> Stackup:
        """Replace only the board's stackup, preserving all other setup."""
        if self._ipc is not None:
            unavailable("set_stackup")
        return _settings.set_stackup(self, stackup)

    @observed
    def stackup(self) -> Stackup:
        """Read the physical construction without interpreting its choices."""
        return _settings.stackup(self)

    @property
    @observed
    def thickness(self) -> float:
        """The finished thickness recorded in the board general block."""
        return _settings.thickness(self)

    def set_limits(self, limits: BoardLimits) -> BoardLimits:
        """Update board-wide manufacturing limits without changing geometry."""
        if self._ipc is not None:
            v = self._ipc.rpc.call(
                "settings", operation="limits", values=limits.as_dict()
            )["values"]
            return BoardLimits(**v)
        return _settings.set_limits(self, limits)

    def limits(self) -> BoardLimits:
        """Read project-wide limits and the mask bridge stored on the board."""
        if self._ipc is not None:
            v = self._ipc.rpc.call("settings", operation="limits", values=None)[
                "values"
            ]
            return BoardLimits(**v)
        return _settings.limits(self)

    def set_net_classes(self, classes: tuple[NetClass, ...]) -> list[NetClass]:
        """Create or update netclasses in the sibling project."""
        if self._ipc is not None:
            v = self._ipc.rpc.call(
                "settings",
                operation="net_classes",
                values=[c.as_dict() for c in classes],
            )["values"]
            return [NetClass(**item) for item in v]
        return _settings.set_net_classes(self, classes)

    def net_classes(self) -> list[NetClass]:
        """Read every project netclass."""
        if self._ipc is not None:
            v = self._ipc.rpc.call("settings", operation="net_classes", values=None)[
                "values"
            ]
            return [NetClass(**item) for item in v]
        return _settings.net_classes(self)

    def set_net_class_patterns(
        self, patterns: tuple[NetClassPattern, ...]
    ) -> list[NetClassPattern]:
        """Replace persistent net-class assignment patterns in the project."""
        if self._ipc is not None:
            v = self._ipc.rpc.call(
                "settings", operation="patterns", values=[p.as_dict() for p in patterns]
            )["values"]
            return [NetClassPattern(**item) for item in v]
        return _settings.set_net_class_patterns(self, patterns)

    def net_class_patterns(self) -> list[NetClassPattern]:
        """Read the project's net-class assignment patterns."""
        if self._ipc is not None:
            v = self._ipc.rpc.call("settings", operation="patterns", values=None)[
                "values"
            ]
            return [NetClassPattern(**item) for item in v]
        return _settings.net_class_patterns(self)

    def assign_net_classes(
        self, assignments: tuple[NetClassAssignment, ...]
    ) -> list[NetClassAssignment]:
        """Replace memberships for the nets mentioned by the caller."""
        if self._ipc is not None:
            v = self._ipc.rpc.call(
                "settings",
                operation="assignments",
                values=[a.as_dict() for a in assignments],
            )["values"]
            return [NetClassAssignment(**item) for item in v]
        return _settings.assign_net_classes(self, assignments)

    def net_policy(self, nets: tuple[str, ...]) -> dict[str, object]:
        """Read effective classes with KiCad's own inheritance resolver."""
        if self._ipc is not None:
            return self._ipc.rpc.call("net_policy", nets=nets)
        return _settings.net_policy(self, nets)

    def net_class_assignments(self) -> list[NetClassAssignment]:
        """Read explicit net-to-netclass memberships."""
        if self._ipc is not None:
            v = self._ipc.rpc.call("settings", operation="assignments", values=None)[
                "values"
            ]
            return [NetClassAssignment(**item) for item in v]
        return _settings.net_class_assignments(self)

    def set_rules(self, rules: tuple[BoardRule, ...]) -> list[BoardRule]:
        """Create or update named custom design rules."""
        if self._ipc is not None:
            replacing = {rule.name for rule in rules}
            if any(
                rule.layer and rule.name not in replacing
                for rule in _settings.rules(self)
            ):
                unavailable(
                    "updating rules while native IPC omits an untouched "
                    "rule's layer condition"
                )
            v = self._ipc.rpc.call(
                "settings", operation="rules", values=[r.as_dict() for r in rules]
            )["values"]
            return self._native_rules(v)
        return _settings.set_rules(self, rules)

    def rules(self) -> list[BoardRule]:
        """Read numeric custom design rules."""
        if self._ipc is not None:
            v = self._ipc.rpc.call("settings", operation="rules", values=None)["values"]
            return self._native_rules(v)
        return _settings.rules(self)

    @edited
    def graphic(
        self,
        kind: str,
        points: list[tuple[float, float]],
        *,
        layer: str,
        width: float = 0.1,
        fill: bool = False,
    ) -> Graphic:
        """Draw one outline or silkscreen primitive and return it."""
        return _graphics.graphic(
            self, kind, points, layer=layer, width=width, fill=fill
        )

    @observed
    def graphics(self, layer: str = "") -> list[Graphic]:
        """Every outline and silkscreen primitive, in file order."""
        return _graphics.graphics(self, layer)

    def outline_polygon(self, *, inset: float, max_error: float) -> tuple[Point, ...]:
        """Return KiCad's resolved outside contour as bounded-error points."""
        if self._ipc is not None:
            from .outline import polygon

            return polygon(self.graphics("Edge.Cuts"), inset, max_error)
        return _graphics.outline_polygon(self, inset=inset, max_error=max_error)

    @edited
    def move_graphic(self, uuid: str, dx: float, dy: float) -> Graphic:
        """Shift one graphic primitive by an offset."""
        return _graphics.move_graphic(self, uuid, dx, dy)

    @edited
    def remove_graphic(self, uuid: str) -> None:
        """Remove one graphic primitive by UUID."""
        return _graphics.remove_graphic(self, uuid)

    @staticmethod
    def _native_rules(values: list[dict[str, Any]]) -> list[BoardRule]:
        """Read the native numeric rules using the existing contract."""
        return [
            BoardRule(
                item["name"],
                item["condition"],
                tuple(
                    Constraint(c["kind"], c.get("min"), c.get("opt"), c.get("max"))
                    for c in item["constraints"]
                ),
                item.get("layer", ""),
            )
            for item in values
        ]

    def _graphic_node(self, uuid: str) -> Node:
        """The top-level graphic carrying *uuid*."""
        return _graphics._graphic_node(self, uuid)

    def _graphic_from_node(self, node: Node) -> Graphic:
        """Read one KiCad graphical node into the board contract."""
        return _graphics._graphic_from_node(self, node)

    def _text_node(self, uuid: str) -> Node:
        """The top-level literal board text carrying *uuid*."""
        return _graphics._text_node(self, uuid)

    def _text_from_node(self, node: Node) -> BoardText:
        """Read one KiCad text node into the board contract."""
        return _graphics._text_from_node(self, node)

    def find_footprints(self, query: str, limit: int = 20) -> list[FootprintDef]:
        """Library footprints whose ``Library:Footprint`` id contains *query*."""
        return _footprints.find_footprints(self, query, limit)

    def footprint_def(self, fp_id: str) -> FootprintDef:
        """One library footprint, with its pads at the footprint origin."""
        return _footprints.footprint_def(self, fp_id)

    def _load_def(self, fp_id: str) -> Node:
        """Load and cache a library footprint's tree."""
        return _footprints._load_def(self, fp_id)

    @edited
    def place(
        self,
        fp_id: str,
        ref: str,
        x: float,
        y: float,
        *,
        anchor: str = "origin",
        rotation: float = 0.0,
        side: str = "F",
        value: str = "",
    ) -> Footprint:
        """Put *fp_id* on the board at ``(x, y)`` as *ref*."""
        return _footprints.place(
            self,
            fp_id,
            ref,
            x,
            y,
            anchor=anchor,
            rotation=rotation,
            side=side,
            value=value,
        )

    @staticmethod
    def _mirror(node: Node) -> None:
        """Flip a footprint's stored geometry to the other side of the board."""
        return _footprints._mirror(node)

    @staticmethod
    def _set_child(node: Node, name: str, atoms: list[Any]) -> None:
        """Replace or append a single child node."""
        existing = node.get(name)
        if existing is not None:
            node.items.remove(existing)
        node.items.insert(2, _node(name, atoms))

    @staticmethod
    def _prop_of(node: Node, name: str) -> Node | None:
        """The ``(property "<name>" ...)`` of a footprint, if it has one."""
        for prop in node.get_all("property"):
            if _text(prop) == name:
                return prop
        return None

    def _set_property(self, node: Node, name: str, value: str) -> None:
        """Set a footprint property, adding it if absent."""
        return _footprints._set_property(self, node, name, value)

    def _find(self, ref: str) -> Node | None:
        """The ``(footprint ...)`` node placed as *ref*, if any."""
        return _footprints._find(self, ref)

    def _require(self, ref: str) -> Node:
        """The node for *ref*, or a :class:`LookupError`."""
        return _footprints._require(self, ref)

    @edited
    def move(
        self, ref: str, x: float, y: float, *, anchor: str = "origin"
    ) -> Footprint:
        """Move a footprint by origin or courtyard centre; copper stays."""
        return _footprints.move(self, ref, x, y, anchor=anchor)

    @edited
    def rotate(self, ref: str, rotation: float) -> Footprint:
        """Set a placed footprint's rotation in degrees."""
        return _footprints.rotate(self, ref, rotation)

    @edited
    def flip(self, ref: str, side: str) -> Footprint:
        """Put a footprint on ``"F"`` or ``"B"``."""
        return _footprints.flip(self, ref, side)

    @edited
    def remove(self, ref: str) -> None:
        """Take a footprint off the board."""
        return _footprints.remove(self, ref)

    @observed
    def footprints(self) -> list[Footprint]:
        """Every placed footprint, in reference order."""
        if self._ipc is not None:
            return sorted(
                (
                    _native.footprint(item)
                    for item in self._ipc.rpc.call("footprints")["items"]
                ),
                key=lambda f: f.ref,
            )
        return _footprints.footprints(self)

    @observed
    def footprint(self, ref: str) -> Footprint:
        """One placed footprint, with its pads at board positions."""
        if self._ipc is not None:
            return _native.footprint(
                self._ipc.rpc.call("footprints", ref=ref)["items"][0]
            )
        return _footprints.footprint(self, ref)

    @observed
    def measure_placement(
        self,
        proposals: tuple[PlacementProposal, ...] = (),
        *,
        edge_clearance: float = 0.0,
        edge_exempt_refs: tuple[str, ...] = (),
    ) -> PlacementMeasurement:
        """Measure current or proposed poses without modifying this board."""
        if self._ipc is not None and proposals:
            with tempfile.TemporaryDirectory(dir=self._path.parent) as name:
                candidate = self._copy_to(Path(name) / self._path.name)
                try:
                    for proposal in proposals:
                        if proposal.side is not None:
                            candidate.flip(proposal.ref, proposal.side)
                        if proposal.rotation is not None:
                            candidate.rotate(proposal.ref, proposal.rotation)
                        candidate.move(
                            proposal.ref, proposal.x, proposal.y, anchor=proposal.anchor
                        )
                    return candidate._measure_current_placement(
                        edge_clearance, edge_exempt_refs
                    )
                finally:
                    if candidate._ipc is not None:
                        candidate._ipc.close()
        return _placement.measure_placement(
            self,
            proposals,
            edge_clearance=edge_clearance,
            edge_exempt_refs=edge_exempt_refs,
        )

    def _measure_current_placement(
        self, edge_clearance: float, edge_exempt_refs: tuple[str, ...]
    ) -> PlacementMeasurement:
        """Measure the current tree; caller choices have already been applied."""
        return _placement._measure_current_placement(
            self, edge_clearance, edge_exempt_refs
        )

    @staticmethod
    def _inside_or_on_outline(point: Point, outline: list[Point]) -> bool:
        """Whether a point is inside or on the sampled board outline."""
        return _placement._inside_or_on_outline(point, outline)

    def _placement_net_lengths(
        self, footprints: list[Footprint]
    ) -> list[PlacementNetLength]:
        """Euclidean minimum-spanning-tree length for every multi-pad net."""
        return _placement._placement_net_lengths(self, footprints)

    def _as_footprint(self, node: Node, ref: str) -> Footprint:
        """Build a :class:`Footprint` from a placed ``(footprint ...)``."""
        return _footprints._as_footprint(self, node, ref)

    def _pad_of(self, node: Node, at: Point, rotation: float) -> Pad:
        """Build a :class:`Pad` from a ``(pad ...)`` node."""
        return _footprints._pad_of(self, node, at, rotation)

    @observed
    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a footprint, by name."""
        return _footprints.fields(self, ref)

    @edited
    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a footprint's fields and return all of them."""
        return _footprints.set_field(self, ref, name, value)

    @edited
    def move_field(
        self,
        ref: str,
        name: str,
        dx: float,
        dy: float,
        *,
        rotation: float | None = None,
        layer: str = "",
        hide: bool | None = None,
    ) -> Point:
        """Move a footprint's field, relative to the footprint's position."""
        return _footprints.move_field(
            self, ref, name, dx, dy, rotation=rotation, layer=layer, hide=hide
        )

    @edited
    def set_net(self, ref: str, pad: str, net: str) -> str:
        """Put EVERY pad of *ref* numbered *pad* on *net*, and return the net."""
        return _footprints.set_net(self, ref, pad, net)

    @observed
    def pad(self, ref: str, pad: str) -> Point:
        """Where *ref*'s *pad* is on the board -- the point to route to."""
        if self._ipc is not None:
            found = self.footprint(ref).pad(pad)
            if found is None:
                raise LookupError(f"{ref} has no pad {pad}")
            return found.at
        return _footprints.pad(self, ref, pad)

    @edited
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
        """Lay one straight copper segment and return it."""
        return _copper.track(self, x1, y1, x2, y2, layer=layer, width=width, net=net)

    @edited
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
        """Drill an explicitly typed plated via joining *layers*."""
        return _copper.via(
            self,
            x,
            y,
            net=net,
            diameter=diameter,
            drill=drill,
            layers=layers,
            kind=kind,
        )

    @edited
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
        """Pour copper inside *points* on *layer*, or fence a region off."""
        return _copper.zone(
            self,
            points,
            layer=layer,
            net=net,
            clearance=clearance,
            pad_connection=pad_connection,
            min_thickness=min_thickness,
            thermal_gap=thermal_gap,
            thermal_spoke_width=thermal_spoke_width,
            priority=priority,
            island_removal=island_removal,
            min_island_area=min_island_area,
            forbids=forbids,
        )

    def refill(self) -> int:
        """Recompute every pour against the copper as it now stands."""
        if self._ipc is not None:
            count = int(self._ipc.rpc.call("refill")["count"])
            self._refresh()
            return count
        return _copper.refill(self)

    @edited
    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        layer: str,
        width: float = 1.0,
        height: float = 1.0,
        thickness: float = 0.15,
        rotation: float = 0.0,
        mirror: bool = False,
        justify: str = "center",
        vertical_justify: str = "center",
    ) -> BoardText:
        """Put one identified literal text item on a board layer."""
        return _graphics.text(
            self,
            x,
            y,
            text,
            layer=layer,
            width=width,
            height=height,
            thickness=thickness,
            rotation=rotation,
            mirror=mirror,
            justify=justify,
            vertical_justify=vertical_justify,
        )

    def texts(self, layer: str = "") -> list[BoardText]:
        """Every literal board text item, in file order."""
        return _graphics.texts(self, layer)

    @edited
    def update_text(
        self,
        uuid: str,
        *,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        layer: str | None = None,
        width: float | None = None,
        height: float | None = None,
        thickness: float | None = None,
        rotation: float | None = None,
        mirror: bool | None = None,
        justify: str | None = None,
        vertical_justify: str | None = None,
    ) -> BoardText:
        """Change only explicitly supplied properties of one text UUID."""
        return _graphics.update_text(
            self,
            uuid,
            x=x,
            y=y,
            text=text,
            layer=layer,
            width=width,
            height=height,
            thickness=thickness,
            rotation=rotation,
            mirror=mirror,
            justify=justify,
            vertical_justify=vertical_justify,
        )

    @edited
    def remove_text(self, uuid: str) -> None:
        """Remove one literal board text item by UUID."""
        _graphics.remove_text(self, uuid)

    def text_bounds(self, uuids: tuple[str, ...] = ()) -> list[TextBounds]:
        """Return KiCad-measured rendered bounds for board texts."""
        if self._ipc is not None:
            return [
                TextBounds(**item)
                for item in self._ipc.rpc.call("bounds", uuids=uuids)["bounds"]
            ]
        return _graphics.text_bounds(self, uuids)

    @edited
    def remove_copper(
        self,
        *,
        uuid: str = "",
        net: str = "",
        layer: str = "",
        tracks: bool = True,
        vias: bool = True,
        zones: bool = False,
        all: bool = False,
    ) -> int:
        """Delete one UUID, or copper filtered by net and layer."""
        return _copper.remove_copper(
            self,
            uuid=uuid,
            net=net,
            layer=layer,
            tracks=tracks,
            vias=vias,
            zones=zones,
            all=all,
        )

    def _require_layer(self, name: str) -> None:
        """Refuse a layer the board does not have."""
        if name not in self.layers:
            raise ValueError(
                f"no layer {name!r} on this board; it has {list(self.layers)}"
            )

    @observed
    def tracks(self) -> list[Track]:
        """Every copper segment on the board."""
        return _copper.tracks(self)

    @observed
    def vias(self) -> list[Via]:
        """Every via on the board."""
        return _copper.vias(self)

    @observed
    def zones(self) -> list[Zone]:
        """Every pour and keep-out."""
        return _copper.zones(self)

    @observed
    def nets(self) -> list[Net]:
        """What the board is MEANT to connect, from its own pads."""
        return _connectivity.nets(self)

    @observed
    def connectivity(self, nets: tuple[str, ...] = ()) -> list[NetConnectivity]:
        """Return pad-bearing connected copper groups without selecting routes."""
        return _connectivity.connectivity(self, nets)

    @observed
    def route_metrics(self, nets: tuple[str, ...] = ()) -> list[RouteMetric]:
        """Measure authored copper on each intended net."""
        return _connectivity.route_metrics(self, nets)

    @observed
    def inspect_pair(
        self,
        first: RoutePath,
        second: RoutePath,
        *,
        gap_min: float,
        gap_max: float,
        max_uncoupled: float | None = None,
        max_skew: float | None = None,
    ) -> dict[str, object]:
        """Inspect caller-selected paths without inferring pair membership."""
        from .differential import inspect_pair

        return inspect_pair(
            self,
            first,
            second,
            gap_min=gap_min,
            gap_max=gap_max,
            max_uncoupled=max_uncoupled,
            max_skew=max_skew,
        )

    @observed
    def unrouted(self) -> list[Connection]:
        """A minimum set of pad-group separations, nearest endpoints first."""
        return _connectivity.unrouted(self)

    @observed
    def fabrication_files(
        self,
        output_dir: str | Path,
        kind: str,
        layers: tuple[str, ...] = (),
        *,
        include_map: bool = True,
    ) -> list[Path]:
        """Export native manufacturing data from an isolated snapshot."""
        from . import manufacturing

        self.assert_current()
        return manufacturing.fabrication_files(
            self,
            output_dir,
            kind,
            layers,
            include_map,
        )

    @observed
    def compare_fabrication_files(
        self,
        files: tuple[Path, ...],
        kind: str,
        layers: tuple[str, ...],
    ) -> list[tuple[str, str]]:
        """Compare native CAM records, ignoring native generation timestamps."""
        from .manufacturing import compare_files

        self.assert_current()
        return compare_files(self, files, kind, layers)

    @observed
    def placement_rows(self) -> list[dict[str, str]]:
        """Read actual assembly poses through the native position exporter."""
        from . import manufacturing

        self.assert_current()
        return manufacturing.placement_rows(self)

    def export_routing_design(self, output_file: str | Path) -> Path:
        """Export the in-memory board to an external routing design file."""
        return _routing_exchange.export_design(self, output_file)

    def import_routing_session(
        self, session_file: str | Path, output_file: str | Path
    ) -> Path:
        """Import an external router's result to a separate board file."""
        if self._ipc is not None:
            unavailable("Specctra session import")
        return _routing_exchange.import_session(self, session_file, output_file)

    def _pad_copper_layers(self, pad: Pad) -> tuple[str, ...]:
        """Copper layers a pad actually reaches."""
        return _connectivity._pad_copper_layers(self, pad)

    def _via_copper_layers(self, via: Via) -> tuple[str, ...]:
        """Copper layers inside a via's declared span."""
        return _connectivity._via_copper_layers(self, via)

    def _routing_findings(self) -> list[Finding]:
        """Factual copper defects KiCad's DRC does not currently report."""
        return _validation._routing_findings(self)

    @observed
    def check(self, *, schematic_parity: bool = False) -> list[Finding]:
        """Every violation, mapped from a position back to a part and pad."""
        return _validation.check(self, schematic_parity=schematic_parity)

    @observed
    def check_proposed(
        self,
        tracks: tuple[Track, ...] = (),
        vias: tuple[Via, ...] = (),
        zones: tuple[Zone, ...] = (),
    ) -> list[Finding]:
        """Check caller-supplied copper on a temporary board copy."""
        return _validation.check_proposed(self, tracks, vias, zones)

    @observed
    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What geometrically touches a point, including track interiors."""
        return _inspection.at(self, x, y, radius)

    @observed
    def region(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        layers: tuple[str, ...] = (),
        include_fills: bool = False,
    ) -> dict[str, object]:
        """Return objects whose conservative bounds intersect a rectangle."""
        return _inspection.region(
            self, x1, y1, x2, y2, layers=layers, include_fills=include_fills
        )

    @observed
    def render(
        self,
        output_file: str | Path,
        *,
        side: str = "top",
        width: int = 1200,
        height: int = 1200,
        quality: str = "basic",
        background: str = "opaque",
        zoom: float = 1.0,
        rotate: str = "",
        perspective: bool = False,
        floor: bool = False,
        pan: str = "",
        pivot: str = "",
    ) -> Path:
        """Render the board in 3D and return the image."""
        self.save()
        return _render.render_board(
            self._path,
            output_file,
            side=side,
            width=width,
            height=height,
            quality=quality,
            background=background,
            zoom=zoom,
            rotate=rotate or None,
            perspective=perspective,
            floor=floor,
            pan=pan or None,
            pivot=pivot or None,
        )

    @observed
    def render_layout(
        self,
        output_file: str | Path,
        *,
        side: str = "top",
        dpi: int = 200,
        copper: bool = True,
        silkscreen: bool = True,
        courtyard: bool = True,
    ) -> Path:
        """Render a fast orthographic placement view."""
        # The monitor may have the editable board open for its own preview.
        # Render an in-memory snapshot beside it so `${KIPRJMOD}` still resolves
        # while no explicit preview needs to replace or lock the design file.
        snapshot = self._path.with_name(
            f".{self._path.stem}.layout-{_uid()}{self._path.suffix}"
        )
        try:
            if self._ipc is not None:
                self._ipc.copy_to(snapshot)
            else:
                snapshot.write_text(dumps(self._tree) + "\n", encoding="utf-8")
            return _render.render_board_layout(
                snapshot,
                output_file,
                side=side,
                dpi=dpi,
                copper=copper,
                silkscreen=silkscreen,
                courtyard=courtyard,
            )
        finally:
            snapshot.unlink(missing_ok=True)
            if self._ipc is not None:
                for suffix in (".kicad_pro", ".kicad_dru"):
                    snapshot.with_suffix(suffix).unlink(missing_ok=True)


def create(path: str | Path, *, layers: int = 2, thickness: float = 1.6) -> Board:
    """Make a new, empty board and return it open for editing.

    Typed as the abstract :class:`~kicad_flow.pcb.api.Board`, not as the
    concrete class, so a caller who takes their type from the return value
    binds to the interface rather than to this backend.
    """
    _copper_names(layers)
    document = Document(Path(path), create=True, layers=layers, thickness=thickness)
    return KiCadBoard(Path(path).resolve(), loads(document.snapshot()), document)


def load(path: str | Path) -> Board:
    """Open the selected board through native IPC."""
    document = Document(Path(path), create=False)
    return KiCadBoard(Path(path).resolve(), loads(document.snapshot()), document)


__all__ = ["KiCadBoard", "create", "load"]
