"""MCP tools: the schematic primitives, one tool per primitive.

There is no design document and no batch format. A tool takes a few scalars
and returns what it made -- notably, every call that places something returns
the **pin positions**, so the next call can wire to them without a lookup and
without repeating the rotation arithmetic.

The tools hold open sheets in memory, keyed by path, so a session is a
sequence of small calls rather than a re-parse each time. `save_sheet` writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..backend import create, load
from ..schematic import Sheet
from . import _meta
from ._app import mcp

#: Sheets currently open, by absolute path. A tool call finds its sheet here
#: rather than re-reading the file, which keeps a long editing session cheap
#: and keeps every call's view of pin positions consistent.
_OPEN: dict[str, Sheet] = {}


def _key(path: str) -> str:
    """The dictionary key for a sheet path."""
    return str(Path(path).resolve())


def _sheet(path: str) -> Sheet:
    """The open sheet for *path*, loading it from disk if need be."""
    key = _key(path)
    if key not in _OPEN:
        if not Path(path).is_file():
            raise LookupError(f"no sheet at {path}; call new_sheet first")
        _OPEN[key] = load(path)
    return _OPEN[key]


def _pages(spec: str) -> list[int]:
    """A ``pages`` selector as page numbers, or a :class:`ValueError`.

    kicad-cli takes a comma-separated list and nothing else -- it answers
    ``"2-4"`` with "No sheets to plot", which reads like an empty design
    rather than a rejected argument. So the range is refused here, by name.
    """
    if not spec.strip():
        return []
    out = []
    for part in spec.split(","):
        token = part.strip()
        if not token.isdigit() or int(token) < 1:
            raise ValueError(
                f"pages must be a comma-separated list of page numbers, "
                f"e.g. '1,3,5' -- got {part.strip()!r}"
                + (" (there is no range syntax)" if "-" in token else "")
            )
        out.append(int(token))
    return out


def _fail(exc: Exception) -> dict[str, Any]:
    """A refusal that says what went wrong."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# -- the sheet ------------------------------------------------------------


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def new_sheet(path: str, title: str = "", paper: str = "A4",
              instance_path: str = "") -> dict[str, Any]:
    """Start a new schematic sheet and open it for editing.

    Nothing is written until `save_sheet`. The returned `size` is the drawable
    area inside the title block -- place within it.

    Args:
        path: Where the sheet will be written.
        title: Title-block title.
        paper: A4, A3, A2, A1 or A0.
        instance_path: Only for a CHILD sheet: the `instance_path` that
            `add_sheet` returned when the parent placed it. Leave empty for a
            standalone sheet or the root of a design. Get it wrong and the
            child's parts are annotated against the wrong sheet, so their nets
            never merge into the design and nothing says so.

    Returns:
        ``{ok, path, uuid, size: [w, h], grid}``.
    """
    try:
        sheet = create(path, paper=paper, title=title,
                       instance_path=instance_path)
    except (ValueError, OSError) as exc:
        return _fail(exc)
    _OPEN[_key(path)] = sheet
    w, h = sheet.size
    return {"ok": True, "path": str(sheet.path), "uuid": sheet.uuid,
            "size": [w, h], "grid": 1.27}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_sheet(path: str, name: str, filename: str, x: float, y: float,
              width: float = 38.1, height: float = 25.4,
              ports: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Put a child sheet on this one, and get back where its ports landed.

    A design of more than one page is two halves that meet BY NAME: a port on
    the box here, and a hierarchical label of the same name inside the child.
    Nothing checks the pairing while you draw; `check_sheet` on the ROOT does.

    Power does not need ports. GND and +3V3 are global -- a power symbol on
    any sheet joins the same net as one on any other. Ports are for signals.

    Then create the child with `new_sheet`, passing the `instance_path` this
    returns, or the child's parts will not join the design's nets.

    Args:
        path: The open parent sheet.
        name: The sheet's name, shown above the box.
        filename: The child's file, e.g. ``"power.kicad_sch"``.
        x: Top-left corner of the box.
        y: Top-left corner of the box.
        width: Box width in mm.
        height: Box height in mm.
        ports: ``[{"name": "SENSE", "kind": "input"}, ...]``. Kind is
            ``input``, ``output``, ``bidirectional``, ``tri_state`` or
            ``passive``. They are spread down the left edge; `move_component`
            does not move them, so choose the box position with that in mind.

    Returns:
        ``{ok, name, filename, uuid, instance_path, x, y, width, height,
        pins: [{name, x, y, ...}]}`` -- the pins are points to wire to.
    """
    try:
        ref = _sheet(path).add_sheet(
            name, filename, x, y, width=width, height=height,
            ports=tuple((p["name"], p.get("kind", "passive"))
                        for p in (ports or [])),
        )
    except (LookupError, KeyError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, **ref.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def save_sheet(path: str) -> dict[str, Any]:
    """Write the open sheet to disk.

    Returns:
        ``{ok, path, parts, wires, labels}`` -- what was written.
    """
    try:
        sheet = _sheet(path)
        written = sheet.save()
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True, "path": str(written), "parts": len(sheet.parts()),
            "wires": len(sheet.wires()), "labels": len(sheet.labels())}


# -- the library ----------------------------------------------------------


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def find_symbol(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the symbol libraries for a part.

    Args:
        query: Matched against ``Library:Symbol`` ids, e.g. ``"Device:R"``,
            ``"MCU_Espressif"``, ``"USB_C"``.
        limit: Most results to return.

    Returns:
        ``{ok, symbols: [{lib_id, description, pins: n, width, height}]}``.
    """
    try:
        found = _blank().find_symbols(query, limit=limit)
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True, "symbols": [
        {"lib_id": s.lib_id, "description": s.description,
         "pins": len(s.pins), "width": s.width, "height": s.height,
         "power": s.power}
        for s in found
    ]}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def symbol_pins(lib_id: str, unit: int = 1) -> dict[str, Any]:
    """The pins a symbol has, before it is placed anywhere.

    Use this to decide how to orient a part and how much room to leave. For
    the positions to actually WIRE to, place the part and read the pins that
    `add_component` returns -- those have the rotation applied.

    Args:
        lib_id: ``Library:Symbol``.
        unit: Which unit to report. A multi-unit symbol answers one at a
            time: reporting them together puts two units' pins at identical
            coordinates, which is a wrong netlist rather than a messy drawing.

    Returns:
        ``{ok, lib_id, units, unit, width, height, pins: [...]}``.
    """
    try:
        sym = _blank().symbol(lib_id, unit=unit)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "lib_id": sym.lib_id, "units": sym.units,
            "unit": sym.unit, "width": sym.width, "height": sym.height,
            "description": sym.description,
            "pins": [p.as_dict() for p in sym.pins]}


