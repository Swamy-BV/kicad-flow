"""Direct native PCB operations in the isolated official KiCad SDK interpreter.

This worker constructs protobuf-backed objects. It has no board parser, file
writer, pcbnew dependency, or schematic codec. Native KiCad owns the document.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any


class NativeWriteError(ValueError):
    """Retain the exact caller item when a staged native write is refused."""

    def __init__(self, message: str, index: int) -> None:
        """Carry the native refusal's source index through the private protocol."""
        super().__init__(message)
        self.index = index


class Worker:
    """Own one PCB connection and its native transaction."""

    def __init__(self) -> None:
        """Load the official bindings only in the selected SDK interpreter."""
        self.api: Any = importlib.import_module("kipy")
        self.bt: Any = importlib.import_module("kipy.board_types")
        self.geo: Any = importlib.import_module("kipy.geometry")
        self.ct: Any = importlib.import_module("kipy.proto.common.types")
        self.cmd: Any = importlib.import_module(
            "kipy.proto.common.commands.editor_commands_pb2"
        )
        self.bc: Any = importlib.import_module("kipy.proto.board.board_commands_pb2")
        self.json: Any = importlib.import_module("google.protobuf.json_format")
        self.empty: Any = importlib.import_module("google.protobuf.empty_pb2").Empty
        self.k: Any = None
        self.b: Any = None
        self.commit: Any = None
        self.gui = False
        self.borrowed = False
        self.pending: dict[str, Any] = {}
        self.created: dict[str, Any] = {}
        self.updated: dict[str, Any] = {}
        self.deleted: set[str] = set()
        self.footprint_cache: dict[str, Any] | None = None
        self.sources: dict[str, int] = {}
        self.ordinal = 0
        self.expected = ""
        self.factory: Any = None
        self.templates: dict[tuple[str, str, float], Any] = {}
        self.root = Path()
        self.cli = ""

    def point(self, x: float, y: float) -> Any:
        """Convert explicit millimetres to native integer units."""
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite")
        return self.geo.Vector2.from_xy_mm(x, y)

    def layer(self, name: str) -> Any:
        """Resolve a canonical board layer without inferring another layer."""
        value = getattr(self.bt.BoardLayer, "BL_" + name.replace(".", "_"), None)
        if value is None or value not in self.b.get_enabled_layers():
            raise ValueError(f"board has no enabled layer {name!r}")
        return value

    def data(self, item: Any) -> dict[str, Any]:
        """Return actual native object properties, including resolved geometry."""
        result: dict[str, Any] = self.json.MessageToDict(
            item.proto, preserving_proto_field_name=True
        )
        return result

    def open(self, args: dict[str, Any]) -> dict[str, Any]:
        """Open an owned headless board or attach to the selected GUI board."""
        path = Path(args["path"]).resolve()
        self.root, self.cli = path, args["cli"]
        self.gui = args["mode"] == "gui"
        self.borrowed = bool(args.get("connection"))
        if self.borrowed:
            self.k = self.api.KiCad(
                **args["connection"], timeout_ms=10000, client_name="kicad-flow-pcb"
            )
        if not self.gui:
            lock = path.with_name(f"~{path.stem}.kicad_pro.lck")
            owned = False
            if lock.is_file() and self.borrowed:
                for getter in (self.k.get_board, self.k.get_schematic):
                    try:
                        project = getter()._doc.project
                    except Exception:
                        # A document handler may not be loaded in this host yet.
                        continue
                    if (
                        Path(project.path).resolve() == path.parent
                        and project.name == path.stem
                    ):
                        owned = True
            if lock.is_file() and not owned:
                raise ValueError("Project is locked; close it or select PCB GUI mode")
            if not args.get("create") and not path.is_file():
                raise FileNotFoundError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        if not self.borrowed:
            self.k = self.api.KiCad(
                headless=not self.gui,
                kicad_cli_path=args["cli"] if not self.gui else None,
                file_path=str(path) if not self.gui and path.is_file() else None,
                timeout_ms=10000,
                client_name="kicad-flow-pcb",
            )
        version = self.k.get_version()
        if not (version.major >= 11 or (version.major == 10 and version.minor == 99)):
            raise ValueError(f"PCB IPC requires KiCad 11; connected to {version}")
        if not self.gui:
            if not path.is_file():
                document = self.k.create_document(
                    str(path), self.ct.DocumentType.DOCTYPE_PCB
                )
            elif self.borrowed:
                document = self.k.open_document(
                    str(path), self.ct.DocumentType.DOCTYPE_PCB
                )
            else:
                document = self.k.get_board().document
            board_type = importlib.import_module("kipy.board").Board
            self.b = board_type(self.k._client, document)
        else:
            self.b = self.k.get_board()
        actual = (Path(self.b.document.project.path) / self.b.name).resolve()
        if self.gui and actual != path:
            raise ValueError(f"Open {path} in the selected KiCad PCB editor first")
        if args.get("create"):
            # Stackup mutation is explicitly unimplemented in this nightly.
            # Do not pretend that a requested thickness was accepted.
            if args.get("thickness", 1.6) != 1.6:
                raise ValueError(
                    "Native UpdateBoardStackup is not implemented in this KiCad; "
                    "creating a non-default thickness is unavailable through IPC"
                )
            self.begin({})
            try:
                self.delete([item.id.value for item in self.b.get_items()])
                self.end({"commit": True})
            except Exception:
                self.end({"commit": False})
                raise
            self.set_layers({"count": args.get("layers", 2)})
        return self.snapshot({})

    def snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        """Observe the document for existing geometry inspection helpers."""
        return {"contents": self.b.get_as_string()}

    def begin(self, args: dict[str, Any]) -> dict[str, Any]:
        """Start one caller batch; no edit is committed independently."""
        if self.commit is not None:
            raise ValueError("A PCB transaction is already open")
        self.commit = self.b.begin_commit()
        self.pending = {}
        self.created = {}
        self.updated = {}
        self.deleted = set()
        self.footprint_cache = {f.id.value: f for f in self.b.get_footprints()}
        self.sources = {}
        self.expected = self.b.get_as_string()
        return {}

    def end(self, args: dict[str, Any]) -> dict[str, Any]:
        """Push or drop the batch and discard its staged object cache."""
        if self.commit is not None:
            commit = self.commit
            if args["commit"]:
                try:
                    self.flush(list(self.created.values()), create=True)
                    self.flush(list(self.updated.values()), create=False)
                    self.b.push_commit(commit, "KiCadFlow PCB edit")
                except Exception:
                    self.b.drop_commit(commit)
                    raise
                finally:
                    self.commit = None
            else:
                self.b.drop_commit(commit)
                self.commit = None
        self.pending = {}
        self.created = {}
        self.updated = {}
        self.deleted = set()
        self.footprint_cache = None
        return {}

    def write(self, item: Any, *, create: bool, flush: bool = False) -> Any:
        """Validate every per-item native status, including successful envelopes."""
        if create and not flush:
            if not item.id.value:
                item.id.value = str(uuid.uuid4())
            self.created[item.id.value] = item
            self.pending[item.id.value] = item
            self.sources[item.id.value] = self.ordinal
            return item
        if not create and not flush:
            target = self.created if item.id.value in self.created else self.updated
            target[item.id.value] = item
            self.pending[item.id.value] = item
            self.sources[item.id.value] = self.ordinal
            return item
        name = "CreateItems" if create else "UpdateItems"
        self.assert_current()
        command = getattr(self.cmd, name)()
        command.header.document.CopyFrom(self.b.document)
        command.items.add().Pack(item.proto)
        result = self.b.client.send(command, getattr(self.cmd, name + "Response"))
        rows = result.created_items if create else result.updated_items
        if result.status != self.ct.IRS_OK or len(rows) != 1:
            raise ValueError(f"KiCad returned an incomplete {name} response")
        if rows[0].status.code != self.cmd.ISC_OK:
            raise NativeWriteError(
                f"KiCad {name}: {rows[0].status.error_message}",
                self.sources.get(item.id.value, self.ordinal),
            )
        made = self.b._item_from_message(rows[0].item)
        if item.id.value and made.id.value != item.id.value:
            raise ValueError(f"KiCad changed the identity during {name}")
        self.pending[made.id.value] = made
        self.expected = self.b.get_as_string()
        return made

    def assert_current(self) -> None:
        """Refuse native editor changes made during the caller's staged batch."""
        if self.commit is not None and self.b.get_as_string() != self.expected:
            raise ValueError(
                "The KiCad editor changed during this batch; inspect and retry"
            )

    def flush(self, items: list[Any], *, create: bool) -> None:
        """Send staged native objects together and validate every item status."""
        if not items:
            return
        self.assert_current()
        name = "CreateItems" if create else "UpdateItems"
        command = getattr(self.cmd, name)()
        command.header.document.CopyFrom(self.b.document)
        for item in items:
            command.items.add().Pack(item.proto)
        result = self.b.client.send(command, getattr(self.cmd, name + "Response"))
        rows = result.created_items if create else result.updated_items
        if result.status != self.ct.IRS_OK or len(rows) != len(items):
            raise ValueError(f"KiCad returned an incomplete {name} response")
        for item, row in zip(items, rows, strict=True):
            if row.status.code != self.cmd.ISC_OK:
                raise NativeWriteError(
                    f"KiCad {name}: {row.status.error_message}",
                    self.sources[item.id.value],
                )
            made = self.b._item_from_message(row.item)
            if made.id.value != item.id.value:
                raise ValueError(f"KiCad changed the identity during {name}")
            self.pending[made.id.value] = made
        self.expected = self.b.get_as_string()

    def delete(self, identities: list[str]) -> None:
        """Check each deletion instead of trusting an overall success status."""
        remaining = []
        for identity in identities:
            if identity in self.created:
                self.created.pop(identity)
                self.pending.pop(identity, None)
            else:
                remaining.append(identity)
            self.updated.pop(identity, None)
            self.deleted.add(identity)
        identities = remaining
        if not identities:
            return
        self.assert_current()
        command = self.cmd.DeleteItems()
        command.header.document.CopyFrom(self.b.document)
        command.item_ids.extend(self.ct.KIID(value=i) for i in identities)
        result = self.b.client.send(command, self.cmd.DeleteItemsResponse)
        deleted = {
            row.id.value
            for row in result.deleted_items
            if row.status == self.cmd.IDS_OK
        }
        if result.status != self.ct.IRS_OK or deleted != set(identities):
            raise ValueError("KiCad refused a PCB item deletion")
        self.expected = self.b.get_as_string()
        for identity in identities:
            self.pending.pop(identity, None)

    def item(self, identity: str, kind: type[Any]) -> Any:
        """Find exactly the requested native object, including staged updates."""
        item = self.pending.get(identity)
        if item is None:
            rows = self.b.get_items_by_id(self.ct.KIID(value=identity))
            item = next(iter(rows), None)
        if not isinstance(item, kind):
            raise LookupError(f"no {kind.__name__} with UUID {identity}")
        return item

    def footprint(self, ref: str) -> Any:
        """Find the exact reference in the current or staged native board."""
        base = self.footprint_cache
        if base is None:
            base = {f.id.value: f for f in self.b.get_footprints()}
        items = {i: f for i, f in base.items() if i not in self.deleted}
        items.update(
            (i, f)
            for i, f in self.pending.items()
            if isinstance(f, self.bt.FootprintInstance)
        )
        matches = [f for f in items.values() if f.reference_field.text.value == ref]
        if len(matches) != 1:
            raise LookupError(f"expected one footprint {ref!r}; found {len(matches)}")
        return matches[0]

    def template(self, fp_id: str, side: str, rotation: float) -> Any:
        """Instantiate library geometry with native IPC in an unsaved board.

        PlaceFootprintFromLibrary cannot take Reference/Value, and new staged
        items are not addressable by UpdateItems. A private in-memory native
        document supplies the correctly flipped library object before one
        CreateItems call adds it to the actual board. No file codec is used.
        """
        key = fp_id, side, rotation % 360
        if key not in self.templates:
            if self.factory is None:
                self.factory = self.api.KiCad(
                    headless=True, kicad_cli_path=self.cli, timeout_ms=10000
                )
                self.factory.create_document(
                    str(
                        self.root.with_name(f".native-library-{uuid.uuid4()}.kicad_pcb")
                    ),
                    self.ct.DocumentType.DOCTYPE_PCB,
                )
                kind = self.ct.LibraryType.LT_FOOTPRINT
                self.factory.load_all_libraries(kind)
                self.factory.wait_for_libraries(kind, timeout_s=45)
            board = self.factory.get_board()
            template = board.place_footprint_from_library(
                fp_id,
                self.point(0, 0),
                orientation=self.geo.Angle.from_degrees(
                    -rotation if side == "B" else rotation
                ),
                layer=self.layer(side + ".Cu"),
            )
            self.templates[key] = template
        made = self.bt.FootprintInstance(self.templates[key].proto)
        identities: dict[str, str] = {}

        def refresh(message: Any) -> None:
            if message.DESCRIPTOR.full_name == "kiapi.common.types.KIID":
                if message.value:
                    message.value = identities.setdefault(
                        message.value, str(uuid.uuid4())
                    )
                return
            for descriptor, value in message.ListFields():
                if descriptor.type != descriptor.TYPE_MESSAGE:
                    continue
                if descriptor.label == descriptor.LABEL_REPEATED:
                    for child in value:
                        refresh(child)
                elif descriptor.message_type.full_name == "google.protobuf.Any":
                    # Packed footprint children are refreshed below through
                    # their official wrappers, before Footprint.proto packs them.
                    continue
                else:
                    refresh(value)

        refresh(made.proto)
        for child in made.definition.items:
            refresh(child.proto)
        return made

    def edit(self, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch the bounded internal primitive protocol."""
        name, values = args["operation"], args["values"]
        self.ordinal = args.get("index", 0)
        if name not in {
            "place",
            "move",
            "rotate",
            "flip",
            "remove",
            "set_field",
            "move_field",
            "set_net",
            "track",
            "via",
            "zone",
            "graphic",
            "move_graphic",
            "remove_graphic",
            "text",
            "update_text",
            "remove_text",
            "remove_copper",
        }:
            raise ValueError(f"Unknown native PCB primitive {name}")
        result: dict[str, Any] = getattr(self, name)(values)
        return result

    def footprints(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read native and staged footprint objects without a file snapshot."""
        if args.get("ref"):
            return {"items": [self.data(self.footprint(args["ref"]))]}
        base = self.footprint_cache
        if base is None:
            base = {f.id.value: f for f in self.b.get_footprints()}
        items = {i: f for i, f in base.items() if i not in self.deleted}
        items.update(
            (i, f)
            for i, f in self.pending.items()
            if isinstance(f, self.bt.FootprintInstance)
        )
        return {"items": [self.data(f) for f in items.values()]}

    def place(self, v: dict[str, Any]) -> dict[str, Any]:
        """Place from a native library, then set explicit caller fields."""
        refs = [
            f.reference_field.text.value
            for f in self.b.get_footprints()
            if f.id.value not in self.deleted
        ]
        refs += [
            f.reference_field.text.value
            for f in self.pending.values()
            if isinstance(f, self.bt.FootprintInstance)
        ]
        if v["ref"] in refs:
            raise ValueError(f"{v['ref']} is already on the board")
        if v["side"] not in ("F", "B"):
            raise ValueError("side must be F or B")
        made = self.template(v["fp_id"], v["side"], v["rotation"])
        made.position = self.point(v["x"], v["y"])
        made.reference_field.text.value = v["ref"]
        made.value_field.text.value = v["value"] or v["fp_id"].split(":")[-1]
        return {"item": self.data(self.write(made, create=True))}

    def move(self, v: dict[str, Any]) -> dict[str, Any]:
        """Move all native footprint children with their parent."""
        item = self.footprint(v["ref"])
        item.position = self.point(v["x"], v["y"])
        return {"item": self.data(self.write(item, create=False))}

    def rotated(self, item: Any, angle: float) -> Any:
        """Retain exact integer geometry on quarter-turn native SDK rotations.

        The SDK rotates points with floating arithmetic and truncates to int.
        Exact quarter turns have integer results; avoid a one-nanometre drift
        at those angles without changing any caller coordinate or design choice.
        """
        before = self.bt.FootprintInstance(item.proto)
        delta = angle - before.orientation.degrees
        item.orientation = self.geo.Angle.from_degrees(angle)
        if abs(delta / 90 - round(delta / 90)) > 1e-8:
            return item
        quarter = round(delta / 90) % 4
        centre = before.position
        registry = importlib.import_module("google.protobuf.symbol_database").Default()

        def correct(old: Any, new: Any) -> None:
            name = old.DESCRIPTOR.full_name
            if name != new.DESCRIPTOR.full_name:
                return
            if name == "kiapi.common.types.Vector2":
                if old == new:
                    return
                dx, dy = old.x_nm - centre.x, old.y_nm - centre.y
                x, y = ((dx, dy), (dy, -dx), (-dx, -dy), (-dy, dx))[quarter]
                new.x_nm, new.y_nm = centre.x + x, centre.y + y
            elif name == "google.protobuf.Any":
                if old.type_url != new.type_url:
                    return
                kind = registry.GetSymbol(old.type_url.rsplit("/", 1)[-1])
                a, b = kind(), kind()
                old.Unpack(a)
                new.Unpack(b)
                correct(a, b)
                new.Pack(b)
            else:
                for descriptor, value in old.ListFields():
                    if descriptor.type != descriptor.TYPE_MESSAGE:
                        continue
                    target = getattr(new, descriptor.name)
                    if descriptor.label == descriptor.LABEL_REPEATED:
                        for a, b in zip(value, target, strict=True):
                            correct(a, b)
                    else:
                        correct(value, target)

        proto = item.proto
        correct(before.proto, proto)
        return self.bt.FootprintInstance(proto)

    def rotate(self, v: dict[str, Any]) -> dict[str, Any]:
        """Use SDK rotation on native geometry and fields."""
        item = self.footprint(v["ref"])
        angle = v["rotation"] + (180 if item.layer == self.bt.BoardLayer.BL_B_Cu else 0)
        item = self.rotated(item, angle)
        return {"item": self.data(self.write(item, create=False))}

    def flip(self, v: dict[str, Any]) -> dict[str, Any]:
        """Let KiCad mirror the footprint, pads, fields and model transform."""
        if v["side"] not in ("F", "B"):
            raise ValueError("side must be F or B")
        item = self.footprint(v["ref"])
        if item.layer != self.layer(v["side"] + ".Cu"):
            self.assert_current()
            angle = item.orientation.degrees
            flipped = self.b.flip_items(item, self.bc.BFD_TOP_BOTTOM)
            self.expected = self.b.get_as_string()
            if len(flipped) != 1 or flipped[0].id != item.id:
                raise ValueError("KiCad refused the footprint flip")
            item = flipped[0]
            item = self.rotated(item, angle + 180)
            item = self.write(item, create=False)
        return {"item": self.data(item)}

    def remove(self, v: dict[str, Any]) -> dict[str, Any]:
        """Delete a footprint by the native reference lookup."""
        self.delete([self.footprint(v["ref"]).id.value])
        return {}

    def field(self, item: Any, name: str, *, create: bool = False) -> Any:
        """Find a named native field, creating only when explicitly requested."""
        for field in item.texts_and_fields:
            if isinstance(field, self.bt.Field) and field.name == name:
                return field
        if not create:
            raise LookupError(f"footprint has no field {name!r}")
        field = self.bt.Field()
        field.name = name
        field.text.layer = self.layer("F.Fab")
        field.text.position = item.position
        field.text.attributes.visible = False
        item.definition.items = [*item.definition.items, field]
        return item.definition.items[-1]

    def set_field(self, v: dict[str, Any]) -> dict[str, Any]:
        """Set one field through its native footprint object."""
        item = self.footprint(v["ref"])
        self.field(item, v["name"], create=True).text.value = v["value"]
        made = self.write(item, create=False)
        return {
            "fields": {
                f.name: f.text.value
                for f in made.texts_and_fields
                if isinstance(f, self.bt.Field)
            }
        }

    def move_field(self, v: dict[str, Any]) -> dict[str, Any]:
        """Translate explicit local offsets into actual native board coordinates."""
        item = self.footprint(v["ref"])
        field = self.field(item, v["name"])
        delta = self.point(v["dx"], v["dy"]).rotate(item.orientation, self.point(0, 0))
        field.text.position = item.position + delta
        if v["rotation"] is not None:
            field.text.attributes.angle = v["rotation"]
        if v["layer"]:
            field.text.layer = self.layer(v["layer"])
        if v["hide"] is not None:
            field.text.attributes.visible = not v["hide"]
            field.visible = not v["hide"]
        self.write(item, create=False)
        return {"point": self.data(field.text)["text"]["position"]}

    def set_net(self, v: dict[str, Any]) -> dict[str, Any]:
        """Assign every physical pad with the supplied logical pad number."""
        item = self.footprint(v["ref"])
        found = [p for p in item.definition.pads if p.number == v["pad"]]
        if not found:
            raise LookupError(f"{v['ref']} has no pad {v['pad']}")
        for pad in found:
            pad.net = self.bt.Net(name=v["net"])
        self.write(item, create=False)
        return {"net": v["net"]}

    def track(self, v: dict[str, Any]) -> dict[str, Any]:
        """Construct one native straight copper segment."""
        if not math.isfinite(v["width"]) or v["width"] <= 0:
            raise ValueError("track width must be finite and positive")
        item = self.bt.Track()
        item.start = self.point(v["x1"], v["y1"])
        item.end = self.point(v["x2"], v["y2"])
        if item.start == item.end:
            raise ValueError("a track must have two different endpoints")
        if not v["layer"].endswith(".Cu"):
            raise ValueError("tracks need a copper layer")
        item.layer = self.layer(v["layer"])
        item.width = round(v["width"] * 1e6)
        item.net = self.bt.Net(name=v["net"])
        return {"item": self.data(self.write(item, create=True))}

    def via(self, v: dict[str, Any]) -> dict[str, Any]:
        """Construct a native via with an explicit drill and layer span."""
        kinds = {
            "through": "VT_THROUGH",
            "blind_buried": "VT_BLIND_BURIED",
            "microvia": "VT_MICRO",
        }
        if v["kind"] not in kinds:
            raise ValueError("unknown via kind")
        layers = v["layers"]
        enabled = [
            self.bt.BoardLayer.BL_F_Cu,
            *(
                getattr(self.bt.BoardLayer, f"BL_In{i}_Cu")
                for i in range(1, self.b.get_copper_layer_count() - 1)
            ),
            self.bt.BoardLayer.BL_B_Cu,
        ]
        span = [self.layer(name) for name in layers]
        if len(span) != 2 or span[0] == span[1]:
            raise ValueError("via needs two different copper layers")
        if any(layer not in enabled for layer in span):
            raise ValueError("via layers must be copper layers")
        if v["kind"] == "through" and set(span) != {enabled[0], enabled[-1]}:
            raise ValueError("a through via must span F.Cu to B.Cu")
        if (
            v["kind"] == "microvia"
            and abs(enabled.index(span[0]) - enabled.index(span[1])) != 1
        ):
            raise ValueError("a microvia must span adjacent copper layers")
        if not (0 < v["drill"] < v["diameter"] and math.isfinite(v["diameter"])):
            raise ValueError("via diameter must exceed its positive drill")
        item = self.bt.Via()
        item.position = self.point(v["x"], v["y"])
        item.type = getattr(self.bt.ViaType, kinds[v["kind"]])
        item.diameter = round(v["diameter"] * 1e6)
        item.drill_diameter = round(v["drill"] * 1e6)
        item.padstack.drill.start_layer, item.padstack.drill.end_layer = span
        item.net = self.bt.Net(name=v["net"])
        return {"item": self.data(self.write(item, create=True))}

    def polygon(self, points: list[list[float]]) -> Any:
        """Encode the supplied polygon, without selecting its vertices."""
        if len(points) < 3:
            raise ValueError("a polygon needs at least three vertices")
        polygon = self.geo.PolygonWithHoles()
        for x, y in points:
            polygon.outline.nodes.append(
                self.geo.PolyLineNode.from_point(self.point(x, y))
            )
        polygon.outline.closed = True
        return polygon

    def zone(self, v: dict[str, Any]) -> dict[str, Any]:
        """Construct the caller's native copper zone or rule area."""
        item = self.bt.Zone()
        item.layers = [self.layer(v["layer"])]
        if not v["layer"].endswith(".Cu"):
            raise ValueError("zone needs a copper layer")
        item.outline = self.polygon(v["points"])
        if v["forbids"]:
            item.type = self.bt.ZoneType.ZT_RULE_AREA
            for name in v["forbids"]:
                key = {
                    "tracks": "keepout_tracks",
                    "vias": "keepout_vias",
                    "pads": "keepout_pads",
                    "pours": "keepout_copper",
                    "footprints": "keepout_footprints",
                }.get(name)
                if key is None:
                    raise ValueError(f"unknown rule-area exclusion {name}")
                setattr(item.proto.rule_area_settings, key, True)
        else:
            if (
                min(v["clearance"], v["thermal_gap"]) < 0
                or min(v["min_thickness"], v["thermal_spoke_width"]) <= 0
            ):
                raise ValueError("invalid zone clearance or thickness")
            item.net = self.bt.Net(name=v["net"])
            item.clearance = round(v["clearance"] * 1e6)
            item.min_thickness = round(v["min_thickness"] * 1e6)
            item.priority = v["priority"]
            modes = {"always": "IRM_ALWAYS", "never": "IRM_NEVER", "area": "IRM_AREA"}
            item.island_mode = getattr(
                self.bt.IslandRemovalMode, modes[v["island_removal"]]
            )
            item.min_island_area = round(v["min_island_area"] * 1e12)
            connection = item.proto.copper_settings.connection
            names = {"thermal": "ZCS_THERMAL", "solid": "ZCS_FULL", "none": "ZCS_NONE"}
            connection.zone_connection = getattr(
                self.bt.ZoneConnectionStyle, names[v["pad_connection"]]
            )
            connection.thermal_spokes.gap.value_nm = round(v["thermal_gap"] * 1e6)
            connection.thermal_spokes.width.value_nm = round(
                v["thermal_spoke_width"] * 1e6
            )
        return {"item": self.data(self.write(item, create=True))}

    def graphic(self, v: dict[str, Any]) -> dict[str, Any]:
        """Construct a native line, arc, circle, rectangle or polygon."""
        classes = {
            "line": "BoardSegment",
            "arc": "BoardArc",
            "circle": "BoardCircle",
            "rectangle": "BoardRectangle",
            "polygon": "BoardPolygon",
        }
        kind = v["kind"]
        if kind not in classes or v["layer"] not in ("Edge.Cuts", "F.SilkS", "B.SilkS"):
            raise ValueError("unsupported graphic kind or layer")
        if v["fill"] and (
            kind not in ("circle", "rectangle", "polygon") or v["layer"] == "Edge.Cuts"
        ):
            raise ValueError("this graphic cannot be filled")
        pts = [self.point(*p) for p in v["points"]]
        if kind != "polygon" and len(pts) != (3 if kind == "arc" else 2):
            raise ValueError("incorrect graphic point count")
        item = getattr(self.bt, classes[kind])()
        item.layer = self.layer(v["layer"])
        if v["width"] < 0 or not math.isfinite(v["width"]):
            raise ValueError("graphic width must be finite and non-negative")
        item.attributes.stroke.width = round(v["width"] * 1e6)
        item.attributes.fill.filled = v["fill"]
        if kind in ("line", "arc"):
            item.start, item.end = pts[0], pts[-1]
            if kind == "arc":
                item.mid = pts[1]
                if item.center() is None:
                    raise ValueError("arc points cannot be collinear")
        elif kind == "circle":
            item.center = pts[0]
            item.radius_point = pts[1]
            if item.radius() == 0:
                raise ValueError("circle radius must be positive")
        elif kind == "rectangle":
            item.top_left, item.bottom_right = pts
        else:
            item.polygons.append(self.polygon(v["points"]))
        return {"item": self.data(self.write(item, create=True))}

    def move_graphic(self, v: dict[str, Any]) -> dict[str, Any]:
        """Move a native graphic by its explicit offset."""
        item = self.item(v["uuid"], self.bt.BoardShape)
        item.move(self.point(v["dx"], v["dy"]))
        return {"item": self.data(self.write(item, create=False))}

    def remove_graphic(self, v: dict[str, Any]) -> dict[str, Any]:
        """Delete the specified native graphic."""
        self.item(v["uuid"], self.bt.BoardShape)
        self.delete([v["uuid"]])
        return {}

    def text(self, v: dict[str, Any]) -> dict[str, Any]:
        """Create native literal text with independent dimensions."""
        item = self.bt.BoardText()
        item.position = self.point(v["x"], v["y"])
        self.text_properties(item, v)
        return {"item": self.data(self.write(item, create=True))}

    def text_properties(self, item: Any, v: dict[str, Any]) -> None:
        """Apply only explicitly supplied native text properties."""
        if v.get("text") is not None:
            item.value = v["text"]
        if v.get("layer") is not None:
            item.layer = self.layer(v["layer"])
        attr = item.attributes
        width = v.get("width")
        height = v.get("height")
        for dimension in (width, height, v.get("thickness")):
            if dimension is not None and (
                dimension <= 0 or not math.isfinite(dimension)
            ):
                raise ValueError("text dimensions must be finite and positive")
        attr.size = self.geo.Vector2.from_xy(
            attr.size.x if width is None else round(width * 1e6),
            attr.size.y if height is None else round(height * 1e6),
        )
        if v.get("thickness") is not None:
            attr.stroke_width = round(v["thickness"] * 1e6)
        if v.get("rotation") is not None:
            attr.angle = v["rotation"] % 360
        if v.get("mirror") is not None:
            attr.mirrored = v["mirror"]
        if v.get("justify") is not None:
            align = {"left": "HA_LEFT", "center": "HA_CENTER", "right": "HA_RIGHT"}
            attr.horizontal_alignment = getattr(self.ct, align[v["justify"]])
        if v.get("vertical_justify") is not None:
            align = {"top": "VA_TOP", "center": "VA_CENTER", "bottom": "VA_BOTTOM"}
            attr.vertical_alignment = getattr(self.ct, align[v["vertical_justify"]])
        attr.visible = True

    def update_text(self, v: dict[str, Any]) -> dict[str, Any]:
        """Edit a native text object, retaining all omitted properties."""
        item = self.item(v["uuid"], self.bt.BoardText)
        item.position = self.point(
            item.position.x / 1e6 if v.get("x") is None else v["x"],
            item.position.y / 1e6 if v.get("y") is None else v["y"],
        )
        self.text_properties(item, v)
        return {"item": self.data(self.write(item, create=False))}

    def remove_text(self, v: dict[str, Any]) -> dict[str, Any]:
        """Delete one native text identity."""
        self.item(v["uuid"], self.bt.BoardText)
        self.delete([v["uuid"]])
        return {}

    def remove_copper(self, v: dict[str, Any]) -> dict[str, Any]:
        """Delete only explicitly filtered native copper objects."""
        if not v["uuid"] and not v["net"] and not v["layer"] and not v["all"]:
            raise ValueError(
                "select uuid, net or layer; use all=True for all selected kinds"
            )
        if v["all"] and (v["uuid"] or v["net"] or v["layer"]):
            raise ValueError("all=True cannot be combined with selectors")
        if v["uuid"] and (v["net"] or v["layer"]):
            raise ValueError("uuid selection cannot be combined with net or layer")
        counts: dict[str, int] = {}
        for kind, items in (
            ("tracks", self.b.get_tracks()),
            ("vias", self.b.get_vias()),
            ("zones", self.b.get_zones()),
        ):
            selected = [
                i.id.value
                for i in items
                if (v[kind] or v["uuid"])
                and (not v["net"] or (i.net and i.net.name == v["net"]))
                and (not v["uuid"] or i.id.value == v["uuid"])
                and (
                    not v["layer"]
                    or self.layer(v["layer"])
                    in (
                        i.layers
                        if kind == "zones"
                        else (i.padstack.drill.start_layer, i.padstack.drill.end_layer)
                        if kind == "vias"
                        else (i.layer,)
                    )
                )
            ]
            self.delete(selected)
            counts[kind] = len(selected)
        return {"count": sum(counts.values())}

    def set_layers(self, v: dict[str, Any]) -> dict[str, Any]:
        """Set native copper count while retaining enabled non-copper layers."""
        if v["count"] not in (2, 4, 6, 8):
            raise ValueError("layer count must be 2, 4, 6 or 8")
        layers = self.b.set_enabled_layers(v["count"], self.b.get_enabled_layers())
        return {"layers": [self.bt.BoardLayer.Name(layer) for layer in layers]}

    def refill(self, args: dict[str, Any]) -> dict[str, Any]:
        """Fill using the running native board, without a save/reload cycle."""
        self.b.refill_zones(max_poll_seconds=45)
        return {"count": len(self.b.get_zones())}

    def clear_fills(self, args: dict[str, Any]) -> dict[str, Any]:
        """Discard cached fills in a native candidate before adding copper."""
        self.begin({})
        try:
            for zone in self.b.get_zones():
                zone.proto.filled = False
                del zone.proto.filled_polygons[:]
                self.write(zone, create=False)
            self.end({"commit": True})
        except Exception:
            self.end({"commit": False})
            raise
        return {}

    def bounds(self, args: dict[str, Any]) -> dict[str, Any]:
        """Measure actual native text geometry."""
        found = {
            i.id.value: i for i in self.b.get_text() if isinstance(i, self.bt.BoardText)
        }
        wanted = args["uuids"] or list(found)
        out = []
        for identity in wanted:
            if identity not in found:
                raise LookupError(f"no board text {identity}")
            box = self.b.get_item_bounding_box(found[identity], include_text=True)
            if box is None:
                raise ValueError(f"KiCad did not measure text {identity}")
            out.append(
                {
                    "uuid": identity,
                    "x": box.pos.x / 1e6,
                    "y": box.pos.y / 1e6,
                    "width": box.size.x / 1e6,
                    "height": box.size.y / 1e6,
                }
            )
        return {"bounds": out}

    def connectivity(self, args: dict[str, Any]) -> dict[str, Any]:
        """Observe native connected item identities for every named pad."""
        groups: list[dict[str, Any]] = []
        indexes: dict[tuple[str, tuple[str, ...]], int] = {}
        pads = {}
        for part in self.b.get_footprints():
            for pad in part.definition.pads:
                if not pad.net.name:
                    continue
                members = tuple(
                    sorted(
                        {
                            pad.id.value,
                            *(i.id.value for i in self.b.get_connected_items(pad)),
                        }
                    )
                )
                key = pad.net.name, members
                if key not in indexes:
                    indexes[key] = len(groups)
                    groups.append({"net": pad.net.name, "uuids": members})
                pads[pad.id.value] = indexes[key]
        return {"groups": groups, "pads": pads}

    def point_connectivity(self, args: dict[str, Any]) -> dict[str, Any]:
        """Observe native connected components among explicit item identities."""
        wanted = set(args["uuids"])
        known = set()
        groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for identity in wanted:
            items = self.b.get_items_by_id(self.ct.KIID(value=identity))
            if not items:
                continue
            item = items[0]
            known.add(identity)
            members = tuple(
                sorted(
                    {identity, *(i.id.value for i in self.b.get_connected_items(item))}
                )
            )
            net = item.net.name if item.net else ""
            groups.setdefault((net, members), []).append(identity)
        return {
            "groups": [{"net": net, "uuids": ids} for (net, _), ids in groups.items()],
            "missing": sorted(wanted - known),
        }

    def settings(self, args: dict[str, Any]) -> dict[str, Any]:
        """Delegate native settings without importing the MCP environment."""
        module = importlib.import_module("ipc_settings")
        result: dict[str, Any] = module.settings(self, args)
        return result

    def net_policy(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read native effective classes; leave unavailable field owners unknown."""
        classes = self.b.get_netclass_for_nets(
            [self.bt.Net(name=n) for n in args["nets"]]
        )
        rows = []
        dimensions = {
            "clearance": "clearance",
            "track_width": "track_width",
            "diff_pair_width": "diff_pair_track_width",
            "diff_pair_gap": "diff_pair_gap",
            "diff_pair_via_gap": "diff_pair_via_gap",
        }
        for net, item in classes.items():
            values = {
                name: getattr(item.proto.board, key).value_nm / 1e6
                if item.proto.board.HasField(key)
                else None
                for name, key in dimensions.items()
            }
            stack = item.proto.board.via_stack
            values["via_diameter"] = (
                stack.copper_layers[0].size.x_nm / 1e6 if stack.copper_layers else None
            )
            values["via_drill"] = (
                stack.drill.diameter.x_nm / 1e6 if stack.HasField("drill") else None
            )
            colors = {}
            for key, holder in (
                ("pcb_color", item.proto.board),
                ("schematic_color", item.proto.schematic),
            ):
                c = holder.color
                colors[key] = (
                    ""
                    if c.a == 0
                    else "#" + "".join(f"{round(v * 255):02X}" for v in (c.r, c.g, c.b))
                )
            rows.append(
                {
                    "net": net,
                    "effective_class": item.name,
                    "dimensions": values,
                    "sources": dict.fromkeys(values),
                    "class_colors": colors,
                }
            )
        return {
            "nets": rows,
            "units": "mm",
            "scope": (
                "netclass preferences; custom and per-object DRC rules may override"
            ),
            "custom_rule_count": len(self.b.get_custom_design_rules().rules),
            "readback_warnings": [
                "Native IPC does not expose per-field netclass ownership"
            ],
        }

    def save(self, args: dict[str, Any]) -> dict[str, Any]:
        """Save the actual board or a private copy through native IPC."""
        if args.get("copy"):
            self.b.save_as(args["copy"], overwrite=False, include_project=True)
        else:
            self.b.save()
        return self.snapshot({})

    def close(self) -> None:
        """Drop unfinished edits and release only worker-owned documents."""
        if self.factory is not None:
            commands = importlib.import_module(
                "kipy.proto.common.commands.project_commands_pb2"
            )
            self.factory._client.send(
                commands.CloseAllDocuments(force=True), self.empty
            )
            self.factory.close()
        if self.k is not None:
            try:
                self.end({"commit": False})
                if self.borrowed and self.b is not None:
                    self.k.close_document(self.b.document)
                elif not self.gui:
                    commands = importlib.import_module(
                        "kipy.proto.common.commands.project_commands_pb2"
                    )
                    self.k._client.send(
                        commands.CloseAllDocuments(force=True), self.empty
                    )
            finally:
                self.k.close()


def main() -> None:
    """Serve a bounded private protocol, with no public raw-IPC escape hatch."""
    worker: Worker | None = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if worker is None:
                    worker = Worker()
                method = request["method"]
                if method not in {
                    "open",
                    "snapshot",
                    "begin",
                    "end",
                    "edit",
                    "set_layers",
                    "refill",
                    "clear_fills",
                    "bounds",
                    "connectivity",
                    "point_connectivity",
                    "footprints",
                    "settings",
                    "net_policy",
                    "save",
                }:
                    raise ValueError("Unknown PCB IPC operation")
                result = {"ok": True, "data": getattr(worker, method)(request["args"])}
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                if isinstance(exc, NativeWriteError):
                    result["index"] = exc.index
            sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    finally:
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    main()
