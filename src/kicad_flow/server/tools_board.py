"""MCP tools: the board primitives, one tool per primitive.

The board counterpart of :mod:`.tools_schematic`, and the same shape. A tool
takes a few scalars and returns what it made -- notably, every call that places
something returns the **pad positions**, so the next call can route to them
without a lookup and without repeating the rotation arithmetic.

There is no autoplacer, no router, no via stitcher and no fanout. Those existed
and decided things a caller could not override. What is left cannot decide
anything: it puts a footprint where it is told and reports where the pads
landed.

Boards are held open in memory, keyed by path, so a session is a sequence of
small calls rather than a re-parse each time. `save_board` writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..backend import create_board, load_board
from ..pcb.api import Board
from . import _meta
from ._app import mcp

#: Boards currently open, by absolute path.
_OPEN: dict[str, Board] = {}


def _key(path: str) -> str:
    """The dictionary key for a board path."""
    return str(Path(path).resolve())


def _board(path: str) -> Board:
    """The open board for *path*, loading it from disk if need be."""
    key = _key(path)
    if key not in _OPEN:
        if not Path(path).is_file():
            raise LookupError(f"no board at {path}; call new_board first")
        _OPEN[key] = load_board(path)
    return _OPEN[key]


def _fail(exc: Exception) -> dict[str, Any]:
    """A refusal that says what went wrong."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


_ERRORS = (LookupError, ValueError, OSError, RuntimeError)


# -- the board ------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def new_board(path: str, layers: int = 2,
              thickness: float = 1.6) -> dict[str, Any]:
    """Start a new board and open it for editing.

    Nothing is written until `save_board`. Set the layer count HERE: changing
    it after routing invalidates the route, because an inner-layer track on a
    layer that no longer exists does not move, it disappears.

    Args:
        path: Where the board will be written.
        layers: Copper layers -- 2, 4 or 6.
        thickness: Board thickness in mm.

    Returns:
        ``{ok, path, layers}``.
    """
    try:
        board = create_board(path, layers=layers, thickness=thickness)
    except _ERRORS as exc:
        return _fail(exc)
    _OPEN[_key(path)] = board
    return {"ok": True, "path": str(board.path), "layers": list(board.layers)}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def save_board(path: str) -> dict[str, Any]:
    """Write the open board to disk."""
    try:
        board = _board(path)
        written = board.save()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "path": str(written),
            "footprints": len(board.footprints()),
            "tracks": len(board.tracks()), "vias": len(board.vias()),
            "zones": len(board.zones())}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_outline(path: str,
                points: list[list[float]]) -> dict[str, Any]:
    """Draw the board edge through *points* -- one closed contour.

    A board with a cutout or several islands is several calls: which contour
    is a hole and which is an island is a decision, and it is yours.

    Args:
        path: The open board.
        points: ``[[x, y], ...]``, at least three, in mm.
    """
    try:
        made = _board(path).outline([(p[0], p[1]) for p in points])
    except (IndexError, *_ERRORS) as exc:
        return _fail(exc)
    return {"ok": True, "points": [p.as_dict() for p in made],
            "size": list(_board(path).size)}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_board_layers(path: str, count: int) -> dict[str, Any]:
    """Set the copper layer count (2, 4 or 6). Do this before routing."""
    try:
        return {"ok": True, "layers": list(_board(path).set_layers(count))}
    except _ERRORS as exc:
        return _fail(exc)


