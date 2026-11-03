"""Connect GUI and CLI clients to the same loopback agent session."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from typing import Any

DEFAULT_PORT = 8472


class AgentClient:
    """Small local HTTP client; it never stores account credentials."""

    def __init__(self, port: int | None = None) -> None:
        """Select the local dashboard port, retaining the established default."""
        self.discover = port is None
        port = DEFAULT_PORT if port is None else port
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        self.port = port
        self.url = f"http://localhost:{port}"
        self.token = ""

    def _request(
        self, route: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=2 if route == "state" else 90
        )
        try:
            connection.request(
                "GET" if body is None else "POST",
                "/agent/" + route,
                body=None if body is None else json.dumps(body),
                headers={
                    "Content-Type": "application/json",
                    "X-KiCadFlow-Chat": self.token,
                },
            )
            response = connection.getresponse()
            try:
                data = json.loads(response.read())
            except ValueError as exc:
                raise RuntimeError(
                    "Restart the older dashboard from this GUI branch to continue"
                ) from exc
            if not isinstance(data, dict):
                raise RuntimeError("Invalid workspace response")
            if response.status != 200:
                raise RuntimeError(str(data.get("error", "Workspace request failed")))
            return data
        finally:
            connection.close()

    def state(self) -> dict[str, Any]:
        """Read the shared session and acquire its local mutation token."""
        data = self._request("state")
        if data.get("service") != "kicad-flow-agent" or data.get("protocol") != 1:
            raise RuntimeError("Restart the dashboard from this GUI branch to continue")
        self.token = str(data["csrf"])
        return data

    def action(self, name: str, **arguments: Any) -> dict[str, Any]:
        """Perform an explicit action against the shared session."""
        if not self.token:
            self.state()
        return self._request(name, arguments)

    def ensure_running(self) -> None:
        """Reuse the local server or launch it without opening a terminal window."""
        if self.discover:
            from .companion import endpoint

            companion = endpoint()
            if companion:
                candidate = AgentClient(companion[0])
                try:
                    state = candidate.state()
                    if state.get("instance") == companion[1] and state.get(
                        "live_editor"
                    ):
                        self.port, self.url, self.token = (
                            candidate.port,
                            candidate.url,
                            candidate.token,
                        )
                        return
                except (OSError, RuntimeError):
                    pass
        try:
            self.state()
            return
        except (ConnectionError, OSError):
            pass
        command = (
            [sys.executable, "monitor"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "kicad_flow.monitor"]
        )
        process = subprocess.Popen(
            [*command, "--port", str(self.port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=os.environ.copy(),
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                self.state()
                return
            except (ConnectionError, OSError):
                if process.poll() is not None:
                    break
                time.sleep(0.15)
        raise RuntimeError(
            "Could not start the workspace; run python -m kicad_flow.monitor"
        )
