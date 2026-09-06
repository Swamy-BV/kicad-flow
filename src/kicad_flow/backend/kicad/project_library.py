"""Install downloaded CAD assets into a KiCad project's local libraries."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from kicad_flow.providers.api import ProjectLibrary
from kicad_flow.providers.types import ImportedLibrary

from ._sexpr import Node, Sym, dumps, loads

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_MODEL_SUFFIXES = {".step", ".stp", ".wrl"}


def _node(name: str, value: str) -> Node:
    """Build one two-atom KiCad table field."""
    return Node([Sym(name), value])


def _library_node(name: str, uri: str) -> Node:
    """Build one project library-table entry."""
    return Node([
        Sym("lib"),
        _node("name", name),
        _node("type", "KiCad"),
        _node("uri", uri),
        _node("options", ""),
        _node("descr", "Imported by KiCadFlow"),
    ])


def _table(path: Path, root_name: str) -> Node:
    """Read an existing table, or create an empty current-format table."""
    if path.is_file():
        tree = loads(path.read_text(encoding="utf-8"))
        if tree.name != root_name:
            raise ValueError(f"{path} is not a {root_name}")
        return tree
    return Node([Sym(root_name), Node([Sym("version"), Sym("7")])])


def _field(node: Node, name: str) -> str:
    """Read the first value of a named table field."""
    field = node.get(name)
    return str(field.items[1]) if field is not None and len(field.items) > 1 else ""


def _register(tree: Node, name: str, uri: str, *, overwrite: bool) -> None:
    """Add or explicitly replace one nickname in a parsed library table."""
    for index, item in enumerate(tree.items):
        if not isinstance(item, Node) or item.name != "lib":
            continue
        if _field(item, "name") != name:
            continue
        existing = _field(item, "uri")
        if existing == uri:
            return
        if not overwrite:
            raise FileExistsError(
                f"library nickname {name!r} already maps to {existing!r}"
            )
        tree.items[index] = _library_node(name, uri)
        return
    tree.items.append(_library_node(name, uri))


def _write_atomic(path: Path, text: str) -> None:
    """Replace a small project metadata file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            if not text.endswith("\n"):
                stream.write("\n")
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            Path(temporary).unlink()


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP while rejecting absolute and parent-traversal members."""
    with zipfile.ZipFile(archive) as bundle:
        root = destination.resolve()
        for member in bundle.infolist():
            resolved = (destination / member.filename).resolve()
            if resolved != root and root not in resolved.parents:
                raise ValueError(f"unsafe ZIP member {member.filename!r}")
        bundle.extractall(destination)


def _asset_files(root: Path) -> Iterator[Path]:
    """Yield regular files below a bundle without following directory links."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def _validate_symbol(path: Path) -> None:
    tree = loads(path.read_text(encoding="utf-8"))
    if tree.name != "kicad_symbol_lib":
        raise ValueError(f"{path} is not a modern KiCad symbol library")


def _rewrite_footprint(
    path: Path, models: dict[str, Path], project_uri: str
) -> list[str]:
    """Point model references at copied files when their basenames match."""
    tree = loads(path.read_text(encoding="utf-8"))
    if tree.name not in ("footprint", "module"):
        raise ValueError(f"{path} is not a KiCad footprint")
    unresolved: list[str] = []
    changed = False
    by_stem: dict[str, list[Path]] = {}
    for model in models.values():
        by_stem.setdefault(model.stem.lower(), []).append(model)
    for model_node in tree.get_all("model"):
        if len(model_node.items) < 2:
            continue
        old = str(model_node.items[1])
        basename = Path(old.replace("\\", "/")).name.lower()
        match = models.get(basename)
        if match is None:
            candidates = by_stem.get(Path(basename).stem.lower(), [])
            match = candidates[0] if len(candidates) == 1 else None
        if match is None:
            unresolved.append(old)
            continue
        model_node.items[1] = f"{project_uri}/{match.name}"
        changed = True
    if changed:
        path.write_text(dumps(tree) + "\n", encoding="utf-8", newline="\n")
    return unresolved


