"""Bounded subprocess transport for official KiCad IPC workers."""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


class IPCError(ValueError, RuntimeError):
    """A native edit/save refusal that autosave must surface to the caller."""

    def __init__(self, message: str, *, item_id: str | None = None) -> None:
        """Retain native identity for the batch's source-index mapping."""
        super().__init__(message)
        self.item_id = item_id
        self.index: int | None = None


class RPC:
    """Serialize requests to one official-IPC worker, with no write retries."""

    def __init__(self, worker: Path) -> None:
        """Initialize the private IPC connection state."""
        if getattr(sys, "frozen", False) and "KICAD_FLOW_IPC_PYTHON" not in os.environ:
            raise IPCError(
                "Set KICAD_FLOW_IPC_PYTHON to the Python with KiCad 11 IPC bindings"
            )
        python = os.environ.get("KICAD_FLOW_IPC_PYTHON", sys.executable)
        self.process = subprocess.Popen(
            [python, "-u", str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.lock = threading.RLock()
        self.replies: queue.Queue[dict[str, Any]] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        atexit.register(self.close)

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    self.replies.put(data)
        finally:
            self.process.stdout.close()
            self.replies.put(
                {
                    "ok": False,
                    "error": "KiCad IPC worker disconnected; inspect before retrying",
                }
            )

    def call(self, method: str, **args: Any) -> dict[str, Any]:
        """Send one serialized request without replaying failed writes."""
        with self.lock:
            if self.process.poll() is not None or self.process.stdin is None:
                raise IPCError(
                    "KiCad IPC disconnected; restart the server after "
                    "inspecting the document"
                )
            try:
                self.process.stdin.write(
                    json.dumps({"method": method, "args": args}) + "\n"
                )
                self.process.stdin.flush()
                result = self.replies.get(timeout=60)
            except (OSError, queue.Empty) as exc:
                self.close()
                raise IPCError(
                    "KiCad IPC did not finish; the write was not replayed. "
                    "Inspect before retrying"
                ) from exc
            if not result.get("ok"):
                error = IPCError(
                    str(result.get("error", "KiCad IPC failed")),
                    item_id=result.get("item_id"),
                )
                error.index = result.get("index")
                raise error
            data: dict[str, Any] = result["data"]
            return data

    def close(self) -> None:
        """Close this owned connection and its headless server."""
        if self.process.poll() is not None:
            return
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.process.terminate()


_hosts: dict[Path, RPC] = {}
_hosts_lock = threading.RLock()


def project_host(path: Path, executable: str) -> dict[str, Any]:
    """Share one native headless project owner across schematic and PCB clients.

    KiCad locks the project, so independent native servers cannot safely open
    the two documents belonging to that project. Only private IPC connection
    parameters cross this boundary; callers never receive them.
    """
    key = path.resolve().with_suffix(".kicad_pro")
    with _hosts_lock:
        if key not in _hosts:
            rpc = RPC(Path(__file__).with_name("ipc_host.py"))
            try:
                rpc.call("start", cli=executable)
            except Exception:
                rpc.close()
                raise
            _hosts[key] = rpc
        return _hosts[key].call("connection")
