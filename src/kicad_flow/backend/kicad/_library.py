"""Load symbol definitions from KiCad's installed ``.kicad_sym`` libraries.

To place a component we copy its full graphical definition out of the library
(e.g. ``Device.kicad_sym``) into the schematic's ``lib_symbols`` block, and read
enough metadata (reference prefix, default value, pin numbers) to build the
placed instance.
"""

from __future__ import annotations

import copy
import os
import re
import tempfile
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from kicad_flow.backend.kicad import _sexpr as sexpr
from kicad_flow.backend.kicad._sexpr import Node
from kicad_flow.backend.kicad.cli import cli

# Matches a top-level (one-tab-indented) symbol name in a .kicad_sym file.
_TOP_SYMBOL_RE = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)


def symbol_dirs() -> list[Path]:
    """Return directories to search for ``.kicad_sym`` libraries.

    Honors ``$KICAD_SYMBOL_DIR`` (semicolon- or os-separated) first, then the
    ``share/kicad/symbols`` folder beside the installed ``kicad-cli``. The scan
    is cached; a fresh list is returned each call so
    callers may mutate it freely.
    """
    return list(_symbol_dirs_cached())


@cache
def _symbol_dirs_cached() -> tuple[Path, ...]:
    """The library search path, computed once per process."""
    dirs: list[Path] = []
    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env:
        for part in env.replace(";", os.pathsep).split(os.pathsep):
            if part:
                dirs.append(Path(part))
    found = cli.path()
    if found:
        candidate = Path(found).parents[1] / "share" / "kicad" / "symbols"
        if candidate.exists():
            dirs.append(candidate)
    return tuple(dirs)


def _project_symbol_libraries(project_dir: Path | None) -> dict[str, Path]:
    """Resolve one project's ``sym-lib-table`` without changing global state."""
    if project_dir is None:
        return {}
    table = project_dir / "sym-lib-table"
    if not table.is_file():
        return {}
    try:
        root = sexpr.loads(table.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, Path] = {}
    for entry in root.get_all("lib"):
        name, uri = entry.get("name"), entry.get("uri")
        if name is None or uri is None or len(name.items) < 2 or len(uri.items) < 2:
            continue
        value = str(uri.items[1]).replace("${KIPRJMOD}", str(project_dir))
        found[str(name.items[1])] = Path(os.path.expandvars(value))
    return found


def symbol_libraries(project_dir: Path | None = None) -> dict[str, Path]:
    """Map every visible symbol-library nickname to its source file."""
    found: dict[str, Path] = {}
    for directory in symbol_dirs():
        for path in _libraries_in(directory):
            found[path.stem] = path
    found.update(_project_symbol_libraries(project_dir))
    return found


def find_library(nickname: str, project_dir: Path | None = None) -> Path:
    """Locate the ``<nickname>.kicad_sym`` file for a library nickname.

    Raises:
        FileNotFoundError: If no matching library file is found.
    """
    project = _project_symbol_libraries(project_dir).get(nickname)
    if project is not None:
        if project.is_file():
            return project
        raise FileNotFoundError(
            f"project symbol library {nickname!r} points to missing file {project}"
        )
    return _find_library_cached(nickname, _symbol_dirs_cached())




#: Where flattened KiCad 10 libraries are cached, one file per nickname.
_FLAT = Path(tempfile.gettempdir()) / "kicad_flow_symbols"


def _flatten(source: Path, target: Path) -> Path:
    """Concatenate a ``<nickname>.kicad_symdir`` into one ``.kicad_sym`` file.

    KiCad 10 ships each symbol library as a DIRECTORY of one file per symbol --
    `Device.kicad_symdir` holds 537 of them -- where every release before it
    shipped a single `Device.kicad_sym`. Nothing here is wrong; the format
    changed, and reading `Device:R` failed outright until this existed.

    The concatenation is written once and reused. It is keyed by the source
    directory's newest modification time, so a KiCad update rebuilds it and an
    unchanged install does not.
    """
    parts = sorted(source.glob("*.kicad_sym"))
    if not parts:
        raise FileNotFoundError(f"symbol directory {source} holds no symbols")
    newest = max(one.stat().st_mtime_ns for one in parts)
    if target.is_file() and target.stat().st_mtime_ns >= newest:
        return target

    header: Node | None = None
    symbols: list[Node] = []
    for one in parts:
        tree = sexpr.loads(one.read_text(encoding="utf-8"))
        if header is None:
            header = tree
        symbols.extend(
            node for node in tree.items
            if isinstance(node, Node) and node.name == "symbol"
        )
    if header is None:
        raise FileNotFoundError(f"symbol directory {source} holds no symbols")
    keep = [
        node for node in header.items
        if not (isinstance(node, Node) and node.name == "symbol")
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sexpr.dumps(Node([*keep, *symbols])), encoding="utf-8")
    return target


