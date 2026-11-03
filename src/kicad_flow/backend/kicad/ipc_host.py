"""Own one native headless KiCad process shared by document clients."""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any


def main() -> None:
    """Expose only private startup and connection metadata to the parent."""
    connection: Any = None
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request["method"] == "start":
                    if connection is not None:
                        raise ValueError("Native project host already started")
                    api = importlib.import_module("kipy")
                    connection = api.KiCad(
                        headless=True,
                        kicad_cli_path=request["args"]["cli"],
                        timeout_ms=10000,
                    )
                elif request["method"] != "connection":
                    raise ValueError("Unknown native host operation")
                result = {
                    "ok": True,
                    "data": {
                        "socket_path": connection._client._socket_path,
                        "kicad_token": connection._client._kicad_token,
                    },
                }
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
    finally:
        if connection is not None:
            try:
                commands = importlib.import_module(
                    "kipy.proto.common.commands.project_commands_pb2"
                )
                empty = importlib.import_module("google.protobuf.empty_pb2").Empty
                connection._client.send(commands.CloseAllDocuments(force=True), empty)
            finally:
                connection.close()


if __name__ == "__main__":
    main()
