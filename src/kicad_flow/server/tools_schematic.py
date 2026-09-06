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

from pydantic import BaseModel, Field, model_validator

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
            raise LookupError(
                f"no file at {path}. An existing .kicad_sch "
                f"reopens by itself -- just name it. Use `new_sheet` only to "
                f"create one, which OVERWRITES whatever is there.")
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
        sheet = create(path, paper=paper, title=title,
                       instance_path=instance_path)
    except (ValueError, OSError) as exc:
        return _fail(exc)
    _OPEN[_key(path)] = sheet
    w, h = sheet.size
    return {"ok": True, "path": str(sheet.path), "uuid": sheet.uuid,
            "size": [w, h], "grid": 1.27}


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
    return {"ok": True, "path": str(written), "parts": len(sheet.parts()),
            "wires": len(sheet.wires()), "labels": len(sheet.labels())}


# -- the library ----------------------------------------------------------


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def find_symbol(query: str, limit: int = 20,
                project_dir: str = "") -> dict[str, Any]:
    """Search the symbol libraries for a part.

    Args:
        query: Matched against ``Library:Symbol`` ids, e.g. ``"Device:R"``,
            ``"MCU_Espressif"``, ``"USB_C"``.
        limit: Most results to return.
        project_dir: Optional KiCad project directory whose local
            ``sym-lib-table`` should also be searched.

    Returns:
        ``{ok, symbols: [{lib_id, description, pins: n, width, height}]}``.
    """
    try:
        found = _blank(project_dir).find_symbols(query, limit=limit)
    except (LookupError, OSError) as exc:
        return _fail(exc)
    return {"ok": True, "symbols": [
        {"lib_id": s.lib_id, "description": s.description,
         "pins": len(s.pins), "width": s.width, "height": s.height,
         "power": s.power}
        for s in found
    ]}


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def symbol_pins(lib_id: str, unit: int = 1,
                project_dir: str = "") -> dict[str, Any]:
    """The pins a symbol has, before it is placed anywhere.

    Use this to decide how to orient a part and how much room to leave. For
    the positions to actually WIRE to, place the part and read the pins that
    `add_component` returns -- those have the rotation applied.

    Args:
        lib_id: ``Library:Symbol``.
        unit: Which unit to report. A multi-unit symbol answers one at a
            time: reporting them together puts two units' pins at identical
            coordinates, which is a wrong netlist rather than a messy drawing.
        project_dir: Optional project containing the symbol's local library.

    Returns:
        ``{ok, lib_id, units, unit, width, height, pins: [...]}``.
    """
    try:
        sym = _blank(project_dir).symbol(lib_id, unit=unit)
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "lib_id": sym.lib_id, "units": sym.units,
            "unit": sym.unit, "width": sym.width, "height": sym.height,
            "description": sym.description,
            "pins": [p.as_dict() for p in sym.pins]}


def _blank(project_dir: str = "") -> Sheet:
    """A throwaway sheet, for library queries that need no file."""
    directory = Path(project_dir).resolve() if project_dir else Path.cwd()
    return create(directory / "_query.kicad_sch")


# -- parts ----------------------------------------------------------------


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


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def get_fields(path: str, ref: str) -> dict[str, Any]:
    """Every field on a part, by name."""
    try:
        return {"ok": True, "ref": ref, "fields": _sheet(path).fields(ref)}
    except LookupError as exc:
        return _fail(exc)


# -- connections ----------------------------------------------------------


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


@mcp.tool(tags=_meta.SCH_INSPECT, annotations=_meta.READ)
def list_labels(path: str) -> dict[str, Any]:
    """Every label, including its stable UUID for move/remove operations."""
    try:
        labels = _sheet(path).labels()
    except LookupError as exc:
        return _fail(exc)
    return {"ok": True, "count": len(labels),
            "labels": [label.as_dict() for label in labels]}


# -- what goes on a sheet, many at a time ---------------------------------
#
# Every one of these takes a LIST. One item is a list of one, so there is no
# singular form to choose between and no `batch` to fall back to: the element
# is a typed model, so a misspelled key is rejected by the schema before
# anything runs. That is the trade -- a single call nests one level deeper,
# and 482 wires cost one round trip instead of 482.
#
# On a refusal they stop, say which INDEX failed, and hand back what already
# landed. A half-finished batch is recoverable; a silent one is not.


