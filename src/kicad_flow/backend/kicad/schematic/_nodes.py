"""Schematic node access, identifiers and page constants."""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Iterator
from typing import Any

from .._sexpr import Node, Sym

# Larger designs use child sheets rather than larger paper.
PAPER = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}


_MARGIN = 10.0


_LABEL_NODE = {
    "local": "label",
    "global": "global_label",
    "hierarchical": "hierarchical_label",
}


def _fmt(value: float) -> str:
    """A number as KiCad writes it: no trailing zeros, no exponent."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _node(name: str, atoms: list[Any] | None = None) -> Node:
    """``(name atom ...)``, with the quoting KiCad expects.

    Numbers and booleans become bare tokens; anything else is written as a
    quoted string. Getting this backwards is not a cosmetic problem -- a paper
    size written as ``""A4""`` is a file KiCad will not open, which has
    happened here before.
    """
    items: list[Node | Sym | str] = [Sym(name)]
    for atom in atoms or []:
        if isinstance(atom, bool):
            items.append(Sym("yes" if atom else "no"))
        elif isinstance(atom, (int, float)):
            items.append(Sym(_fmt(float(atom))))
        else:
            items.append(atom)
    return Node(items)


# Fixed namespace: rebuilding the same design must preserve its identifiers.
_UUID_NS = _uuid.UUID("6b3f7a1e-9c2d-5e48-9f10-2a7c4d8e0b53")


def _walk(node: Node) -> Iterator[Node]:
    """Every node in the tree, depth first."""
    yield node
    for item in node.items:
        if isinstance(item, Node):
            yield from _walk(item)


def _uid() -> str:
    """A fresh random UUID, for the few places nothing stable identifies."""
    return str(_uuid.uuid4())


def _uid_from(key: str) -> str:
    """A UUID derived from *key*, so a rebuild writes the same one.

    Every identifier used to be `uuid4`, which meant re-running a build
    rewrote every symbol instance even when nothing about the design had
    changed: fc's five pages churned about 4,000 lines a run, and a diff could
    not show what had actually moved. Deriving them from something stable
    about the thing makes the file a function of the design.

    It also makes a sheet's `instance_path` stable, because that path IS its
    uuid -- so re-creating a root no longer orphans its children.
    """
    return str(_uuid.uuid5(_UUID_NS, key))


def _atom(node: Node | None, index: int) -> str | None:
    """The *index*-th atom AFTER the node name, as text.

    ``items[0]`` is the name -- ``(at 1 2)`` is ``["at", "1", "2"]`` -- so
    every accessor here counts from the first real value. Reading index 0 as
    the first value instead of the name is the mistake this exists to stop.
    """
    if node is None or len(node.items) <= index + 1:
        return None
    return str(node.items[index + 1])


def _set(node: Node, index: int, value: float) -> None:
    """Write the *index*-th atom after the node name, same convention as _atom.

    Readers and writers must agree about the name occupying slot 0. They did
    not, and `rotate` wrote the angle into the Y coordinate -- the part moved
    instead of turning, and every pin position that followed was wrong.
    """
    while len(node.items) <= index + 1:
        node.items.append(Sym("0"))
    node.items[index + 1] = Sym(_fmt(float(value)))


def _f(node: Node | None, index: int, default: float = 0.0) -> float:
    """Float value at *index*, or *default*."""
    raw = _atom(node, index)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _text(node: Node | None, index: int = 0, default: str = "") -> str:
    """String value at *index*, or *default*."""
    raw = _atom(node, index)
    return default if raw is None else raw
