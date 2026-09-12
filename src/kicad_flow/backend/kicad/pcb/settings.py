"""Board layers, construction settings and project rule access."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from kicad_flow.pcb.types import (
    BoardLimits,
    BoardRule,
    NetClass,
    NetClassAssignment,
    Stackup,
    StackupLayer,
)

from .._sexpr import Node, Sym
from . import project as _project
from ._constants import (
    _STACKUP_KINDS,
)
from ._nodes import (
    _f,
    _node,
    _set,
    _text,
)

if TYPE_CHECKING:
    from .board import KiCadBoard


def set_stackup(self: KiCadBoard, stackup: Stackup) -> Stackup:
    """Replace only the board's stackup, preserving all other setup."""
    if not stackup.layers:
        raise ValueError("stackup needs at least one layer")
    if stackup.edge_connector not in ("", "yes", "bevelled"):
        raise ValueError("edge_connector must be empty, 'yes' or 'bevelled'")
    copper = tuple(
        layer.name for layer in stackup.layers if layer.kind.lower() == "copper"
    )
    if copper != self.layers:
        raise ValueError(
            f"stackup copper layers must be {list(self.layers)}, "
            f"in that order; got {list(copper)}"
        )
    for layer in stackup.layers:
        if not layer.name.strip() or not layer.kind.strip():
            raise ValueError("stackup layer name and kind cannot be empty")
        if layer.kind not in _STACKUP_KINDS:
            raise ValueError(
                f"stackup layer {layer.name!r} has unknown kind {layer.kind!r}"
            )
        if layer.kind in ("copper", "core", "prepreg") and layer.thickness is None:
            raise ValueError(f"physical stackup layer {layer.name!r} needs a thickness")
        if layer.thickness is not None and layer.thickness <= 0:
            raise ValueError(f"stackup layer {layer.name!r} thickness must be positive")
        if layer.epsilon_r is not None and layer.epsilon_r <= 0:
            raise ValueError(f"stackup layer {layer.name!r} epsilon_r must be positive")
        if layer.loss_tangent is not None and layer.loss_tangent < 0:
            raise ValueError(
                f"stackup layer {layer.name!r} loss_tangent cannot be negative"
            )
    physical_kinds = [
        layer.kind
        for layer in stackup.layers
        if layer.kind in ("copper", "core", "prepreg")
    ]
    if (
        not physical_kinds
        or physical_kinds[0] != "copper"
        or physical_kinds[-1] != "copper"
    ):
        raise ValueError("physical stackup must start and end with copper")
    if any(a == b == "copper" for a, b in itertools.pairwise(physical_kinds)):
        raise ValueError(
            "each pair of copper layers needs at least one "
            "core/prepreg dielectric between them"
        )

    children: list[Node | Sym | str] = [Sym("stackup")]
    for layer in stackup.layers:
        values: list[Node | Sym | str] = [Sym("layer"), layer.name]
        values.append(_node("type", [layer.kind]))
        if layer.thickness is not None:
            values.append(_node("thickness", [layer.thickness]))
        if layer.material:
            values.append(_node("material", [layer.material]))
        if layer.epsilon_r is not None:
            values.append(_node("epsilon_r", [layer.epsilon_r]))
        if layer.loss_tangent is not None:
            values.append(_node("loss_tangent", [layer.loss_tangent]))
        if layer.color:
            values.append(_node("color", [layer.color]))
        children.append(Node(values))
    if stackup.copper_finish:
        children.append(_node("copper_finish", [stackup.copper_finish]))
    children.append(_node("dielectric_constraints", [stackup.dielectric_constraints]))
    if stackup.edge_connector:
        children.append(_node("edge_connector", [Sym(stackup.edge_connector)]))
    children.append(_node("castellated_pads", [stackup.castellated_pads]))
    children.append(_node("edge_plating", [stackup.edge_plating]))
    made = Node(children)

    setup = self._tree.get("setup")
    if setup is None:
        raise LookupError("board has no setup block")
    old = setup.get("stackup")
    if old is None:
        setup.items.insert(1, made)
    else:
        setup.items[setup.items.index(old)] = made

    physical = sum(
        layer.thickness or 0.0
        for layer in stackup.layers
        if layer.kind.lower() in ("copper", "core", "prepreg")
    )
    general = self._tree.get("general")
    thickness = general.get("thickness") if general is not None else None
    if thickness is not None and physical > 0:
        _set(thickness, 0, physical)
    return self.stackup()


