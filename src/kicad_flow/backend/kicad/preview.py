"""Isolated schematic hierarchy snapshots for live preview CLI processes."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ._sexpr import Node, dumps, loads


@contextlib.contextmanager
def schematic_snapshot(source: Path) -> Iterator[Path]:
    """Copy referenced sheets, preserving relative layout and project identity.

    Read live files only briefly; the CLI holds handles exclusively on copies.
    Absolute sheet references are remapped into the temporary hierarchy too.
    """
    source = source.resolve()
    sheets: dict[Path, tuple[str, Node, list[tuple[Node, Path]]]] = {}
    pending = [source]
    while pending:
        path = pending.pop()
        if path in sheets:
            continue
        text = path.read_text(encoding="utf-8")
        tree = loads(text)
        references: list[tuple[Node, Path]] = []
        for sheet in tree.get_all("sheet"):
            for prop in sheet.get_all("property"):
                if len(prop.items) >= 3 and prop.items[1] == "Sheetfile":
                    child = (path.parent / str(prop.items[2])).resolve()
                    references.append((prop, child))
                    pending.append(child)
        sheets[path] = (text, tree, references)
    common = Path(os.path.commonpath([str(p.parent) for p in sheets]))
    with tempfile.TemporaryDirectory(prefix="kicad-flow-preview-") as name:
        root = Path(name)
        targets = {p: root / p.relative_to(common) for p in sheets}
        for path, (text, tree, references) in sheets.items():
            target = targets[path]
            target.parent.mkdir(parents=True, exist_ok=True)
            changed = False
            for prop, child in references:
                if Path(str(prop.items[2])).is_absolute():
                    prop.items[2] = os.path.relpath(targets[child], target.parent)
                    changed = True
            target.write_text(dumps(tree) if changed else text, encoding="utf-8")
            project = path.with_suffix(".kicad_pro")
            if project.is_file():
                target.with_suffix(".kicad_pro").write_bytes(project.read_bytes())
        yield targets[source]