# -- the library ----------------------------------------------------------


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def find_footprint(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the footprint libraries for a land pattern.

    Matched against ``Library:Footprint`` ids -- search by package or family,
    ``"0603"``, ``"LQFP-64"``, ``"PinHeader_1x06"``.
    """
    try:
        found = _blank().find_footprints(query, limit=limit)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "footprints": [
        {"fp_id": f.fp_id, "courtyard": list(f.courtyard),
         "pads": len(f.pads), "has_pth": f.has_pth} for f in found]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def footprint_pads(fp_id: str) -> dict[str, Any]:
    """A library footprint's pads and size, before it is placed anywhere.

    Use the COURTYARD to decide how much room to leave -- not the bounding
    box, which includes silkscreen, and not the pad extent, which excludes the
    body. For the positions to actually ROUTE to, place it and read the pads
    `place_footprint` returns.
    """
    try:
        found = _blank().footprint_def(fp_id)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **found.as_dict()}


def _blank() -> Board:
    """A throwaway board, for library queries that need no file."""
    return create_board(Path.cwd() / "_query.kicad_pcb")


# -- parts ----------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def place_footprint(path: str, fp_id: str, ref: str, x: float, y: float,
                    rotation: float = 0.0, side: str = "F",
                    value: str = "") -> dict[str, Any]:
    """Place a footprint on the board at ``(x, y)`` as *ref*.

    **The returned pads are the point of this call.** Each carries the board
    position to route to, with rotation and side already applied.

    ``(x, y)`` is the footprint's ORIGIN, which on many parts sits at pad 1
    rather than in the middle. The reply's `courtyard_offset` is the vector
    from the origin to the courtyard centre -- add it to place by centre.

    Args:
        path: The open board.
        fp_id: ``Library:Footprint``.
        ref: Reference designator, e.g. ``"R1"``.
        x: Position in mm.
        y: Position in mm.
        rotation: Degrees. ANY angle -- a board is not on a 90-degree grid.
        side: ``"F"`` front or ``"B"`` back. A part on the back is MIRRORED.
        value: Value field.
    """
    try:
        part = _board(path).place(fp_id, ref, x, y, rotation=rotation,
                                  side=side, value=value)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **part.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprint(path: str, ref: str, x: float, y: float) -> dict[str, Any]:
    """Move a placed footprint. Its pads move with it; copper does not."""
    try:
        return {"ok": True, **_board(path).move(ref, x, y).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def rotate_footprint(path: str, ref: str, rotation: float) -> dict[str, Any]:
    """Turn a placed footprint to *rotation* degrees. Any angle."""
    try:
        return {"ok": True, **_board(path).rotate(ref, rotation).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def flip_footprint(path: str, ref: str, side: str) -> dict[str, Any]:
    """Put a footprint on ``"F"`` or ``"B"``.

    Flipping MIRRORS it: the pads run the other way. Anything you routed to
    the old pad positions now goes nowhere -- read them back.
    """
    try:
        return {"ok": True, **_board(path).flip(ref, side).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def remove_footprint(path: str, ref: str) -> dict[str, Any]:
    """Take a footprint off the board."""
    try:
        _board(path).remove(ref)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "removed": ref}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint(path: str, ref: str) -> dict[str, Any]:
    """One placed footprint and its pad positions."""
    try:
        return {"ok": True, **_board(path).footprint(ref).as_dict()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_footprints(path: str, with_pads: bool = False) -> dict[str, Any]:
    """Every footprint on the board.

    Args:
        path: The open board.
        with_pads: Include every pad position. Off by default -- on a full
            board that is most of the reply.
    """
    try:
        parts = _board(path).footprints()
    except _ERRORS as exc:
        return _fail(exc)
    out = []
    for p in parts:
        d = p.as_dict()
        if not with_pads:
            d["pads"] = len(p.pads)
        out.append(d)
    return {"ok": True, "count": len(out), "footprints": out}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_pad(path: str, ref: str, pad: str) -> dict[str, Any]:
    """Where one pad is on the board -- the point to route to.

    This is the call that makes the rest usable: it applies the footprint's
    rotation and side so you never have to. A track drawn to where a pad
    would have been unrotated looks connected and is not.
    """
    try:
        point = _board(path).pad(ref, pad)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "pad": pad, **point.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_pad_net(path: str, ref: str, pad: str, net: str) -> dict[str, Any]:
    """Put a pad on a net.

    A library footprint carries no nets -- it is a land pattern, not a
    circuit. Without them the board is geometry: nothing is unrouted because
    nothing is connected, a plane joins nothing, and DRC calls every track a
    short.

    Which pad is on which net is a fact the SCHEMATIC holds. Read it with
    `list_nets` on the sheet and apply it here, one pad at a time.
    """
    try:
        return {"ok": True, "ref": ref, "pad": pad,
                "net": _board(path).set_net(ref, pad, net)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def get_footprint_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a footprint, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _board(path).fields(ref)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def set_footprint_field(path: str, ref: str, name: str,
                        value: str) -> dict[str, Any]:
    """Set one of a footprint's fields, e.g. ``Value`` or ``LCSC``."""
    try:
        return {"ok": True, "ref": ref,
                "fields": _board(path).set_field(ref, name, value)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def move_footprint_field(path: str, ref: str, name: str, dx: float, dy: float,
                         rotation: float | None = None, layer: str = "",
                         hide: bool | None = None) -> dict[str, Any]:
    """Move a footprint's Reference or Value, relative to the footprint.

    The library places these and cannot know what ends up beside them. On a
    dense board they land on their own part, a neighbour, or a pad -- and
    turning a part turns its designator with it, so a row of parts at
    different angles gets a row of differently-slanted labels over the top of
    them.

    On fine-pitch passives the reference is wider than the part it names;
    `hide=true` takes it off the silkscreen without losing it.
    """
    try:
        at = _board(path).move_field(ref, name, dx, dy, rotation=rotation,
                                     layer=layer, hide=hide)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "field": name, **at.as_dict()}


# -- copper ---------------------------------------------------------------


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_track(path: str, x1: float, y1: float, x2: float, y2: float,
              layer: str, width: float, net: str = "") -> dict[str, Any]:
    """Lay one straight copper segment on one layer.

    A corner is two calls and a layer change is a via. That is deliberate:
    where a track turns and where it changes layer are routing decisions.

    Copper on the wrong layer connects nothing, so *layer* is required --
    `new_board` reports which exist.
    """
    try:
        made = _board(path).track(x1, y1, x2, y2, layer=layer, width=width,
                                  net=net)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **made.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_via(path: str, x: float, y: float, net: str = "",
            diameter: float = 0.6, drill: float = 0.3) -> dict[str, Any]:
    """Drill a plated via joining front to back."""
    try:
        made = _board(path).via(x, y, net=net, diameter=diameter, drill=drill)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, **made.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_zone(path: str, points: list[list[float]], layer: str, net: str = "",
             clearance: float = 0.0,
             forbids: list[str] | None = None) -> dict[str, Any]:
    """Pour copper inside *points* on *layer*, tied to *net*.

    With *forbids* it is a KEEP-OUT instead -- a region refusing any of
    ``tracks``, ``vias``, ``pads``, ``pours``, ``footprints``.

    A pour is not filled until `refill_zones`. An unfilled zone is an outline
    that connects nothing and renders as almost nothing.
    """
    try:
        made = _board(path).zone([(p[0], p[1]) for p in points], layer=layer,
                                 net=net, clearance=clearance,
                                 forbids=tuple(forbids or ()))
    except (IndexError, *_ERRORS) as exc:
        return _fail(exc)
    return {"ok": True, **made.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def refill_zones(path: str) -> dict[str, Any]:
    """Recompute every pour against the copper as it now stands.

    Tracks laid after a pour do not update it, so a board looks poured while
    the fill still hugs routing that has moved.
    """
    try:
        return {"ok": True, "filled": _board(path).refill()}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def add_board_text(path: str, x: float, y: float, text: str, layer: str,
                   size: float = 1.0, rotation: float = 0.0,
                   mirror: bool = False) -> dict[str, Any]:
    """Put text on a layer -- a legend, a fab note, a part marking.

    Back-side silkscreen wants ``mirror=true`` or it reads reversed.
    """
    try:
        at = _board(path).text(x, y, text, layer=layer, size=size,
                               rotation=rotation, mirror=mirror)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "text": text, "layer": layer, **at.as_dict()}


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_copper(path: str, net: str = "", layer: str = "",
                  tracks: bool = True, vias: bool = True) -> dict[str, Any]:
    """Delete copper, filtered by net and layer, and say how much went.

    The undo for routing. Filters are AND-ed and an empty one matches
    everything, so calling this with no arguments strips the board.
    """
    try:
        gone = _board(path).remove_copper(net=net, layer=layer, tracks=tracks,
                                          vias=vias)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "removed": gone}