class KiCadProjectLibrary(ProjectLibrary):
    """A project's isolated symbol, footprint, and 3D-model libraries."""

    def __init__(self, project_dir: Path) -> None:
        """Open the project directory without touching global KiCad state."""
        self._project = project_dir.resolve()
        if not self._project.is_dir():
            raise FileNotFoundError(
                f"project directory does not exist: {self._project}"
            )
        self._root = self._project / "kicad-flow-libraries"
        self._manifest = self._root / "libraries.json"

    def _manifest_data(self) -> list[dict[str, Any]]:
        if not self._manifest.is_file():
            return []
        value = json.loads(self._manifest.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError(f"{self._manifest} must contain a JSON list")
        return [item for item in value if isinstance(item, dict)]

    def libraries(self) -> list[ImportedLibrary]:
        """List only bundles recorded by this importer."""
        return [
            ImportedLibrary(
                name=str(item.get("name", "")),
                source=str(item.get("source", "")),
                source_url=str(item.get("source_url", "")),
                symbol_libraries=tuple(
                    str(path)
                    for path in item.get(
                        "symbol_libraries", item.get("symbols", [])
                    )
                ),
                footprint_libraries=tuple(
                    str(path)
                    for path in item.get(
                        "footprint_libraries", item.get("footprints", [])
                    )
                ),
                models=tuple(str(path) for path in item.get("models", [])),
                unresolved_model_references=tuple(
                    str(path)
                    for path in item.get("unresolved_model_references", [])
                ),
            )
            for item in self._manifest_data()
        ]

    def import_bundle(
        self,
        name: str,
        source: str,
        source_path: Path,
        *,
        source_url: str = "",
        overwrite: bool = False,
    ) -> ImportedLibrary:
        """Validate and install every supported CAD asset in one bundle."""
        if _NAME.fullmatch(name) is None:
            raise ValueError(
                "name must start with a letter and contain at most 64 letters, "
                "digits, dots, underscores, or hyphens"
            )
        origin = source_path.resolve()
        if not origin.exists():
            raise FileNotFoundError(f"component bundle does not exist: {origin}")
        destination = (self._root / name).resolve()
        if self._root.resolve() not in destination.parents:
            raise ValueError("library destination escaped the project")
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"project library {name!r} already exists; pass overwrite=true"
            )

        self._root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{name}.", dir=self._root) as raw:
            stage = Path(raw)
            unpacked = stage / "source"
            unpacked.mkdir()
            if origin.is_dir():
                shutil.copytree(origin, unpacked, dirs_exist_ok=True)
            elif origin.suffix.lower() == ".zip":
                _safe_extract(origin, unpacked)
            else:
                shutil.copy2(origin, unpacked / origin.name)

            assets = list(_asset_files(unpacked))
            symbols = [path for path in assets if path.suffix.lower() == ".kicad_sym"]
            footprints = [
                path for path in assets if path.suffix.lower() == ".kicad_mod"
            ]
            models = [path for path in assets if path.suffix.lower() in _MODEL_SUFFIXES]
            if not symbols and not footprints and not models:
                raise ValueError(
                    "bundle has no .kicad_sym, .kicad_mod, .step/.stp, or .wrl assets"
                )
            for symbol in symbols:
                _validate_symbol(symbol)

            install = stage / "install"
            symbol_dir = install / "symbols"
            footprint_dir = install / f"{name}.pretty"
            model_dir = install / "3dmodels"
            for folder in (symbol_dir, footprint_dir, model_dir):
                folder.mkdir(parents=True)
            copied_models: dict[str, Path] = {}
            for model in models:
                target = model_dir / model.name
                if target.exists():
                    raise ValueError(f"duplicate model filename {model.name!r}")
                shutil.copy2(model, target)
                copied_models[target.name.lower()] = target
            for symbol in symbols:
                target = symbol_dir / symbol.name
                if target.exists():
                    raise ValueError(f"duplicate symbol filename {symbol.name!r}")
                shutil.copy2(symbol, target)
            unresolved: list[str] = []
            model_uri = f"${{KIPRJMOD}}/kicad-flow-libraries/{name}/3dmodels"
            for footprint in footprints:
                target = footprint_dir / footprint.name
                if target.exists():
                    raise ValueError(f"duplicate footprint filename {footprint.name!r}")
                shutil.copy2(footprint, target)
                unresolved.extend(_rewrite_footprint(target, copied_models, model_uri))

            sym_table = _table(self._project / "sym-lib-table", "sym_lib_table")
            fp_table = _table(self._project / "fp-lib-table", "fp_lib_table")
            symbol_ids: list[str] = []
            for index, symbol in enumerate(sorted(symbol_dir.glob("*.kicad_sym"))):
                nickname = name if len(symbols) == 1 else f"{name}_{index + 1}"
                uri = f"${{KIPRJMOD}}/kicad-flow-libraries/{name}/symbols/{symbol.name}"
                _register(sym_table, nickname, uri, overwrite=overwrite)
                symbol_ids.append(nickname)
            footprint_ids: list[str] = []
            if footprints:
                uri = f"${{KIPRJMOD}}/kicad-flow-libraries/{name}/{name}.pretty"
                _register(fp_table, name, uri, overwrite=overwrite)
                footprint_ids.append(name)

            if destination.exists():
                shutil.rmtree(destination)
            install.rename(destination)
            _write_atomic(self._project / "sym-lib-table", dumps(sym_table))
            _write_atomic(self._project / "fp-lib-table", dumps(fp_table))

        result = ImportedLibrary(
            name=name,
            source=source,
            source_url=source_url,
            symbol_libraries=tuple(symbol_ids),
            footprint_libraries=tuple(footprint_ids),
            models=tuple(
                str(path.relative_to(self._project)).replace("\\", "/")
                for path in sorted(destination.glob("3dmodels/*"))
            ),
            unresolved_model_references=tuple(sorted(set(unresolved))),
        )
        records = [item for item in self._manifest_data() if item.get("name") != name]
        records.append(result.as_dict())
        _write_atomic(self._manifest, json.dumps(records, indent=2, sort_keys=True))
        return result


__all__ = ["KiCadProjectLibrary"]