def _blank() -> Sheet:
    """A throwaway sheet, for library queries that need no file."""
    return create(Path.cwd() / "_query.kicad_sch")


# -- parts ----------------------------------------------------------------


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_component(path: str, lib_id: str, ref: str, x: float, y: float,
                  value: str = "", rotation: float = 0.0, mirror: str = "",
                  unit: int = 1) -> dict[str, Any]:
    """Place one unit of a symbol on the sheet at ``(x, y)``.

    **The returned pins are the point of this call.** Each carries the sheet
    position to wire to, with rotation and mirroring already applied, and the
    direction it points so you know which way to leave.

    Args:
        path: The open sheet.
        lib_id: ``Library:Symbol``, e.g. ``"Device:R"``.
        ref: Reference designator, e.g. ``"R1"``.
        x: Position in mm; snapped to the 1.27 mm grid.
        y: Position in mm.
        value: Value field, e.g. ``"10k"``. Defaults to the symbol's own.
        rotation: 0, 90, 180 or 270 degrees, counter-clockwise.
            Any other angle is REFUSED -- KiCad will not open a sheet
            holding a symbol at, say, 45.
        mirror: ``"x"``, ``"y"`` or empty.
        unit: Which unit of a multi-unit symbol -- an LM358 is two op-amps
            plus a shared power unit. `symbol_pins` reports how many there
            are. Units share a reference and are placed one call each, so
            ``(ref, unit)`` names a placed thing.

    Returns:
        ``{ok, ref, lib_id, x, y, rotation, pins: [{number, name, x, y,
        orientation, kind}]}``.
    """
    try:
        part = _sheet(path).place(lib_id, ref, x, y, value=value,
                                  rotation=rotation, mirror=mirror, unit=unit)
    except (LookupError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, **part.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_component(path: str, ref: str, x: float, y: float,
                   unit: int = 1) -> dict[str, Any]:
    """Move a placed part. Returns it with its pins at their new positions."""
    try:
        return {"ok": True, **_sheet(path).move(ref, x, y, unit=unit).as_dict()}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def rotate_component(path: str, ref: str, rotation: float,
                     unit: int = 1) -> dict[str, Any]:
    """Turn a placed part to *rotation* degrees (0, 90, 180, 270).

    Returns it with the pins where they now are -- which is why this is worth
    a call rather than deleting and re-placing. Any angle that is not a
    quarter turn is refused: KiCad will not open a sheet holding one.
    """
    try:
        return {"ok": True,
                **_sheet(path).rotate(ref, rotation, unit=unit).as_dict()}
    except (LookupError, ValueError) as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def mirror_component(path: str, ref: str, axis: str,
                     unit: int = 1) -> dict[str, Any]:
    """Mirror a placed part about ``"x"``, ``"y"``, or ``""`` for neither."""
    try:
        return {"ok": True,
                **_sheet(path).mirror(ref, axis, unit=unit).as_dict()}
    except (LookupError, ValueError) as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def remove_component(path: str, ref: str, unit: int = 1) -> dict[str, Any]:
    """Take one placed unit off the sheet."""
    try:
        _sheet(path).remove(ref, unit=unit)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "removed": ref}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_component(path: str, ref: str, unit: int = 1) -> dict[str, Any]:
    """One placed unit and its pin positions."""
    try:
        return {"ok": True, **_sheet(path).part(ref, unit=unit).as_dict()}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_components(path: str, with_pins: bool = False) -> dict[str, Any]:
    """Every part on the sheet.

    Args:
        path: The open sheet.
        with_pins: Include every pin position. Off by default -- on a full
            sheet that is most of the reply, and `get_component` gets one part.
    """
    try:
        parts = _sheet(path).parts()
    except LookupError as exc:
        return _fail(exc)
    out = []
    for p in parts:
        d = p.as_dict()
        if not with_pins:
            d["pins"] = len(p.pins)
        out.append(d)
    return {"ok": True, "count": len(out), "parts": out}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_pin(path: str, ref: str, pin: str) -> dict[str, Any]:
    """Where one pin is on the sheet -- the point to wire to.

    *pin* may be its number (``"1"``) or its name (``"VCC"``).
    """
    try:
        point = _sheet(path).pin(ref, pin)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "pin": pin, **point.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def set_field(path: str, ref: str, name: str, value: str) -> dict[str, Any]:
    """Set one of a part's fields.

    ``Footprint`` is the field the board export reads, so this is how a part
    gets a footprint -- there is no separate call, because a footprint is not
    special here. ``Value``, ``Datasheet``, ``MPN`` and custom names work the
    same way.

    Args:
        path: The open sheet.
        ref: The part.
        name: Field name, e.g. ``"Footprint"``.
        value: What to set it to.

    Returns:
        ``{ok, ref, fields}`` -- every field on the part.
    """
    try:
        fields = _sheet(path).set_field(ref, name, value)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "fields": fields}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_field(path: str, ref: str, name: str, dx: float, dy: float,
               rotation: float | None = None,
               justify: str = "") -> dict[str, Any]:
    """Move a part's Reference or Value label, relative to the part.

    Labels are placed automatically when a part is placed or turned -- to the
    side a vertical part's wires do not leave from, above and below a
    horizontal one. That default keeps them off the wires, but on a crowded
    sheet two parts' labels can still meet, and only you know which should
    give way.

    Args:
        path: The open sheet.
        ref: The part.
        name: ``"Reference"``, ``"Value"``, or any other field.
        dx: Offset from the part's position, in mm.
        dy: Offset from the part's position, in mm.
        rotation: Text angle in degrees; omit to leave it alone.
        justify: ``"left"``, ``"right"``, or empty to centre.

    Returns:
        ``{ok, ref, field, x, y}``.
    """
    try:
        at = _sheet(path).move_field(ref, name, dx, dy, rotation=rotation,
                                     justify=justify)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "ref": ref, "field": name, **at.as_dict()}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a part, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _sheet(path).fields(ref)}
    except LookupError as exc:
        return _fail(exc)