def _libraries_in(directory: Path) -> list[Path]:
    """Every symbol library in *directory*, whichever format it is in.

    A single `<nickname>.kicad_sym` where there is one, and a flattened
    `<nickname>.kicad_symdir` where KiCad 10 has one instead. Searching for the
    single-file form alone found nothing at all on a KiCad 10 install: the
    symbol search returned zero results for `STM32F405` on a machine with 222
    libraries on it.
    """
    found: list[Path] = []
    seen: set[str] = set()
    for one in sorted(directory.glob("*.kicad_sym")):
        found.append(one)
        seen.add(one.stem)
    for folder in sorted(directory.glob("*.kicad_symdir")):
        if folder.stem in seen or not folder.is_dir():
            continue
        try:
            found.append(_flatten(folder, _FLAT / f"{folder.stem}.kicad_sym"))
        except (OSError, FileNotFoundError):
            continue
    return found


@cache
def _find_library_cached(nickname: str, dirs: tuple[Path, ...]) -> Path:
    """Resolve a nickname against a fixed search path (cached per path)."""
    for d in dirs:
        candidate = d / f"{nickname}.kicad_sym"
        if candidate.is_file():
            return candidate
    # Then KiCad 10's directory format, flattened on first use.
    for d in dirs:
        folder = d / f"{nickname}.kicad_symdir"
        if folder.is_dir():
            return _flatten(folder, _FLAT / f"{nickname}.kicad_sym")
    searched = ", ".join(str(d) for d in dirs) or "(no search dirs)"
    raise FileNotFoundError(
        f"symbol library {nickname!r} not found. Searched: {searched}"
    )


@dataclass
class LibrarySymbol:
    """A symbol definition extracted from a library, ready to place.

    Attributes:
        lib_id: The full ``Library:Symbol`` id, e.g. ``"Device:R"``.
        definition: The library's ``(symbol ...)`` node, re-keyed so its name
            is *lib_id* (ready to drop into a schematic's ``lib_symbols``).
            Derived symbols (``extends``) are flattened into a self-contained
            definition.
        reference_prefix: Designator prefix, e.g. ``"R"`` or ``"#PWR"``.
        default_value: The symbol's default Value field, e.g. ``"R"``, ``"GND"``.
        pin_numbers: Every pin number the symbol exposes, in document order.
        is_power: Whether this is a power symbol (has a ``(power ...)`` marker).
        unit_count: Number of drawable units (1 for single-unit parts).
    """

    lib_id: str
    definition: Node
    reference_prefix: str
    default_value: str
    pin_numbers: list[str] = field(default_factory=list)
    is_power: bool = False
    unit_count: int = 1


def subsymbol_unit(name: str) -> int | None:
    """Parse the unit index from a sub-symbol name like ``"LM358_1_1"``.

    KiCad names sub-symbols ``<base>_<unit>_<style>``; unit ``0`` holds body
    graphics common to every unit. Returns None if the name is not in that form.
    """
    parts = name.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1])
    return None


def _property_value(symbol: Node, name: str) -> str | None:
    """Return the value of the named ``(property "<name>" "<value>" ...)``."""
    for prop in symbol.get_all("property"):
        if len(prop.items) >= 3 and prop.items[1] == name:
            return str(prop.items[2])
    return None


def _collect_pin_numbers(node: Node) -> list[str]:
    """Recursively collect pin numbers from a symbol and its sub-symbols."""
    numbers: list[str] = []
    for pin in node.get_all("pin"):
        num = pin.get("number")
        if num is not None and len(num.items) >= 2:
            numbers.append(str(num.items[1]))
    for sub in node.get_all("symbol"):
        numbers.extend(_collect_pin_numbers(sub))
    return numbers


def _unit_count(node: Node) -> int:
    """Return the highest unit index among a symbol's sub-symbols (min 1).

    Unit 0 holds body graphics common to every unit, so a symbol whose only
    sub-symbols are unit 0 still has a single placeable unit.
    """
    units = [
        u
        for sub in node.get_all("symbol")
        if (u := subsymbol_unit(str(sub.items[1]))) is not None
    ]
    return max([*units, 1])


def _find_symbol(lib: Node, name: str) -> Node | None:
    """Return the top-level ``(symbol "<name>" ...)`` node, or None."""
    for sym in lib.get_all("symbol"):
        if len(sym.items) >= 2 and sym.items[1] == name:
            return sym
    return None


