"""Export and read a schematic's netlist via ``kicad-cli``."""

from __future__ import annotations

import tempfile
from pathlib import Path

from kicad_flow.backend.kicad import _sexpr as sexpr
from kicad_flow.backend.kicad._sexpr import Node
from kicad_flow.backend.kicad.cli import cli


def export_netlist(schematic_path: str | Path) -> Node:
    """Export a schematic to a KiCad netlist and return its parsed tree.

    Raises:
        FileNotFoundError: If the schematic does not exist.
        KiCadCliError: If the export fails.
    """
    path = Path(schematic_path)
    if not path.is_file():
        raise FileNotFoundError(f"schematic not found: {path}")
    with tempfile.TemporaryDirectory() as tmp:
        net = Path(tmp) / "out.net"
        cli.sch_netlist(path, net)
        return sexpr.loads(net.read_text(encoding="utf-8"))


def list_nets(schematic_path: str | Path) -> list[dict[str, object]]:
    """Return the schematic's nets as ``{name, nodes}`` (nodes are ``ref.pin``).

    Single-pin nets (KiCad's ``unconnected-*``) are included so callers can
    spot dangling pins.
    """
    tree = export_netlist(schematic_path)
    nets_node = tree.get("nets")
    result: list[dict[str, object]] = []
    if nets_node is None:
        return result
    for net in nets_node.get_all("net"):
        name_node = net.get("name")
        name = str(name_node.items[1]) if name_node is not None else ""
        nodes: list[str] = []
        for node in net.get_all("node"):
            ref = node.get("ref")
            pin = node.get("pin")
            if ref is not None and pin is not None:
                nodes.append(f"{ref.items[1]}.{pin.items[1]}")
        result.append({"name": name, "nodes": sorted(nodes)})
    return result
