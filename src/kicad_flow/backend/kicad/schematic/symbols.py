"""Symbol discovery, unit geometry and library definition readback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kicad_flow.schematic.types import (
    Pin,
    Point,
    SymbolDef,
)

from .. import _library as library
from .._sexpr import Node
from ._nodes import (
    _f,
    _text,
)

if TYPE_CHECKING:
    from .sheet import KiCadSheet


def _units_of(definition: Node) -> list[int]:
    """Every drawable unit index a symbol defines, excluding the common one."""
    seen = set()
    for sub in definition.get_all("symbol"):
        index = library.subsymbol_unit(_text(sub, 0))
        if index:
            seen.add(index)
    return sorted(seen)


def _in_unit(holder: Node, definition: Node, unit: int) -> bool:
    """Whether *holder*'s graphics belong to *unit*.

    KiCad names sub-symbols ``<base>_<unit>_<style>`` and unit 0 holds what
    every unit shares. Taking them all at once is what made an LM358 report
    eight pins with two pairs at identical coordinates.
    """
    if holder is definition:
        return True
    index = library.subsymbol_unit(_text(holder, 0))
    return index in (None, 0, unit)


def _symbol_pins(
    definition: Node,
    unit: int = 1,
) -> list[tuple[str, str, float, float, float, str, float]]:
    """Every ``(pin ...)`` of one unit, as raw library-space tuples.

    A KiCad symbol keeps its pins in child ``(symbol ...)`` units rather than
    at the top level, so this walks one level down as well.
    """
    out: list[tuple[str, str, float, float, float, str, float]] = []
    for holder in [definition, *definition.get_all("symbol")]:
        if not _in_unit(holder, definition, unit):
            continue
        for pin in holder.get_all("pin"):
            at = pin.get("at")
            kind = _text(pin, 0, "passive")
            out.append(
                (
                    _text(pin.get("number")),
                    _text(pin.get("name")),
                    _f(at, 0),
                    _f(at, 1),
                    _f(at, 2),
                    kind,
                    _f(pin.get("length"), 0, 2.54),
                )
            )
    return out


def _extent(
    pins: list[tuple[Any, ...]], definition: Node, unit: int = 1
) -> tuple[float, float, float, float]:
    """One unit's drawn box as ``(left, bottom, right, top)`` in symbol space."""
    xs: list[float] = []
    ys: list[float] = []
    for holder in [definition, *definition.get_all("symbol")]:
        if not _in_unit(holder, definition, unit):
            continue
        for kind in ("rectangle", "polyline", "circle", "arc"):
            for shape in holder.get_all(kind):
                for corner in ("start", "end", "center", "mid"):
                    node = shape.get(corner)
                    if node is not None:
                        xs.append(_f(node, 0))
                        ys.append(_f(node, 1))
                # A polyline carries its geometry in (pts (xy ...)), not in
                # start/end. Missing those measured an LED as barely taller
                # than its pins, and its value label landed on the symbol.
                pts = shape.get("pts")
                if pts is not None:
                    for xy in pts.get_all("xy"):
                        xs.append(_f(xy, 0))
                        ys.append(_f(xy, 1))
    for pin in pins:
        xs.append(float(pin[2]))
        ys.append(float(pin[3]))
    if not xs:
        return (-1.27, -1.27, 1.27, 1.27)
    return (min(xs), min(ys), max(xs), max(ys))


def find_symbols(self: KiCadSheet, query: str, limit: int = 20) -> list[SymbolDef]:
    """Library symbols whose ``Library:Symbol`` id contains *query*.

    Matching is on the id alone: checking descriptions would mean loading
    every symbol in every library, and there are a couple of hundred of
    them. Search by part number or family -- ``"USB_C_Receptacle"``,
    ``"AP2112"`` -- and read the description off the result.
    """
    found: list[SymbolDef] = []
    if limit <= 0:
        return found
    needle = query.lower()
    for nickname, file in library.symbol_libraries(self._path.parent).items():
        # A qualified substring can only match this library's suffix.
        # Preserve substring semantics ("vice:R" still matches Device:R).
        if ":" in needle and not nickname.lower().endswith(needle.split(":", 1)[0]):
            continue
        for name in library._library_symbol_names(str(file)):
            lib_id = f"{nickname}:{name}"
            if needle not in lib_id.lower():
                continue
            try:
                found.append(self.symbol(lib_id))
            except (LookupError, ValueError):
                continue
            if len(found) >= limit:
                return found
    return found


def symbol(self: KiCadSheet, lib_id: str, *, unit: int = 1) -> SymbolDef:
    """One unit of a symbol, with its pins at the symbol origin."""
    try:
        sym = self._load(lib_id)
    except Exception as exc:  # the loader raises several types
        raise LookupError(f"no symbol {lib_id!r}: {exc}") from exc
    raw = _symbol_pins(sym.definition, unit)
    pins = tuple(
        Pin(
            number=n,
            name=nm,
            at=Point(px, py),
            orientation=ang,
            kind=kind,
            length=length,
        )
        for n, nm, px, py, ang, kind, length in raw
    )
    box = _extent(list(raw), sym.definition, unit)
    left, bottom, right, top = box
    return SymbolDef(
        lib_id=lib_id,
        description=_text(self._prop_of(sym.definition, "Description"), 1),
        keywords=_text(self._prop_of(sym.definition, "ki_keywords"), 1),
        units=max(1, len(_units_of(sym.definition))),
        unit=unit,
        pins=pins,
        width=round(right - left, 3),
        height=round(top - bottom, 3),
        bounds=tuple(round(v, 3) for v in box),  # type: ignore[arg-type]
        power=sym.is_power,
    )