# -- reading back ---------------------------------------------------------


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_copper(path: str) -> dict[str, Any]:
    """Every track, via and zone on the board."""
    try:
        board = _board(path)
        return {"ok": True,
                "tracks": [t.as_dict() for t in board.tracks()],
                "vias": [v.as_dict() for v in board.vias()],
                "zones": [z.as_dict() for z in board.zones()]}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def list_board_nets(path: str) -> dict[str, Any]:
    """What the board is MEANT to connect, from its own pads.

    Intent, not fact. Whether copper actually joins these pads is
    `unrouted_connections`, and the two disagreeing is the normal state of a
    board mid-layout.
    """
    try:
        nets = _board(path).nets()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(nets),
            "nets": [n.as_dict() for n in nets]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def unrouted_connections(path: str, limit: int = 40) -> dict[str, Any]:
    """Every pair of pads on a net with no copper between them.

    The work remaining, NAMED rather than counted, nearest first -- so you can
    route one. A filled plane counts as copper, so pads on a poured net are
    not reported.
    """
    try:
        found = sorted(_board(path).unrouted(), key=lambda c: c.distance)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "connections": [c.as_dict() for c in found[:limit]]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def check_board(path: str) -> dict[str, Any]:
    """Every design-rule violation, named by part and pad.

    Runs DRC and maps each violation from a position back to the pad that
    sits there, so a finding reads ``R1.2 clearance to U1.7`` rather than
    ``something at (25.46, 10.45)``.
    """
    try:
        found = _board(path).check()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True,
            "errors": sum(1 for f in found if f.severity == "error"),
            "warnings": sum(1 for f in found if f.severity == "warning"),
            "findings": [f.as_dict() for f in found]}


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def what_is_on_board(path: str, x: float, y: float,
                     radius: float = 0.01) -> dict[str, Any]:
    """What is at a point: pads, track ends and vias.

    The one query worth having while routing -- *is this actually connected?*
    """
    try:
        return {"ok": True, **_board(path).at(x, y, radius)}
    except _ERRORS as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.WRITE)
