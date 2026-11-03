"""Native project and design-rule operations for the isolated IPC worker."""

from __future__ import annotations

import hashlib
import importlib
import math
from typing import Any


def _rules_revision(self: Any) -> str | None:
    """Observe native-written rule bytes without editing the document."""
    path = self.root.with_suffix(".kicad_dru")
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _rule_layer(self: Any, rule: Any, scope: str, source: Any) -> None:
    """Translate an explicit saved or caller-supplied layer selector."""
    rule.proto.ClearField("layer_condition")
    if scope in ("outer", "inner"):
        rule.layer_mode = getattr(source.CustomRuleLayerMode, "CRLM_" + scope.upper())
    elif scope:
        rule.single_layer = self.layer(scope)


def settings(self: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Read and change supported native design settings without file writes."""
    operation = args["operation"]
    values = args.get("values")
    if operation in ("rules_accept", "rules_rollback"):
        if self.rules_checkpoint is None:
            raise ValueError("No custom rule write awaits verification")
        if _rules_revision(self) != self.rules_revision:
            raise ValueError(
                "custom rules changed externally during verification; "
                "reload_board before further rule writes"
            )
        if operation == "rules_rollback":
            result = self.b.set_custom_design_rules(self.rules_checkpoint)
            if result.status not in (self.bc.CRS_VALID, self.bc.CRS_NONE):
                raise ValueError("KiCad could not restore the previous custom rules")
        self.rules_checkpoint = None
        return {}
    if operation == "limits":
        rules = self.b.get_design_rules().rules
        names = {
            "min_clearance": "min_clearance",
            "min_track_width": "min_track_width",
            "min_via_diameter": "min_via_size",
            "min_via_drill": "min_through_drill",
            "min_annular_width": "min_via_annular_width",
            "min_hole_clearance": "hole_clearance",
            "min_hole_to_hole": "hole_to_hole_min",
            "min_copper_edge_clearance": "copper_edge_clearance",
            "min_silk_clearance": "silk_clearance",
            "min_text_height": "min_silk_text_height",
            "min_text_thickness": "min_silk_text_thickness",
            "min_groove_width": "min_groove_width",
        }
        mask = {
            "solder_mask_to_copper_clearance": "mask_to_copper_clearance",
            "min_solder_mask_bridge": "mask_min_width",
        }
        if values is not None:
            for name, value in values.items():
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be finite and non-negative")
                target = (
                    getattr(rules.proto.constraints, names[name])
                    if name in names
                    else getattr(rules.proto.solder_mask_paste, mask[name])
                )
                target.value_nm = round(value * 1e6)
            rules = self.b.set_design_rules(rules).rules
        return {
            "values": {
                **{
                    name: getattr(rules.proto.constraints, key).value_nm / 1e6
                    for name, key in names.items()
                },
                **{
                    name: getattr(rules.proto.solder_mask_paste, key).value_nm / 1e6
                    for name, key in mask.items()
                },
            }
        }
    project = self.b.get_project()
    pt = importlib.import_module("kipy.project_types")
    if operation == "net_classes":
        classes = {item.name: item for item in project.get_net_classes()}
        dimensions = {
            "clearance": "clearance",
            "track_width": "track_width",
            "diff_pair_width": "diff_pair_track_width",
            "diff_pair_gap": "diff_pair_gap",
            "diff_pair_via_gap": "diff_pair_via_gap",
        }
        if values is not None:
            if len({v["name"] for v in values}) != len(values):
                raise ValueError("netclass names must be unique within one call")
            for value in values:
                for key, number in value.items():
                    if (
                        key != "name"
                        and not key.endswith("color")
                        and number is not None
                        and (not math.isfinite(number) or number < 0)
                    ):
                        raise ValueError(f"{key} must be finite and non-negative")
            changed = []
            for value in values:
                name = value["name"]
                if not name.strip():
                    raise ValueError("netclass name cannot be empty")
                item = classes.get(name, pt.NetClass(None))
                item.proto.name = name
                item.proto.type = 1
                board = item.proto.board
                for key, target in dimensions.items():
                    if value.get(key) is not None:
                        if not math.isfinite(value[key]) or value[key] < 0:
                            raise ValueError(f"{key} must be finite and non-negative")
                        getattr(board, target).value_nm = round(value[key] * 1e6)
                for prefix, target in (
                    ("via", "via_stack"),
                    ("microvia", "microvia_stack"),
                ):
                    if any(
                        value.get(prefix + "_" + key) is not None
                        for key in ("diameter", "drill")
                    ):
                        via = self.bt.Via()
                        existing = getattr(board, target)
                        if existing.copper_layers:
                            via.proto.pad_stack.CopyFrom(existing)
                        if value.get(prefix + "_diameter") is not None:
                            via.diameter = round(value[prefix + "_diameter"] * 1e6)
                        if value.get(prefix + "_drill") is not None:
                            via.drill_diameter = round(value[prefix + "_drill"] * 1e6)
                        getattr(board, target).CopyFrom(via.proto.pad_stack)
                for key, holder in (
                    ("pcb_color", board),
                    ("schematic_color", item.proto.schematic),
                ):
                    color = value.get(key)
                    if color is not None:
                        if color and (
                            len(color) not in (7, 9) or not color.startswith("#")
                        ):
                            raise ValueError("color must be #RRGGBB or #RRGGBBAA")
                        for index, channel in enumerate(("r", "g", "b", "a")):
                            number = (
                                int(color[1 + index * 2 : 3 + index * 2], 16) / 255
                                if color and index * 2 + 3 <= len(color)
                                else (1 if color and index == 3 else 0)
                            )
                            setattr(holder.color, channel, number)
                changed.append(item)
            project.set_net_classes(changed)
            classes = {item.name: item for item in project.get_net_classes()}
        rows: list[dict[str, Any]] = []
        for name, item in sorted(classes.items()):
            row: dict[str, Any] = {"name": name}
            board = item.proto.board
            for key, target in dimensions.items():
                if board.HasField(target):
                    row[key] = getattr(board, target).value_nm / 1e6
            for prefix, target in (
                ("via", "via_stack"),
                ("microvia", "microvia_stack"),
            ):
                stack = getattr(board, target)
                if stack.copper_layers:
                    row[prefix + "_diameter"] = stack.copper_layers[0].size.x_nm / 1e6
                    row[prefix + "_drill"] = stack.drill.diameter.x_nm / 1e6
            for key, holder in (
                ("pcb_color", board),
                ("schematic_color", item.proto.schematic),
            ):
                row[key] = ""
                if holder.HasField("color"):
                    c = holder.color
                    row[key] = (
                        ""
                        if c.a == 0
                        else "#"
                        + "".join(
                            f"{round(channel * 255):02X}" for channel in (c.r, c.g, c.b)
                        )
                        + (f"{round(c.a * 255):02X}" if c.a != 1 else "")
                    )
            rows.append(row)
        if values is not None:
            by_name = {row["name"]: row for row in rows}
            rows = [by_name[value["name"]] for value in values]
        return {"values": rows}
    if operation in ("patterns", "assignments"):
        assignments = project.get_net_class_assignments()
        if values is not None:
            known_classes = {c.name for c in project.get_net_classes()}
            for value in values:
                if value["net_class"] not in known_classes:
                    raise LookupError(f"unknown netclass {value['net_class']}")
                key = "pattern" if operation == "patterns" else "net"
                if not value[key].strip():
                    raise ValueError(f"{key} cannot be empty")
            if operation == "patterns" and len({v["pattern"] for v in values}) != len(
                values
            ):
                raise ValueError("patterns must be unique")
            cmd = importlib.import_module(
                "kipy.proto.common.commands.project_commands_pb2"
            )
            command = cmd.SetNetClassAssignments()
            command.project.CopyFrom(self.b.document.project)
            command.merge_mode = self.ct.MMM_REPLACE
            if operation == "patterns":
                command.assignments.extend(a.proto for a in assignments.assignments)
                for value in values:
                    command.pattern_assignments.add(
                        pattern=value["pattern"], netclass=value["net_class"]
                    )
            else:
                grouped = {a.net: list(a.netclasses) for a in assignments.assignments}
                for value in values:
                    grouped[value["net"]] = [
                        v["net_class"] for v in values if v["net"] == value["net"]
                    ]
                command.pattern_assignments.extend(
                    p.proto for p in assignments.pattern_assignments
                )
                for net, members in grouped.items():
                    command.assignments.add(net=net, netclasses=members)
            self.k._client.send(command, self.empty)
            assignments = project.get_net_class_assignments()
        if operation == "patterns":
            return {
                "values": [
                    {"pattern": p.pattern, "net_class": p.netclass}
                    for p in assignments.pattern_assignments
                ]
            }
        return {
            "values": [
                {"net": a.net, "net_class": c}
                for a in assignments.assignments
                for c in a.netclasses
            ]
        }
    if operation == "rules":
        source = importlib.import_module("kipy.board_rules")
        if values is not None and args["revision"] != _rules_revision(self):
            raise ValueError("custom rules changed before the native write; retry")
        response = self.b.get_custom_design_rules()
        if response.status == self.bc.CRS_INVALID:
            raise ValueError(
                "Existing custom rules are invalid: " + response.error_text
            )
        rules = {r.name: r for r in response.rules}
        aliases = {"diff_pair_uncoupled": "max_uncoupled"}
        if values is not None:
            if self.rules_checkpoint is not None:
                raise ValueError("A previous native custom rule write is unverified")
            if rules.keys() != args["scopes"].keys():
                raise ValueError("native custom rules differ from the saved snapshot")
            for name, rule in rules.items():
                _rule_layer(self, rule, args["scopes"][name], source)
            original = [source.CustomRule(rule.proto) for rule in rules.values()]
            if len({r["name"] for r in values}) != len(values):
                raise ValueError("rule names must be unique within one call")
            for value in values:
                if (
                    not value["name"].strip()
                    or not value["condition"].strip()
                    or not value["constraints"]
                ):
                    raise ValueError(
                        "rule name, condition and constraints cannot be empty"
                    )
                existing = rules.get(value["name"])
                rule = source.CustomRule(
                    existing.proto if existing is not None else None
                )
                rule.proto.ClearField("constraints")
                rule.name, rule.condition = value["name"], value["condition"]
                if existing is None:
                    rule.severity = self.ct.RS_ERROR
                _rule_layer(self, rule, value.get("layer", ""), source)
                for constraint in value["constraints"]:
                    bounds = [
                        constraint[k]
                        for k in ("min", "opt", "max")
                        if constraint.get(k) is not None
                    ]
                    if (
                        not bounds
                        or any(not math.isfinite(v) or v < 0 for v in bounds)
                        or bounds != sorted(bounds)
                    ):
                        raise ValueError(
                            "constraint needs finite non-negative, ordered bounds"
                        )
                    kind = aliases.get(constraint["kind"], constraint["kind"])
                    native = rule.proto.constraints.add()
                    native.type = getattr(
                        source.CustomRuleConstraintType, "CRCT_" + kind.upper()
                    )
                    for key in ("min", "opt", "max"):
                        if constraint.get(key) is not None:
                            setattr(native.numeric, key, round(constraint[key] * 1e6))
                rules[rule.name] = rule
            if args["revision"] != _rules_revision(self):
                raise ValueError("custom rules changed before the native write; retry")
            response = self.b.set_custom_design_rules(list(rules.values()))
            if response.status != self.bc.CRS_VALID:
                if _rules_revision(self) != args["revision"]:
                    self.b.set_custom_design_rules(original)
                raise ValueError(response.error_text)
            self.rules_checkpoint = original
            self.rules_revision = _rules_revision(self)
        rows = []
        for rule in response.rules:
            row = {
                "name": rule.name,
                "condition": rule.condition,
                "constraints": [],
            }
            if rule.single_layer is not None:
                row["layer"] = (
                    self.bt.BoardLayer.Name(rule.single_layer)
                    .removeprefix("BL_")
                    .replace("_", ".")
                )
            elif rule.layer_mode:
                row["layer"] = "outer" if rule.layer_mode == 1 else "inner"
            for constraint in rule.proto.constraints:
                kind = (
                    source.CustomRuleConstraintType.Name(constraint.type)
                    .removeprefix("CRCT_")
                    .lower()
                )
                kind = "diff_pair_uncoupled" if kind == "max_uncoupled" else kind
                row["constraints"].append(
                    {
                        "kind": kind,
                        **{
                            key: getattr(constraint.numeric, key) / 1e6
                            for key in ("min", "opt", "max")
                            if constraint.numeric.HasField(key)
                        },
                    }
                )
            rows.append(row)
        return {"values": rows}
    raise ValueError("Unknown native PCB settings operation")
