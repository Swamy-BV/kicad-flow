"""Let the CLI find the native companion without changing dashboard ports."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from .http import AgentHTTP
from .session import AgentSession


def registry_path() -> Path:
    """Return the local endpoint-discovery file, which contains no credentials."""
    return Path(
        os.environ.get(
            "KICAD_FLOW_COMPANION_STATE",
            str(Path.home() / ".kicad-flow" / "companion.json"),
        )
    )


def endpoint() -> tuple[int, str] | None:
    """Read a candidate endpoint; clients must verify its instance over HTTP."""
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        port, instance = data["port"], data["instance"]
        if isinstance(port, int) and 1 <= port <= 65535 and isinstance(instance, str):
            return port, instance
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class _Server(ThreadingHTTPServer):
    """Keep local endpoint identity on the server, separate from credentials."""

    instance: str = ""
    daemon_threads = True


class CompanionService:
    """Expose the native window's agent session to local CLI clients only."""

    def __init__(self, session: AgentSession, focus: Callable[[], None]) -> None:
        """Bind a private OS-selected endpoint, leaving 8471 and 8472 alone."""
        adapter = AgentHTTP(session, focus)

        class Handler(BaseHTTPRequestHandler):
            state = SimpleNamespace(active=None)

            def log_message(self, *_args: object) -> None:
                pass

            def _send(self, code: int, ctype: str, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

            def do_GET(self) -> None:
                if urlparse(self.path).path == "/agent/state":
                    adapter.get(self)
                else:
                    self._send(404, "text/plain", b"Native companion")

            def do_POST(self) -> None:
                adapter.post(self)

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.server.instance = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"port": self.server.server_port, "instance": self.server.instance}
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def close(self) -> None:
        """Stop this local service and remove only its own discovery record."""
        if endpoint() == (self.server.server_port, self.server.instance):
            registry_path().unlink(missing_ok=True)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
