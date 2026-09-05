"""KiCad behind the interface: reads and writes ``.kicad_sch`` directly.

Nothing above :class:`~kicad_flow.schematic.api.Sheet` knows this file exists.
The s-expression reader in :mod:`kicad_flow.backend.kicad._sexpr` and the library loader
in :mod:`kicad_flow.backend.kicad._library` are used as plumbing -- finding a
``.kicad_sym`` on disk and flattening a derived symbol is environment work, not
schematic work, and rewriting it would buy nothing.

**The pin arithmetic is the whole point of this module**, so it is spelled out
in :func:`_pin_on_sheet`. Everything else is bookkeeping.
"""

from __future__ import annotations

import contextlib
import copy
import math
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kicad_flow.schematic.api import GRID, Sheet, snap
from kicad_flow.schematic.types import (
    Finding,
    Net,
    NetPin,
    Part,
    Pin,
    Point,
    SheetRef,
    SymbolDef,
)

from .. import _library as library
from .._sexpr import Node, Sym, dumps, loads

#: Paper sizes, and the margin KiCad's title block eats on each edge.
#: A4 and A3, and nothing larger on purpose. A2 upwards is a page nobody reads
#: -- it is printed at a size that does not exist on a desk, and on screen it
#: is a long pan at a zoom where the pin numbers are gone. A design that will
#: not fit A3 wants another SHEET, which costs one `add_sheets` call and gives
#: the design a structure a reader can follow.
PAPER = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}
_MARGIN = 10.0

_LABEL_NODE = {"local": "label", "global": "global_label",
               "hierarchical": "hierarchical_label"}


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


#: Namespace for every identifier this backend writes. Fixed, so the same
#: design produces the same file on every build.
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


#: The only angles a symbol can be placed at.
_QUARTER_TURNS = (0.0, 90.0, 180.0, 270.0)


def _quarter_turn(rotation: float) -> float:
    """*rotation* normalised to 0/90/180/270, or a :class:`ValueError`.

    KiCad turns a symbol in quarter turns and nothing else. Any other angle
    writes a file it will not open -- measured, 45 and 30 both give "Failed to
    load schematic" while 90 and 180 are fine. This used to be accepted: the
    call returned pin positions computed off the angle, reported success, and
    left the fault for whatever opened the file next. It also put those pins
    off the 1.27 mm grid, which the rest of the module guarantees they are on.

    Only symbols are restricted. A label or a field takes any angle and KiCad
    loads it, so `label` and `move_field` do not go through here.
    """
    turned = rotation % 360.0
    if turned not in _QUARTER_TURNS:
        raise ValueError(
            f"rotation must be 0, 90, 180 or 270, not {rotation!r}"
        )
    return turned


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


def _pin_on_sheet(px: float, py: float, pangle: float, at: Point,
                  rotation: float, mirror: str) -> tuple[Point, float]:
    """Where a pin lands once its part is placed, rotated and mirrored.

    This is the arithmetic every caller would otherwise repeat and quietly get
    wrong. Three things have to line up:

    1. **The Y axis flips.** A library draws with Y increasing *upward*; a
       sheet has Y increasing *downward*. So a pin at library ``y = +3.81``
       sits 3.81 mm **above** the part's origin on the sheet.
    2. **Rotation is counter-clockwise on screen**, which -- with Y already
       flipped -- is the ordinary rotation matrix applied to the flipped offset.
    3. **Mirroring happens after rotation**, negating one axis of the offset,
       and it also reverses the direction the pin points.

    Returns the sheet point and the direction the pin points, in degrees with
    0 = right and 90 = up, so a caller knows which way to leave.
    """
    dx, dy = px, -py           # (1) into sheet space
    theta = math.radians(rotation % 360.0)
    cos, sin = math.cos(theta), math.sin(theta)
    rx = dx * cos + dy * sin   # (2) counter-clockwise, Y already down
    ry = -dx * sin + dy * cos
    angle = (pangle + rotation) % 360.0
    if mirror == "y":          # (3) mirrored about the vertical axis
        rx, angle = -rx, (180.0 - angle) % 360.0
    elif mirror == "x":
        ry, angle = -ry, (-angle) % 360.0
    return Point(at.x + rx, at.y + ry), angle


def _field_angle(rotation: float) -> float:
    """The text angle a field needs so it reads horizontally.

    KiCad ADDS the symbol's rotation to its field text -- but only for the
    quarter turns. At 0 and 180 it leaves the text upright on its own, so
    compensating there turns it upside down instead. Measured by rendering a
    row of resistors at all four rotations, both ways, and looking at it.
    """
    return (-rotation) % 360.0 if rotation % 180 else 0.0


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
    definition: Node, unit: int = 1,
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
            out.append((_text(pin.get("number")), _text(pin.get("name")),
                        _f(at, 0), _f(at, 1), _f(at, 2), kind,
                        _f(pin.get("length"), 0, 2.54)))
    return out


def _extent(pins: list[tuple[Any, ...]], definition: Node,
            unit: int = 1) -> tuple[float, float, float, float]:
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