# -- connections ----------------------------------------------------------


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_wire(path: str, x1: float, y1: float, x2: float,
             y2: float) -> dict[str, Any]:
    """Draw one straight wire segment between two points.

    A corner is two calls. That is deliberate: where a wire turns is a drawing
    decision, and nothing here will make it for you.
    """
    try:
        a, b = _sheet(path).wire(x1, y1, x2, y2)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "start": a.as_dict(), "end": b.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_junction(path: str, x: float, y: float) -> dict[str, Any]:
    """Mark a point where crossing wires connect.

    Wires that cross without one are NOT connected.
    """
    try:
        return {"ok": True, **_sheet(path).junction(x, y).as_dict()}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_label(path: str, x: float, y: float, text: str, kind: str = "local",
              rotation: float = 0.0, justify: str = "left") -> dict[str, Any]:
    """Name a net at a point.

    Two labels with the same text are the same net, with no wire between them.

    Args:
        path: The open sheet.
        x: Position in mm; snapped to the grid.
        y: Position in mm.
        text: The net name.
        kind: ``"local"`` (this sheet), ``"global"`` (the whole design), or
            ``"hierarchical"`` (a port on this sheet).
        rotation: Degrees. Only meaningful for a VERTICAL label (90, 270): a
            horizontal global label renders identically at 0 and 180, because
            rotation turns the box along with the text.
        justify: ``"left"`` or ``"right"`` -- THIS is what points a global or
            hierarchical label. Use ``right`` on a part's LEFT-hand pins: it
            puts the flag's tip on the right, where the wire arrives, and
            grows the box away from the part. Use ``left`` on its right-hand
            pins. Get it backwards and the wire runs straight through the
            text.
    """
    try:
        at = _sheet(path).label(x, y, text, kind=kind, rotation=rotation,
                                justify=justify)
    except (LookupError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, "text": text, "kind": kind, "justify": justify,
            **at.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power(path: str, x: float, y: float, net: str,
              rotation: float = 0.0) -> dict[str, Any]:
    """Place a power symbol -- ``GND``, ``+3V3``, ``+5V``, ``VCC``.

    Returns the placed part; its single pin is the point to wire to.
    *rotation* is a quarter turn: 0, 90, 180 or 270.
    """
    try:
        part = _sheet(path).power(x, y, net, rotation=rotation)
    except (LookupError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, **part.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power_flag(path: str, x: float, y: float,
                   rotation: float = 0.0) -> dict[str, Any]:
    """Place a PWR_FLAG, which tells ERC a net is driven.

    Wire it to the net it vouches for. Without one, ERC reports every supply
    as undriven.
    """
    try:
        part = _sheet(path).power_flag(x, y, rotation=rotation)
    except (LookupError, ValueError) as exc:
        return _fail(exc)
    return {"ok": True, **part.as_dict()}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_no_connect(path: str, x: float, y: float) -> dict[str, Any]:
    """Mark a pin deliberately unconnected, so ERC stops reporting it."""
    try:
        return {"ok": True, **_sheet(path).no_connect(x, y).as_dict()}
    except LookupError as exc:
        return _fail(exc)


# -- reading back ---------------------------------------------------------


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.WRITE)
def render_schematic(path: str, output_dir: str = "", dpi: int = 150,
                     black_and_white: bool = False, pages: str = "",
                     save: bool = True) -> dict[str, Any]:
    """Render the sheet to PNG -- one file per page -- so you can look at it.

    LOOK AT THE SHEET. `check_sheet` catches the electrical half and cannot
    see the other one: a label printed over a pin number, a power symbol
    through a net name, a wire drawn across text, a page that is correct and
    unreadable. Every readability fault found in this project so far was found
    by rendering and looking, and by nothing else -- ERC reported 0 errors and
    0 warnings for all of them.

    Args:
        path: The open sheet. On a hierarchy, render the ROOT to get every
            page; a child renders alone.
        output_dir: Where the PNGs go. Defaults to the sheet's own folder.
        dpi: Raster resolution. 150 reads a full A3 page; go higher and crop
            when checking one block for overprints.
        black_and_white: Render without colour.
        pages: Which pages, as a comma-separated list of page numbers --
            ``"3"`` or ``"1,3,5"``. Empty means all. There is no range
            syntax: kicad-cli refuses ``"2-4"`` with "No sheets to plot".
            Each PNG is named for the page it IS, not for its position in
            the selection.
        save: Write the sheet before rendering, so the picture is of what you
            have DRAWN rather than what was last saved. Leave it on unless you
            deliberately want the file as it stands on disk. Note the same
            caveat as `check_sheet`: a child sheet is read from disk, so save
            every child before rendering the root.

    Returns:
        ``{ok, path, count, images: [...]}``.
    """
    try:
        wanted = _pages(pages)
        sheet = _sheet(path)
        if save:
            sheet.save()
        images = sheet.render(
            output_dir=Path(output_dir) if output_dir else None, dpi=dpi,
            black_and_white=black_and_white, pages=pages or None,
        )
        # kicad-cli exports the SELECTION as a fresh document, so page 2 comes
        # back numbered 1 and a single page comes back with no number at all.
        # Left alone, `pages="2,3,4"` writes fc-1/fc-2/fc-3 holding pages
        # 2/3/4 -- names that are wrong rather than merely unhelpful.
        if wanted:
            # kicad-cli emits the selection in DOCUMENT order however it was
            # listed, so `pages="5,1"` comes back as page 1 then page 5 and
            # pairing with the given order labels both of them wrongly.
            wanted = sorted(set(wanted))
            # And through temporaries, because the selection is renumbered
            # from 1: renaming in place walks fc-1 onto the fc-2 that has not
            # been renamed yet, and the second file is lost.
            stem = sheet.path.stem
            staged = [(f.replace(f.with_name(f".{f.name}.tmp")), f.suffix)
                      for f in images]
            images = [f.replace(f.with_name(f"{stem}-{n}{suffix}"))
                      for (f, suffix), n in zip(staged, wanted, strict=False)]
    except (LookupError, OSError, RuntimeError, ValueError) as exc:
        return _fail(exc)
    numbers = wanted or list(range(1, len(images) + 1))
    return {"ok": True, "path": str(sheet.path), "count": len(images),
            "pages": numbers[:len(images)],
            "images": [str(p) for p in images]}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def what_is_at(path: str, x: float, y: float,
               radius: float = 0.01) -> dict[str, Any]:
    """What meets at a point: pins, wire ends and labels.

    The one query worth having while drawing, because it answers the only
    question that matters -- *is this actually connected?* A wire drawn to
    where a pin looked like it was reports one thing here, not two.
    """
    try:
        return {"ok": True, **_sheet(path).at(x, y, radius)}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_nets(path: str) -> dict[str, Any]:
    """What the sheet ACTUALLY connects -- not what you think you drew.

    Read back from KiCad itself. A schematic can be a valid file that opens
    and renders correctly while its wires join nothing, and this is the only
    call that will tell you. Check it after wiring, before believing a sheet
    is done.

    Returns:
        ``{ok, count, nets: [{name, count, pins: [{ref, pin, name}]}]}``.
    """
    try:
        nets = _sheet(path).nets()
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True, "count": len(nets),
            "nets": [n.as_dict() for n in nets]}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def check_sheet(path: str) -> dict[str, Any]:
    """Every rule violation, named by part and pin.

    Runs the electrical rule check and maps each violation from a position
    back to the pin that sits there, so a finding reads ``U1.20 VCCIO is not
    driven`` rather than ``something at (175.26, 93.98)``.

    On a design of more than one page, run this on the ROOT: it walks the
    whole hierarchy, and each finding carries the `sheet` it is on. A finding
    on a child page comes back without a `ref`, because the lookup from
    position to pin only covers the sheet you called it on -- the page name is
    what locates it.

    Returns:
        ``{ok, errors, warnings, findings: [{severity, kind, message, sheet,
        ref, pin, x, y}]}``.
    """
    try:
        found = _sheet(path).check()
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True,
            "errors": sum(1 for f in found if f.severity == "error"),
            "warnings": sum(1 for f in found if f.severity == "warning"),
            "findings": [f.as_dict() for f in found]}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def next_ref(path: str, prefix: str) -> dict[str, Any]:
    """The next unused reference with this prefix, e.g. ``"R"`` -> ``"R7"``.

    All the annotation this API needs: `add_component` demands a reference
    and refuses a duplicate, so a sheet cannot end up unannotated. This just
    saves you keeping a counter.
    """
    try:
        return {"ok": True, "prefix": prefix, "ref": _sheet(path).next_ref(prefix)}
    except LookupError as exc:
        return _fail(exc)


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_wires(path: str) -> dict[str, Any]:
    """Every wire segment on the sheet."""
    try:
        segments = _sheet(path).wires()
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "count": len(segments),
            "wires": [{"start": a.as_dict(), "end": b.as_dict()}
                      for a, b in segments]}


__all__ = [
    "add_component",
    "add_junction",
    "add_label",
    "add_no_connect",
    "add_power",
    "add_power_flag",
    "add_sheet",
    "add_wire",
    "check_sheet",
    "find_symbol",
    "get_component",
    "get_fields",
    "get_pin",
    "list_components",
    "list_nets",
    "list_wires",
    "mirror_component",
    "move_component",
    "move_field",
    "new_sheet",
    "next_ref",
    "remove_component",
    "render_schematic",
    "rotate_component",
    "save_sheet",
    "set_field",
    "symbol_pins",
    "what_is_at",
]
