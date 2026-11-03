"""Read native effective dimensions and their explicit constituent owners."""

from __future__ import annotations

from typing import Any

_DIMENSIONS = {
    "clearance": "clearance",
    "track_width": "track_width",
    "diff_pair_width": "diff_pair_track_width",
    "diff_pair_gap": "diff_pair_gap",
    "diff_pair_via_gap": "diff_pair_via_gap",
}


def dimensions(item: Any) -> dict[str, int | None]:
    """Observe optional dimensions independently, in native nanometres."""
    board = item.proto.board
    values = {
        name: getattr(board, key).value_nm if board.HasField(key) else None
        for name, key in _DIMENSIONS.items()
    }
    stack = board.via_stack
    values["via_diameter"] = (
        stack.copper_layers[0].size.x_nm if stack.copper_layers else None
    )
    values["via_drill"] = stack.drill.diameter.x_nm if stack.HasField("drill") else None
    return {
        key: value if value is not None and value >= 0 else None
        for key, value in values.items()
    }


def policy(self: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Use native constituent order and field presence; verify each owner."""
    effective = self.b.get_netclass_for_nets(
        [self.bt.Net(name=n) for n in args["nets"]]
    )
    explicit = {c.name: c for c in self.b.get_project().get_net_classes()}
    raw = {name: dimensions(item) for name, item in explicit.items()}
    rows, warnings = [], []
    for net, item in effective.items():
        names = list(item.proto.constituents) or [item.name]
        # NET_SETTINGS::makeEffectiveNetclass retains constituents in resolution
        # order; NETCLASS::Serialize returns that same vector. Use the native
        # order, including its priority/name tie-breaking, rather than rebuilding
        # membership or sorting names with a different Unicode collation.
        ordered = names if all(name in explicit for name in names) else []
        values = dimensions(item)
        sources = {}
        for key, actual in values.items():
            owner = next((name for name in ordered if raw[name][key] is not None), None)
            sources[key] = (
                owner if owner is not None and raw[owner][key] == actual else None
            )
            if actual is not None and sources[key] is None:
                warnings.append(
                    f"Native netclass owner could not be verified: {net}.{key}"
                )
        colors = {}
        for key, holder in (
            ("pcb_color", item.proto.board),
            ("schematic_color", item.proto.schematic),
        ):
            c = holder.color
            colors[key] = (
                ""
                if c.a == 0
                else (
                    "#"
                    + "".join(f"{round(v * 255):02X}" for v in (c.r, c.g, c.b))
                    + (f"{round(c.a * 255):02X}" if c.a != 1 else "")
                )
            )
        rows.append(
            {
                "net": net,
                "effective_class": item.name,
                "dimensions": {
                    key: value / 1e6 if value is not None else None
                    for key, value in values.items()
                },
                "sources": sources,
                "class_colors": colors,
            }
        )
    return {
        "nets": rows,
        "units": "mm",
        "scope": "netclass preferences; custom and per-object DRC rules may override",
        "custom_rule_count": len(self.b.get_custom_design_rules().rules),
        "readback_warnings": warnings,
    }