def render_board(path: str, output_file: str, side: str = "top",
                 width: int = 1200, height: int = 1200) -> dict[str, Any]:
    """Render the board to a PNG you can actually look at.

    LOOK AT THE BOARD. `check_board` has the rule answer and cannot see a
    part 6 mm from where you put it, a designator printed over a pad, a block
    of passives piled in one corner, or an outline that renders as one piece
    and would mill as three. It saves the board first, so the picture is of
    what you have drawn.

    Render BOTH sides -- half the parts are usually on the back, and the
    bottom view is MIRRORED, so left and right swap.
    """
    try:
        board = _board(path)
        image = board.render(output_file, side=side, width=width,
                             height=height)
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "board": str(board.path), "image": str(image),
            "side": side}


__all__ = [
    "add_board_text",
    "add_outline",
    "add_track",
    "add_via",
    "add_zone",
    "check_board",
    "find_footprint",
    "flip_footprint",
    "footprint_pads",
    "get_footprint",
    "get_footprint_fields",
    "get_pad",
    "list_board_nets",
    "list_copper",
    "list_footprints",
    "move_footprint",
    "move_footprint_field",
    "new_board",
    "place_footprint",
    "refill_zones",
    "remove_copper",
    "remove_footprint",
    "render_board",
    "rotate_footprint",
    "save_board",
    "set_board_layers",
    "set_footprint_field",
    "set_pad_net",
    "unrouted_connections",
    "what_is_on_board",
]
