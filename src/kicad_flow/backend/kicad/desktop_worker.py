"""Native desktop lifecycle worker, using the installed official IPC bindings."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


class Desktop:
    """Own connections to visible editors; never terminate those editors."""

    def __init__(self) -> None:
        """Initialize connection metadata without touching an editor."""
        self.api = importlib.import_module("kipy")
        self.types = importlib.import_module("kipy.proto.common.types")
        self.connections: dict[str, tuple[str, Any]] = {}
        self.selected: list[str] = []

    def _identity(self, connection: Any, path: Path) -> bool:
        if path.suffix == ".kicad_sch":
            sheet = connection.get_schematic()
            actual = Path(sheet._doc.project.path) / sheet.get_hierarchy().root.filename
        else:
            board = connection.get_board()
            actual = Path(board.document.project.path) / board.name
        return bool(actual.resolve() == path)

    def open(self, args: dict[str, Any]) -> dict[str, Any]:
        """Reuse a matching editor or start an isolated native GUI process."""
        self.selected = args["documents"]
        for filename in args["documents"]:
            path = Path(filename).resolve()
            if filename in self.connections:
                socket, connection = self.connections[filename]
                try:
                    if self._identity(connection, path):
                        continue
                except Exception:
                    pass
                connection.close()
                del self.connections[filename]
            known = args.get("known", {}).get(filename)
            for candidate in [known, None] if known else [None]:
                if candidate is not None and not str(candidate).startswith("ipc://"):
                    continue
                connection = self.api.KiCad(
                    socket_path=candidate, timeout_ms=350, client_name="kicad-flow-chat"
                )
                try:
                    if self._identity(connection, path):
                        self.connections[filename] = (
                            connection._client._socket_path,
                            connection,
                        )
                        break
                except Exception:
                    pass
                connection.close()
            if filename in self.connections:
                continue
            # KiCad's GUI server uses the OS temp directory for its socket.
            # Isolate its endpoint from unrelated KiCad instances without
            # changing the user's KiCad preferences or opening headless CAD.
            directory = tempfile.mkdtemp(
                prefix="kicad-flow-gui-", dir=args["directory"]
            )
            socket = "ipc://" + str(Path(directory) / "kicad" / "api.sock")
            environment = {
                **os.environ,
                "TMP": directory,
                "TEMP": directory,
                "TMPDIR": directory,
            }
            frame = "sch" if path.suffix == ".kicad_sch" else "pcb"
            process = subprocess.Popen(
                [args["executable"], "--new", "--frame", frame, str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            connection = self.api.KiCad(
                socket_path=socket, timeout_ms=500, client_name="kicad-flow-chat"
            )
            deadline = time.monotonic() + 24
            error = ""
            while time.monotonic() < deadline:
                try:
                    version = connection.get_version()
                    if version.major < 11 and (version.major, version.minor) != (
                        10,
                        99,
                    ):
                        raise ValueError("The visible editor must be KiCad 11")
                    if self._identity(connection, path):
                        self.connections[filename] = (socket, connection)
                        break
                except Exception as exc:
                    error = str(exc)
                if process.poll() is not None:
                    break
                time.sleep(0.2)
            else:
                connection.close()
                raise ValueError(
                    "KiCad opened but IPC is not ready. Finish any startup dialogs and "
                    "enable the KiCad IPC server in Preferences, then retry. " + error
                )
            if filename not in self.connections:
                connection.close()
                raise ValueError("KiCad could not open the selected document: " + error)
        return {
            **self.status(),
            "connections": {path: self.connections[path][0] for path in self.selected},
        }

    def status(self) -> dict[str, Any]:
        """Observe identity afresh so closing/changing editors is visible."""
        documents = []
        for filename in self.selected:
            entry = self.connections.get(filename)
            connected = False
            error = ""
            try:
                connected = bool(entry and self._identity(entry[1], Path(filename)))
                ready = connected
                if connected and entry:
                    editor = (
                        entry[1].get_schematic()
                        if filename.endswith(".kicad_sch")
                        else entry[1].get_board()
                    )
                    # This bounded observation asks for no objects. Unlike
                    # GetOpenDocuments, it observes the editor's busy guard.
                    editor.get_items_by_id([])
            except Exception as exc:
                ready = False
                error = str(exc)
            documents.append(
                {
                    "path": filename,
                    "name": Path(filename).name,
                    "kind": "schematic" if filename.endswith(".kicad_sch") else "board",
                    "ready": ready,
                    "connected": connected,
                    "error": error,
                }
            )
        return {
            "documents": documents,
            "ready": bool(documents)
            and all(document["ready"] for document in documents),
        }

    def close(self) -> None:
        """Close only IPC clients; native windows and unsaved work remain open."""
        for _, connection in self.connections.values():
            connection.close()


def main() -> None:
    """Serve lifecycle requests over the existing private worker transport."""
    desktop: Desktop | None = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if desktop is None:
                    desktop = Desktop()
                method = request["method"]
                if method == "open":
                    data = desktop.open(request["args"])
                elif method == "status":
                    data = desktop.status()
                else:
                    raise ValueError("Unknown desktop operation")
                result = {"ok": True, "data": data}
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(result), flush=True)
    finally:
        if desktop is not None:
            desktop.close()


if __name__ == "__main__":
    main()
