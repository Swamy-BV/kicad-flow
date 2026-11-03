"""Launch native editors and observe their IPC document identities."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .cli import cli
from .ipc import RPC


class DesktopProject:
    """Keep project selection attached to actual visible KiCad documents."""

    def __init__(self) -> None:
        """Start no application until the user selects a project."""
        self.rpc: RPC | None = None
        self.project: Path | None = None
        self.connections: dict[str, str] = {}

    def open(self, path: Path) -> dict[str, Any]:
        """Open the explicitly selected project in native schematic/PCB editors."""
        if not path.is_file() or path.suffix not in (
            ".kicad_pro",
            ".kicad_sch",
            ".kicad_pcb",
        ):
            raise ValueError("Choose a KiCad project, schematic or PCB file")
        executable = Path(cli.require_ipc()).with_name(
            "kicad.exe" if os.name == "nt" else "kicad"
        )
        if not executable.is_file():
            raise ValueError(
                f"KiCad application not found beside kicad-cli: {executable}"
            )
        project = path.resolve().with_suffix(".kicad_pro")
        documents = [
            p
            for p in (
                project.with_suffix(".kicad_sch"),
                project.with_suffix(".kicad_pcb"),
            )
            if p.is_file()
        ]
        if not documents:
            raise ValueError("This project has no saved schematic or PCB to open")
        if self.rpc is None:
            self.rpc = RPC(Path(__file__).with_name("desktop_worker.py"))
        cache = (
            Path.home()
            / ".kicad-flow"
            / "editors"
            / (hashlib.sha256(str(project).encode()).hexdigest() + ".json")
        )
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        result = self.rpc.call(
            "open",
            executable=str(executable),
            documents=[str(p) for p in documents],
            directory=tempfile.gettempdir(),
            known=known,
        )
        self.project = project
        self.connections = result["connections"]
        os.environ["KICAD_FLOW_GUI_CONNECTIONS"] = json.dumps(self.connections)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.connections), encoding="utf-8")
        temporary.replace(cache)
        return {
            "project_file": str(project),
            "documents": result["documents"],
            "ready": result["ready"],
        }

    def status(self) -> dict[str, Any]:
        """Read document availability without opening or changing a design."""
        if self.rpc is None:
            return {"documents": [], "ready": False}
        return self.rpc.call("status")

    def close(self) -> None:
        """Disconnect IPC while leaving the user's native editors open."""
        if self.rpc is not None:
            self.rpc.close()
            self.rpc = None
