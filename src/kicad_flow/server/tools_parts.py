"""MCP tools for provider catalogues and project-local CAD bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..backend import project_library
from ..providers import part_provider_names, parts_provider
from . import _meta
from ._app import mcp

_ERRORS = (
    FileExistsError,
    FileNotFoundError,
    LookupError,
    OSError,
    RuntimeError,
    ValueError,
)


def _fail(exc: Exception) -> dict[str, Any]:
    """Return one explicit, typed refusal."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(tags=_meta.PARTS_INSPECT, annotations=_meta.READ)
def get_parts_provider_status(provider: str = "jlcpcb") -> dict[str, Any]:
    """Report whether a provider's local parts catalogue is usable.

    This does not download or update anything. JLCPCB stock and prices are a
    snapshot; the timestamp says how old that snapshot is.
    """
    try:
        status = parts_provider(provider).status()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "providers": list(part_provider_names()),
            **status.as_dict()}


@mcp.tool(tags=_meta.PARTS_INSPECT, annotations=_meta.READ)
def search_parts(
    provider: str,
    query: str,
    limit: int = 20,
    package: str = "",
    manufacturer: str = "",
    assembly_type: str = "",
    min_stock: int = 0,
) -> dict[str, Any]:
    """Search one provider catalogue with explicit, composable filters.

    Args:
        provider: Provider registry name; currently ``jlcpcb``.
        query: Text matched against provider number, manufacturer part number,
            and description.
        limit: Number of results, from 1 through 100.
        package: Optional case-insensitive package substring.
        manufacturer: Optional case-insensitive manufacturer substring.
        assembly_type: Empty, ``basic``, ``preferred``, or ``extended``.
        min_stock: Minimum assembly stock in this local snapshot.

    Returns:
        Normalized parts including provider/MPN identifiers, package,
        assembly class, snapshot stock, prices, and source links.
    """
    try:
        found = parts_provider(provider).search(
            query,
            limit=limit,
            package=package,
            manufacturer=manufacturer,
            assembly_type=assembly_type,
            min_stock=min_stock,
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "provider": provider.lower(), "count": len(found),
            "parts": [part.as_dict() for part in found]}


@mcp.tool(tags=_meta.PARTS_PRIMARY, annotations=_meta.WRITE)
def import_project_library(
    project_dir: str,
    name: str,
    source: str,
    source_path: str,
    source_url: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Install a downloaded KiCad CAD bundle into one project only.

    ``source_path`` may be an extracted directory, ZIP, or individual supported
    file. Modern ``.kicad_sym``, ``.kicad_mod``, STEP/STP, and WRL files are
    copied below ``<project>/kicad-flow-libraries/<name>``. The project's
    ``sym-lib-table`` and ``fp-lib-table`` are updated; KiCad's global library
    configuration is never touched.

    Download authenticated SnapEDA/SnapMagic or Component Search Engine bundles
    in an interactive browser first. Never put passwords or session tokens in
    these arguments. ``source`` and ``source_url`` record provenance only.

    Existing project assets are protected unless the caller explicitly sets
    ``overwrite=true``.
    """
    try:
        result = project_library(project_dir).import_bundle(
            name,
            source,
            Path(source_path),
            source_url=source_url,
            overwrite=overwrite,
        )
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "library": result.as_dict()}


@mcp.tool(tags=_meta.PARTS_INSPECT, annotations=_meta.READ)
def list_project_libraries(project_dir: str) -> dict[str, Any]:
    """List external CAD bundles installed in one KiCad project."""
    try:
        found = project_library(project_dir).libraries()
    except _ERRORS as exc:
        return _fail(exc)
    return {"ok": True, "count": len(found),
            "libraries": [item.as_dict() for item in found]}


__all__ = [
    "get_parts_provider_status",
    "import_project_library",
    "list_project_libraries",
    "search_parts",
]
