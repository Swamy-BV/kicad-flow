"""Native PCB sessions; public primitives keep their existing contracts."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import inspect
import os
import threading
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar, cast

from ..cli import cli
from ..ipc import RPC, IPCError, gui_connection, project_host
from . import native_types as native

F = TypeVar("F", bound=Callable[..., Any])
_documents: weakref.WeakValueDictionary[Path, Document] = weakref.WeakValueDictionary()


class Document:
    """Own a native PCB document and guard its persisted revision."""

    def __init__(
        self,
        path: Path,
        *,
        create: bool,
        layers: int = 2,
        thickness: float = 1.6,
        mode: str | None = None,
    ) -> None:
        """Open exactly the selected native session with no backend fallback."""
        self.path = path.resolve()
        previous = _documents.get(self.path)
        if previous is not None:
            previous.close()
        self.lock = threading.RLock()
        self.depth = 0
        self.observing = 0
        self.save_pending = False
        self.operation_index = 0
        self.revision = self.disk_revision()
        mode = mode or os.environ.get("KICAD_FLOW_PCB_MODE", "headless")
        if mode not in ("headless", "gui"):
            raise ValueError("KICAD_FLOW_PCB_MODE must be headless or gui")
        executable = cli.require_ipc()
        connection = (
            project_host(self.path, executable)
            if mode == "headless"
            else gui_connection(self.path)
        )
        self.rpc = RPC(Path(__file__).with_name("ipc_worker.py"))
        try:
            self.rpc.call(
                "open",
                path=str(self.path),
                mode=mode,
                create=create,
                layers=layers,
                thickness=thickness,
                cli=executable,
                connection=connection,
            )
        except Exception:
            self.close()
            raise
        _documents[self.path] = self

    def disk_revision(self) -> str | None:
        """Hash exact board bytes so another writer cannot be overwritten."""
        return (
            hashlib.sha256(self.path.read_bytes()).hexdigest()
            if self.path.is_file()
            else None
        )

    def assert_current(self) -> None:
        """Refuse external file changes before an edit or save."""
        if self.disk_revision() != self.revision:
            if (
                self.path.is_file()
                and self.path.read_text(encoding="utf-8") == self.snapshot()
            ):
                self.revision = self.disk_revision()
                return
            raise RuntimeError(
                "board changed on disk; reload_board before editing or saving: "
                + str(self.path)
            )

    def snapshot(self) -> str:
        """Read the actual native document for read-only geometry helpers."""
        return str(self.rpc.call("snapshot")["contents"])

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Join nested writes in one native commit and rollback on failure."""
        with self.lock:
            if self.depth:
                yield
                return
            self.assert_current()
            self.rpc.call("begin")
            self.operation_index = 0
            self.depth += 1
            try:
                yield
                self.assert_current()
            except Exception:
                self.save_pending = False
                self.rpc.call("end", commit=False)
                raise
            else:
                try:
                    prepared = self.rpc.call("prepare")
                    _verify_fields(prepared)
                except Exception:
                    self.save_pending = False
                    self.rpc.call("end", commit=False)
                    raise
                try:
                    self.rpc.call("end", commit=True)
                except Exception:
                    self.save_pending = False
                    raise
            finally:
                self.depth -= 1
            if self.save_pending:
                self.save_pending = False
                self.save()

    def edit(self, operation: str, values: dict[str, Any]) -> Any:
        """Send caller coordinates directly to native object operations."""
        result = self.rpc.call(
            "edit", operation=operation, values=values, index=self.operation_index
        )
        self.operation_index += 1
        item = result.get("item", {})
        if operation in ("place", "move", "rotate", "flip"):
            return native.footprint(item)
        if operation == "track":
            return native.track(item)
        if operation == "via":
            return native.via(item)
        if operation == "zone":
            return native.zone(item)
        if operation in ("text", "update_text"):
            return native.text(item)
        if operation in ("graphic", "move_graphic"):
            return native.graphic(item)
        if operation == "move_field":
            return native.point(result["point"])
        if operation == "set_field":
            return result["fields"]
        if operation == "set_net":
            return result["net"]
        if operation == "remove_copper":
            return int(result["count"])
        return None

    def save(self) -> Path:
        """Let native KiCad save the authoritative board."""
        with self.lock:
            self.assert_current()
            if self.depth:
                self.save_pending = True
                return self.path
            self.rpc.call("save")
            self.revision = self.disk_revision()
        return self.path

    def copy_to(self, path: Path) -> None:
        """Save a native private snapshot without changing the source document."""
        self.rpc.call("save", copy=str(path.resolve()))

    def close(self) -> None:
        """Disconnect GUI sessions and close owned headless documents."""
        self.rpc.close()


def _verify_fields(prepared: dict[str, Any]) -> None:
    """Check real serialized text angles; affected nightlies echo false angles.

    This is a read-only native snapshot observation. It never edits or feeds a
    document string back to KiCad; rejection drops the active native commit.
    """
    if not prepared["fields"]:
        return
    from .._sexpr import loads
    from ._nodes import _f, _text

    tree = loads(prepared["snapshot"])
    footprints = {_text(fp.get("uuid")): fp for fp in tree.get_all("footprint")}
    for field in prepared["fields"]:
        parent = footprints.get(field["parent"])
        prop = (
            next(
                (p for p in parent.get_all("property") if _text(p) == field["name"]),
                None,
            )
            if parent is not None
            else None
        )
        if prop is None or prop.get("at") is None:
            raise IPCError("KiCad did not retain the edited footprint field")
        actual = _f(prop.get("at"), 2) % 360
        if abs((actual - field["angle"] + 180) % 360 - 180) > 1e-6:
            error = IPCError(
                f"KiCad IPC did not apply {field['name']}'s absolute text angle: "
                f"requested {field['angle']:g}, native save reports {actual:g}; "
                "the field batch was rolled back"
            )
            error.index = field["index"]
            raise error


def observed(function: F) -> F:
    """Refresh observations once at the outermost public read."""

    @functools.wraps(function)
    def invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        doc = self._ipc
        if doc is None or doc.depth or doc.observing:
            return function(self, *args, **kwargs)
        with doc.lock:
            doc.assert_current()
            self._refresh()
            doc.observing += 1
            try:
                return function(self, *args, **kwargs)
            finally:
                doc.observing -= 1

    return cast(F, invoke)


def edited(function: F) -> F:
    """Use native objects for attached boards; never serialize an edit diff."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        doc = self._ipc
        if doc is None:
            return function(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        values.pop("self")
        with doc.transaction():
            if function.__name__ in ("place", "move"):
                anchor = values.pop("anchor")
                if anchor not in ("origin", "courtyard_center"):
                    raise ValueError("anchor must be origin or courtyard_center")
                if anchor == "courtyard_center":
                    # This is coordinate arithmetic, never a placement choice.
                    if function.__name__ == "place":
                        from kicad_flow.pcb.types import Point

                        from ._geometry import _pad_on_board

                        definition = self.footprint_def(values["fp_id"])
                        c = definition.courtyard_center
                        back = values["side"] == "B"
                        offset = _pad_on_board(
                            c.x,
                            -c.y if back else c.y,
                            Point(0, 0),
                            values["rotation"] + (180 if back else 0),
                        )
                    else:
                        offset = self.footprint(values["ref"]).courtyard_offset
                    values["x"] -= offset.x
                    values["y"] -= offset.y
            return doc.edit(function.__name__, values)

    return cast(F, invoke)


def unavailable(operation: str) -> None:
    """Surface a native capability gap instead of editing a project file."""
    raise IPCError(f"{operation} is not available through this KiCad 11 IPC backend")
