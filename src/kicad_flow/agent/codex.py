"""Local Codex app-server JSON-RPC transport; credentials stay with Codex."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Any


class CodexProcess:
    """Multiplex replies and notifications without blocking the reader."""

    def __init__(
        self,
        notify: Callable[[str, dict[str, Any]], None],
        request: Callable[[Any, str, dict[str, Any]], None],
    ) -> None:
        """Initialize private local state for this dashboard connection."""
        self._notify = notify
        self._request = request
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("Install Codex CLI, then connect again.")
        args = [
            executable,
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            "plugins._default.enabled=false",
            "-c",
            "apps._default.enabled=false",
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.multi_agent=false",
        ]
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "RUST_LOG": "error"},
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self.rpc(
                "initialize",
                {
                    "clientInfo": {
                        "name": "kicad_flow_agent",
                        "title": "KiCadFlow",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self._write({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    @property
    def alive(self) -> bool:
        """Whether the transport process is running."""
        return self._process.poll() is None

    def _write(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            stream = self._process.stdin
            if stream is None or not self.alive:
                raise RuntimeError("Codex disconnected. Reconnect to continue.")
            try:
                stream.write(json.dumps(message, ensure_ascii=True) + "\n")
                stream.flush()
            except (OSError, ValueError) as exc:
                raise RuntimeError("Codex disconnected.") from exc

    def rpc(
        self, method: str, params: dict[str, Any], timeout: float = 45
    ) -> dict[str, Any]:
        """Send one request and wait for its matching response."""
        result: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._next_id += 1
            key = self._next_id
            self._pending[key] = result
        try:
            self._write({"id": key, "method": method, "params": params})
            try:
                message = result.get(timeout=timeout)
            except queue.Empty as exc:
                raise RuntimeError(
                    "Codex did not acknowledge the request. Reconnect and "
                    "inspect the conversation before retrying."
                ) from exc
            if message.get("error"):
                raise RuntimeError(str(message["error"].get("message", "Codex error")))
            data = message.get("result", {})
            return data if isinstance(data, dict) else {"result": data}
        finally:
            with self._pending_lock:
                self._pending.pop(key, None)

    def answer(self, key: Any, result: dict[str, Any]) -> None:
        """Answer one server-initiated request."""
        self._write({"id": key, "result": result})

    def project_config(self, root: str) -> dict[str, Any]:
        """Disable inherited integrations without changing the user's config."""
        config = self.rpc("config/read", {"includeLayers": False, "cwd": root})[
            "config"
        ]
        overrides: dict[str, Any] = {
            category: {name: {"enabled": False} for name in config.get(category, {})}
            for category in ("mcp_servers", "plugins", "apps")
        }
        for category in ("plugins", "apps"):
            overrides[category]["_default"] = {"enabled": False}
        overrides["features"] = {
            "remote_plugin": False,
            "apps": False,
            "shell_tool": False,
            "multi_agent": False,
        }
        return overrides

    def check_integrations(self, thread_id: str) -> None:
        """Refuse a turn if a native integration bypasses the scoped bridge."""
        cursor = None
        while True:
            result = self.rpc(
                "mcpServerStatus/list",
                {
                    "threadId": thread_id,
                    "limit": 100,
                    "cursor": cursor,
                },
            )
            if any(server.get("tools") for server in result.get("data", [])):
                raise RuntimeError(
                    "Codex exposed additional integrations; chat stopped"
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break

    def _read(self) -> None:
        try:
            stream = self._process.stdout
            if stream is None:
                return
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                method = message.get("method")
                params = message.get("params", {})
                if method:
                    if not isinstance(params, dict):
                        params = {}
                    if "id" in message:
                        self._request(message["id"], str(method), params)
                    else:
                        self._notify(str(method), params)
                else:
                    key = message.get("id")
                    with self._pending_lock:
                        waiting = (
                            self._pending.get(key) if isinstance(key, int) else None
                        )
                    if waiting is not None:
                        with contextlib.suppress(queue.Full):
                            waiting.put_nowait(message)
        finally:
            if self._process.stdout is not None:
                self._process.stdout.close()
            with self._pending_lock:
                for waiting in self._pending.values():
                    with contextlib.suppress(queue.Full):
                        waiting.put_nowait({"error": {"message": "Codex disconnected"}})
            self._notify("disconnected", {})

    def close(self) -> None:
        """Stop this child only; leave other Codex sessions alone."""
        if self.alive:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._process.stdin is not None:
            with contextlib.suppress(OSError):
                self._process.stdin.close()
        # The reader owns stdout: closing it here can wait indefinitely on a
        # blocked Windows pipe read while Codex is finishing an interrupted turn.
        self._reader.join(timeout=1)