class NewPart(BaseModel):
    """One part for `add_components`."""

    lib_id: str = Field(description="Library symbol, e.g. 'Device:R'.")
    ref: str = Field(description="Reference, e.g. 'R1'. Must be unused.")
    x: float = Field(description="Position in mm; snapped to the 1.27 grid.")
    y: float = Field(description="Position in mm.")
    value: str = Field(default="", description="Shown value, e.g. '10k'.")
    rotation: float = Field(default=0.0, description="0, 90, 180 or 270.")
    mirror: str = Field(default="", description="'x', 'y', or empty.")
    unit: int = Field(default=1, description="Unit of a multi-unit symbol.")


class Segment(BaseModel):
    """One wire for `add_wires`, from one point to another."""

    x1: float = Field(description="Start, in mm; snapped to the grid.")
    y1: float = Field(description="Start, in mm.")
    x2: float = Field(description="End, in mm.")
    y2: float = Field(description="End, in mm.")


class Spot(BaseModel):
    """One point, for `add_junctions` and `add_no_connects`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")


class LabelTarget(BaseModel):
    """One label selected by stable UUID, or legacy snapped position."""

    uuid: str = Field(
        default="", description="Identity returned by add/list_labels.")
    x: float | None = Field(
        default=None, description="Legacy position in mm when UUID is empty.")
    y: float | None = Field(
        default=None, description="Legacy position in mm when UUID is empty.")

    @model_validator(mode="after")
    def valid_target(self) -> LabelTarget:
        """Require UUID alone or a complete coordinate pair."""
        if self.uuid:
            if self.x is not None or self.y is not None:
                raise ValueError("select a label by uuid or position, not both")
        elif self.x is None or self.y is None:
            raise ValueError("a label target needs uuid or both x and y")
        return self


class NewLabel(BaseModel):
    """One label for `add_labels`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    text: str = Field(description="The net name.")
    kind: str = Field(default="local",
                      description="'local', 'global' or 'hierarchical'.")
    rotation: float = Field(default=0.0, description="Local, global and "
                            "hierarchical labels can be vertical at 90 or 270; "
                            "horizontal reads the same at 0 and 180.")
    justify: str = Field(default="left", description="Text growth direction: "
                         "'left' grows rightward and 'right' grows leftward. "
                         "Set this explicitly for local labels: use 'right' "
                         "on left-side pins and 'left' on right-side pins.")


class NewPower(BaseModel):
    """One power symbol for `add_power`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    net: str = Field(description="Rail name, e.g. 'GND', '+3V3'.")
    rotation: float = Field(default=0.0, description="0, 90, 180 or 270.")


class NewFlag(BaseModel):
    """One PWR_FLAG for `add_power_flags`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm.")
    rotation: float = Field(default=0.0, description="0, 90, 180 or 270.")


class NewSheetBox(BaseModel):
    """One child-sheet box for `add_sheets`."""

    name: str = Field(description="Sheet name, shown above the box.")
    filename: str = Field(description="Child file, e.g. 'power.kicad_sch'.")
    x: float = Field(description="Top-left corner, in mm.")
    y: float = Field(description="Top-left corner, in mm.")
    width: float = Field(default=38.1, description="Box width in mm.")
    height: float = Field(default=25.4, description="Box height in mm.")
    ports: list[dict[str, str]] = Field(
        default_factory=list,
        description='[{"name": "SENSE", "kind": "input"}, ...].')


class PartMove(BaseModel):
    """One absolute move for `move_components`."""

    ref: str = Field(description="Reference to move.")
    x: float = Field(description="New position in mm; snapped to the grid.")
    y: float = Field(description="New position in mm.")
    unit: int = Field(default=1, description="Unit of a multi-unit symbol.")


class PartTurn(BaseModel):
    """One rotation for `rotate_components`."""

    ref: str = Field(description="Reference to turn.")
    rotation: float = Field(description="0, 90, 180 or 270.")
    unit: int = Field(default=1, description="Unit of a multi-unit symbol.")


class PartFlip(BaseModel):
    """One mirroring for `mirror_components`."""

    ref: str = Field(description="Reference to mirror.")
    axis: str = Field(description="'x', 'y', or empty to clear it.")
    unit: int = Field(default=1, description="Unit of a multi-unit symbol.")


