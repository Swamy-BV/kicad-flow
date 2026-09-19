"""Board outline, graphic primitives and text editing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from kicad_flow.pcb.types import (
    BoardText,
    Graphic,
    Point,
    TextBounds,
)

from .._sexpr import Node, Sym, dumps
from ._constants import (
    _GRAPHIC_KINDS,
    _GRAPHIC_LAYERS,
    _GRAPHIC_NODES,
    _OUTLINE_POLYGON,
)
from ._geometry import (
    _circle_through,
)
from ._nodes import (
    _f,
    _fmt,
    _node,
    _set,
    _text,
    _uid,
)
from ._state import BoardState

_TEXT_BOUNDS = r"""
import json, sys
import pcbnew

job = json.load(open(sys.argv[1], encoding="utf-8"))
selected = set(job["uuids"])
board = pcbnew.LoadBoard(job["board"])
found = []
for drawing in board.Drawings():
    if drawing.GetClass() != "PCB_TEXT":
        continue
    uuid = str(drawing.m_Uuid.AsString())
    if selected and uuid not in selected:
        continue
    box = drawing.GetBoundingBox()
    found.append({
        "uuid": uuid,
        "x": mm(box.GetX(), 6),
        "y": mm(box.GetY(), 6),
        "width": mm(box.GetWidth(), 6),
        "height": mm(box.GetHeight(), 6),
    })
print(json.dumps({"bounds": found}))
"""


def graphic(
    self: BoardState,
    kind: str,
    points: list[tuple[float, float]],
    *,
    layer: str,
    width: float = 0.1,
    fill: bool = False,
) -> Graphic:
    """Draw one outline or silkscreen primitive and return it."""
    if kind not in _GRAPHIC_NODES:
        raise ValueError(f"kind must be one of {sorted(_GRAPHIC_NODES)}, not {kind!r}")
    if layer not in _GRAPHIC_LAYERS:
        raise ValueError(
            f"graphic layer must be one of {list(_GRAPHIC_LAYERS)}, not {layer!r}"
        )
    expected = {"line": 2, "arc": 3, "circle": 2, "rectangle": 2}
    if kind == "polygon":
        if len(points) < 3:
            raise ValueError("a polygon needs at least 3 points")
    elif len(points) != expected[kind]:
        raise ValueError(f"a {kind} needs {expected[kind]} points")
    made = tuple(Point(float(x), float(y)) for x, y in points)
    if width < 0:
        raise ValueError("graphic width cannot be negative")
    if kind == "arc" and _circle_through(*made) is None:
        raise ValueError("an arc's start, mid and end cannot be collinear")
    if kind == "circle" and made[0] == made[1]:
        raise ValueError("a circle needs a non-zero radius")
    if fill and kind not in ("circle", "rectangle", "polygon"):
        raise ValueError(f"a {kind} cannot be filled")
    if fill and layer == "Edge.Cuts":
        raise ValueError("Edge.Cuts shapes cannot be filled")

    uid = _uid()
    stroke = _node("stroke", [_node("width", [width]), _node("type", [Sym("solid")])])
    common = [stroke]
    if kind in ("circle", "rectangle", "polygon"):
        common.append(_node("fill", [Sym("yes" if fill else "no")]))
    common.extend([_node("layer", [layer]), _node("uuid", [uid])])
    if kind == "line":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    elif kind == "arc":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("mid", [made[1].x, made[1].y]),
            _node("end", [made[2].x, made[2].y]),
        ]
    elif kind == "circle":
        geometry = [
            _node("center", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    elif kind == "rectangle":
        geometry = [
            _node("start", [made[0].x, made[0].y]),
            _node("end", [made[1].x, made[1].y]),
        ]
    else:
        geometry = [_node("pts", [_node("xy", [point.x, point.y]) for point in made])]
    self._tree.items.append(_node(_GRAPHIC_NODES[kind], geometry + common))
    return Graphic(uid, kind, layer, made, float(width), fill)


def graphics(self: BoardState, layer: str = "") -> list[Graphic]:
    """Every outline and silkscreen primitive, in file order."""
    if layer and layer not in _GRAPHIC_LAYERS:
        raise ValueError(
            f"graphic layer must be one of {list(_GRAPHIC_LAYERS)}, not {layer!r}"
        )
    out: list[Graphic] = []
    for item in self._tree.items:
        if not isinstance(item, Node) or item.name not in _GRAPHIC_KINDS:
            continue
        found = self._graphic_from_node(item)
        if found.layer in _GRAPHIC_LAYERS and (not layer or found.layer == layer):
            out.append(found)
    return out


def outline_polygon(
    self: BoardState, *, inset: float, max_error: float
) -> tuple[Point, ...]:
    """Return KiCad's resolved outside contour as bounded-error points."""
    if inset < 0:
        raise ValueError("outline inset cannot be negative")
    if max_error <= 0:
        raise ValueError("outline max_error must be positive")

    from ._runner import run_pcbnew

    scratch = self._path.with_name(
        f".{self._path.stem}.outline-{_uid()}{self._path.suffix}"
    )
    try:
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        result = run_pcbnew(
            _OUTLINE_POLYGON,
            {
                "board_path": str(scratch),
                "inset": inset,
                "max_error": max_error,
            },
        )
    finally:
        for artifact in (
            scratch,
            scratch.with_suffix(".kicad_pro"),
            scratch.with_suffix(".kicad_prl"),
            scratch.with_suffix(".kicad_dru"),
            Path(f"{scratch}-bak"),
        ):
            artifact.unlink(missing_ok=True)
    raw = result.get("points")
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError("board outline did not produce a usable polygon")
    return tuple(Point(float(item[0]), float(item[1])) for item in raw)