def _materialize(lib: Node, symbol: Node) -> Node:
    """Flatten a symbol, resolving ``extends`` into a self-contained definition.

    The result's top name is the symbol's own (library-local) name, its
    sub-symbols are re-prefixed to match, and its properties override the base
    symbol's. Non-derived symbols are returned as a plain deep copy.

    Raises:
        KeyError: If an extended base symbol cannot be found.
    """
    local = str(symbol.items[1])
    ext = symbol.get("extends")
    if ext is None:
        return copy.deepcopy(symbol)

    base_name = str(ext.items[1])
    base = _find_symbol(lib, base_name)
    if base is None:
        raise KeyError(f"base symbol {base_name!r} extended by {local!r} not found")

    result = _materialize(lib, base)  # recursively flatten the base first
    result.items[1] = local

    # Override base properties with the derived symbol's own (by name).
    derived_props = {
        str(p.items[1]): copy.deepcopy(p) for p in symbol.get_all("property")
    }
    seen: set[str] = set()
    merged: list[Node | object] = []
    for it in result.items:
        if isinstance(it, Node) and it.name == "property":
            pname = str(it.items[1])
            merged.append(derived_props.get(pname, it))
            seen.add(pname)
        else:
            merged.append(it)
    insert_at = next(
        (
            i
            for i, it in enumerate(merged)
            if isinstance(it, Node) and it.name == "symbol"
        ),
        len(merged),
    )
    extra = [p for n, p in derived_props.items() if n not in seen]
    for off, prop in enumerate(extra):
        merged.insert(insert_at + off, prop)
    result.items = merged  # type: ignore[assignment]

    # Re-prefix sub-symbols from the base name to the derived name.
    for sub in result.get_all("symbol"):
        sname = str(sub.items[1])
        if sname.startswith(base_name + "_"):
            sub.items[1] = local + sname[len(base_name) :]
    return result


@cache
def _load_library_tree(path_str: str) -> Node:
    """Parse and cache a ``.kicad_sym`` file (keyed by path string)."""
    return sexpr.loads(Path(path_str).read_text(encoding="utf-8"))


def load_symbol(lib_id: str, project_dir: Path | None = None) -> LibrarySymbol:
    """Load a symbol by its ``Library:Symbol`` id.

    The returned :class:`LibrarySymbol` is **shared**, not a private copy --
    building one schematic asks for the same handful of symbols thousands of
    times (a sheet of decoupling caps loads ``power:GND`` once per geometry
    query), and materialising each one deep-copies its whole definition tree.
    Treat it as read-only; the one place that stores a definition *into* a
    schematic (:func:`~kicad_flow.sch.components.ensure_lib_symbol`) copies it
    first, so a library written to on disk after the first read is not seen.

    Args:
        lib_id: e.g. ``"Device:R"`` or ``"power:GND"``.
        project_dir: Optional project whose local ``sym-lib-table`` is visible.

    Returns:
        A shared :class:`LibrarySymbol` with the re-keyed definition and metadata.

    Raises:
        ValueError: If *lib_id* is not of the form ``Library:Symbol``.
        FileNotFoundError: If the library file is missing.
        KeyError: If the symbol (or an extended base) is not present.
    """
    if ":" not in lib_id:
        raise ValueError(f"lib_id must be 'Library:Symbol', got {lib_id!r}")
    nickname = lib_id.split(":", 1)[0]
    return _load_symbol_cached(lib_id, str(find_library(nickname, project_dir)))


@cache
def _load_symbol_cached(lib_id: str, library_path: str) -> LibrarySymbol:
    """Materialize a symbol from one resolved library path."""
    if ":" not in lib_id:
        raise ValueError(f"lib_id must be 'Library:Symbol', got {lib_id!r}")
    nickname, symbol_name = lib_id.split(":", 1)

    lib = _load_library_tree(library_path)
    match = _find_symbol(lib, symbol_name)
    if match is None:
        raise KeyError(f"symbol {symbol_name!r} not found in library {nickname!r}")

    # Flatten (resolving extends), then re-key the top name to the full lib_id.
    definition = _materialize(lib, match)
    definition.items[1] = lib_id

    return LibrarySymbol(
        lib_id=lib_id,
        definition=definition,
        reference_prefix=_property_value(definition, "Reference") or "U",
        default_value=_property_value(definition, "Value") or symbol_name,
        pin_numbers=_collect_pin_numbers(definition),
        is_power=definition.get("power") is not None,
        unit_count=_unit_count(definition),
    )


@cache
def _library_symbol_names(path_str: str) -> tuple[str, ...]:
    """Return top-level symbol names in a ``.kicad_sym`` file (cached).

    Uses a fast text scan rather than a full parse -- only names are needed.
    """
    text = Path(path_str).read_text(encoding="utf-8")
    return tuple(_TOP_SYMBOL_RE.findall(text))