class FieldValue(BaseModel):
    """One field to set, for `set_fields`."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, e.g. 'Footprint'.")
    value: str = Field(description="The value to write.")


class FieldShift(BaseModel):
    """One field to move, for `move_fields`."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, e.g. 'Reference'.")
    dx: float = Field(description="Offset from the part's position, in mm.")
    dy: float = Field(description="Offset from the part's position, in mm.")
    rotation: float | None = Field(default=None,
                                   description="Absolute text angle, or null.")
    justify: str = Field(default="", description="'left', 'right' or empty.")


def _partial(exc: Exception, index: int, key: str,
             done: list[Any]) -> dict[str, Any]:
    """A refusal that says which element failed and what already landed."""
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
            "index": index, key: done}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_components(path: str, parts: list[NewPart]) -> dict[str, Any]:
    """Place parts on the sheet, in order.

    Each reply carries that part's pins at their positions ON THE SHEET, with
    rotation and mirroring already applied. Place everything first, then read
    the pins out of this reply and draw the wires with `add_wires`: a wire
    aimed at a coordinate you worked out yourself, rather than one reported
    here, looks connected and is not.

    Args:
        path: The open sheet.
        parts: The parts, in order.

    Returns:
        `parts`, one entry per placement, each with its pins.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(parts):
        try:
            made = sheet.place(p.lib_id, p.ref, p.x, p.y, value=p.value,
                               rotation=p.rotation, mirror=p.mirror,
                               unit=p.unit)
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "parts", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "parts": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_wires(path: str, wires: list[Segment]) -> dict[str, Any]:
    """Draw wires, each between two points.

    A wire connects by TOUCHING a pin, so both ends are snapped to the grid
    and must land exactly on the pin coordinates `add_components` reported.
    Two wires that cross do not connect unless a junction says they do.

    Args:
        path: The open sheet.
        wires: The segments, in order.

    Returns:
        `wires`, one `{start, end}` per segment as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, w in enumerate(wires):
        try:
            a, b = sheet.wire(w.x1, w.y1, w.x2, w.y2)
        except LookupError as exc:
            return _partial(exc, i, "wires", out)
        out.append({"start": a.as_dict(), "end": b.as_dict()})
    return {"ok": True, "count": len(out), "wires": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_labels(path: str, labels: list[NewLabel]) -> dict[str, Any]:
    """Name nets at points. Two labels with the same text are one net.

    Prefer direct wires between nearby components. For a necessary local label,
    draw a short wire stub away from the component and place the label at its
    free end; anchoring local text directly on a pin often draws it over the
    body. Set its justification explicitly for the side of the component.

    Args:
        path: The open sheet.
        labels: The labels, in order.

    Returns:
        `labels`, one entry per label with where it landed.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, lb in enumerate(labels):
        try:
            made = sheet.label(lb.x, lb.y, lb.text, kind=lb.kind,
                               rotation=lb.rotation, justify=lb.justify)
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "labels", out)
        out.append(made.as_dict())
    return {"ok": True, "count": len(out), "labels": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_junctions(path: str, points: list[Spot]) -> dict[str, Any]:
    """Join crossing wires. Without one, wires that cross are separate nets.

    Args:
        path: The open sheet.
        points: Where to put them.

    Returns:
        `points`, each as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(points):
        try:
            out.append(sheet.junction(p.x, p.y).as_dict())
        except LookupError as exc:
            return _partial(exc, i, "points", out)
    return {"ok": True, "count": len(out), "points": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_no_connects(path: str, points: list[Spot]) -> dict[str, Any]:
    """Mark pins deliberately unconnected, so ERC stops reporting them.

    Args:
        path: The open sheet.
        points: Pin positions, from `add_components` or `get_pin`.

    Returns:
        `points`, each as it was snapped.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(points):
        try:
            out.append(sheet.no_connect(p.x, p.y).as_dict())
        except LookupError as exc:
            return _partial(exc, i, "points", out)
    return {"ok": True, "count": len(out), "points": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power(path: str, symbols: list[NewPower]) -> dict[str, Any]:
    """Place power symbols. A rail joins BY NAME across every sheet.

    Singular where the rest are plural, because `add_powers` is not
    English. It takes a list like every other write here.

    Args:
        path: The open sheet.
        symbols: The symbols, in order.

    Returns:
        `symbols`, each with its single pin -- the point to wire to.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, s in enumerate(symbols):
        try:
            out.append(sheet.power(s.x, s.y, s.net,
                                   rotation=s.rotation).as_dict())
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "symbols", out)
    return {"ok": True, "count": len(out), "symbols": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_power_flags(path: str, flags: list[NewFlag]) -> dict[str, Any]:
    """Place PWR_FLAGs, which tell ERC a rail is driven.

    A rail of only power INPUTS reads as undriven however many symbols sit on
    it; one flag per rail is what settles that.

    Args:
        path: The open sheet.
        flags: The flags, in order.

    Returns:
        `flags`, each with its single pin.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, f in enumerate(flags):
        try:
            out.append(sheet.power_flag(f.x, f.y,
                                        rotation=f.rotation).as_dict())
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "flags", out)
    return {"ok": True, "count": len(out), "flags": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_sheets(path: str, sheets: list[NewSheetBox]) -> dict[str, Any]:
    """Put child-sheet boxes on this one, and say where their ports landed.

    A design of more than one page is two halves that meet BY NAME: a port on
    the box here, and a hierarchical label of the same name inside the child.
    Nothing checks the pairing while you draw; `check_sheet` on the ROOT does.
    Power needs no ports -- it is global.

    Then create each child with `new_sheet`, passing back the `instance_path`
    returned here, or the child's parts will not join the design's nets.

    Args:
        path: The open parent sheet.
        sheets: The boxes, in order.

    Returns:
        `sheets`, each with its `instance_path` and port positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, s in enumerate(sheets):
        try:
            ref = sheet.add_sheet(
                s.name, s.filename, s.x, s.y, width=s.width, height=s.height,
                ports=tuple((p["name"], p.get("kind", "passive"))
                            for p in s.ports),
            )
        except (LookupError, KeyError, ValueError) as exc:
            return _partial(exc, i, "sheets", out)
        out.append(ref.as_dict())
    return {"ok": True, "count": len(out), "sheets": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_components(path: str, moves: list[PartMove] | None = None,
                    refs: list[str] | None = None, dx: float = 0.0,
                    dy: float = 0.0, unit: int = 1) -> dict[str, Any]:
    """Move parts: each to a position of its own, or a set by one offset.

    Two ways, because a caller wants both. `moves` puts each part somewhere
    absolute. `refs` with `dx`/`dy` SHIFTS that set, which is what moving a
    block of a sheet actually is -- every other move is absolute, so without
    it a caller reads each position back, adds the offset itself, and calls
    once per part.

    Choosing the set is a separate question and stays one: `list_components`
    reports every part and where it is, you filter it however you like, and
    pass the references here. Nothing is inferred from context.

    **Wires do not follow.** A part moved out from under its wires is joined
    to nothing, and only `list_nets` says so.

    Args:
        path: The open sheet.
        moves: Absolute placements, one per part.
        refs: Parts to shift. Ignored when `moves` is given.
        dx: Offset in mm, with `refs`.
        dy: Offset in mm, with `refs`.
        unit: Unit of a multi-unit symbol, with `refs`.

    Returns:
        `moved`, each part at its new position.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    if moves is None and refs is None:
        return {"ok": False,
                "error": "give either moves=[...] or refs=[...] with dx/dy"}
    out: list[dict[str, Any]] = []
    if moves is not None:
        for i, m in enumerate(moves):
            try:
                out.append(sheet.move(m.ref, m.x, m.y, unit=m.unit).as_dict())
            except (LookupError, ValueError) as exc:
                return _partial(exc, i, "moved", out)
    else:
        for i, ref in enumerate(refs or []):
            try:
                was = sheet.part(ref, unit=unit).at
                out.append(sheet.move(ref, was.x + dx, was.y + dy,
                                      unit=unit).as_dict())
            except (LookupError, ValueError) as exc:
                return _partial(exc, i, "moved", out)
    return {"ok": True, "count": len(out), "moved": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def rotate_components(path: str, turns: list[PartTurn]) -> dict[str, Any]:
    """Turn parts. A rotation moves the pins, and the reply says where to.

    Args:
        path: The open sheet.
        turns: The rotations, in order.

    Returns:
        `turned`, each part with its pins at their new positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, t in enumerate(turns):
        try:
            out.append(sheet.rotate(t.ref, t.rotation, unit=t.unit).as_dict())
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "turned", out)
    return {"ok": True, "count": len(out), "turned": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def mirror_components(path: str, mirrors: list[PartFlip]) -> dict[str, Any]:
    """Mirror parts about an axis, and say where the pins ended up.

    Args:
        path: The open sheet.
        mirrors: The mirrorings, in order.

    Returns:
        `mirrored`, each part with its pins at their new positions.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, m in enumerate(mirrors):
        try:
            out.append(sheet.mirror(m.ref, m.axis, unit=m.unit).as_dict())
        except (LookupError, ValueError) as exc:
            return _partial(exc, i, "mirrored", out)
    return {"ok": True, "count": len(out), "mirrored": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_components(path: str, refs: list[str],
                      unit: int = 1) -> dict[str, Any]:
    """Take parts off the sheet. Wires and labels stay where they are.

    Args:
        path: The open sheet.
        refs: References to remove.
        unit: Unit of a multi-unit symbol.

    Returns:
        `removed`, the references that went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    gone: list[Any] = []
    for i, ref in enumerate(refs):
        try:
            sheet.remove(ref, unit=unit)
        except LookupError as exc:
            return _partial(exc, i, "removed", gone)
        gone.append(ref)
    return {"ok": True, "count": len(gone), "removed": gone}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def set_fields(path: str, fields: list[FieldValue]) -> dict[str, Any]:
    """Set fields on parts -- `Footprint`, `Datasheet`, a custom one.

    Args:
        path: The open sheet.
        fields: The values, in order.

    Returns:
        `fields`, each part's full field set after the write.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, f in enumerate(fields):
        try:
            got = sheet.set_field(f.ref, f.name, f.value)
        except LookupError as exc:
            return _partial(exc, i, "fields", out)
        out.append({"ref": f.ref, "fields": got})
    return {"ok": True, "count": len(out), "fields": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_fields(path: str, moves: list[FieldShift]) -> dict[str, Any]:
    """Move a part's text relative to the part, so it stops printing on it.

    A library places these and cannot know what ends up beside them.

    Args:
        path: The open sheet.
        moves: The moves, in order.

    Returns:
        `moved`, each field at its new position.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, m in enumerate(moves):
        try:
            at = sheet.move_field(m.ref, m.name, m.dx, m.dy,
                                  rotation=m.rotation, justify=m.justify)
        except LookupError as exc:
            return _partial(exc, i, "moved", out)
        out.append({"ref": m.ref, "field": m.name, **at.as_dict()})
    return {"ok": True, "count": len(out), "moved": out}



class SheetNote(BaseModel):
    """One note for `add_texts`."""

    x: float = Field(description="Position in mm; snapped to the grid.")
    y: float = Field(description="Position in mm. This is the text's BASELINE,"
                     " so a note grows downward from here.")
    text: str = Field(description="The note. Newlines are kept.")
    size: float = Field(default=1.27, description="Text height in mm. 1.27 "
                        "matches a label; 2.54 reads as a heading.")
    rotation: float = Field(default=0.0, description="Degrees. Any angle.")
    bold: bool = Field(default=False, description="Bold, for a heading.")
    justify: str = Field(default="left",
                         description="'left', 'right' or 'center'.")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def add_texts(path: str, notes: list[SheetNote]) -> dict[str, Any]:
    """Write notes on the sheet -- plain text that connects nothing.

    THIS IS NOT `add_labels`. A label names a net and joins everything it
    touches; a note is ignored by ERC and never appears in `list_nets`. Put
    the things a reader needs and the netlist must not have here: a revision
    block, a derivation, "all VBAT caps 50 V", why a resistor is 13k7.

    The board has `add_board_texts`; this is the schematic's equivalent.

    Args:
        path: The open sheet.
        notes: The notes, in order.

    Returns:
        `notes`, each with the point it was snapped to.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, n in enumerate(notes):
        try:
            at = sheet.text(n.x, n.y, n.text, size=n.size,
                            rotation=n.rotation, bold=n.bold,
                            justify=n.justify)
        except (LookupError, ValueError) as exc:
            return {**_fail(exc), "index": i, "notes": out}
        out.append({"text": n.text, "size": n.size, **at.as_dict()})
    return {"ok": True, "count": len(out), "notes": out}


# -- editing what is already drawn ----------------------------------------
#
# Labels have stable UUIDs; use those after moving or mirroring nearby parts.
# Coordinate selection remains for compatibility and for junctions, wires and
# no-connects. Every call says how many it FOUND, so doing nothing cannot pass
# for success.


class WireEnds(BaseModel):
    """One wire, named by the two points it runs between."""

    x1: float = Field(description="One end, in mm; snapped to the grid.")
    y1: float = Field(description="One end, in mm.")
    x2: float = Field(description="The other end, in mm.")
    y2: float = Field(description="The other end, in mm.")


class WireShift(WireEnds):
    """One wire to shift, and by how much."""

    dx: float = Field(description="Offset in mm. Both ends move together.")
    dy: float = Field(description="Offset in mm.")


class LabelShift(LabelTarget):
    """One label, and how far to move it."""

    dx: float = Field(description="Offset in mm.")
    dy: float = Field(description="Offset in mm.")


class LabelTurn(LabelTarget):
    """One label, and the angle to turn it to."""

    rotation: float = Field(description="Degrees. 90 or 270 for a vertical "
                            "label; horizontal reads the same at 0 and 180.")


class SheetMove(BaseModel):
    """One child-sheet box to move."""

    name: str = Field(description="The sheet name shown above the box.")
    x: float = Field(description="New top-left corner, in mm.")
    y: float = Field(description="New top-left corner, in mm.")


class FieldRef(BaseModel):
    """One field to delete."""

    ref: str = Field(description="The part.")
    name: str = Field(description="Field name, for example MPN.")
    unit: int = Field(default=1, description="Unit of a multi-unit symbol.")


def _counted(sheet: Sheet, items: list[Any], each: Any,
             key: str) -> dict[str, Any]:
    """Run *each* over *items*, totalling how many it found."""
    found = 0
    for i, item in enumerate(items):
        try:
            found += each(sheet, item)
        except (LookupError, ValueError) as exc:
            return {**_fail(exc), "index": i, key: found}
    return {"ok": True, "count": len(items), key: found}


def _remove_label(sheet: Sheet, target: LabelTarget) -> int:
    """Remove one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.remove_label_by_id(target.uuid)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.remove_label(target.x, target.y)


def _move_label(sheet: Sheet, target: LabelShift) -> int:
    """Move one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.move_label_by_id(target.uuid, target.dx, target.dy)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.move_label(target.x, target.y, target.dx, target.dy)


def _rotate_label(sheet: Sheet, target: LabelTurn) -> int:
    """Rotate one UUID target, or every legacy target at a position."""
    if target.uuid:
        sheet.rotate_label_by_id(target.uuid, target.rotation)
        return 1
    assert target.x is not None and target.y is not None
    return sheet.rotate_label(target.x, target.y, target.rotation)


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_wires(path: str, wires: list[WireEnds]) -> dict[str, Any]:
    """Delete wires running between the given pairs of points.

    Either direction matches -- a segment does not know which end was drawn
    first. `list_wires` reports the coordinates to pass here.

    Args:
        path: The open sheet.
        wires: The segments to delete.

    Returns:
        `removed`, how many segments actually went. Zero means nothing was at
        those coordinates.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(wires),
                    lambda s, w: s.remove_wire(w.x1, w.y1, w.x2, w.y2),
                    "removed")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_wires(path: str, wires: list[WireShift]) -> dict[str, Any]:
    """Shift wires by an offset. Both ends move, so length and angle survive.

    A wire moved off a pin is no longer joined to it and nothing on the sheet
    says so -- `list_nets` is what says so.

    Args:
        path: The open sheet.
        wires: The segments to shift, and by how much.

    Returns:
        `moved`, how many segments were found and shifted.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(
        sheet, list(wires),
        lambda s, w: s.move_wire(w.x1, w.y1, w.x2, w.y2, w.dx, w.dy), "moved")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_labels(path: str, points: list[LabelTarget]) -> dict[str, Any]:
    """Delete labels by UUID, or legacy snapped position.

    Args:
        path: The open sheet.
        points: UUID targets from `add_labels`/`list_labels`, or positions.

    Returns:
        `removed`, how many labels actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(points), _remove_label, "removed")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_labels(path: str, moves: list[LabelShift]) -> dict[str, Any]:
    """Shift labels by an offset.

    A label names the net it TOUCHES. Move one off its wire and it names
    nothing, quietly -- read `list_nets` back afterwards.

    Args:
        path: The open sheet.
        moves: Each label's UUID or position, and how far to move it.

    Returns:
        `moved`, how many labels were found and shifted.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(moves), _move_label, "moved")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def rotate_labels(path: str, turns: list[LabelTurn]) -> dict[str, Any]:
    """Turn labels at these points.

    Which way a GLOBAL label points is its justification, not its rotation --
    see `add_labels`.

    Args:
        path: The open sheet.
        turns: Each label's UUID or position, and the requested angle.

    Returns:
        `turned`, how many labels were found and turned.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(turns), _rotate_label, "turned")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_junctions(path: str, points: list[Spot]) -> dict[str, Any]:
    """Delete junctions at these points.

    Removing one separates wires that cross there into different nets, so read
    `list_nets` back afterwards.

    Args:
        path: The open sheet.
        points: Where the junctions are.

    Returns:
        `removed`, how many junctions actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(points),
                    lambda s, p: s.remove_junction(p.x, p.y), "removed")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_no_connects(path: str, points: list[Spot]) -> dict[str, Any]:
    """Delete no-connect marks at these points.

    A no-connect SUPPRESSES an ERC error. Taking one off lets a real fault be
    reported again, which is usually the reason to.

    Args:
        path: The open sheet.
        points: Where the marks are.

    Returns:
        `removed`, how many marks actually went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    return _counted(sheet, list(points),
                    lambda s, p: s.remove_no_connect(p.x, p.y), "removed")


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.WRITE)
def move_sheets(path: str, moves: list[SheetMove]) -> dict[str, Any]:
    """Move child-sheet boxes, and say where their ports ended up.

    The box moves and its ports move with it. The child FILE and its
    `instance_path` do not change, so nothing downstream needs rebuilding --
    which is the point: re-creating a root to move a box regenerates its UUID
    and orphans every child.

    Args:
        path: The open parent sheet.
        moves: The boxes to move, by name.

    Returns:
        `sheets`, each box with its new position and port coordinates.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, m in enumerate(moves):
        try:
            out.append(sheet.move_sheet(m.name, m.x, m.y).as_dict())
        except LookupError as exc:
            return {**_fail(exc), "index": i, "sheets": out}
    return {"ok": True, "count": len(out), "sheets": out}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_sheets(path: str, names: list[str]) -> dict[str, Any]:
    """Take child-sheet boxes off this sheet, by name.

    The child FILES are left alone. This removes the boxes that refer to them,
    so the design stops walking into those pages.

    Args:
        path: The open parent sheet.
        names: Sheet names, as shown above each box.

    Returns:
        `removed`, the names that went.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    gone: list[str] = []
    for i, name in enumerate(names):
        try:
            sheet.remove_sheet(name)
        except LookupError as exc:
            return {**_fail(exc), "index": i, "removed": gone}
        gone.append(name)
    return {"ok": True, "count": len(gone), "removed": gone}


