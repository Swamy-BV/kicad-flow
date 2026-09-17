"""Create or reconcile a board from the schematic's physical components."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import _meta
from .._app import mcp
from ..schematic_tools.session import _sheet
from .models import SchematicPlacement
from .netlist import sync_board_nets
from .session import _ERRORS, _OPEN, _board, _fail, _fresh_board, _key


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def update_board_from_schematic(
    schematic_path: str,
    board_path: str,
    placements: list[SchematicPlacement] | None = None,
    net_names: dict[str, str] | None = None,
    layers: int = 2,
    thickness: float = 1.6,
) -> dict[str, Any]:
    """Export a missing PCB or sync an existing one from its schematic.

    The schematic supplies footprint IDs, values and pad nets. `placements`
    supplies only poses for components absent from the board; existing poses
    are preserved. A missing PCB needs one pose per physical component. This
    call never removes extra board footprints or rewrites existing copper.
    Save is part of the call; a refusal leaves the board unchanged.
    """
    key = _key(board_path)
    created = key not in _OPEN and not Path(board_path).is_file()
    try:
        sheet = _sheet(schematic_path)
        components = sheet.board_components()
        missing_fields = sorted(c.ref for c in components if not c.footprint)
        if missing_fields:
            raise ValueError(
                f"schematic components lack Footprint fields: {missing_fields}"
            )
        supplied = placements or []
        poses = {item.ref: item for item in supplied}
        if len(poses) != len(supplied):
            raise ValueError("placements contain duplicate references")

        board = (_fresh_board(board_path, layers, thickness)
                 if created else _board(board_path))
        footprints = board.footprints()
        existing = {item.ref: item for item in footprints}
        source = {item.ref: item for item in components}
        if len(existing) != len(footprints):
            raise ValueError("PCB contains duplicate footprint references")
        if len(source) != len(components):
            raise ValueError("schematic contains duplicate component references")
        missing = sorted(set(source) - set(existing))
        if set(poses) != set(missing):
            raise ValueError(
                f"placements must name exactly the missing schematic components: "
                f"{missing}"
            )
        mismatched = sorted(
            ref for ref in set(source) & set(existing)
            if source[ref].footprint != existing[ref].fp_id
        )
        if mismatched:
            raise ValueError(f"PCB footprints differ from schematic: {mismatched}")

        with board.transaction():
            for ref in missing:
                part, pose = source[ref], poses[ref]
                board.place(
                    part.footprint, ref, pose.x, pose.y,
                    anchor=pose.anchor, rotation=pose.rotation,
                    side=pose.side, value=part.value,
                )
            for ref in sorted(set(source) & set(existing)):
                if existing[ref].value != source[ref].value:
                    board.set_field(ref, "Value", source[ref].value)
            if created:
                _OPEN[key] = board
            synced = sync_board_nets(
                schematic_path, board_path, net_names=net_names,
            )
            if not synced["ok"]:
                raise ValueError(synced["error"])
            board.save(validate=True)
    except _ERRORS as exc:
        if created:
            _OPEN.pop(key, None)
        return _fail(exc)
    return {
        "ok": True,
        "created": created,
        "board_path": str(board.path),
        "placed": missing,
        "extra_board_refs": sorted(set(existing) - set(source)),
        "net_count": synced["net_count"],
        "changed_pad_count": synced["changed_count"],
    }
