"""Native schematic object edits in the isolated official SDK interpreter.

No project or codec files are written here. Caller batches retain native
objects until a single checked CreateItems/UpdateItems commit.
"""

from __future__ import annotations

import importlib
import math
import uuid
from itertools import pairwise
from pathlib import Path
from typing import Any, cast


class Edits:
    """Stage one sheet's explicit primitive edits as native objects."""

    def __init__(self, worker: Any) -> None:
        """Share the worker's owned connection, without importing the MCP layer."""
        self.w = worker
        self.st = importlib.import_module("kipy.schematic_types")
        self.geo = importlib.import_module("kipy.geometry")
        self.common = importlib.import_module("kipy.common_types")
        self.ct = worker.types
        self.json = importlib.import_module("google.protobuf.json_format")
        self.commit: Any = None
        self.sch: Any = None
        self.items: dict[str, Any] = {}
        self.created: dict[str, Any] = {}
        self.updated: dict[str, Any] = {}
        self.removed: set[str] = set()
        self.origins: dict[str, int] = {}
        self.ordinal = 0
        self.expected = ""
        self.instance = ""
        self.path = Path()
        self.aliases: dict[str, tuple[str, str, str]] = {}
        self.factory: Any = None
        self.templates: dict[tuple[str, int], Any] = {}

    def begin(self, args: dict[str, Any]) -> dict[str, Any]:
        """Observe once, then stage all edits without mutating the live document."""
        if self.commit is not None:
            raise ValueError("A schematic transaction is already open")
        self.sch = self.w.view(args["instance"])
        self.instance, self.path = args["instance"], Path(args["path"])
        self.expected = self.sch.get_as_string()
        if self.expected != args["expected"]:
            raise ValueError(
                "Schematic changed in KiCad; read fresh state before editing"
            )
        self.items = {i.id.value: i for i in self.sch.get_items()}
        self.created, self.updated, self.removed, self.origins, self.aliases = (
            {},
            {},
            set(),
            {},
            {},
        )
        self.commit = self.sch.begin_commit()
        return {}

    def end(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate every native item status and push or drop the entire batch."""
        if self.commit is None:
            return {}
        commit, self.commit = self.commit, None
        try:
            if args["commit"]:
                if self.sch.get_as_string() != self.expected:
                    raise ValueError(
                        "The KiCad editor changed during this batch; inspect and retry"
                    )
                if self.removed:
                    self.w.delete(self.sch, sorted(self.removed))
                if self.created:
                    self.w.write_items(
                        self.sch, list(self.created.values()), create=True
                    )
                if self.updated:
                    self.w.write_items(
                        self.sch, list(self.updated.values()), create=False
                    )
                self.sch.push_commit(commit, "KiCadFlow schematic batch")
                for identity in self.removed:
                    self.w.filenames.pop(identity, None)
                self.w.filenames.update(self.aliases)
            else:
                self.sch.drop_commit(commit)
        except Exception:
            self.sch.drop_commit(commit)
            raise
        finally:
            self.items = {}
        return cast(dict[str, Any], self.w.snapshot({"instance": self.instance}))

    def point(self, x: float, y: float) -> Any:
        """Snap caller millimetres once to the existing schematic grid."""
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite")
        return self.geo.Vector2.from_xy(
            round(x / 1.27) * 1270000, round(y / 1.27) * 1270000
        )

    def raw_point(self, x: float, y: float) -> Any:
        """Convert non-grid field offsets and dimensions to native integers."""
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("dimensions must be finite")
        return self.geo.Vector2.from_xy(round(x * 1e6), round(y * 1e6))

    def xy(self, point: Any) -> list[float]:
        """Observe native integer coordinates in millimetres."""
        return [point.x / 1e6, point.y / 1e6]

    def angle(self, value: float) -> int:
        """Resolve an explicit quarter-turn orientation."""
        if not math.isfinite(value) or value % 90:
            raise ValueError("rotation must be 0, 90, 180 or 270")
        return int(
            getattr(self.st.SchematicSymbolOrientation, "SSO_" + str(int(value % 360)))
        )

    def data(self, item: Any) -> dict[str, Any]:
        """Return actual native definition and instance properties."""
        return cast(
            dict[str, Any],
            self.json.MessageToDict(item.proto, preserving_proto_field_name=True),
        )

    def put(self, item: Any, *, create: bool = False) -> Any:
        """Retain native objects, including multiple edits to a new item."""
        if create:
            item.id.value = item.id.value or str(uuid.uuid4())
        identity = item.id.value
        if hasattr(item, "path"):
            item.proto.path.CopyFrom(self.sch._doc.sheet_path)
        self.items[identity] = item
        (self.created if create or identity in self.created else self.updated)[
            identity
        ] = item
        self.origins[identity] = self.ordinal
        return item

    def delete(self, items: list[Any]) -> int:
        """Remove exactly the matching objects from the staged native document."""
        for item in items:
            identity = item.id.value
            self.items.pop(identity, None)
            self.updated.pop(identity, None)
            if identity in self.created:
                self.created.pop(identity)
            else:
                self.removed.add(identity)
            self.origins[identity] = self.ordinal
        return len(items)

    def item(self, identity: str, kind: type[Any]) -> Any:
        """Find one exact typed native identity."""
        item = self.items.get(identity)
        if not isinstance(item, kind):
            raise LookupError(f"no {kind.__name__} with uuid {identity!r}")
        return item

    def symbols(self) -> list[Any]:
        """Observe the selected sheet, including its staged symbols."""
        return [
            i
            for i in self.items.values()
            if isinstance(i, self.st.SchematicSymbolInstance)
        ]

    def symbol(self, ref: str, unit: int = 1) -> Any:
        """Find the explicit reference and unit without annotation inference."""
        found = [i for i in self.symbols() if i.reference == ref and i.unit == unit]
        if len(found) != 1:
            raise LookupError(f"expected one {ref!r} unit {unit}; found {len(found)}")
        return found[0]

    def fields(self, item: Any) -> list[Any]:
        """Observe standard and caller-authored instance fields."""
        return [
            item.reference_field,
            item.value_field,
            item.footprint_field,
            item.datasheet_field,
            item.description_field,
            *item.user_fields,
        ]

    def field(self, item: Any, name: str) -> Any:
        """Resolve the named field exactly."""
        field = next((f for f in self.fields(item) if f.name == name), None)
        if field is None:
            raise LookupError(f"{item.reference} has no field {name!r}")
        return field

    def field_values(self, item: Any) -> dict[str, str]:
        """Observe field names and values from the current native instance."""
        return {f.name: f.text.value for f in self.fields(item)}

    def text_style(
        self,
        text: Any,
        *,
        size: float = 1.27,
        justify: str = "center",
        bottom: bool = False,
    ) -> None:
        """Set explicit style defaults, never reading surrounding geometry."""
        if size <= 0 or not math.isfinite(size):
            raise ValueError("text size must be finite and positive")
        if justify not in ("left", "center", "right"):
            raise ValueError("justify must be left, center or right")
        attr = text.attributes
        attr.size = self.raw_point(size, size)
        attr.horizontal_alignment = getattr(self.ct, "HA_" + justify.upper())
        attr.vertical_alignment = self.ct.VA_BOTTOM if bottom else self.ct.VA_CENTER
        attr.visible = True
        attr.line_spacing = 1.0

    def template(self, lib_id: str, unit: int) -> Any:
        """Obtain native library geometry in a private unsaved editor."""
        key = lib_id, unit
        if key not in self.templates:
            if self.factory is None:
                self.factory = self.w.api.KiCad(
                    headless=True, kicad_cli_path=self.w.cli, timeout_ms=10000
                )
                self.factory.create_document(
                    str(
                        self.w.root.with_name(
                            f".native-symbols-{uuid.uuid4()}.kicad_sch"
                        )
                    ),
                    self.ct.DocumentType.DOCTYPE_SCHEMATIC,
                )
                self.factory.load_all_libraries(self.ct.LibraryType.LT_SYMBOL)
                self.factory.wait_for_libraries(
                    self.ct.LibraryType.LT_SYMBOL, timeout_s=45
                )
            sch = self.factory.get_schematic()
            sch = sch.for_sheet(sch.get_hierarchy().root)
            self.templates[key] = sch.place_symbol_from_library(
                lib_id, self.raw_point(0, 0), unit=unit, reference="X1"
            )
        proto = type(self.templates[key].proto)()
        proto.CopyFrom(self.templates[key].proto)
        registry = importlib.import_module("google.protobuf.symbol_database").Default()

        def refresh(message: Any) -> None:
            name = message.DESCRIPTOR.full_name
            if name == "kiapi.common.types.KIID":
                if message.value:
                    message.value = str(uuid.uuid4())
            elif name == "google.protobuf.Any":
                native = registry.GetSymbol(message.type_url.rsplit("/", 1)[-1])()
                message.Unpack(native)
                refresh(native)
                message.Pack(native)
            else:
                for descriptor, value in message.ListFields():
                    if descriptor.type != descriptor.TYPE_MESSAGE:
                        continue
                    if descriptor.label == descriptor.LABEL_REPEATED:
                        for child in value:
                            refresh(child)
                    else:
                        refresh(value)

        refresh(proto)
        return self.st.SchematicSymbolInstance(proto)

    def layout(self, item: Any, values: dict[str, Any]) -> None:
        """Apply the existing explicit field defaults in absolute sheet space."""
        for value in values.get("field_layout", []):
            field = self.field(item, value["name"])
            field.text.position = self.raw_point(value["x"], value["y"])
            self.text_style(field.text, justify=value["justify"] or "center")
            # Field angles/justification are local to the parent transform,
            # although the IPC field position is in absolute sheet space.
            field.text.attributes.angle = value["angle"]
        item.fields_autoplaced = False

    def transform(self, item: Any, rotation: float, mirror: str) -> None:
        """Encode an equivalent native canonical transform before deserialization.

        Native Deserialize applies the two mirror setters sequentially. Its
        canonical form for X-mirror plus 180 degrees is Y-mirror plus zero;
        sending the former lets the later false Y flag erase that reflection.
        Normalize the representation, never the caller's physical transform.
        """
        self.angle(rotation)
        rotation %= 360
        if mirror == "x" and rotation == 180:
            rotation, mirror = 0, "y"
        elif mirror == "y" and rotation:
            rotation, mirror = (rotation + 180) % 360, "x"
        item.transform.orientation = self.angle(rotation)
        item.transform.mirror_x = mirror == "x"
        item.transform.mirror_y = mirror == "y"

    def place(self, v: dict[str, Any]) -> dict[str, Any]:
        """Stage a native library-backed symbol with explicit annotation."""
        if any(i.reference == v["ref"] and i.unit == v["unit"] for i in self.symbols()):
            raise ValueError(f"{v['ref']} unit {v['unit']} is already on the sheet")
        if v["mirror"] not in ("", "x", "y"):
            raise ValueError("mirror must be '', 'x' or 'y'")
        item = self.template(v["lib_id"], v["unit"])
        item.reference, item.value = v["ref"], v["value"] or item.value
        item.position = self.point(v["x"], v["y"])
        self.transform(item, v["rotation"], v["mirror"])
        item.footprint_field.text.value = ""
        item.footprint_field.text.position = item.position
        item.footprint_field.visible = False
        item.reference_field.visible = not v["ref"].startswith("#")
        item.value_field.visible = True
        self.layout(item, v)
        return {
            "item": self.data(self.put(item, create=True)),
            "created_ids": [item.id.value],
        }

    def move(self, v: dict[str, Any]) -> dict[str, Any]:
        """Translate the native symbol and every instance field by the same offset."""
        item = self.symbol(v["ref"], v["unit"])
        target = self.point(v["x"], v["y"])
        delta = target - item.position
        for field in self.fields(item):
            field.text.position = field.text.position + delta
        item.position = target
        return {"item": self.data(self.put(item))}

    def rotate(self, v: dict[str, Any]) -> dict[str, Any]:
        """Change the native transform and retain the explicit field defaults."""
        item = self.symbol(v["ref"], v["unit"])
        mirror = (
            "x" if item.transform.mirror_x else "y" if item.transform.mirror_y else ""
        )
        self.transform(item, v["rotation"], mirror)
        self.layout(item, v)
        return {"item": self.data(self.put(item))}

    def mirror(self, v: dict[str, Any]) -> dict[str, Any]:
        """Set native mirror flags, preserving the field positions."""
        if v["axis"] not in ("", "x", "y"):
            raise ValueError("mirror axis must be '', 'x' or 'y'")
        item = self.symbol(v["ref"], v["unit"])
        rotation = int(
            self.st.SchematicSymbolOrientation.Name(
                item.transform.orientation
            ).removeprefix("SSO_")
        )
        self.transform(item, rotation, v["axis"])
        return {"item": self.data(self.put(item))}

    def set_field(self, v: dict[str, Any]) -> dict[str, Any]:
        """Update or create one literal native instance field."""
        item = self.symbol(v["ref"])
        try:
            field = self.field(item, v["name"])
        except LookupError:
            field = self.st.SchematicField()
            field.name = v["name"]
            field.text.position = item.position
            self.text_style(field.text)
            field.visible = False
            item.user_fields = [*item.user_fields, field]
            field = self.field(item, v["name"])
        field.text.value = v["value"]
        self.put(item)
        return {"value": self.field_values(item)}

    def remove_field(self, v: dict[str, Any]) -> dict[str, Any]:
        """Remove an explicit custom field; native mandatory fields cannot vanish."""
        item = self.symbol(v["ref"], v["unit"])
        self.field(item, v["name"])
        if v["name"] not in {f.name for f in item.user_fields}:
            raise ValueError(
                "Native mandatory fields cannot be removed; set their value instead"
            )
        item.user_fields = [f for f in item.user_fields if f.name != v["name"]]
        self.put(item)
        return {"value": self.field_values(item)}

    def move_field(self, v: dict[str, Any]) -> dict[str, Any]:
        """Place the named field at an explicit offset in sheet coordinates."""
        item = self.symbol(v["ref"])
        field = self.field(item, v["name"])
        field.text.position = item.position + self.raw_point(v["dx"], v["dy"])
        if v["rotation"] is not None:
            field.text.attributes.angle = v["rotation"] % 360
        field.text.attributes.horizontal_alignment = getattr(
            self.ct, "HA_" + (v["justify"] or "center").upper()
        )
        self.put(item)
        return {"point": self.xy(field.text.position)}

    def at_point(self, kinds: tuple[type[Any], ...], v: dict[str, Any]) -> list[Any]:
        """Find native connection objects at the exact snapped anchor."""
        at = self.point(v["x"], v["y"])
        return [
            i for i in self.items.values() if isinstance(i, kinds) and i.position == at
        ]

    def wires(self, v: dict[str, Any]) -> list[Any]:
        """Find exact endpoint pairs in either direction."""
        a, b = self.point(v["x1"], v["y1"]), self.point(v["x2"], v["y2"])
        return [
            i
            for i in self.items.values()
            if isinstance(i, self.st.SchematicLine)
            and i.type == self.st.SchematicLineType.SLT_WIRE
            and ((i.start == a and i.end == b) or (i.start == b and i.end == a))
        ]

    def connection(self, name: str, v: dict[str, Any]) -> dict[str, Any]:
        """Create native wires, junctions and no-connects directly."""
        if name == "junction":
            at = self.point(v["x"], v["y"])
            if self.at_point((self.st.Junction,), v):
                return {"point": self.xy(at)}
            if self.junction_degree(at) < 3:
                raise ValueError(
                    "KiCad computes junctions from connections and discards a "
                    "free junction. This point has fewer than three incident "
                    "wire branches/pins; create the connections first."
                )
        if name == "wire":
            item = self.st.SchematicLine()
            item.type = self.st.SchematicLineType.SLT_WIRE
            item.start, item.end = (
                self.point(v["x1"], v["y1"]),
                self.point(v["x2"], v["y2"]),
            )
            result: dict[str, Any] = {
                "points": [self.xy(item.start), self.xy(item.end)]
            }
        else:
            item = (
                self.st.Junction() if name == "junction" else self.st.NoConnectMarker()
            )
            item.position = self.point(v["x"], v["y"])
            result = {"point": self.xy(item.position)}
        self.put(item, create=True)
        return {**result, "created_ids": [item.id.value]}

    def junction_degree(self, at: Any) -> int:
        """Count exact native wire rays and selected-unit pins at a point."""
        count = 0
        for item in self.items.values():
            if (
                isinstance(item, self.st.SchematicLine)
                and item.type == self.st.SchematicLineType.SLT_WIRE
            ):
                a, b = item.start, item.end
                dx, dy = b.x - a.x, b.y - a.y
                px, py = at.x - a.x, at.y - a.y
                dot, length = px * dx + py * dy, dx * dx + dy * dy
                if length and px * dy == py * dx and 0 <= dot <= length:
                    count += 1 if dot in (0, length) else 2
            elif isinstance(item, self.st.SchematicSymbolInstance):
                quarter = (
                    int(
                        self.st.SchematicSymbolOrientation.Name(
                            item.transform.orientation
                        ).removeprefix("SSO_")
                    )
                    // 90
                )
                for child in item.definition.items:
                    if child.unit not in (None, 0, item.unit) or not isinstance(
                        child.item, self.st.SchematicPin
                    ):
                        continue
                    if child.proto.body_style.style not in (0, item.body_style or 1):
                        continue
                    pin = child.item.position
                    x, y = (
                        (pin.x, pin.y),
                        (pin.y, -pin.x),
                        (-pin.x, -pin.y),
                        (-pin.y, pin.x),
                    )[quarter]
                    if item.transform.mirror_x:
                        y = -y
                    if item.transform.mirror_y:
                        x = -x
                    count += item.position.x + x == at.x and item.position.y + y == at.y
        return count

    def label_types(self) -> tuple[type[Any], ...]:
        """The primitive's three supported native label classes."""
        return self.st.LocalLabel, self.st.GlobalLabel, self.st.HierarchicalLabel

    def spin(self, rotation: float, justify: str, *, local: bool) -> int:
        """Map explicit label orientation to KiCad's native spin style."""
        self.angle(rotation)
        if justify not in ("left", "right"):
            raise ValueError("label justify must be left or right")
        styles = ("RIGHT", "UP", "LEFT", "BOTTOM")
        # Native spin styles encode both the text angle and its anchored side.
        reverse = justify == "right"
        return int(
            getattr(
                self.st.SchematicLabelSpinStyle,
                "SLSS_"
                + styles[(int(rotation % 360) // 90 + (2 if reverse else 0)) % 4],
            )
        )

    def label_reply(self, item: Any) -> dict[str, Any]:
        """Observe the native orientation instead of echoing an ignored text angle."""
        local = isinstance(item, self.st.LocalLabel)
        style = self.st.SchematicLabelSpinStyle.Name(item.spin_style).removeprefix(
            "SLSS_"
        )
        rotation = {"RIGHT": 0, "UP": 90, "LEFT": 180, "BOTTOM": 270}[style]
        return {
            "uuid": item.id.value,
            "text": item.text.value,
            "kind": "local"
            if local
            else "global"
            if isinstance(item, self.st.GlobalLabel)
            else "hierarchical",
            "point": self.xy(item.position),
            "rotation": rotation,
            "justify": "left" if style in ("RIGHT", "UP") else "right",
        }

    def label(self, v: dict[str, Any]) -> dict[str, Any]:
        """Stage a native label, with its connection anchor separate from its text."""
        kinds = dict(
            zip(("local", "global", "hierarchical"), self.label_types(), strict=True)
        )
        if v["kind"] not in kinds:
            raise ValueError("label kind must be local, global or hierarchical")
        item = kinds[v["kind"]]()
        item.position = self.point(v["x"], v["y"])
        item.text.position = item.position
        item.text.value = v["text"]
        self.text_style(item.text, justify=v["justify"], bottom=v["kind"] == "local")
        item.spin_style = self.spin(
            v["rotation"], v["justify"], local=v["kind"] == "local"
        )
        if not isinstance(item, self.st.LocalLabel):
            item.shape = self.st.SchematicLabelShape.SLSH_INPUT
        self.put(item, create=True)
        return {**self.label_reply(item), "created_ids": [item.id.value]}

    def note(self, name: str, v: dict[str, Any]) -> dict[str, Any]:
        """Create or update literal native notes, retaining omitted properties."""
        create = name == "text"
        item = (
            self.st.SchematicText()
            if create
            else self.item(v["uuid"], self.st.SchematicText)
        )
        if create:
            self.text_style(item.text, bottom=True)
        item.position = self.point(
            v["x"] if v.get("x") is not None else item.position.x / 1e6,
            v["y"] if v.get("y") is not None else item.position.y / 1e6,
        )
        attr = item.text.attributes
        if v.get("text") is not None:
            item.value = v["text"]
        if v.get("size") is not None:
            if not math.isfinite(v["size"]) or v["size"] <= 0:
                raise ValueError("text size must be finite and positive")
            attr.size = self.raw_point(v["size"], v["size"])
            attr.stroke_width = round(v["size"] * 0.2 * 1e6)
        if v.get("rotation") is not None:
            attr.angle = v["rotation"] % 360
        if v.get("bold") is not None:
            attr.bold = v["bold"]
        if v.get("justify") is not None:
            if v["justify"] not in ("left", "center", "right"):
                raise ValueError("text justify must be left, center or right")
            attr.horizontal_alignment = getattr(self.ct, "HA_" + v["justify"].upper())
        self.put(item, create=create)
        return {
            "uuid": item.id.value,
            "text": item.value,
            "point": self.xy(item.position),
            "size": attr.size.x / 1e6,
            "rotation": attr.angle,
            "bold": attr.bold,
            "justify": self.ct.HorizontalAlignment.Name(attr.horizontal_alignment)
            .removeprefix("HA_")
            .lower(),
            "created_ids": [item.id.value] if create else [],
        }

    def graphic_reply(self, item: Any) -> dict[str, Any]:
        """Observe the native schematic polyline and stroke."""
        shape = item.shape
        pts = [self.xy(node.point) for node in shape.polygons[0].outline.nodes]
        rectangle = (
            len(pts) == 5
            and pts[0] == pts[-1]
            and len({p[0] for p in pts}) == 2
            and len({p[1] for p in pts}) == 2
        )
        return {
            "uuid": item.id.value,
            "kind": "rectangle" if rectangle else "polyline",
            "points": [pts[0], pts[2]] if rectangle else pts,
            "width": shape.attributes.stroke.width / 1e6,
            "stroke": self.ct.StrokeLineStyle.Name(shape.attributes.stroke.style)
            .removeprefix("SLS_")
            .lower(),
        }

    def graphic(self, v: dict[str, Any]) -> dict[str, Any]:
        """Stage a native non-electrical polyline or rectangle."""
        if v["kind"] not in ("rectangle", "polyline"):
            raise ValueError("schematic graphic kind must be polyline or rectangle")
        pts = [self.point(*p) for p in v["points"]]
        if v["kind"] == "rectangle":
            if len(pts) != 2 or pts[0].x == pts[1].x or pts[0].y == pts[1].y:
                raise ValueError("a rectangle needs two distinct opposite corners")
            a, b = pts
            pts = [
                a,
                self.geo.Vector2.from_xy(b.x, a.y),
                b,
                self.geo.Vector2.from_xy(a.x, b.y),
                a,
            ]
        if len(pts) < 2 or any(a == b for a, b in pairwise(pts)):
            raise ValueError("a polyline needs non-zero segments")
        if not math.isfinite(v["width"]) or v["width"] < 0:
            raise ValueError("graphic width must be finite and non-negative")
        if v["stroke"] not in (
            "default",
            "solid",
            "dash",
            "dot",
            "dash_dot",
            "dash_dot_dot",
        ):
            raise ValueError("unsupported stroke style")
        shape = self.common.Polygon()
        polygon = self.geo.PolygonWithHoles(auto_close=False)
        for pt in pts:
            polygon.outline.nodes.append(self.geo.PolyLineNode.from_point(pt))
        shape.polygons.append(polygon)
        shape.attributes.stroke.width = round(v["width"] * 1e6)
        shape.attributes.stroke.style = getattr(self.ct, "SLS_" + v["stroke"].upper())
        item = self.st.SchematicGraphicShape()
        item.shape = shape
        self.put(item, create=True)
        return {**self.graphic_reply(item), "created_ids": [item.id.value]}

    def sheet(self, name: str) -> Any:
        """Resolve exactly one named child-sheet symbol."""
        found = [
            i
            for i in self.items.values()
            if isinstance(i, self.st.SheetSymbol) and i.name_field.text.value == name
        ]
        if len(found) != 1:
            raise LookupError(
                f"expected one child sheet named {name!r}; found {len(found)}"
            )
        return found[0]

    def sheet_reply(self, item: Any) -> dict[str, Any]:
        """Observe sheet-port coordinates and its actual hierarchy identity."""
        filename = item.filename_field.text.value
        alias = self.aliases.get(item.id.value) or self.w.filenames.get(item.id.value)
        if alias and filename == alias[1]:
            filename = alias[2]
        return {
            "name": item.name_field.text.value,
            "filename": filename,
            "point": self.xy(item.position),
            "size": self.xy(item.size),
            "uuid": item.id.value,
            "instance_path": self.instance + "/" + item.id.value,
            "pins": [
                {
                    "number": p.text.value,
                    "name": p.text.value,
                    "x": p.position.x / 1e6,
                    "y": p.position.y / 1e6,
                    "orientation": 180.0,
                    "kind": self.port_kind(p.shape),
                    "length": 0.0,
                }
                for p in item.pins
            ],
        }

    def port_kind(self, shape: int) -> str:
        """Translate native port direction names into the contract vocabulary."""
        kind = (
            str(self.st.SchematicLabelShape.Name(shape)).removeprefix("SLSH_").lower()
        )
        return {"bidi": "bidirectional", "tristate": "tri_state"}.get(kind, kind)

    def add_sheet(self, v: dict[str, Any]) -> dict[str, Any]:
        """Stage a native sheet and ports; refuse existing-file attachment."""
        target = (self.path.parent / v["filename"]).resolve()
        if target.exists():
            raise ValueError(
                f"This native IPC build cannot attach an existing child: {target}"
            )
        item = self.st.SheetSymbol()
        item.id.value = str(uuid.uuid4())
        item.position, item.size = (
            self.point(v["x"], v["y"]),
            self.point(v["width"], v["height"]),
        )
        if item.size.x <= 0 or item.size.y <= 0:
            raise ValueError("sheet dimensions must be positive")
        item.name_field.name, item.name_field.text.value = "Sheetname", v["name"]
        item.filename_field.name, item.filename_field.text.value = (
            "Sheetfile",
            target.as_posix(),
        )
        for field, dy in (
            (item.name_field, -1270000),
            (item.filename_field, item.size.y + 1270000),
        ):
            field.visible = True
            self.text_style(field.text)
            field.text.position = item.position + self.geo.Vector2.from_xy(0, dy)
        pins = []
        for index, (name, kind) in enumerate(v["ports"]):
            pin = self.st.SheetPin()
            pin.id.value = str(uuid.uuid4())
            pin.position = item.position + self.raw_point(0, 2.54 + index * 2.54)
            pin.text.value = name
            self.text_style(pin.text, justify="right")
            pin.spin_style = self.st.SchematicLabelSpinStyle.SLSS_LEFT
            pin.side = self.st.SheetSide.SHS_LEFT
            pin.shape = getattr(
                self.st.SchematicLabelShape,
                "SLSH_"
                + {"bidirectional": "BIDI", "tri_state": "TRISTATE"}.get(
                    kind, kind.upper()
                ),
            )
            pins.append(pin)
        item.pins = pins
        used = {s.page_number for s in self.w.k.get_schematic().get_hierarchy()}
        used.update(
            i.page_number
            for i in self.items.values()
            if isinstance(i, self.st.SheetSymbol)
        )
        page = 1
        while str(page) in used:
            page += 1
        item.page_number = str(page)
        self.aliases[item.id.value] = (self.instance, target.as_posix(), v["filename"])
        self.put(item, create=True)
        return {**self.sheet_reply(item), "created_ids": [item.id.value]}

    def edit(self, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch only primitive edits; the native protocol is private."""
        if self.commit is None or args["instance"] != self.instance:
            raise ValueError("No native transaction for this sheet")
        self.ordinal = args["index"]
        name, v = args["operation"], args["values"]
        if name in (
            "place",
            "move",
            "rotate",
            "mirror",
            "set_field",
            "remove_field",
            "move_field",
            "label",
            "graphic",
            "add_sheet",
        ):
            return cast(dict[str, Any], getattr(self, name)(v))
        if name in ("wire", "junction", "no_connect"):
            return self.connection(name, v)
        if name in ("text", "update_text"):
            return self.note(name, v)
        if name == "remove":
            self.delete([self.symbol(v["ref"], v["unit"])])
            return {}
        if name in ("remove_wire", "move_wire"):
            found = self.wires(v)
            if name == "remove_wire":
                return {"value": self.delete(found)}
            for item in found:
                offset = self.raw_point(v["dx"], v["dy"])
                item.start = self.point(*(self.xy(item.start + offset)))
                item.end = self.point(*(self.xy(item.end + offset)))
                self.put(item)
            return {"value": len(found)}
        if name in (
            "remove_label",
            "remove_junction",
            "remove_no_connect",
            "move_label",
            "rotate_label",
        ):
            kinds = (
                self.label_types()
                if "label" in name
                else (self.st.Junction,)
                if "junction" in name
                else (self.st.NoConnectMarker,)
            )
            found = self.at_point(kinds, v)
            if name.startswith("remove"):
                return {"value": self.delete(found)}
            for item in found:
                if name == "move_label":
                    item.position = self.point(
                        *(self.xy(item.position + self.raw_point(v["dx"], v["dy"])))
                    )
                else:
                    item.spin_style = self.spin(
                        v["rotation"],
                        self.label_reply(item)["justify"],
                        local=isinstance(item, self.st.LocalLabel),
                    )
                self.put(item)
            return {"value": len(found)}
        if name in ("remove_label_by_id", "move_label_by_id", "rotate_label_by_id"):
            item = self.items.get(v["uuid"])
            if not isinstance(item, self.label_types()):
                raise LookupError(f"no label with uuid {v['uuid']!r}")
            if name.startswith("remove"):
                self.delete([item])
                return {}
            if name.startswith("move"):
                item.position = self.point(
                    *(self.xy(item.position + self.raw_point(v["dx"], v["dy"])))
                )
            else:
                item.spin_style = self.spin(
                    v["rotation"],
                    self.label_reply(item)["justify"],
                    local=isinstance(item, self.st.LocalLabel),
                )
            self.put(item)
            return self.label_reply(item)
        if name in ("remove_text", "remove_graphic", "move_graphic"):
            item = self.item(
                v["uuid"],
                self.st.SchematicText
                if name == "remove_text"
                else self.st.SchematicGraphicShape,
            )
            if name.startswith("remove"):
                self.delete([item])
                return {}
            shape = item.shape
            for node in shape.polygons[0].outline.nodes:
                node.point = self.point(
                    *(self.xy(node.point + self.raw_point(v["dx"], v["dy"])))
                )
            item.shape = shape
            self.put(item)
            return self.graphic_reply(item)
        if name in ("move_sheet", "remove_sheet"):
            item = self.sheet(v["name"])
            if name.startswith("remove"):
                self.delete([item])
                return {}
            target = self.point(v["x"], v["y"])
            delta = target - item.position
            for field in (item.name_field, item.filename_field, *item.user_fields):
                field.text.position = field.text.position + delta
            for pin in item.pins:
                pin.position = pin.position + delta
            item.position = target
            self.put(item)
            return self.sheet_reply(item)
        raise ValueError(f"Unsupported native schematic edit: {name}")

    def observe(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read instance facts, including staged objects within the active batch."""
        if self.commit is None:
            self.items = {
                i.id.value: i for i in self.w.view(args["instance"]).get_items()
            }
        name, v = args["operation"], args["values"]
        if name == "part":
            return {"item": self.data(self.symbol(v["ref"], v["unit"]))}
        if name == "parts":
            return {
                "items": [
                    self.data(i)
                    for i in sorted(self.symbols(), key=lambda i: i.reference)
                ]
            }
        if name == "fields":
            return {"value": self.field_values(self.symbol(v["ref"]))}
        raise ValueError(f"Unsupported native schematic observation: {name}")

    def close(self) -> None:
        """Release only the private unsaved library editor."""
        if self.factory is not None:
            commands = importlib.import_module(
                "kipy.proto.common.commands.project_commands_pb2"
            )
            empty = importlib.import_module("google.protobuf.empty_pb2").Empty
            try:
                self.factory._client.send(commands.CloseAllDocuments(force=True), empty)
            finally:
                self.factory.close()
