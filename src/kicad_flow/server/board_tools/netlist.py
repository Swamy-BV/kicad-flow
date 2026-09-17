"""Transfer schematic net membership to already placed board footprints."""

from __future__ import annotations

from typing import Any

from .. import _meta
from .._app import mcp
from ..schematic_tools.session import _sheet
from .session import _ERRORS, _board, _fail


@mcp.tool(tags=_meta.PCB_PRIMARY, annotations=_meta.WRITE)
def sync_board_nets(
    schematic_path: str,
    board_path: str,
    net_names: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply the schematic's actual nets to all corresponding PCB pads.

    Place footprints first. `net_names` optionally maps exact schematic net
    names to explicit PCB aliases; unspecified names carry over unchanged.
    NC pins lose any old board assignment. The write is atomic and refuses
    changed net assignments when existing copper could retain old net names.
    `dry_run` reports the same changes without writing, including whether
    existing copper blocks their application. Successful MCP writes autosave.
    """
    try:
        sheet = _sheet(schematic_path)
        board = _board(board_path)
        aliases = net_names or {}
        source = sheet.nets()
        connected_names = {
            item.name for item in source
            if not item.no_connect
        }
        unknown = sorted(set(aliases) - connected_names)
        if unknown:
            raise ValueError(f"unknown schematic net names: {unknown}")
        if any(not value for value in aliases.values()):
            raise ValueError("board net aliases must be nonempty")

        target_names: dict[str, str] = {}
        desired: dict[tuple[str, str], str] = {}
        for item in source:
            target = "" if item.no_connect else aliases.get(item.name, item.name)
            if not item.no_connect:
                previous = target_names.setdefault(target, item.name)
                if previous != item.name:
                    raise ValueError(
                        f"board net {target!r} combines {previous!r} "
                        f"and {item.name!r}"
                    )
            for pin in item.pins:
                key = (pin.ref, pin.pin)
                if key in desired and desired[key] != target:
                    raise ValueError(f"schematic assigns {key} to two nets")
                desired[key] = target

        actual: dict[tuple[str, str], str] = {}
        for footprint in board.footprints():
            for pad in footprint.pads:
                if not pad.number or pad.kind == "npth":
                    continue
                key = (footprint.ref, pad.number)
                previous = actual.setdefault(key, pad.net)
                if previous != pad.net:
                    raise ValueError(f"same-number pads {key} have different nets")
        missing = sorted(set(desired) - set(actual))
        if missing:
            raise LookupError(f"schematic pins lack placed board pads: {missing}")
        extra_netted = sorted(
            key for key, name in actual.items() if name and key not in desired
        )
        if extra_netted:
            raise ValueError(
                f"board pads absent from schematic have nets: {extra_netted}"
            )

        changes = [
            {"ref": ref, "pad": pad, "before": actual[(ref, pad)], "after": name}
            for (ref, pad), name in sorted(desired.items())
            if actual[(ref, pad)] != name
        ]
        copper_blocks = bool(
            any(change["before"] for change in changes)
            and (board.tracks() or board.vias() or board.zones())
        )
        if copper_blocks and not dry_run:
            raise ValueError("net changes would leave existing copper on old nets")
        if not dry_run:
            with board.transaction():
                for change in changes:
                    board.set_net(change["ref"], change["pad"], change["after"])
    except _ERRORS as exc:
        return _fail(exc)
    return {
        "ok": True,
        "dry_run": dry_run,
        "safe_to_apply": not copper_blocks,
        "net_count": len(target_names),
        "pad_count": len(desired),
        "changed_count": len(changes),
        "changes": changes,
    }