def move_graphic(self: BoardState, uuid: str, dx: float, dy: float) -> Graphic:
    """Shift one graphic primitive by an offset."""
    node = self._graphic_node(uuid)
    for name in ("start", "mid", "end", "center"):
        point = node.get(name)
        if point is not None:
            point.items = [
                point.items[0],
                Sym(_fmt(_f(point, 0) + dx)),
                Sym(_fmt(_f(point, 1) + dy)),
            ]
    pts = node.get("pts")
    if pts is not None:
        for point in pts.get_all("xy"):
            point.items = [
                point.items[0],
                Sym(_fmt(_f(point, 0) + dx)),
                Sym(_fmt(_f(point, 1) + dy)),
            ]
    return self._graphic_from_node(node)


def remove_graphic(self: BoardState, uuid: str) -> None:
    """Remove one graphic primitive by UUID."""
    self._tree.items.remove(self._graphic_node(uuid))


def _graphic_node(self: BoardState, uuid: str) -> Node:
    """The top-level graphic carrying *uuid*."""
    for item in self._tree.items:
        if (
            isinstance(item, Node)
            and item.name in _GRAPHIC_KINDS
            and _text(item.get("uuid")) == uuid
        ):
            return item
    raise LookupError(f"no graphic with uuid {uuid!r}")


def _graphic_from_node(self: BoardState, node: Node) -> Graphic:
    """Read one KiCad graphical node into the board contract."""
    kind = _GRAPHIC_KINDS[node.name]
    names = {
        "line": ("start", "end"),
        "arc": ("start", "mid", "end"),
        "circle": ("center", "end"),
        "rectangle": ("start", "end"),
    }
    if kind == "polygon":
        pts = node.get("pts")
        point_nodes = pts.get_all("xy") if pts is not None else []
    else:
        point_nodes = []
        for name in names[kind]:
            point = node.get(name)
            if point is not None:
                point_nodes.append(point)
    points = tuple(Point(_f(point, 0), _f(point, 1)) for point in point_nodes)
    stroke = node.get("stroke")
    fill = node.get("fill")
    return Graphic(
        _text(node.get("uuid")),
        kind,
        _text(node.get("layer")),
        points,
        _f(stroke.get("width"), 0) if stroke is not None else 0.0,
        _text(fill) in ("yes", "solid") if fill is not None else False,
    )