class KiCadSheet(Sheet):
    """A ``.kicad_sch`` file, edited through the primitive API."""

    def __init__(self, path: Path, tree: Node, paper: str,
                 instance_path: str = "") -> None:
        """Wrap an already-parsed sheet. Use :func:`create` or :func:`load`."""
        self._path = Path(path)
        self._tree = tree
        self._paper = paper
        self._instance_path = instance_path
        self._defs: dict[str, library.LibrarySymbol] = {}
        # Every identifier already in the file. A derived uuid is checked
        # against it, so two identical things -- two wires between the same
        # pair of points -- cannot end up sharing one.
        self._uids: set[str] = {
            _text(node.get("uuid"))
            for node in _walk(tree) if node.get("uuid") is not None
        }

    def _uid_for(self, key: str) -> str:
        """A stable uuid for *key*, made unique if that key repeats."""
        n = 1
        while True:
            candidate = _uid_from(key if n == 1 else f"{key}#{n}")
            if candidate not in self._uids:
                self._uids.add(candidate)
                return candidate
            n += 1

    @property
    def uuid(self) -> str:
        """This sheet's own identifier."""
        return _text(self._tree.get("uuid"))

    @property
    def _where(self) -> str:
        """The instance path parts on this sheet record.

        A sheet opened on its own is the root and its parts live at ``/<own
        uuid>``. A sheet that is a child of another was told its place when it
        was created, and its parts live at ``/<root>/<sheet symbol>`` -- get
        this wrong and the child's parts annotate against the wrong sheet, so
        their nets never merge into the design.
        """
        return self._instance_path or ("/" + self.uuid)

    # -- the sheet --------------------------------------------------------

    @property
    def path(self) -> Path:
        """Where this sheet will be written."""
        return self._path

    @property
    def size(self) -> tuple[float, float]:
        """The drawable ``(width, height)`` inside the title block."""
        w, h = PAPER.get(self._paper, PAPER["A4"])
        return (w - 2 * _MARGIN, h - 2 * _MARGIN)

    def add_sheet(self, name: str, filename: str, x: float, y: float, *,
                  width: float = 38.1, height: float = 25.4,
                  ports: tuple[tuple[str, str], ...] = ()) -> SheetRef:
        """Put a child sheet on this one, and return where its ports landed."""
        at = Point(snap(x), snap(y))
        uid = self._uid_for(f"sheet:{name}:{filename}")
        node = _node("sheet", [
            _node("at", [at.x, at.y]),
            _node("size", [snap(width), snap(height)]),
            _node("stroke", [_node("width", [0]),
                             _node("type", [Sym("solid")])]),
            _node("fill", [_node("color", [0, 0, 0, 0])]),
            _node("uuid", [uid]),
            self._property("Sheetname", name, at.x, at.y - 1.27),
            self._property("Sheetfile", filename, at.x,
                           at.y + snap(height) + 1.27),
        ])
        pins = []
        for index, (port, kind) in enumerate(ports):
            py = at.y + snap(2.54 + index * 2.54)
            node.items.append(_node("pin", [
                port, Sym(kind),
                _node("at", [at.x, py, 180]),
                _node("effects", [
                    _node("font", [_node("size", [1.27, 1.27])]),
                    _node("justify", [Sym("right")]),
                ]),
                _node("uuid", [self._uid_for(f"port:{name}:{port}")]),
            ]))
            pins.append(Pin(number=port, name=port, at=Point(at.x, py),
                            orientation=180.0, kind=kind, length=0.0))
        # A page number per sheet, so the design has an order.
        table = self._tree.get("sheet_instances")
        page = len(table.get_all("path")) + 1 if table is not None else 2
        node.items.append(_node("instances", [
            _node("project", ["", _node("path", [
                self._where, _node("page", [str(page)]),
            ])]),
        ]))
        if table is not None:
            table.items.append(_node("path", [
                "/" + uid, _node("page", [str(page)]),
            ]))
        self._tree.items.append(node)
        return SheetRef(name=name, filename=filename, at=at,
                        size=(snap(width), snap(height)), uuid=uid,
                        instance_path=f"{self._where}/{uid}",
                        pins=tuple(pins))

    def save(self) -> Path:
        """Write the sheet to disk and return its path."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        return self._path

    # -- the library ------------------------------------------------------

    def find_symbols(self, query: str, limit: int = 20) -> list[SymbolDef]:
        """Library symbols whose ``Library:Symbol`` id contains *query*.

        Matching is on the id alone: checking descriptions would mean loading
        every symbol in every library, and there are a couple of hundred of
        them. Search by part number or family -- ``"USB_C_Receptacle"``,
        ``"AP2112"`` -- and read the description off the result.
        """
        found: list[SymbolDef] = []
        needle = query.lower()
        for lib in library.symbol_dirs():
            # Not a plain glob: KiCad 10 keeps a library as a `.kicad_symdir`
            # FOLDER, and looking only for single files finds nothing at all on
            # a stock install.
            for file in library._libraries_in(lib):
                for name in library._library_symbol_names(str(file)):
                    lib_id = f"{file.stem}:{name}"
                    if needle not in lib_id.lower():
                        continue
                    try:
                        found.append(self.symbol(lib_id))
                    except (LookupError, ValueError):
                        continue
                    if len(found) >= limit:
                        return found
        return found

    def symbol(self, lib_id: str, *, unit: int = 1) -> SymbolDef:
        """One unit of a symbol, with its pins at the symbol origin."""
        try:
            sym = self._load(lib_id)
        except Exception as exc:  # the loader raises several types
            raise LookupError(f"no symbol {lib_id!r}: {exc}") from exc
        raw = _symbol_pins(sym.definition, unit)
        pins = tuple(
            Pin(number=n, name=nm, at=Point(px, py), orientation=ang,
                kind=kind, length=length)
            for n, nm, px, py, ang, kind, length in raw
        )
        box = _extent(list(raw), sym.definition, unit)
        left, bottom, right, top = box
        return SymbolDef(
            lib_id=lib_id,
            description=_text(self._prop_of(sym.definition, "Description"), 1),
            keywords=_text(self._prop_of(sym.definition, "ki_keywords"), 1),
            units=max(1, len(_units_of(sym.definition))), unit=unit,
            pins=pins, width=round(right - left, 3),
            height=round(top - bottom, 3),
            bounds=tuple(round(v, 3) for v in box),  # type: ignore[arg-type]
            power=sym.is_power,
        )

    @staticmethod
    def _prop_of(definition: Node, name: str) -> Node | None:
        """The ``(property "<name>" ...)`` node of a symbol, if it has one."""
        for prop in definition.get_all("property"):
            if _text(prop) == name:
                return prop
        return None

    def _load(self, lib_id: str) -> library.LibrarySymbol:
        """Load and cache a library symbol."""
        if lib_id not in self._defs:
            self._defs[lib_id] = library.load_symbol(lib_id)
        return self._defs[lib_id]

    # -- parts ------------------------------------------------------------

    def place(self, lib_id: str, ref: str, x: float, y: float, *,
              value: str = "", rotation: float = 0.0, mirror: str = "",
              unit: int = 1) -> Part:
        """Put one unit of *lib_id* on the sheet at ``(x, y)`` as *ref*."""
        rotation = _quarter_turn(rotation)
        if self._find(ref, unit) is not None:
            raise ValueError(f"{ref} unit {unit} is already on the sheet")
        sym = self._load(lib_id)
        self._ensure_lib_symbol(lib_id, sym)
        at = Point(snap(x), snap(y))
        node = _node("symbol", [
            _node("lib_id", [lib_id]),
            _node("at", [at.x, at.y, rotation]),
            _node("unit", [unit]),
            _node("exclude_from_sim", [Sym("no")]),
            _node("in_bom", [Sym("yes")]),
            _node("on_board", [Sym("yes")]),
            _node("dnp", [Sym("no")]),
            _node("uuid", [self._uid_for(f"symbol:{ref}:{unit}")]),
        ])
        if mirror in ("x", "y"):
            node.items.insert(3, _node("mirror", [Sym(mirror)]))
        node.items.append(self._property("Reference", ref, at.x, at.y,
                                         hide=ref.startswith("#")))
        node.items.append(self._property("Value", value or sym.default_value,
                                         at.x, at.y))
        node.items.append(self._property("Footprint", "", at.x, at.y, hide=True))
        self._layout_fields(node, lib_id, rotation, mirror, unit)
        for pin in _symbol_pins(sym.definition, unit):
            node.items.append(
                _node("pin", [pin[0], _node(
                    "uuid", [self._uid_for(f"pin:{ref}:{unit}:{pin[0]}")])])
            )
        # Without this, KiCad does not consider the symbol annotated, and a
        # wire between two of its pins connects nothing -- see _instances.
        node.items.append(self._instances(ref, unit))
        self._tree.items.append(node)
        # The unit that was just placed, not unit 1: `part` defaults, and
        # returning the default here handed back another unit's pins.
        return self.part(ref, unit=unit)

    def _instances(self, ref: str, unit: int = 1) -> Node:
        """The ``(instances ...)`` block tying a symbol to this sheet.

        KiCad 6 and later record a placed symbol's reference here as well as
        in its Reference property, and it is THIS that annotation reads. A
        symbol without it is unannotated: its pins do not join the nets they
        sit on, so a wire drawn between two pins connects nothing, ERC calls
        that wire dangling, and the net never reaches the netlist.

        It was invisible for a while because every net that had a label or a
        power symbol on it still appeared -- those carry their own identity.
        Only plain pin-to-pin wires vanished.
        """
        return _node("instances", [
            _node("project", ["", _node("path", [
                self._where,
                _node("reference", [ref]),
                _node("unit", [unit]),
            ])]),
        ])

    def _property(self, name: str, value: str, x: float, y: float, *,
                  hide: bool = False, justify: str = "") -> Node:
        """One ``(property ...)`` node on a placed symbol."""
        effects: list[Any] = [
            _node("font", [_node("size", [1.27, 1.27])])
        ]
        if justify:
            effects.append(_node("justify", [Sym(justify)]))
        if hide:
            effects.append(_node("hide", [Sym("yes")]))
        return _node("property", [
            name, value,
            _node("at", [round(x, 3), round(y, 3), 0]),
            _node("effects", effects),
        ])

    def _layout_fields(self, node: Node, lib_id: str, rotation: float,
                       mirror: str = "", unit: int = 1) -> None:
        """Put a part's Reference and Value where no wire is going to be.

        Which side depends on how the part is oriented, because that is where
        its wires leave from:

        * **Pins leaving top and bottom** -- a capacitor, a resistor standing
          up -- get their labels stacked to the RIGHT. Above and below is
          exactly where the wires run, and a reference 1.27 mm over a
          capacitor sits on the wire climbing to the rail.
        * **Pins leaving left and right** -- a regulator, a fuse lying down --
          get Reference above and Value below, which is clear for the same
          reason.

        This is what KiCad itself does, and for the same reason. A caller who
        wants otherwise moves the field: see :meth:`move_field`.
        """
        at = node.get("at")
        if at is None:
            return
        cx, cy = _f(at, 0), _f(at, 1)
        left, bottom, right, top = self.symbol(lib_id, unit=unit).bounds
        # Take the box's four corners through the same transform the pins get,
        # so the answer is where the part actually sits on the sheet. A symbol
        # is NOT centred on its origin -- a USB-C receptacle's body hangs
        # 22.86 mm below it, and treating it as centred put the value label
        # exactly on the ground pin.
        here = Point(cx, cy)
        corners = [_pin_on_sheet(x, y, 0.0, here, rotation, mirror)[0]
                   for x in (left, right) for y in (bottom, top)]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        angle = _field_angle(rotation)

        # Which way the wires will leave, from the pins themselves rather than
        # from the rotation: a symbol can be drawn either way round, and it is
        # the pin directions that decide where a wire goes.
        pins = self._as_part(node, "?").pins
        upright = sum(1 for p in pins if p.orientation % 180 == 90)
        if pins and upright * 2 >= len(pins):
            places = (("Reference", max(xs) - cx + 1.27, -1.27),
                      ("Value", max(xs) - cx + 1.27, 1.27))
            justify = "left"
        else:
            places = (("Reference", 0.0, min(ys) - cy - 2.54),
                      ("Value", 0.0, max(ys) - cy + 2.54))
            justify = ""

        for name, dx, dy in places:
            prop = self._prop_of(node, name)
            if prop is None:
                continue
            pat = prop.get("at")
            if pat is None:
                continue
            _set(pat, 0, round(cx + dx, 3))
            _set(pat, 1, round(cy + dy, 3))
            _set(pat, 2, angle)
            self._justify(prop, justify)

    @staticmethod
    def _justify(prop: Node, justify: str) -> None:
        """Set or clear a field's justification, leaving the rest alone."""
        effects = prop.get("effects")
        if effects is None:
            return
        existing = effects.get("justify")
        if existing is not None:
            effects.items.remove(existing)
        if justify:
            effects.items.append(_node("justify", [Sym(justify)]))

    def _ensure_lib_symbol(self, lib_id: str,
                           sym: library.LibrarySymbol) -> None:
        """Copy a symbol's definition into the sheet's ``lib_symbols``."""
        table = self._tree.get("lib_symbols")
        if table is None:
            table = _node("lib_symbols", [])
            self._tree.items.insert(4, table)
        for existing in table.get_all("symbol"):
            if _text(existing) == lib_id:
                return
        # A copy: the loader shares its definitions between callers.
        table.items.append(copy.deepcopy(sym.definition))

    def _find(self, ref: str, unit: int = 1) -> Node | None:
        """The ``(symbol ...)`` node placed as *ref*'s *unit*, if any."""
        for node in self._tree.get_all("symbol"):
            if node.get("lib_id") is None:
                continue
            prop = self._prop_of(node, "Reference")
            if (prop is not None and _text(prop, 1) == ref
                    and int(_f(node.get("unit"), 0, 1)) == unit):
                return node
        return None

    def _require(self, ref: str, unit: int = 1) -> Node:
        """The node for *ref*'s *unit*, or a :class:`LookupError`."""
        node = self._find(ref, unit)
        if node is None:
            raise LookupError(f"{ref} unit {unit} is not on the sheet")
        return node

    def part(self, ref: str, *, unit: int = 1) -> Part:
        """One placed unit, with its pins at sheet positions."""
        return self._as_part(self._require(ref, unit), ref)

    def _as_part(self, node: Node, ref: str) -> Part:
        """Build a :class:`Part` from a placed ``(symbol ...)`` node."""
        lib_id = _text(node.get("lib_id"))
        at_node = node.get("at")
        at = Point(_f(at_node, 0), _f(at_node, 1))
        rotation = _f(at_node, 2)
        mirror = _text(node.get("mirror")) if node.get("mirror") else ""
        unit = int(_f(node.get("unit"), 0, 1))
        value_prop = self._prop_of(node, "Value")
        pins = []
        for number, name, px, py, pang, kind, length in _symbol_pins(
                self._load(lib_id).definition, unit):
            point, angle = _pin_on_sheet(px, py, pang, at, rotation, mirror)
            pins.append(Pin(number=number, name=name, at=point,
                            orientation=angle, kind=kind, length=length))
        return Part(ref=ref, lib_id=lib_id,
                    value=_text(value_prop, 1) if value_prop else "",
                    at=at, rotation=rotation, mirror=mirror, unit=unit,
                    pins=tuple(pins), uuid=_text(node.get("uuid")))

    def parts(self) -> list[Part]:
        """Every placed part, in reference order."""
        out = []
        for node in self._tree.get_all("symbol"):
            if node.get("lib_id") is None:
                continue
            prop = self._prop_of(node, "Reference")
            if prop is not None:
                out.append(self._as_part(node, _text(prop, 1)))
        return sorted(out, key=lambda p: p.ref)

    def move(self, ref: str, x: float, y: float, *,
             unit: int = 1) -> Part:
        """Move a placed part. Its pins move with it."""
        node = self._require(ref, unit)
        at = node.get("at")
        if at is None:
            raise LookupError(f"{ref} has no position")
        dx, dy = snap(x) - _f(at, 0), snap(y) - _f(at, 1)
        _set(at, 0, snap(x))
        _set(at, 1, snap(y))
        for prop in node.get_all("property"):  # the fields ride along
            pat = prop.get("at")
            if pat is not None:
                _set(pat, 0, round(_f(pat, 0) + dx, 3))
                _set(pat, 1, round(_f(pat, 1) + dy, 3))
        return self.part(ref, unit=unit)

    def rotate(self, ref: str, rotation: float, *,
               unit: int = 1) -> Part:
        """Set a placed part's rotation in degrees."""
        rotation = _quarter_turn(rotation)
        node = self._require(ref, unit)
        at = node.get("at")
        if at is None:
            raise LookupError(f"{ref} has no position")
        _set(at, 2, rotation)
        # The fields have to follow: a part turned on its side with its label
        # left where it was reads sideways and sits in the wrong place.
        self._layout_fields(node, _text(node.get("lib_id")),
                            rotation, _text(node.get("mirror")),
                            unit)
        return self.part(ref, unit=unit)

    def mirror(self, ref: str, axis: str, *, unit: int = 1) -> Part:
        """Mirror a placed part about ``"x"``, ``"y"`` or ``""`` for neither."""
        if axis not in ("", "x", "y"):
            raise ValueError(f"mirror axis must be '', 'x' or 'y', not {axis!r}")
        node = self._require(ref, unit)
        existing = node.get("mirror")
        if existing is not None:
            node.items.remove(existing)
        if axis:
            node.items.insert(3, _node("mirror", [Sym(axis)]))
        return self.part(ref, unit=unit)

    def remove(self, ref: str, *, unit: int = 1) -> None:
        """Take one placed unit off the sheet."""
        self._tree.items.remove(self._require(ref, unit))

    # -- editing what is already drawn --------------------------------------

    def _at_point(self, kinds: tuple[str, ...], x: float,
                  y: float) -> list[Node]:
        """Every node of these kinds whose ``at`` is this snapped point."""
        want = (snap(x), snap(y))
        found = []
        for kind in kinds:
            for node in self._tree.get_all(kind):
                at = node.get("at")
                if at is not None and (_f(at, 0), _f(at, 1)) == want:
                    found.append(node)
        return found

    def _wires_between(self, x1: float, y1: float, x2: float,
                       y2: float) -> list[Node]:
        """Wire nodes joining these two snapped points, either way round."""
        a, b = (snap(x1), snap(y1)), (snap(x2), snap(y2))
        found = []
        for node in self._tree.get_all("wire"):
            pts = node.get("pts")
            xy = pts.get_all("xy") if pts is not None else []
            if len(xy) < 2:
                continue
            ends = ((_f(xy[0], 0), _f(xy[0], 1)), (_f(xy[1], 0), _f(xy[1], 1)))
            if ends == (a, b) or ends == (b, a):
                found.append(node)
        return found

    def remove_wire(self, x1: float, y1: float, x2: float, y2: float) -> int:
        """Delete wires running between these two points."""
        found = self._wires_between(x1, y1, x2, y2)
        for node in found:
            self._tree.items.remove(node)
        return len(found)

    def move_wire(self, x1: float, y1: float, x2: float, y2: float,
                  dx: float, dy: float) -> int:
        """Shift wires between these points by ``(dx, dy)``."""
        found = self._wires_between(x1, y1, x2, y2)
        for node in found:
            pts = node.get("pts")
            for xy in (pts.get_all("xy") if pts is not None else []):
                _set(xy, 0, snap(_f(xy, 0) + dx))
                _set(xy, 1, snap(_f(xy, 1) + dy))
        return len(found)

    def remove_label(self, x: float, y: float) -> int:
        """Delete labels at this point, of any kind."""
        found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
        for node in found:
            self._tree.items.remove(node)
        return len(found)

    def move_label(self, x: float, y: float, dx: float, dy: float) -> int:
        """Shift labels at this point by ``(dx, dy)``."""
        found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
        for node in found:
            at = node.get("at")
            if at is None:                     # _at_point cannot return one
                continue
            _set(at, 0, snap(_f(at, 0) + dx))
            _set(at, 1, snap(_f(at, 1) + dy))
        return len(found)

    def rotate_label(self, x: float, y: float, rotation: float) -> int:
        """Turn labels at this point."""
        turn = _quarter_turn(rotation)
        found = self._at_point(tuple(_LABEL_NODE.values()), x, y)
        for node in found:
            at = node.get("at")
            if at is None:                     # _at_point cannot return one
                continue
            if len(at.items) > 3:
                _set(at, 2, turn)
            else:
                at.items.append(Sym(_fmt(turn)))
        return len(found)

    def remove_junction(self, x: float, y: float) -> int:
        """Delete junctions at this point."""
        found = self._at_point(("junction",), x, y)
        for node in found:
            self._tree.items.remove(node)
        return len(found)

    def remove_no_connect(self, x: float, y: float) -> int:
        """Delete no-connect marks at this point."""
        found = self._at_point(("no_connect",), x, y)
        for node in found:
            self._tree.items.remove(node)
        return len(found)

    def _sheet_node(self, name: str) -> Node:
        """The child-sheet box called *name*."""
        for node in self._tree.get_all("sheet"):
            if _text(self._prop_of(node, "Sheetname"), 1) == name:
                return node
        have = ", ".join(
            _text(self._prop_of(n, "Sheetname"), 1)
            for n in self._tree.get_all("sheet")) or "none"
        raise LookupError(f"no child sheet named {name!r}; this sheet has {have}")

    def _sheet_ref(self, node: Node) -> SheetRef:
        """Describe a child-sheet box that is already on the sheet."""
        at = node.get("at")
        size = node.get("size")
        pins = tuple(
            Pin(number=_text(p), name=_text(p), orientation=180.0,
                kind=_text(p.get("pin")) or "passive", length=0.0,
                at=Point(_f(p.get("at"), 0), _f(p.get("at"), 1)))
            for p in node.get_all("pin"))
        uid = _text(node.get("uuid"))
        return SheetRef(
            name=_text(self._prop_of(node, "Sheetname"), 1),
            filename=_text(self._prop_of(node, "Sheetfile"), 1),
            at=Point(_f(at, 0), _f(at, 1)),
            size=(_f(size, 0), _f(size, 1)), uuid=uid,
            instance_path=f"{self._where}/{uid}", pins=pins)

    def move_sheet(self, name: str, x: float, y: float) -> SheetRef:
        """Move a child-sheet box, and say where its ports ended up."""
        node = self._sheet_node(name)
        at = node.get("at")
        if at is None:
            raise LookupError(f"child sheet {name!r} has no position")
        was = Point(_f(at, 0), _f(at, 1))
        now = Point(snap(x), snap(y))
        dx, dy = now.x - was.x, now.y - was.y
        _set(at, 0, now.x)
        _set(at, 1, now.y)
        # The box's own text and every port move with it: a port is placed on
        # the box edge, so leaving them behind detaches the page's interface.
        for child in list(node.get_all("property")) + list(node.get_all("pin")):
            pat = child.get("at")
            if pat is not None:
                _set(pat, 0, snap(_f(pat, 0) + dx))
                _set(pat, 1, snap(_f(pat, 1) + dy))
        return self._sheet_ref(node)

    def remove_sheet(self, name: str) -> None:
        """Take a child-sheet box off this sheet. The child FILE is left."""
        self._tree.items.remove(self._sheet_node(name))

    def remove_field(self, ref: str, name: str, *,
                     unit: int = 1) -> dict[str, str]:
        """Delete a field from a part and return the fields it has left."""
        node = self._require(ref, unit)
        prop = self._prop_of(node, name)
        if prop is None:
            raise LookupError(f"{ref} has no field {name!r}")
        node.items.remove(prop)
        return self.fields(ref)

    def set_field(self, ref: str, name: str, value: str) -> dict[str, str]:
        """Set one of a part's fields and return all of them."""
        node = self._require(ref)
        prop = self._prop_of(node, name)
        if prop is None:
            at = node.get("at")
            node.items.append(self._property(name, value, _f(at, 0),
                                             _f(at, 1), hide=True))
        else:
            prop.items[2] = value
        return self.fields(ref)

    def move_field(self, ref: str, name: str, dx: float, dy: float, *,
                   rotation: float | None = None,
                   justify: str = "") -> Point:
        """Move a field relative to its part, and return where it landed."""
        node = self._require(ref)
        prop = self._prop_of(node, name)
        if prop is None:
            have = ", ".join(self.fields(ref))
            raise LookupError(f"{ref} has no field {name!r}; it has {have}")
        at = node.get("at")
        pat = prop.get("at")
        if at is None or pat is None:
            raise LookupError(f"{ref}.{name} has no position")
        where = Point(round(_f(at, 0) + dx, 3), round(_f(at, 1) + dy, 3))
        _set(pat, 0, where.x)
        _set(pat, 1, where.y)
        if rotation is not None:
            _set(pat, 2, rotation % 360.0)
        self._justify(prop, justify)
        return where

    def fields(self, ref: str) -> dict[str, str]:
        """Every field on a part, by name."""
        node = self._require(ref)
        return {_text(prop, 0): _text(prop, 1)
                for prop in node.get_all("property")}

    def pin(self, ref: str, pin: str) -> Point:
        """Where *ref*'s *pin* is on the sheet -- the point to wire to."""
        part = self.part(ref)
        found = part.pin(pin)
        if found is None:
            have = ", ".join(p.number for p in part.pins)
            raise LookupError(f"{ref} has no pin {pin!r}; it has {have}")
        return found.at

    # -- connections ------------------------------------------------------

    def wire(self, x1: float, y1: float, x2: float, y2: float) -> list[Point]:
        """Draw one straight wire segment and return its ends."""
        a, b = Point(snap(x1), snap(y1)), Point(snap(x2), snap(y2))
        self._tree.items.append(_node("wire", [
            _node("pts", [_node("xy", [a.x, a.y]),
                              _node("xy", [b.x, b.y])]),
            _node("stroke", [_node("width", [0]),
                                 _node("type", [Sym("default")])]),
            _node("uuid", [self._uid_for(
                f"wire:{a.x},{a.y}:{b.x},{b.y}")]),
        ]))
        return [a, b]

    def junction(self, x: float, y: float) -> Point:
        """Mark a point where crossing wires connect."""
        at = Point(snap(x), snap(y))
        self._tree.items.append(_node("junction", [
            _node("at", [at.x, at.y]),
            _node("diameter", [0]),
            _node("color", [0, 0, 0, 0]),
            _node("uuid", [self._uid_for(f"junction:{at.x},{at.y}")]),
        ]))
        return at

    def label(self, x: float, y: float, text: str, *, kind: str = "local",
              rotation: float = 0.0, justify: str = "left") -> Point:
        """Attach a net name at a point."""
        if kind not in _LABEL_NODE:
            raise ValueError(f"label kind must be one of {list(_LABEL_NODE)}")
        if justify not in ("left", "right"):
            raise ValueError(f"justify must be 'left' or 'right', not {justify!r}")
        at = Point(snap(x), snap(y))
        # A LOCAL label is text sitting on a wire, so its baseline goes at the
        # anchor and it reads above: `bottom` as well as a side.
        #
        # A GLOBAL or HIERARCHICAL label is a flag drawn AROUND its text, and
        # the side is what points it: `right` puts the tip on the right and
        # grows the box leftward. `bottom` on one of these pins the baseline
        # to the anchor instead of centring the text in the flag, which is why
        # every one of them was drawn with its name riding out of the top of
        # its own box.
        sides = [Sym(justify)]
        if kind == "local":
            sides.append(Sym("bottom"))
        node = _node(_LABEL_NODE[kind], [
            text,
            _node("at", [at.x, at.y, rotation % 360.0]),
            _node("effects", [
                _node("font", [_node("size", [1.27, 1.27])]),
                _node("justify", sides),
            ]),
            _node("uuid", [self._uid_for(
                f"label:{kind}:{text}:{at.x},{at.y}")]),
        ])
        if kind != "local":
            # After the text, not before it: items[0] is the node's own name,
            # so index 1 is the label's text. Putting the shape there gives
            # `(hierarchical_label (shape input) "VBUS" ...)`, which KiCad
            # parses without complaint and then does not match to a sheet pin.
            node.items.insert(2, _node("shape", [Sym("input")]))
        self._tree.items.append(node)
        return at

    def power(self, x: float, y: float, net: str, *,
              rotation: float = 0.0) -> Part:
        """Place a power symbol for *net* and return it."""
        return self.place(f"power:{net}", self._next_hash_ref("#PWR"), x, y,
                          value=net, rotation=rotation)

    def power_flag(self, x: float, y: float, *, rotation: float = 0.0) -> Part:
        """Place a PWR_FLAG, which tells ERC a net is driven."""
        return self.place("power:PWR_FLAG", self._next_hash_ref("#FLG"), x, y,
                          value="PWR_FLAG", rotation=rotation)

    def _next_hash_ref(self, prefix: str) -> str:
        """The next free ``#PWR0001``-style reference."""
        used = {p.ref for p in self.parts() if p.ref.startswith(prefix)}
        n = 1
        while f"{prefix}{n:04d}" in used:
            n += 1
        return f"{prefix}{n:04d}"

    def no_connect(self, x: float, y: float) -> Point:
        """Mark a pin deliberately unconnected."""
        at = Point(snap(x), snap(y))
        self._tree.items.append(_node("no_connect", [
            _node("at", [at.x, at.y]),
            _node("uuid", [self._uid_for(f"noconnect:{at.x},{at.y}")]),
        ]))
        return at

    # -- reading back -----------------------------------------------------

    def wires(self) -> list[tuple[Point, Point]]:
        """Every wire segment on the sheet."""
        out = []
        for node in self._tree.get_all("wire"):
            pts = node.get("pts")
            if pts is None:
                continue
            xy = pts.get_all("xy")
            if len(xy) >= 2:
                out.append((Point(_f(xy[0], 0), _f(xy[0], 1)),
                            Point(_f(xy[1], 0), _f(xy[1], 1))))
        return out

    def labels(self) -> list[dict[str, object]]:
        """Every label, as ``{x, y, text, kind, rotation}``."""
        out: list[dict[str, object]] = []
        for kind, name in _LABEL_NODE.items():
            for node in self._tree.get_all(name):
                at = node.get("at")
                just = node.get("effects")
                just = just.get("justify") if just is not None else None
                out.append({"text": _text(node), "kind": kind,
                            "x": _f(at, 0), "y": _f(at, 1),
                            "rotation": _f(at, 2),
                            "justify": _text(just) if just is not None else ""})
        return out

    @contextlib.contextmanager
    def _scratch(self) -> Iterator[Path]:
        """The sheet as a file on disk, for a tool that needs a path.

        Written BESIDE the real file, not in a temp directory. The sheet in
        memory may be ahead of what is on disk, so a copy is needed -- but a
        root sheet names its children by relative filename, and a copy off in
        a temp directory cannot see them. That produced a design reporting
        eight unmatched ports and no nets at all, which reads exactly like a
        wiring mistake and was not one.
        """
        scratch = self._path.parent / f".{self._path.stem}.scratch.kicad_sch"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(dumps(self._tree) + "\n", encoding="utf-8")
        try:
            yield scratch
        finally:
            scratch.unlink(missing_ok=True)

    def nets(self) -> list[Net]:
        """What this sheet actually connects, from KiCad's own netlist."""
        # Imported here, not at module scope: the package root imports both
        # this and the interchange layer, and one of the two has to go second.
        from . import netlist as _netlist

        with self._scratch() as scratch:
            tree = _netlist.export_netlist(scratch)
        found: list[Net] = []
        table = tree.get("nets")
        for node in (table.get_all("net") if table is not None else []):
            pins = tuple(
                NetPin(ref=_text(item.get("ref")), pin=_text(item.get("pin")),
                       name=_text(item.get("pinfunction")))
                for item in node.get_all("node")
            )
            found.append(Net(name=_text(node.get("name")), pins=pins))
        return sorted(found, key=lambda n: n.name)

    def check(self) -> list[Finding]:
        """Every violation, mapped from a position back to a part and pin."""
        from ..cli import cli as _kicad

        where: dict[tuple[float, float], tuple[str, str]] = {}
        for part in self.parts():
            for pin in part.pins:
                key = (round(pin.at.x, 2), round(pin.at.y, 2))
                where[key] = (part.ref, pin.number)

        with self._scratch() as scratch:
            data = _kicad.erc(scratch)

        out: list[Finding] = []
        for sheet in data.get("sheets", []):
            page = str(sheet.get("path", "/"))
            for violation in sheet.get("violations", []):
                items = violation.get("items") or [{}]
                for item in items:
                    pos = item.get("pos") or {}
                    # KiCad reports ERC positions in hundredths of a
                    # millimetre's worth of inches -- x100 puts them back in mm.
                    at = Point(round(float(pos.get("x", 0.0)) * 100, 3),
                               round(float(pos.get("y", 0.0)) * 100, 3))
                    # The lookup only covers THIS sheet's parts, so a
                    # finding on a child page comes back without a ref. The
                    # page name is what locates it in that case.
                    ref, number = where.get(
                        (round(at.x, 2), round(at.y, 2)), ("", ""))
                    out.append(Finding(
                        severity=str(violation.get("severity", "error")),
                        kind=str(violation.get("type", "")),
                        message=str(violation.get("description", "")),
                        ref=ref, pin=number, sheet=page, at=at))
        return out

    def render(self, *, output_dir: Path | None = None, dpi: int = 150,
               black_and_white: bool = False,
               pages: str | None = None) -> list[Path]:
        """Render the sheet to PNG, one per page, via kicad-cli."""
        from .. import render as _render

        return _render.export_png(self._path, output_dir=output_dir, dpi=dpi,
                                  black_and_white=black_and_white, pages=pages)

    def next_ref(self, prefix: str) -> str:
        """The next unused reference with this prefix."""
        used = set()
        for part in self.parts():
            if part.ref.startswith(prefix):
                tail = part.ref[len(prefix):]
                if tail.isdigit():
                    used.add(int(tail))
        n = 1
        while n in used:
            n += 1
        return f"{prefix}{n}"

    def at(self, x: float, y: float, radius: float = 0.01) -> dict[str, object]:
        """What meets at a point: pins, wire ends and labels."""
        def near(px: float, py: float) -> bool:
            return abs(px - x) <= radius and abs(py - y) <= radius

        pins = [{"ref": part.ref, "pin": pin.number, "name": pin.name}
                for part in self.parts() for pin in part.pins
                if near(pin.at.x, pin.at.y)]
        ends = sum(1 for a, b in self.wires()
                   if near(a.x, a.y) or near(b.x, b.y))
        labels = [lab for lab in self.labels()
                  if near(float(lab["x"]), float(lab["y"]))]  # type: ignore[arg-type]
        return {"x": round(x, 3), "y": round(y, 3), "pins": pins,
                "wire_ends": ends, "labels": labels,
                "connected": len(pins) + ends + len(labels) > 1}


