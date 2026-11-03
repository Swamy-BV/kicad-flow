"""Schematic open-document registry, file tools and atomic list writes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...backend import _library_sheet, create, load
from ...schematic import Sheet
from .. import _meta
from .._app import mcp

_OPEN: dict[str, Sheet] = {}


def _key(path: str) -> str:
    """The dictionary key for a sheet path."""
    return os.path.normcase(str(Path(path).resolve()))


def _sheet(path: str) -> Sheet:
    """The open sheet for *path*, loading it from disk if need be."""
    key = _key(path)
    if key not in _OPEN:
        if not Path(path).is_file():
            raise LookupError(
                f"no file at {path}. An existing .kicad_sch "
                f"reopens by itself -- just name it. Use `new_sheet` only to "
                f"create one, which OVERWRITES whatever is there."
            )
        _OPEN[key] = load(path)
    return _OPEN[key]


def _fail(exc: Exception) -> dict[str, Any]:
    """A refusal that says what went wrong."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def new_sheet(
    path: str, title: str = "", paper: str = "A4", instance_path: str = ""
) -> dict[str, Any]:
    """Start a new schematic sheet and open it for editing.

    The sheet is created in the native editor. Successful writes autosave by
    default; with autosave disabled, `save_sheet` persists it. The returned
    `size` is the drawable area inside the title block -- place within it.

    Args:
        path: Where the sheet will be written.
        title: Title-block title.
        paper: ``"A4"`` (default) or ``"A3"``. Prefer another functional A4
            child sheet. Use A3 only when one indivisible block cannot remain
            legible on A4 after reasonable layout.
        instance_path: Only for a CHILD sheet: the `instance_path` that
            `add_sheet` returned when the parent placed it. Leave empty for a
            standalone sheet or the root of a design. Get it wrong and the
            child's parts are annotated against the wrong sheet, so their nets
            never merge into the design and nothing says so.

    Returns:
        ``{ok, path, uuid, size: [w, h], grid}``.
    """
    try:
        sheet = create(path, paper=paper, title=title, instance_path=instance_path)
        w, h = sheet.size
        uuid = sheet.uuid
    except (ValueError, OSError, RuntimeError) as exc:
        return _fail(exc)
    _OPEN[_key(path)] = sheet
    return {
        "ok": True,
        "path": str(sheet.path),
        "uuid": uuid,
        "size": [w, h],
        "grid": 1.27,
    }


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def save_sheet(path: str) -> dict[str, Any]:
    """Write the open sheet to disk.

    Returns:
        ``{ok, path, parts, wires, labels}`` -- what was written.
    """
    try:
        sheet = _sheet(path)
        written = sheet.save(validate=True)
    except (LookupError, OSError, RuntimeError) as exc:
        return _fail(exc)
    return {
        "ok": True,
        "path": str(written),
        "parts": len(sheet.parts()),
        "wires": len(sheet.wires()),
        "labels": len(sheet.labels()),
    }


def _blank(project_dir: str = "") -> Sheet:
    """A library-only observation facade, independent of the editing mode."""
    directory = Path(project_dir).resolve() if project_dir else Path.cwd()
    return _library_sheet(directory)


def _atomic_items(
    sheet: Sheet, items: list[Any], key: str, each: Any
) -> dict[str, Any]:
    """Apply a typed list all-or-nothing and identify a refused element."""
    out: list[Any] = []
    _failed_index = 0
    try:
        with sheet.transaction():
            for _failed_index, item in enumerate(items):
                out.append(each(sheet, item))
    except (LookupError, OSError, ValueError) as exc:
        native_index = getattr(exc, "index", None)
        if isinstance(native_index, int):
            _failed_index = native_index
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "index": _failed_index,
            "applied_count": 0,
            key: [],
        }
    return {"ok": True, "count": len(out), key: out}


def _counted(sheet: Sheet, items: list[Any], each: Any, key: str) -> dict[str, Any]:
    """Run counted edits atomically, totalling how many objects they found."""
    result = _atomic_items(sheet, items, "results", each)
    if not result.get("ok"):
        result.pop("results", None)
        result[key] = 0
        return result
    values = result.pop("results")
    result[key] = sum(values)
    return result