def text(
    self: BoardState,
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
    """Put one identified text item on a board layer."""
    _validate_text(width, height, thickness, justify, vertical_justify)
    at = Point(float(x), float(y))
    effects: list[Any] = [
        _node(
            "font",
            [_node("size", [height, width]), _node("thickness", [thickness])],
        )
    ]
    alignment = [
        Sym(value) for value in (justify, vertical_justify) if value != "center"
    ]
    if mirror:
        alignment.append(Sym("mirror"))
    if alignment:
        effects.append(_node("justify", alignment))
    node = _node(
        "gr_text",
        [
            text,
            _node("at", [at.x, at.y, rotation % 360.0]),
            _node("layer", [layer]),
            _node("uuid", [_uid()]),
            _node("effects", effects),
        ],
    )
    self._tree.items.append(node)
    return self._text_from_node(node)


def texts(self: BoardState, layer: str = "") -> list[BoardText]:
    """Read literal board text in file order."""
    out: list[BoardText] = []
    for item in self._tree.items:
        if not isinstance(item, Node) or item.name != "gr_text":
            continue
        found = self._text_from_node(item)
        if not layer or found.layer == layer:
            out.append(found)
    return out


def update_text(
    self: BoardState,
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
    """Apply only caller-supplied properties to one text UUID."""
    node = self._text_node(uuid)
    current = self._text_from_node(node)
    made_width = current.width if width is None else float(width)
    made_height = current.height if height is None else float(height)
    made_thickness = current.thickness if thickness is None else float(thickness)
    made_justify = current.justify if justify is None else justify
    made_vertical = (
        current.vertical_justify if vertical_justify is None else vertical_justify
    )
    _validate_text(
        made_width, made_height, made_thickness, made_justify, made_vertical
    )
    node.items[1] = current.text if text is None else text
    at = node.get("at")
    if at is None:
        at = _node("at", [current.at.x, current.at.y, current.rotation])
        node.items.append(at)
    _set(at, 0, current.at.x if x is None else float(x))
    _set(at, 1, current.at.y if y is None else float(y))
    _set(at, 2, current.rotation if rotation is None else rotation % 360.0)
    layer_node = node.get("layer")
    if layer_node is None:
        node.items.append(_node("layer", [current.layer if layer is None else layer]))
    elif layer is not None:
        _set(layer_node, 0, layer)
    effects = node.get("effects")
    if effects is None:
        effects = _node("effects")
        node.items.append(effects)
    font = effects.get("font")
    if font is None:
        font = _node("font")
        effects.items.append(font)
    size = font.get("size")
    if size is None:
        font.items.append(_node("size", [made_height, made_width]))
    else:
        _set(size, 0, made_height)
        _set(size, 1, made_width)
    stroke = font.get("thickness")
    if stroke is None:
        font.items.append(_node("thickness", [made_thickness]))
    else:
        _set(stroke, 0, made_thickness)
    old_alignment = effects.get("justify")
    if old_alignment is not None:
        effects.items.remove(old_alignment)
    made_mirror = current.mirror if mirror is None else mirror
    alignment = [
        Sym(value)
        for value in (made_justify, made_vertical)
        if value != "center"
    ]
    if made_mirror:
        alignment.append(Sym("mirror"))
    if alignment:
        effects.items.append(_node("justify", alignment))
    return self._text_from_node(node)


def remove_text(self: BoardState, uuid: str) -> None:
    """Remove one board text item by UUID."""
    self._tree.items.remove(self._text_node(uuid))


def text_bounds(
    self: BoardState, uuids: tuple[str, ...] = ()
) -> list[TextBounds]:
    """Ask KiCad for axis-aligned rendered text rectangles."""
    found = self.texts()
    known = {item.uuid for item in found}
    missing = sorted(set(uuids) - known)
    if missing:
        raise LookupError(f"no board text with uuid(s): {', '.join(missing)}")
    selected = list(uuids) if uuids else [item.uuid for item in found]
    if not selected:
        return []
    from ._runner import run_pcbnew

    self._path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=self._path.parent) as name:
        snapshot = Path(name) / self._path.name
        snapshot.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        result = run_pcbnew(
            _TEXT_BOUNDS, {"board": str(snapshot), "uuids": selected}
        )
    measured = {
        str(item["uuid"]): TextBounds(
            str(item["uuid"]),
            float(item["x"]),
            float(item["y"]),
            float(item["width"]),
            float(item["height"]),
        )
        for item in result.get("bounds", [])
    }
    absent = [uuid for uuid in selected if uuid not in measured]
    if absent:
        raise RuntimeError(
            f"KiCad did not measure board text uuid(s): {', '.join(absent)}"
        )
    return [measured[uuid] for uuid in selected]


def _validate_text(
    width: float,
    height: float,
    thickness: float,
    justify: str,
    vertical_justify: str,
) -> None:
    """Validate explicit text geometry and alignment."""
    if justify not in ("left", "center", "right"):
        raise ValueError("justify must be left, center or right")
    if vertical_justify not in ("top", "center", "bottom"):
        raise ValueError("vertical_justify must be top, center or bottom")
    if width <= 0 or height <= 0 or thickness <= 0:
        raise ValueError("text width, height and thickness must be positive")


def _text_node(self: BoardState, uuid: str) -> Node:
    """Find one top-level literal board text item."""
    for item in self._tree.items:
        if (
            isinstance(item, Node)
            and item.name == "gr_text"
            and _text(item.get("uuid")) == uuid
        ):
            return item
    raise LookupError(f"no board text with uuid {uuid!r}")


def _text_from_node(self: BoardState, node: Node) -> BoardText:
    """Read one KiCad text node into the format-neutral board contract."""
    at = node.get("at")
    effects = node.get("effects")
    font = effects.get("font") if effects is not None else None
    size = font.get("size") if font is not None else None
    stroke = font.get("thickness") if font is not None else None
    alignment = effects.get("justify") if effects is not None else None
    values = {str(item) for item in alignment.items[1:]} if alignment else set()
    return BoardText(
        uuid=_text(node.get("uuid")),
        text=_text(node),
        at=Point(_f(at, 0), _f(at, 1)),
        layer=_text(node.get("layer")),
        width=_f(size, 1, 1.0),
        height=_f(size, 0, 1.0),
        thickness=_f(stroke, 0, 0.15),
        rotation=_f(at, 2) % 360.0,
        mirror="mirror" in values,
        justify=(
            "left" if "left" in values
            else "right" if "right" in values
            else "center"
        ),
        vertical_justify=(
            "top" if "top" in values
            else "bottom" if "bottom" in values
            else "center"
        ),
    )