@mcp.tool(tags=_meta.SCH_PRIMARY, annotations=_meta.DESTRUCTIVE)
def remove_fields(path: str, fields: list[FieldRef]) -> dict[str, Any]:
    """Delete fields from parts.

    Setting a field to an empty string is a DIFFERENT thing: it stays present
    and blank, KiCad keeps writing it, and a BOM still sees the column.

    Args:
        path: The open sheet.
        fields: The fields to delete.

    Returns:
        `fields`, each part's remaining fields after the delete.
    """
    try:
        sheet = _sheet(path)
    except LookupError as exc:
        return _fail(exc)
    out: list[dict[str, Any]] = []
    for i, f in enumerate(fields):
        try:
            left = sheet.remove_field(f.ref, f.name, unit=f.unit)
        except LookupError as exc:
            return {**_fail(exc), "index": i, "fields": out}
        out.append({"ref": f.ref, "fields": left})
    return {"ok": True, "count": len(out), "fields": out}

__all__ = [
    "add_components", "add_junctions", "add_labels",
    "add_no_connects", "add_power", "add_power_flags",
    "add_sheets", "add_texts", "add_wires",
    "check_sheet", "find_symbol", "get_component",
    "get_fields", "get_pin", "list_components",
    "list_labels", "list_nets", "list_wires", "mirror_components",
    "move_components", "move_fields", "move_labels",
    "move_sheets", "move_wires", "new_sheet",
    "next_ref", "remove_components", "remove_fields",
    "remove_junctions", "remove_labels", "remove_no_connects",
    "remove_sheets", "remove_wires", "render_schematic",
    "rotate_components", "rotate_labels", "save_sheet",
    "set_fields", "symbol_pins", "what_is_at",
]
