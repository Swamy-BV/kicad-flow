"""Private JSON-lines worker for the official KiCad 11 IPC bindings.

This runs in the configured IPC Python, keeping protobuf/NNG dependencies out
of the MCP process. Project edits stage native objects in checked transactions.
Native KiCad alone saves project documents; this worker has no conversion codec.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast


def _close_documents(connection: Any) -> None:
    """Release owned headless documents and locks before stopping the server."""
    commands = importlib.import_module(
        "kipy.proto.common.commands.project_commands_pb2"
    )
    empty = importlib.import_module("google.protobuf.empty_pb2").Empty
    connection._client.send(commands.CloseAllDocuments(force=True), empty)


class NativeItemError(ValueError):
    """A native per-item refusal, retaining the identity the caller supplied."""

    def __init__(self, message: str, item_id: str) -> None:
        """Keep the exact refused object for source-index reporting."""
        super().__init__(message)
        self.item_id = item_id


class Worker:
    """Own one KiCad connection and serialize requests to its editor."""

    def __init__(self) -> None:
        """Initialize the private IPC connection state."""
        self.k: Any = None
        self.root = Path()
        self.cli = ""
        self.edits: Any = None
        self.gui = False
        self.borrowed = False
        # Native creation needs a resolved path in affected nightlies. Retain
        # the caller's spelling until native save canonicalizes the reference.
        self.filenames: dict[str, tuple[str, str, str]] = {}
        self.api: Any = importlib.import_module("kipy")
        self.types: Any = importlib.import_module("kipy.proto.common.types")
        self.commands: Any = importlib.import_module(
            "kipy.proto.common.commands.editor_commands_pb2"
        )
        importlib.import_module("kipy.schematic_types")
        self.edits = importlib.import_module("ipc_edits").Edits(self)

    def probe(self, args: dict[str, Any]) -> dict[str, str]:
        """Report the bindings without opening or changing a KiCad document."""
        metadata = importlib.import_module("importlib.metadata")
        return {"bindings": metadata.version("kicad-python")}

    def write_items(self, sch: Any, items: list[Any], *, create: bool) -> None:
        """Check native per-item statuses, including responses echoing refused items."""
        name = "CreateItems" if create else "UpdateItems"
        command = getattr(self.commands, name)()
        command.header.document.CopyFrom(sch._doc)
        packed_type = importlib.import_module("google.protobuf.any_pb2").Any
        for item in items:
            packed = packed_type()
            packed.Pack(item.proto)
            command.items.append(packed)
        result = sch._kicad.send(command, getattr(self.commands, name + "Response"))
        rows = result.created_items if create else result.updated_items
        if result.status != self.types.IRS_OK or len(rows) != len(items):
            raise ValueError(f"KiCad returned an incomplete {name} response")
        for index, row in enumerate(rows):
            if row.status.code != self.commands.ISC_OK:
                raise NativeItemError(
                    f"KiCad {name} item {index}: {row.status.error_message}",
                    items[index].id.value,
                )
            returned = sch._item_from_message(row.item)
            if returned.id.value != items[index].id.value:
                raise ValueError(f"KiCad changed the identity of {name} item {index}")

    def delete(self, sch: Any, identities: list[str]) -> None:
        """Check every deletion status; native overall success is insufficient."""
        command = self.commands.DeleteItems()
        command.header.document.CopyFrom(sch._doc)
        command.item_ids.extend(self.types.KIID(value=i) for i in identities)
        result = sch._kicad.send(command, self.commands.DeleteItemsResponse)
        deleted = {
            item.id.value
            for item in result.deleted_items
            if item.status == self.commands.IDS_OK
        }
        if result.status != self.types.IRS_OK or deleted != set(identities):
            failed = next((i for i in identities if i not in deleted), identities[0])
            raise NativeItemError(
                "KiCad refused to delete: "
                + ", ".join(sorted(set(identities) - deleted)),
                failed,
            )

    def view(self, instance: str = "") -> Any:
        """Find the explicitly requested loaded sheet instance."""
        schematic = self.k.get_schematic()
        if not instance:
            return schematic.for_sheet(schematic.get_hierarchy().root)
        for sheet in schematic.get_hierarchy():
            if str(sheet.path) == instance:
                return schematic.for_sheet(sheet)
        raise ValueError(f"No loaded schematic instance at {instance}")

    def open(self, args: dict[str, Any]) -> dict[str, Any]:
        """Connect to the selected editor or create an owned headless session."""
        self.root = Path(args["path"]).resolve()
        self.cli = args["cli"]
        self.gui = args["mode"] == "gui"
        if self.gui:
            self.k = self.api.KiCad(timeout_ms=10000, client_name="kicad-flow")
            sch = self.k.get_schematic()
            project = sch._doc.project
            filename = sch.get_hierarchy().root.filename
            actual = (Path(project.path) / filename).resolve()
            if actual != self.root:
                raise ValueError(f"Open {self.root} in the selected KiCad editor first")
        else:
            if not self.root.is_file() and not args.get("create"):
                raise FileNotFoundError(self.root)
            if args.get("create"):
                self.root.parent.mkdir(parents=True, exist_ok=True)
            lock = self.root.with_name(f"~{self.root.stem}.kicad_pro.lck")
            self.borrowed = bool(args.get("connection"))
            if self.borrowed:
                self.k = self.api.KiCad(**args["connection"], timeout_ms=10000)
            owned = False
            if lock.is_file() and self.borrowed:
                for getter in (self.k.get_board, self.k.get_schematic):
                    try:
                        project = getter()._doc.project
                    except Exception:
                        # A document handler may not be loaded in this host yet.
                        continue
                    if (
                        Path(project.path).resolve() == self.root.parent
                        and project.name == self.root.stem
                    ):
                        owned = True
            if lock.is_file() and not owned:
                raise ValueError(
                    "The project has a KiCad lock. Close its editor or select "
                    "KICAD_FLOW_SCHEMATIC_MODE=gui; headless IPC will not bypass it."
                )
            if self.borrowed:
                if self.root.is_file():
                    self.k.open_document(
                        str(self.root), self.types.DocumentType.DOCTYPE_SCHEMATIC
                    )
            else:
                self.k = self.api.KiCad(
                    headless=True,
                    kicad_cli_path=args["cli"],
                    timeout_ms=10000,
                    file_path=str(self.root) if self.root.is_file() else None,
                )
        version = self.k.get_version()
        if not (version.major >= 11 or (version.major == 10 and version.minor == 99)):
            raise ValueError(f"Schematic IPC requires KiCad 11; connected to {version}")
        if not self.gui and not self.root.is_file():
            self.k.create_document(
                str(self.root), self.types.DocumentType.DOCTYPE_SCHEMATIC
            )
        if args.get("create"):
            self.initialize(args)
        return self.snapshot({})

    def initialize(self, args: dict[str, Any]) -> dict[str, Any]:
        """Reset an explicitly requested new sheet and set its metadata."""
        sch = self.view(args.get("instance", ""))
        items = sch.get_items()
        if items:
            transaction = sch.begin_commit()
            try:
                self.delete(sch, [item.id.value for item in items])
                sch.push_commit(transaction, "New schematic")
            except Exception:
                sch.drop_commit(transaction)
                raise
        page = sch.get_page_settings()
        page.page_size = getattr(self.types.PageSize, "PS_" + args.get("paper", "A4"))
        sch.set_page_settings(page)
        title = sch.get_title_block()
        title.title = args.get("title", "")
        sch.set_title_block(title)
        return self.snapshot(args)

    def snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read the current native sheet and its hierarchy identity."""
        sch = self.view(args.get("instance", ""))
        instance = "/" + "/".join(item.value for item in sch._doc.sheet_path.path)
        return {
            "contents": sch.get_as_string(),
            "instance": instance,
            "filenames": {
                identity: {"absolute": absolute, "relative": relative}
                for identity, (parent, absolute, relative) in self.filenames.items()
                if parent == instance
            },
        }

    def hierarchy(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read filenames, instances and unsaved contents from KiCad."""
        sch = self.k.get_schematic()
        found: list[dict[str, Any]] = []

        def visit(sheet: Any, parent: Path, *, root: bool = False) -> None:
            file = Path(sheet.filename)
            file = (
                self.root
                if root
                else (
                    (parent / file).resolve()
                    if not file.is_absolute()
                    else file.resolve()
                )
            )
            view = sch.for_sheet(sheet)
            found.append(
                {
                    "path": str(file),
                    "instance": str(sheet.path),
                    "contents": view.get_as_string(),
                    "filenames": {
                        identity: {"absolute": absolute, "relative": relative}
                        for identity, (
                            parent,
                            absolute,
                            relative,
                        ) in self.filenames.items()
                        if parent == str(sheet.path)
                    },
                }
            )
            for child in sheet.children:
                visit(child, file.parent)

        visit(sch.get_hierarchy().root, self.root.parent, root=True)
        return {"sheets": found}

    def nets(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read native pin connectivity, scoped by hierarchy instance identity."""
        schematic = self.k.get_schematic()
        view = self.view(args.get("instance", ""))
        commands = importlib.import_module(
            "kipy.proto.schematic.schematic_commands_pb2"
        )
        common = importlib.import_module("kipy.common_types")
        selected = str(common.SheetPath(proto=view._doc.sheet_path))
        command = commands.GetSchematicNetlist()
        command.document.CopyFrom(view._doc)
        command.types.append(self.types.KOT_SCH_PIN)
        response = view._kicad.send(command, commands.SchematicNetlistResponse)
        pins: dict[tuple[str, str], dict[str, str]] = {}
        for sheet in schematic.get_hierarchy():
            instance = str(sheet.path)
            if instance != selected and not instance.startswith(selected + "/"):
                continue
            for symbol in schematic.for_sheet(sheet).get_symbols():
                if symbol.reference.startswith("#"):
                    continue
                for pin in symbol.definition.pins:
                    pins[(instance, pin.id.value)] = {
                        "ref": symbol.reference,
                        "pin": pin.number,
                        "name": "" if pin.name == "~" else pin.name,
                    }
        result = []
        for net in response.nets:
            connected: dict[tuple[str, str], dict[str, str]] = {}
            for sheet in net.sheets:
                instance = str(common.SheetPath(proto=sheet.path))
                for identity in sheet.items:
                    pin = pins.get((instance, identity.value))
                    if pin is not None:
                        connected[(pin["ref"], pin["pin"])] = pin
            if connected:
                result.append(
                    {
                        "name": net.name,
                        "pins": [connected[key] for key in sorted(connected)],
                    }
                )
        return {"nets": sorted(result, key=lambda item: item["name"])}

    def begin(self, args: dict[str, Any]) -> dict[str, Any]:
        """Begin one atomic caller batch on the selected sheet."""
        return cast(dict[str, Any], self.edits.begin(args))

    def end(self, args: dict[str, Any]) -> dict[str, Any]:
        """Commit or drop the staged native objects."""
        return cast(dict[str, Any], self.edits.end(args))

    def edit(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply explicit primitive parameters to native objects."""
        return cast(dict[str, Any], self.edits.edit(args))

    def observe(self, args: dict[str, Any]) -> dict[str, Any]:
        """Observe native instance facts, including staged objects."""
        return cast(dict[str, Any], self.edits.observe(args))

    def canonicalize_filenames(self) -> None:
        """Restore portable references through native IPC before saving."""
        if not self.filenames:
            return
        schematic = self.k.get_schematic()
        changes: list[tuple[Any, list[Any]]] = []
        for sheet in schematic.get_hierarchy():
            view = schematic.for_sheet(sheet)
            items = []
            for item in view.get_sheet_symbols():
                alias = self.filenames.get(item.id.value)
                if alias is not None and item.filename_field.text.value == alias[1]:
                    item.filename_field.text.value = alias[2]
                    items.append(item)
            if items:
                changes.append((view, items))
        if changes:
            root = schematic.for_sheet(schematic.get_hierarchy().root)
            commit = root.begin_commit()
            try:
                for view, items in changes:
                    self.write_items(view, items, create=False)
                root.push_commit(commit, "Save relative sheet references")
            except Exception:
                root.drop_commit(commit)
                raise
        self.filenames.clear()

    def save(self, args: dict[str, Any]) -> dict[str, Any]:
        """Persist the authoritative document through native IPC."""
        try:
            self.canonicalize_filenames()
            self.view(args.get("instance", "")).save()
        except Exception as exc:
            server = getattr(self.k, "_server", None)
            details = list(server._stderr_lines.queue)[-10:] if server else []
            message = str(exc) + ("\n" + "\n".join(details) if details else "")
            raise ValueError(message) from exc
        return self.snapshot(args)

    def close(self) -> None:
        """Close this owned connection and its headless server."""
        if self.edits is not None:
            self.edits.end({"commit": False})
            self.edits.close()
        if self.k is not None:
            try:
                if self.borrowed:
                    self.k.close_document(self.view()._doc)
                elif not self.gui:
                    _close_documents(self.k)
            finally:
                self.k.close()


def main() -> None:
    """Serve only the bounded internal protocol; never expose it as MCP tools."""
    worker: Worker | None = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if worker is None:
                    worker = Worker()
                method = request["method"]
                if method not in {
                    "probe",
                    "open",
                    "initialize",
                    "snapshot",
                    "hierarchy",
                    "nets",
                    "begin",
                    "end",
                    "edit",
                    "observe",
                    "save",
                }:
                    raise ValueError("Unknown schematic IPC operation")
                result = {"ok": True, "data": getattr(worker, method)(request["args"])}
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                if isinstance(exc, NativeItemError):
                    result["item_id"] = exc.item_id
                    result["index"] = (
                        worker.edits.origins.get(exc.item_id) if worker else None
                    )
            sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    finally:
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