def create(path: str | Path, *, paper: str = "A4", title: str = "",
           instance_path: str = "") -> Sheet:
    """Make a new, empty sheet and return it open for editing.

    Typed as the abstract :class:`~kicad_flow.schematic.api.Sheet`, not as the
    concrete class, so a caller who takes their type from the return value
    binds to the interface rather than to this backend.
    """
    if paper not in PAPER:
        raise ValueError(
            f"paper must be A4 or A3, not {paper!r}. There is deliberately "
            f"nothing larger: a page too big for A3 is one nobody reads. Put "
            f"the overflow on another sheet with add_sheets instead."
        )
    tree = _node("kicad_sch", [
        _node("version", [20250114]),
        _node("generator", ["kicad_flow"]),
        _node("generator_version", ["10.0"]),
        _node("uuid", [_uid_from(f"file:{Path(path).name}")]),
        _node("paper", [paper]),
        _node("lib_symbols", []),
        _node("sheet_instances", [
            _node("path", ["/", _node("page", ["1"])]),
        ]),
    ])
    if title:
        tree.items.insert(5, _node("title_block", [
            _node("title", [title]),
        ]))
    return KiCadSheet(Path(path), tree, paper, instance_path)


def load(path: str | Path, *, instance_path: str = "") -> Sheet:
    """Open an existing sheet.

    *instance_path* matters only for a child in a hierarchy; see
    :meth:`KiCadSheet._where`.
    """
    file = Path(path)
    tree = loads(file.read_text(encoding="utf-8"))
    return KiCadSheet(file, tree, _text(tree.get("paper"), 0, "A4"),
                      instance_path)


__all__ = ["GRID", "PAPER", "KiCadSheet", "create", "load"]
