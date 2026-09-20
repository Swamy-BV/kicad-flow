"""Create or reconcile a board from the schematic's physical components."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...pcb.api import Board
from ...pcb.types import Footprint, Pad
from .. import _meta
from .._app import mcp
from ..limits import BatchItems
from ..schematic_tools.session import _sheet
from .models import SchematicPlacement
from .netlist import _sync_board_nets
from .session import _ERRORS, _OPEN, _board, _fail, _fresh_board, _key


def _same_contact(before: Pad, after: Pad) -> bool:
    """Whether existing copper can see the same land after a footprint change."""
    return (
        before.at == after.at
        and before.size == after.size
        and before.layers == after.layers
        and before.kind == after.kind
        and before.shape == after.shape
        and before.rotation == after.rotation
    )


def _pad_has_copper(board: Board, pad: Pad) -> bool:
    """Conservatively observe same-net copper in the old pad's local area."""
    if not pad.net:
        return False
    radius = (pad.size[0] ** 2 + pad.size[1] ** 2) ** 0.5 / 2
    region = board.region(
        pad.at.x - radius, pad.at.y - radius,
        pad.at.x + radius, pad.at.y + radius,
    )
    for kind in ("tracks", "vias", "zones"):
        items = region[kind]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("net") != pad.net:
                continue
            layer = item.get("layer")
            if layer is None or layer in pad.layers or "*.Cu" in pad.layers:
                return True
    return False


def _check_changed_contacts(
    old: Footprint, new: Footprint, connected_pads: set[str],
    desired: dict[tuple[str, str], str],
) -> None:
    """Refuse a footprint change that would silently strand existing copper."""
    new_pads = {pad.number: pad for pad in new.pads}
    for pad in old.pads:
        if pad.number not in connected_pads:
            continue
        target = desired.get((old.ref, pad.number), "")
        replacement = new_pads.get(pad.number)
        if (
            target != pad.net
            or replacement is None
            or not _same_contact(pad, replacement)
        ):
            raise ValueError(
                f"{old.ref}.{pad.number}: existing copper on {pad.net!r} "
                "cannot be proven connected after the footprint change; "
                "remove or reroute that copper first"
            )


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def update_board_from_schematic(
    schematic_path: str,
    board_path: str,
    placements: BatchItems[SchematicPlacement] | None = None,
    net_names: dict[str, str] | None = None,
    layers: int = 2,
    thickness: float = 1.6,
) -> dict[str, Any]:
    """Export a missing PCB or sync an existing one from its schematic.

    The schematic supplies footprint IDs, values and pad nets. `placements`
    supplies poses for new components and for changed footprint assignments.
    Supply one batch within the advertised placement limit, then repeat with
    refs from `remaining_refs` until `complete` is true. Only supplied refs
    are placed or replaced; deferred footprints and their nets stay unchanged.
    A changed footprint is removed and added fresh at the supplied pose, rather
    than inheriting its old pose. Other existing poses are preserved. Existing
    copper is never rewritten; a change that cannot preserve its contacts is
    refused with the affected reference and pad. Save is part of the call;
    a refusal leaves the board unchanged.
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
        mismatched = sorted(
            ref for ref in set(source) & set(existing)
            if source[ref].footprint != existing[ref].fp_id
        )
        missing = sorted(set(source) - set(existing))
        pending = set(missing) | set(mismatched)
        unexpected = sorted(set(poses) - pending)
        if unexpected:
            raise ValueError(
                "placements must name only missing or changed schematic "
                f"components; unexpected refs: {unexpected}; "
                f"pending refs: {sorted(pending)}"
            )
        if pending and not poses:
            raise ValueError(
                "supply a placement batch for missing or changed schematic "
                f"components: {sorted(pending)}"
            )
        to_place = sorted(poses)
        changed = sorted(set(mismatched) & set(poses))
        remaining = frozenset(pending - set(poses))

        aliases = net_names or {}
        desired = {
            (pin.ref, pin.pin): (
                "" if net.no_connect else aliases.get(net.name, net.name)
            )
            for net in sheet.nets() for pin in net.pins
        }
        connected_pads = {
            ref: {
                pad.number for pad in existing[ref].pads
                if _pad_has_copper(board, pad)
            }
            for ref in changed
        }

        with board.transaction():
            for ref in changed:
                board.remove(ref)
            for ref in to_place:
                part, pose = source[ref], poses[ref]
                new = board.place(
                    part.footprint, ref, pose.x, pose.y,
                    anchor=pose.anchor, rotation=pose.rotation,
                    side=pose.side, value=part.value,
                )
                if ref in changed:
                    _check_changed_contacts(
                        existing[ref], new, connected_pads[ref], desired,
                    )
            for ref in sorted(set(source) & set(existing) - set(mismatched)):
                if existing[ref].value != source[ref].value:
                    board.set_field(ref, "Value", source[ref].value)
            if created:
                _OPEN[key] = board
            synced = _sync_board_nets(
                sheet, board, net_names=net_names, deferred_refs=remaining,
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
        "placed": to_place,
        "changed_footprints": changed,
        "remaining_refs": sorted(remaining),
        "complete": not remaining,
        "extra_board_refs": sorted(set(existing) - set(source)),
        "net_count": synced["net_count"],
        "changed_pad_count": synced["changed_count"],
    }
