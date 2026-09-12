"""PCB MCP tools for rendering."""

from __future__ import annotations

from typing import Any

from .. import _meta
from .._app import mcp
from .session import (
    _ERRORS,
    _board,
    _fail,
)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.WRITE)
def render_board_layout(
    path: str,
    output_file: str,
    side: str = "top",
    dpi: int = 200,
    copper: bool = True,
    silkscreen: bool = True,
    courtyard: bool = True,
) -> dict[str, Any]:
    """Render a fast orthographic PNG/SVG for placement inspection.

    Edge.Cuts and the selected side's fabrication layer are always shown.
    Copper, silkscreen and courtyard overlays are independent caller choices.
    Unlike the 3D render, this makes part spacing and boundary violations easy
    to inspect during every placement iteration.
    """
    try:
        board = _board(path)
        image = board.render_layout(
            output_file,
            side=side,
            dpi=dpi,
            copper=copper,
            silkscreen=silkscreen,
            courtyard=courtyard,
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "board": str(board.path),
        "image": str(image),
        "side": side,
        "courtyard": courtyard,
    }


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.WRITE)
def render_board(
    path: str,
    output_file: str,
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
) -> dict[str, Any]:
    """Render the board in 3D to a PNG or JPEG you can actually look at.

    LOOK AT THE BOARD. `check_board` has the rule answer and cannot see a
    part 6 mm from where you put it, a designator printed over a pad, a block
    of passives piled in one corner, or an outline that renders as one piece
    and would mill as three. It saves the board first, so the picture is of
    what you have drawn.

    Render BOTH sides -- half the parts are usually on the back, and the
    bottom view is MIRRORED, so left and right swap.

    Args:
        path: The open board.
        output_file: Destination ending in ``.png``, ``.jpg`` or ``.jpeg``.
        side: ``top``, ``bottom``, ``left``, ``right``, ``front`` or ``back``.
        width: Image width in pixels.
        height: Image height in pixels.
        quality: ``basic``, ``high``, ``user`` or ``job_settings``.
        background: ``opaque``, ``transparent`` or ``default``.
        zoom: Camera zoom; 1 fits the board.
        rotate: Board rotation as ``X,Y,Z`` degrees; ``-30,0,25`` isometric.
        perspective: Use perspective instead of orthographic projection.
        floor: Include a floor, shadows and post-processing.
        pan: Camera translation as ``X,Y,Z``.
        pivot: Orbit pivot as ``X,Y,Z`` centimetres from board centre.
    """
    try:
        board = _board(path)
        image = board.render(
            output_file,
            side=side,
            width=width,
            height=height,
            quality=quality,
            background=background,
            zoom=zoom,
            rotate=rotate,
            perspective=perspective,
            floor=floor,
            pan=pan,
            pivot=pivot,
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "board": str(board.path),
        "image": str(image),
        "side": side,
        "quality": quality,
        "perspective": perspective,
    }
