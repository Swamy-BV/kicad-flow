"""Loopback-only HTTP adapter for dashboard project chat."""

from __future__ import annotations

import json
import secrets
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .session import AgentSession


class Request(Protocol):
    """Small HTTP surface used by the chat adapter."""

    path: str
    headers: Any
    rfile: Any
    server: Any
    state: Any

    def _send(self, code: int, ctype: str, body: bytes) -> None: ...


class AgentHTTP:
    """Serve one local chat with a per-process mutation token."""

    def __init__(self) -> None:
        """Initialize private local state for this dashboard connection."""
        self.chat = AgentSession()
        self.token = secrets.token_urlsafe(32)

    def _local(self, request: Request) -> bool:
        port = request.server.server_port
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
        host = request.headers.get("Host", "").lower()
        origin = request.headers.get("Origin")
        return host in allowed and (
            origin is None or origin in {f"http://{item}" for item in allowed}
        )

    def get(self, request: Request) -> None:
        """Read state without starting Codex or sending a model request."""
        if not self._local(request):
            self._json(request, {"error": "Local dashboard origin required"}, 403)
            return
        query = parse_qs(urlparse(request.path).query)
        data = self.chat.snapshot(query.get("since", [""])[0])
        if request.state.active:
            data["suggested_project"] = str(request.state.active.parent.resolve())
        data["csrf"] = self.token
        data["service"] = "kicad-flow-agent"
        data["protocol"] = 1
        self._json(request, data)

    def post(self, request: Request) -> Any:
        """Dispatch explicit user actions after origin, token and size checks."""
        if not self._local(request) or not secrets.compare_digest(
            request.headers.get("X-KiCadFlow-Chat", ""), self.token
        ):
            self._json(
                request, {"error": "Refresh the local dashboard to continue"}, 403
            )
            return None
        try:
            length = int(request.headers.get("Content-Length", "0"))
            if not 0 < length <= 100000:
                raise ValueError("Invalid request size")
            if request.headers.get_content_type() != "application/json":
                raise ValueError("Send a JSON request")
            body = json.loads(request.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            action = urlparse(request.path).path.removeprefix("/agent/")
            result: dict[str, Any] = {"ok": True}
            preview = None
            if action == "connect":
                result = self.chat.connect()
            elif action == "login":
                result = self.chat.login(device=body.get("device") is True)
            elif action == "cancel-login":
                self.chat.cancel_login()
            elif action == "disconnect":
                self.chat.disconnect()
            elif action == "project":
                preview = self.chat.select(str(body.get("path", "")))
            elif action == "send":
                self.chat.send(
                    str(body.get("text", "")),
                    str(body.get("model", "")),
                    str(body.get("mode", "edit")),
                )
            elif action == "stop":
                self.chat.stop()
            elif action == "new":
                self.chat.reset()
            elif action == "respond":
                answers = body.get("answers", {})
                if not isinstance(answers, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in answers.items()
                ):
                    raise ValueError("Provide an answer for each question ID")
                self.chat.respond(str(body.get("id", "")), answers)
            else:
                self._json(request, {"error": "Unknown chat action"}, 404)
                return None
            self._json(request, result)
            return (self.chat.root, preview) if action == "project" else None
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            self._json(request, {"error": str(exc)}, 400)
            return None

    @staticmethod
    def _json(request: Request, data: dict[str, Any], code: int = 200) -> None:
        request._send(code, "application/json", json.dumps(data).encode())