def stackup(self: KiCadBoard) -> Stackup:
    """Read the physical construction without interpreting its choices."""
    setup = self._tree.get("setup")
    node = setup.get("stackup") if setup is not None else None
    if node is None:
        return Stackup(())
    layers = []
    for item in node.get_all("layer"):
        layers.append(
            StackupLayer(
                name=_text(item),
                kind=_text(item.get("type")),
                thickness=(
                    None
                    if item.get("thickness") is None
                    else _f(item.get("thickness"), 0)
                ),
                material=_text(item.get("material")),
                epsilon_r=(
                    None
                    if item.get("epsilon_r") is None
                    else _f(item.get("epsilon_r"), 0)
                ),
                loss_tangent=(
                    None
                    if item.get("loss_tangent") is None
                    else _f(item.get("loss_tangent"), 0)
                ),
                color=_text(item.get("color")),
            )
        )
    return Stackup(
        layers=tuple(layers),
        copper_finish=_text(node.get("copper_finish")),
        dielectric_constraints=(_text(node.get("dielectric_constraints")) == "yes"),
        edge_connector=_text(node.get("edge_connector")),
        castellated_pads=_text(node.get("castellated_pads")) == "yes",
        edge_plating=_text(node.get("edge_plating")) == "yes",
    )


def thickness(self: KiCadBoard) -> float:
    """The finished thickness recorded in the board general block."""
    general = self._tree.get("general")
    node = general.get("thickness") if general is not None else None
    return _f(node, 0) if node is not None else 0.0


def set_limits(self: KiCadBoard, limits: BoardLimits) -> BoardLimits:
    """Update board-wide manufacturing limits without changing geometry."""
    if any(value < 0 for value in limits.as_dict().values()):
        raise ValueError("board limits cannot be negative")
    _project.set_limits(self._path, limits)
    if limits.min_solder_mask_bridge is not None:
        setup = self._tree.get("setup")
        if setup is None:
            raise LookupError("board has no setup block")
        node = setup.get("solder_mask_min_width")
        if node is None:
            setup.items.insert(
                1, _node("solder_mask_min_width", [limits.min_solder_mask_bridge])
            )
        else:
            _set(node, 0, limits.min_solder_mask_bridge)
    return self.limits()


def limits(self: KiCadBoard) -> BoardLimits:
    """Read project-wide limits and the mask bridge stored on the board."""
    values: dict[str, Any] = vars(_project.limits(self._path)).copy()
    setup = self._tree.get("setup")
    node = setup.get("solder_mask_min_width") if setup is not None else None
    values["min_solder_mask_bridge"] = _f(node, 0) if node is not None else None
    return BoardLimits(**values)


def set_net_classes(self: KiCadBoard, classes: tuple[NetClass, ...]) -> list[NetClass]:
    """Create or update netclasses in the sibling project."""
    return _project.set_net_classes(self._path, classes)


def net_classes(self: KiCadBoard) -> list[NetClass]:
    """Read every project netclass."""
    return _project.net_classes(self._path)


def assign_net_classes(
    self: KiCadBoard, assignments: tuple[NetClassAssignment, ...]
) -> list[NetClassAssignment]:
    """Replace memberships for the nets mentioned by the caller."""
    return _project.assign(self._path, assignments)


def net_policy(self: KiCadBoard, nets: tuple[str, ...]) -> dict[str, object]:
    """Read effective classes with KiCad's own inheritance resolver."""
    return _project.net_policy(self._path, nets)


def net_class_assignments(self: KiCadBoard) -> list[NetClassAssignment]:
    """Read explicit net-to-netclass memberships."""
    return _project.assignments(self._path)


def set_rules(self: KiCadBoard, rules: tuple[BoardRule, ...]) -> list[BoardRule]:
    """Create or update named custom design rules."""
    return _project.set_rules(self._path, rules)


def rules(self: KiCadBoard) -> list[BoardRule]:
    """Read numeric custom design rules."""
    return _project.rules(self._path)
