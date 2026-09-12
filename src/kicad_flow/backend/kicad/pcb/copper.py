"""Track, via and zone serialization, editing and fill operations."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from kicad_flow.pcb.types import (
    Point,
    Track,
    Via,
    Zone,
)

from .._sexpr import Node, Sym, loads
from ._constants import (
    _ISLAND_MODES,
    _ISLAND_MODES_BY_NUMBER,
    _REFILL,
    _VIA_KINDS,
)
from ._nodes import (
    _f,
    _net_name,
    _node,
    _text,
    _uid,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def track(
    self: KiCadBoard,
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
    self._require_layer(layer)
    values = (float(x1), float(y1), float(x2), float(y2), float(width))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("track coordinates and width must be finite")
    if width <= 0:
        raise ValueError("track width must be positive")
    if x1 == x2 and y1 == y2:
        raise ValueError("a track must have two different endpoints")
    uid = _uid()
    made = Track(
        Point(float(x1), float(y1)),
        Point(float(x2), float(y2)),
        layer,
        float(width),
        net,
        uid,
    )
    self._tree.items.append(
        _node(
            "segment",
            [
                _node("start", [made.start.x, made.start.y]),
                _node("end", [made.end.x, made.end.y]),
                _node("width", [made.width]),
                _node("layer", [layer]),
                _node("net", [net]),
                _node("uuid", [uid]),
            ],
        )
    )
    return made


def via(
    self: KiCadBoard,
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
    if kind not in _VIA_KINDS:
        raise ValueError(f"via kind must be one of {sorted(_VIA_KINDS)}")
    if len(layers) != 2 or layers[0] == layers[1]:
        raise ValueError("via layers must name two different copper layers")
    for name in layers:
        self._require_layer(name)
    first, last = (self.layers.index(name) for name in layers)
    if kind == "through" and {first, last} != {0, len(self.layers) - 1}:
        raise ValueError("a through via must span F.Cu to B.Cu")
    if kind == "microvia" and abs(first - last) != 1:
        raise ValueError("a microvia must span adjacent copper layers")
    values = (float(x), float(y), float(diameter), float(drill))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("via coordinates and dimensions must be finite")
    if diameter <= 0 or drill <= 0:
        raise ValueError("via diameter and drill must be positive")
    if drill >= diameter:
        raise ValueError("via drill must be smaller than its diameter")
    uid = _uid()
    made = Via(
        Point(float(x), float(y)), float(diameter), float(drill), net, layers, kind, uid
    )
    items: list[Any] = [
        _node("at", [made.at.x, made.at.y]),
        _node("size", [made.diameter]),
        _node("drill", [made.drill]),
        _node("layers", list(layers)),
        _node("net", [net]),
        _node("uuid", [uid]),
    ]
    native_kind = _VIA_KINDS[kind]
    if native_kind:
        # KiCad stores the type as a bare token: ``(via blind ...)``.
        items.insert(0, Sym(native_kind))
    self._tree.items.append(_node("via", items))
    return made


def zone(
    self: KiCadBoard,
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
    if len(points) < 3:
        raise ValueError("a zone needs at least 3 points")
    self._require_layer(layer)
    if pad_connection not in {"thermal", "solid", "none"}:
        raise ValueError("pad_connection must be 'thermal', 'solid', or 'none'")
    if island_removal not in _ISLAND_MODES:
        raise ValueError(f"island_removal must be one of {sorted(_ISLAND_MODES)}")
    numeric = (
        clearance,
        min_thickness,
        thermal_gap,
        thermal_spoke_width,
        min_island_area,
    )
    if not all(math.isfinite(float(value)) for value in numeric):
        raise ValueError("zone dimensions must be finite")
    if clearance < 0 or min_island_area < 0:
        raise ValueError("zone clearance and minimum island area cannot be negative")
    if min_thickness <= 0 or thermal_gap <= 0 or thermal_spoke_width <= 0:
        raise ValueError("zone thickness and thermal dimensions must be positive")
    if priority < 0:
        raise ValueError("zone priority cannot be negative")
    uid = _uid()
    made = Zone(
        net=net,
        layer=layer,
        points=tuple(Point(float(x), float(y)) for x, y in points),
        filled=False,
        pad_connection=pad_connection,
        clearance=float(clearance),
        min_thickness=float(min_thickness),
        thermal_gap=float(thermal_gap),
        thermal_spoke_width=float(thermal_spoke_width),
        priority=int(priority),
        island_removal=island_removal,
        min_island_area=float(min_island_area),
        forbids=forbids,
        uuid=uid,
    )
    polygon = _node(
        "polygon", [_node("pts", [_node("xy", [p.x, p.y]) for p in made.points])]
    )
    items: list[Any] = [
        _node("net", [net]),
        _node("layer", [layer]),
        _node("uuid", [uid]),
        _node("hatch", [Sym("edge"), 0.5]),
        _node(
            "connect_pads",
            (
                []
                if pad_connection == "thermal"
                else [Sym("yes" if pad_connection == "solid" else "no")]
            )
            + [_node("clearance", [clearance])],
        ),
        _node("min_thickness", [min_thickness]),
        _node(
            "fill",
            [
                Sym("yes"),
                _node("thermal_gap", [thermal_gap]),
                _node("thermal_bridge_width", [thermal_spoke_width]),
                _node("island_removal_mode", [_ISLAND_MODES[island_removal]]),
                *(
                    [_node("island_area_min", [min_island_area])]
                    if island_removal == "area"
                    else []
                ),
            ],
        ),
        polygon,
    ]
    if priority:
        items.insert(3, _node("priority", [priority]))
    if forbids:
        allowed = {"tracks", "vias", "pads", "pours", "footprints"}
        bad = set(forbids) - allowed
        if bad:
            raise ValueError(
                f"forbids must be from {sorted(allowed)}, not {sorted(bad)}"
            )
        items.insert(
            3,
            _node(
                "keepout",
                [
                    _node(
                        "tracks",
                        [Sym("not_allowed" if "tracks" in forbids else "allowed")],
                    ),
                    _node(
                        "vias", [Sym("not_allowed" if "vias" in forbids else "allowed")]
                    ),
                    _node(
                        "pads", [Sym("not_allowed" if "pads" in forbids else "allowed")]
                    ),
                    _node(
                        "copperpour",
                        [Sym("not_allowed" if "pours" in forbids else "allowed")],
                    ),
                    _node(
                        "footprints",
                        [Sym("not_allowed" if "footprints" in forbids else "allowed")],
                    ),
                ],
            ),
        )
    self._tree.items.append(_node("zone", items))
    return made


def refill(self: KiCadBoard) -> int:
    """Recompute every pour against the copper as it now stands.

    The one thing the file cannot do for itself. Filling a zone means
    running KiCad's own filler over the copper as it stands, so this is
    the single place the board side enters pcbnew -- through KiCad's
    bundled Python, because the module is not importable from ours.
    """
    from ._runner import run_pcbnew

    self.save()
    result = run_pcbnew(_REFILL, {"board_path": str(self._path)})
    self._tree = loads(self._path.read_text(encoding="utf-8"))
    # `zones`, not `filled`: pcbnew reports how many it refilled and the
    # first version of this read a key that was never there, so a board
    # with two pours reported none and looked unfilled.
    count = result.get("zones", 0)
    return int(count) if isinstance(count, int) else 0


def remove_copper(
    self: KiCadBoard,
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
    if not uuid and not net and not layer and not all:
        raise ValueError(
            "select uuid, net or layer; use all=True to remove all selected kinds"
        )
    if all and (uuid or net or layer):
        raise ValueError("all=True cannot be combined with selectors")
    if uuid and (net or layer):
        raise ValueError("uuid selection cannot be combined with net or layer")
    kinds = (
        [("segment", "layer"), ("via", "layers"), ("zone", "layer")]
        if uuid
        else ([("segment", "layer")] if tracks else [])
        + ([("via", "layers")] if vias else [])
        + ([("zone", "layer")] if zones else [])
    )
    gone = 0
    for name, layer_key in kinds:
        for node in list(self._tree.get_all(name)):
            if uuid and _text(node.get("uuid")) != uuid:
                continue
            if net and _net_name(node.get("net")) != net:
                continue
            if layer:
                holder = node.get(layer_key)
                names = (
                    [_text(holder)]
                    if layer_key == "layer"
                    else [str(x) for x in (holder.items[1:] if holder else [])]
                )
                if layer not in names:
                    continue
            self._tree.items.remove(node)
            gone += 1
    return gone


def tracks(self: KiCadBoard) -> list[Track]:
    """Every copper segment on the board."""
    out = []
    for node in self._tree.get_all("segment"):
        start, end = node.get("start"), node.get("end")
        out.append(
            Track(
                Point(_f(start, 0), _f(start, 1)),
                Point(_f(end, 0), _f(end, 1)),
                _text(node.get("layer")),
                _f(node.get("width"), 0),
                _net_name(node.get("net")),
                _text(node.get("uuid")),
            )
        )
    return out


def vias(self: KiCadBoard) -> list[Via]:
    """Every via on the board."""
    out = []
    for node in self._tree.get_all("via"):
        at = node.get("at")
        layers = node.get("layers")
        names = [str(x) for x in (layers.items[1:] if layers else [])]
        native_kind = (
            str(node.items[1])
            if len(node.items) > 1
            and not isinstance(node.items[1], Node)
            and str(node.items[1]) in {"blind", "micro"}
            else ""
        )
        kind = {"blind": "blind_buried", "micro": "microvia"}.get(
            native_kind, "through"
        )
        out.append(
            Via(
                Point(_f(at, 0), _f(at, 1)),
                _f(node.get("size"), 0),
                _f(node.get("drill"), 0),
                _net_name(node.get("net")),
                (names[0], names[-1]) if names else ("F.Cu", "B.Cu"),
                kind,
                _text(node.get("uuid")),
            )
        )
    return out


def zones(self: KiCadBoard) -> list[Zone]:
    """Every pour and keep-out."""
    out = []
    for node in self._tree.get_all("zone"):
        polygon = node.get("polygon")
        pts = polygon.get("pts") if polygon is not None else None
        points = tuple(
            Point(_f(xy, 0), _f(xy, 1)) for xy in (pts.get_all("xy") if pts else [])
        )
        keepout = node.get("keepout")
        connect = node.get("connect_pads")
        fill = node.get("fill")
        island_number = (
            int(_f(fill.get("island_removal_mode"), 0))
            if fill is not None and fill.get("island_removal_mode")
            else 0
        )
        forbids = tuple(
            name
            for name, key in (
                ("tracks", "tracks"),
                ("vias", "vias"),
                ("pads", "pads"),
                ("pours", "copperpour"),
                ("footprints", "footprints"),
            )
            if keepout is not None and _text(keepout.get(key)) == "not_allowed"
        )
        out.append(
            Zone(
                net=_net_name(node.get("net")),
                layer=_text(node.get("layer")),
                points=points,
                filled=node.get("filled_polygon") is not None,
                pad_connection={"yes": "solid", "no": "none"}.get(
                    _text(connect), "thermal"
                ),
                clearance=(
                    _f(connect.get("clearance"), 0)
                    if connect is not None and connect.get("clearance")
                    else 0.0
                ),
                min_thickness=_f(node.get("min_thickness"), 0),
                thermal_gap=(
                    _f(fill.get("thermal_gap"), 0)
                    if fill is not None and fill.get("thermal_gap")
                    else 0.0
                ),
                thermal_spoke_width=(
                    _f(fill.get("thermal_bridge_width"), 0)
                    if fill is not None and fill.get("thermal_bridge_width")
                    else 0.0
                ),
                priority=(
                    int(_f(node.get("priority"), 0))
                    if node.get("priority") is not None
                    else 0
                ),
                island_removal=_ISLAND_MODES_BY_NUMBER.get(island_number, "always"),
                min_island_area=(
                    _f(fill.get("island_area_min"), 0)
                    if fill is not None and fill.get("island_area_min")
                    else 0.0
                ),
                forbids=forbids,
                uuid=_text(node.get("uuid")),
            )
        )
    return out
