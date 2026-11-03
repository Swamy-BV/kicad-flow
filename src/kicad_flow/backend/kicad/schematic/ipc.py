"""KiCad 11 document sessions, snapshots and atomic native item commits."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from kicad_flow.schematic.api import snap
from kicad_flow.schematic.types import Net, NetPin, Point

from .._sexpr import Node, dumps, loads
from ..cli import cli
from ..ipc import RPC, gui_connection, project_host
from ..ipc import IPCError as SchematicIPCError
from . import native_types as native
from ._geometry import _field_style, _pin_on_sheet
from ._nodes import _text, _walk

__all__ = ["Document", "SchematicIPCError"]


def rebase_instances(tree: Node, where: str, root: str, project_name: str) -> None:
    """Rebase instance paths only inside a private standalone snapshot."""
    for node in _walk(tree):
        if node.name != "instances":
            continue
        for project in node.get_all("project"):
            project.items[1] = project_name
            for path in project.get_all("path"):
                value = _text(path)
                if value == where or value.startswith(where + "/"):
                    path.items[1] = "/" + root + value[len(where) :]


class Project:
    """Own the native hierarchy and the disk revisions it was loaded from."""

    def __init__(self, path: Path, *, create: bool, paper: str, title: str) -> None:
        """Initialize the private IPC connection state."""
        self.path = path.resolve()
        self.lock = threading.RLock()
        self.revisions: dict[Path, str | None] = {}
        mode = os.environ.get("KICAD_FLOW_SCHEMATIC_MODE", "headless")
        if mode not in ("headless", "gui"):
            raise ValueError("KICAD_FLOW_SCHEMATIC_MODE must be headless or gui")
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
                cli=executable,
                create=create,
                paper=paper,
                title=title,
                connection=connection,
            )
            self.observe_disk()
        except Exception:
            self.rpc.close()
            raise

    def sheets(self) -> list[dict[str, Any]]:
        """Observe every loaded sheet in the native hierarchy."""
        data: list[dict[str, Any]] = self.rpc.call("hierarchy")["sheets"]
        return data

    def observe_disk(self) -> None:
        """Record revisions after a successful native load or save."""
        for sheet in self.sheets():
            path = Path(sheet["path"]).resolve()
            self.revisions[path] = _revision(path)

    def save(self, instance: str) -> dict[str, Any]:
        """Persist the authoritative document through native IPC."""
        sheets = self.sheets()
        for sheet in sheets:
            path = Path(sheet["path"]).resolve()
            revision = _revision(path)
            if (
                path in self.revisions
                and revision != self.revisions[path]
                and (
                    not path.is_file()
                    or path.read_text(encoding="utf-8") != sheet["contents"]
                )
            ):
                raise SchematicIPCError(
                    f"Schematic changed on disk outside this IPC session: {path}"
                )
        result = self.rpc.call("save", instance=instance)
        self.observe_disk()
        return result


def _revision(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


_projects: list[Project] = []
_pool_lock = threading.RLock()


class Document:
    """One sheet view, bound explicitly by filename and hierarchy instance."""

    def __init__(
        self,
        path: Path,
        *,
        create: bool = False,
        paper: str = "A4",
        title: str = "",
        instance: str = "",
    ) -> None:
        """Initialize the private IPC connection state."""
        self.path = path.resolve()
        self.create = create
        self.paper, self.title, self.instance = paper, title, instance
        self.project: Project | None = None
        self.contents = ""
        self.created_ids: list[str] = []

    def connect(self) -> Project:
        """Bind this view to the explicitly selected project and instance."""
        if self.project is not None:
            return self.project
        with _pool_lock:
            matches: list[tuple[Project, dict[str, Any]]] = []
            for project in _projects:
                for sheet in project.sheets():
                    if Path(sheet["path"]).resolve() == self.path and (
                        not self.instance or sheet["instance"] == self.instance
                    ):
                        matches.append((project, sheet))
            if len(matches) > 1:
                raise ValueError(
                    "This sheet has several instances; supply instance_path"
                )
            if matches:
                self.project, sheet = matches[0]
                self.instance = sheet["instance"]
                if self.create:
                    self.project.rpc.call(
                        "initialize",
                        instance=self.instance,
                        paper=self.paper,
                        title=self.title,
                    )
            else:
                if self.instance:
                    raise ValueError(
                        "The parent sheet instance is not open in this IPC session"
                    )
                self.project = Project(
                    self.path, create=self.create, paper=self.paper, title=self.title
                )
                _projects.append(self.project)
            self.create = False
            return self.project

    def snapshot(self) -> str:
        """Read the current native sheet and its hierarchy identity."""
        result = self.connect().rpc.call("snapshot", instance=self.instance)
        return self._observe(result)

    def _observe(self, result: dict[str, Any]) -> str:
        """Keep the native revision while projecting pending portable filenames."""
        self.instance = str(result["instance"])
        self.contents = str(result["contents"])
        return self.logical_contents(result)

    @staticmethod
    def logical_contents(result: dict[str, Any]) -> str:
        """Project pending native path spellings into the caller's representation."""
        contents = str(result["contents"])
        aliases = result.get("filenames", {})
        if not aliases:
            return contents
        tree = loads(contents)
        for sheet in tree.get_all("sheet"):
            alias = aliases.get(_text(sheet.get("uuid")))
            if alias is None:
                continue
            for prop in sheet.get_all("property"):
                if _text(prop) == "Sheetfile" and _text(prop, 1) == alias["absolute"]:
                    prop.items[2] = alias["relative"]
        return dumps(tree)

    def begin(self) -> None:
        """Open one native transaction without authoring a file representation."""
        self.created_ids = []
        self.connect().rpc.call(
            "begin", instance=self.instance, path=str(self.path), expected=self.contents
        )

    def end(self, *, commit: bool) -> str:
        """Commit or drop the native batch and observe its authoritative state."""
        result = self.connect().rpc.call("end", commit=commit)
        return self._observe(result) if result else self.contents

    def observe(self, operation: str, values: dict[str, Any]) -> Any:
        """Read native instance facts, including the batch's staged objects."""
        reply = self.connect().rpc.call(
            "observe", operation=operation, instance=self.instance, values=values
        )
        return native.result(operation, reply)

    def edit(
        self, sheet: Any, operation: str, values: dict[str, Any], index: int
    ) -> Any:
        """Send primitive parameters to native objects, never a serialized diff."""
        result_operation = operation
        if operation in ("power", "power_flag"):
            prefix = "#PWR" if operation == "power" else "#FLG"
            used = {p.ref for p in self.observe("parts", {})}
            number = 1
            while f"{prefix}{number:04d}" in used:
                number += 1
            net = values.pop("net") if operation == "power" else "PWR_FLAG"
            values.update(
                lib_id=f"power:{net}",
                ref=f"{prefix}{number:04d}",
                value=net,
                mirror="",
                unit=1,
            )
            operation = "place"
        if operation in ("place", "rotate"):
            if operation == "place":
                lib_id, unit = values["lib_id"], values["unit"]
                at, mirror = (
                    Point(snap(values["x"]), snap(values["y"])),
                    values["mirror"],
                )
            else:
                part = self.observe(
                    "part", {"ref": values["ref"], "unit": values["unit"]}
                )
                lib_id, unit, at, mirror = part.lib_id, part.unit, part.at, part.mirror
            definition = sheet.symbol(lib_id, unit=unit)
            left, bottom, right, top = definition.bounds
            rotation = values["rotation"]
            corners = [
                _pin_on_sheet(x, y, 0, at, rotation, mirror)[0]
                for x in (left, right)
                for y in (bottom, top)
            ]
            angles = [
                _pin_on_sheet(p.at.x, p.at.y, p.orientation, at, rotation, mirror)[1]
                for p in definition.pins
            ]
            upright = sum(a % 180 == 90 for a in angles)
            if angles and upright * 2 >= len(angles):
                x = max(p.x for p in corners) + 1.27
                places = [("Reference", x, at.y - 1.27), ("Value", x, at.y + 1.27)]
                justify = "left"
            else:
                places = [
                    ("Reference", at.x, min(p.y for p in corners) - 2.54),
                    ("Value", at.x, max(p.y for p in corners) + 2.54),
                ]
                justify = "center"
            angle, stored_justify = _field_style(rotation, mirror, justify)
            values["field_layout"] = [
                {
                    "name": n,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "justify": stored_justify,
                    "angle": angle,
                }
                for n, x, y in places
            ]
        reply = self.connect().rpc.call(
            "edit",
            operation=operation,
            instance=self.instance,
            values=values,
            index=index,
        )
        self.created_ids.extend(reply.get("created_ids", []))
        return native.result(result_operation, reply)

    def nets(self) -> list[Net]:
        """Observe native connectivity for this sheet and its descendants."""
        result = self.connect().rpc.call("nets", instance=self.instance)
        return [
            Net(
                name=item["name"],
                pins=tuple(NetPin(**p) for p in item["pins"]),
                no_connect=item["name"].startswith("unconnected-("),
            )
            for item in result["nets"]
        ]

    def save(self) -> None:
        """Persist the authoritative document through native IPC."""
        self.connect().save(self.instance)
