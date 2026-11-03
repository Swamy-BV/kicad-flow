"""Local project chat using Codex and the existing MCP primitives."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .codex import CodexProcess
from .tools import TOOLS, invoke

_INSTRUCTIONS = """You are the KiCadFlow project assistant. Work only in the
selected project. Use kicad_search to discover exact MCP schemas and kicad_call
to operate on CAD. Never write KiCad file syntax or bypass MCP with file edits.
Load only the relevant get_workflow topic for substantial work. The caller
chooses all placements and routing; use returned pin/pad coordinates. Respect
advertised batch limits. Inspect net membership, ERC/DRC, connectivity and renders
before claiming a design is complete; use kicad_view_image to see rendered files.
Explain remaining findings. Keep replies concise, using plain text.
If KiCad reports that it is busy, stop CAD calls and ask the user to finish
the active editor command or dialog before continuing. Do not retry edits.
Ask for missing electrical requirements instead of guessing them. Ask mode
permits only inspection; it cannot save, render to files, or edit CAD. Ask the
user to select Edit mode when a write is necessary.
"""


class AgentSession:
    """Own one local agent connection and independently saved project threads."""

    def __init__(
        self,
        project_opener: Callable[[Path], dict[str, Any]] | None = None,
        project_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize private local state for this dashboard connection."""
        self.lock = threading.RLock()
        self._actions = threading.Lock()
        self.process: CodexProcess | None = None
        self.root: Path | None = None
        self.thread_id = ""
        self.turn_id = ""
        self.model = ""
        self.mode = "edit"
        self.messages: list[dict[str, Any]] = []
        self.account: dict[str, Any] | None = None
        self.models: list[dict[str, Any]] = []
        self.busy = False
        self.stopping = False
        self.error = ""
        self.phase = "Connect Codex to start"
        self.revision = 0
        self.pending: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, Any] = {}
        self._loaded = False
        self.login_id = ""
        self.login_info: dict[str, str] = {}
        self._login_results: dict[str, dict[str, Any]] = {}
        self.activity: list[dict[str, Any]] = []
        self._tools = threading.Lock()
        self.project_opener = project_opener
        self.project_status = project_status
        self.editor: dict[str, Any] = {}

    def _changed(self) -> None:
        self.revision += 1

    def snapshot(self, since: str = "") -> dict[str, Any]:
        """Return display state only, never credentials or raw process events."""
        with self.lock:
            if since == str(self.revision):
                return {"revision": self.revision, "changed": False}
            return {
                "revision": self.revision,
                "changed": True,
                "installed": shutil.which("codex") is not None,
                "connected": bool(self.process and self.process.alive),
                "account": self.account,
                "models": self.models,
                "project": str(self.root) if self.root else "",
                "project_name": (
                    Path(self.editor["project_file"]).stem
                    if self.editor.get("project_file")
                    else self.root.name
                    if self.root
                    else ""
                ),
                "thread_id": self.thread_id,
                "model": self.model,
                "mode": self.mode,
                "messages": list(self.messages),
                "busy": self.busy,
                "phase": self.phase,
                "error": self.error,
                "usage": self.usage,
                "pending": [
                    {"id": k, "method": v["method"], "params": v["params"]}
                    for k, v in self.pending.items()
                ],
                "login": dict(self.login_info),
                "activity": list(self.activity),
                "live_editor": self.project_opener is not None,
                "editor": self.editor,
            }

    def _process(self) -> CodexProcess:
        if self.process is None or not self.process.alive:
            self.process = CodexProcess(self._notify, self._request)
            self._loaded = False
        return self.process

    @staticmethod
    def _editor_error(status: dict[str, Any]) -> str:
        documents = status.get("documents", [])
        if documents and all(doc.get("connected") for doc in documents):
            return "KiCad is busy; finish the active editor command or dialog first"
        return "Reopen the selected KiCad project before continuing"

    def refresh_editor(self) -> None:
        """Observe editor availability without inference or design writes."""
        with self._actions:
            if self.project_status is None or self.root is None:
                return
            try:
                status = self.project_status()
            except (OSError, RuntimeError, ValueError) as exc:
                status = {"ready": False, "error": str(exc)}
            with self.lock:
                changed = any(self.editor.get(k) != v for k, v in status.items())
                self.editor.update(status)
                if not self.busy:
                    if not status.get("ready"):
                        phase = self._editor_error(status)
                    elif changed:
                        phase = "Ready" if self.account else "Sign in with ChatGPT"
                    else:
                        phase = self.phase
                    changed = changed or phase != self.phase
                    self.phase = phase
                if changed:
                    self._changed()

    def connect(self) -> dict[str, Any]:
        """Connect without starting an inference turn or initiating sign-in."""
        with self._actions:
            if self.busy:
                raise ValueError("Stop the current turn before reconnecting")
            process = self._process()
            self._read_account(process)
            return self.snapshot()

    def _read_account(self, process: CodexProcess) -> None:
        account = process.rpc("account/read", {"refreshToken": False}).get("account")
        models = process.rpc("model/list", {"limit": 100}).get("data", [])
        with self.lock:
            self.account = (
                {k: account.get(k) for k in ("type", "email", "planType")}
                if isinstance(account, dict)
                else None
            )
            self.models = [
                {k: m.get(k) for k in ("id", "model", "displayName", "isDefault")}
                for m in models
                if isinstance(m, dict)
            ]
            if self.model not in {m.get("model") for m in self.models}:
                default = next(
                    (m for m in self.models if m.get("isDefault")),
                    self.models[0] if self.models else {},
                )
                self.model = str(default.get("model") or "")
            self.phase = "Ready" if self.account else "Sign in with ChatGPT"
            self.error = ""
            self._changed()

    def login(self, device: bool = False) -> dict[str, Any]:
        """Start Codex-owned sign-in without accepting or reading credentials."""
        with self._actions:
            if self.busy:
                raise ValueError("Stop the current turn before signing in")
            if self.login_id:
                raise ValueError("Finish or cancel the pending sign-in first")
            result = self._process().rpc(
                "account/login/start",
                {"type": "chatgptDeviceCode" if device else "chatgpt"},
            )
            with self.lock:
                self.login_id = str(result["loginId"])
                self.login_info = {
                    "auth_url": str(
                        result.get("authUrl") or result.get("verificationUrl", "")
                    ),
                    "user_code": str(result.get("userCode", "")),
                }
                self.phase = "Finish sign-in in your browser"
                self.error = ""
                self._changed()
                info = dict(self.login_info)
                completed = self._login_results.pop(self.login_id, None)
                if completed is not None:
                    self._notify("account/login/completed", completed)
            return info

    def cancel_login(self) -> None:
        """Cancel this connection's unfinished browser or device login."""
        with self._actions:
            self._cancel_login()

    def _cancel_login(self) -> None:
        if self.login_id and self.process and self.process.alive:
            self.process.rpc("account/login/cancel", {"loginId": self.login_id})
        with self.lock:
            self.login_id = ""
            self.login_info = {}
            self.phase = "Sign-in cancelled"
            self._changed()

    def disconnect(self) -> None:
        """Unlink this app connection, keeping the user's other Codex sessions."""
        with self._actions:
            if self.busy:
                raise ValueError("Stop the current turn before disconnecting")
            self._cancel_login()
            if self.process:
                self.process.close()
            with self.lock:
                self.process = None
                self.account = None
                self.models = []
                self._loaded = False
                self.phase = "Codex disconnected"
                self._changed()

    def _refresh_account(self, expected: CodexProcess | None) -> None:
        # Notifications run on the JSON reader; RPC must run on another thread.
        try:
            with self._actions:
                if (
                    expected is None
                    or self.process is not expected
                    or not expected.alive
                ):
                    return
                self._read_account(expected)
        except (OSError, RuntimeError, ValueError) as exc:
            with self.lock:
                self.error = str(exc)
                self._changed()

    def select(self, supplied: str) -> Path | None:
        """Select an existing folder or KiCad file; retain its saved chat."""
        with self._actions:
            if self.busy:
                raise ValueError("Stop the current turn before changing project")
            path = Path(supplied).expanduser()
            if not path.is_absolute():
                raise ValueError("Choose an absolute project folder or KiCad file")
            path = path.resolve(strict=True)
            editor = self.project_opener(path) if self.project_opener else {}
            preview: Path | None = None
            if path.is_file():
                if path.suffix not in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
                    raise ValueError("Select a KiCad project, schematic or PCB")
                if path.suffix != ".kicad_pro":
                    preview = path
                else:
                    preview = next(
                        (
                            p
                            for p in (
                                path.with_suffix(".kicad_sch"),
                                path.with_suffix(".kicad_pcb"),
                            )
                            if p.is_file()
                        ),
                        None,
                    )
                path = path.parent
            if not path.is_dir() or path == Path(path.anchor):
                raise ValueError("Choose a project folder, not a drive root")
            if preview is None:
                preview = next(
                    iter(
                        sorted(path.glob("*.kicad_sch"))
                        + sorted(path.glob("*.kicad_pcb"))
                    ),
                    None,
                )
            with self.lock:
                self.root = path
                self.editor = editor
                self.thread_id = ""
                self.turn_id = ""
                self.messages = []
                self.usage = {}
                self.activity = []
                self.pending.clear()
                self._loaded = False
                self.error = ""
                self.phase = "Project selected"
                self._restore()
                self._changed()
            return preview

    def send(self, text: str, model: str, mode: str) -> None:
        """Start one explicit user turn; concurrent submissions are refused."""
        text = text.strip()
        if not text or len(text) > 20000:
            raise ValueError("Enter a message of at most 20,000 characters")
        if mode not in ("ask", "edit"):
            raise ValueError("Choose Ask or Edit mode")
        with self._actions:
            if self.project_status and mode == "edit":
                status = self.project_status()
                if not status.get("ready"):
                    raise ValueError(self._editor_error(status))
            with self.lock:
                if self.root is None:
                    raise ValueError("Select a project first")
                if self.busy:
                    raise ValueError("A turn is already running")
                if not self.account or not self.process or not self.process.alive:
                    raise ValueError("Connect a Codex account before sending")
                if model not in {m.get("model") for m in self.models}:
                    raise ValueError("Choose a model from the connected account")
                self.busy = True
                self.stopping = False
                self.turn_id = ""
                self.mode, self.model = mode, model
                self.error = ""
                self.activity = []
                self.phase = "Starting"
                self._changed()
            try:
                process = self._process()
                if not self._loaded:
                    params: dict[str, Any] = {
                        "cwd": str(self.root),
                        "model": model,
                        "sandbox": "read-only",
                        "approvalPolicy": "on-request",
                        "developerInstructions": _INSTRUCTIONS
                        + (
                            "\nThis is a live KiCad companion. Changes must appear in "
                            "the open native editors through GUI IPC. Do not render "
                            "images as a substitute for the editor. Use native "
                            "connectivity and validation tools to check changes.\n"
                            if self.project_opener
                            else ""
                        ),
                        "config": process.project_config(str(self.root)),
                    }
                    if self.thread_id:
                        params["threadId"] = self.thread_id
                        result = process.rpc("thread/resume", params)
                    else:
                        params["dynamicTools"] = TOOLS
                        result = process.rpc("thread/start", params)
                    self.thread_id = str(result["thread"]["id"])
                    process.check_integrations(self.thread_id)
                    self._loaded = True
                with self.lock:
                    self.messages.append(
                        {"id": uuid.uuid4().hex, "role": "user", "text": text}
                    )
                    self.messages = self.messages[-100:]
                    self._save()
                    self._changed()
                result = process.rpc(
                    "turn/start",
                    {
                        "threadId": self.thread_id,
                        "model": model,
                        "input": [{"type": "text", "text": text}],
                    },
                )
                with self.lock:
                    self.turn_id = str(result["turn"]["id"])
                    if self.busy:
                        self.phase = "Working"
                    self._changed()
            except Exception as exc:
                with self.lock:
                    self.busy = False
                    self.error = str(exc)
                    self.phase = "Turn could not start; inspect before retrying"
                    self._save()
                    self._changed()
                raise

    def stop(self) -> None:
        """Interrupt inference; an already executing CAD call may finish."""
        with self._actions:
            if self.busy and self.thread_id and self.turn_id:
                with self.lock:
                    self.stopping = True
                try:
                    self._process().rpc(
                        "turn/interrupt",
                        {"threadId": self.thread_id, "turnId": self.turn_id},
                    )
                except RuntimeError as exc:
                    if "no active turn to interrupt" not in str(exc):
                        raise
                    with self.lock:
                        self.busy = False
                        self.phase = "Completed"
                        self.pending.clear()
                        self._save()
                with self.lock:
                    if self.busy:
                        self.phase = "Stopping; active CAD operation may finish"
                    self._changed()

    def reset(self) -> None:
        """Start a fresh conversation for the selected project, keeping files."""
        with self._actions, self.lock:
            if self.busy:
                raise ValueError("Stop the current turn before starting a new chat")
            self.thread_id = ""
            self.turn_id = ""
            self.messages = []
            self.pending.clear()
            self.activity = []
            self.usage = {}
            self.error = ""
            self._loaded = False
            self._save()
            self._changed()

    def respond(self, key: str, answers: dict[str, str]) -> None:
        """Answer a pending question or decline a non-CAD native operation."""
        with self.lock:
            request = self.pending.get(key)
            if request is None:
                raise ValueError("That request is no longer pending")
            questions = request["params"].get("questions", [])
            if any(not answers.get(q["id"], "").strip() for q in questions):
                raise ValueError("Answer each question before submitting")
            self.pending.pop(key)
            self._changed()
        params, method = request["params"], request["method"]
        if method == "item/tool/requestUserInput":
            result: dict[str, Any] = {
                "answers": {
                    q["id"]: {"answers": [answers[q["id"]]]}
                    for q in params.get("questions", [])
                }
            }
        else:
            result = {"decision": "decline"}
        self._process().answer(request["rpc_id"], result)

    def _request(self, key: Any, method: str, params: dict[str, Any]) -> None:
        if method == "item/tool/call":
            threading.Thread(target=self._tool, args=(key, params), daemon=True).start()
            return
        if method == "item/tool/requestUserInput":
            with self.lock:
                identity = uuid.uuid4().hex
                self.pending[identity] = {
                    "rpc_id": key,
                    "method": method,
                    "params": params,
                }
                self.phase = "Waiting for your answer"
                self._changed()
            return
        # CAD writes are authorized through the scoped MCP bridge. Native
        # shell/file escalation is not needed and must not broaden that scope.
        with contextlib.suppress(RuntimeError):
            if method == "item/permissions/requestApproval":
                result: dict[str, Any] = {"permissions": {}, "scope": "turn"}
            elif method == "mcpServer/elicitation/request":
                result = {"action": "decline", "content": None}
            else:
                result = {"decision": "decline"}
            self._process().answer(key, result)

    def _tool(self, key: Any, params: dict[str, Any]) -> None:
        # The primitive sessions hold native document state. Serialize CAD calls
        # even when a model issues several dynamic tool requests concurrently.
        with self._tools:
            try:
                self._run_tool(key, params)
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                    if self.activity and self.activity[-1]["status"] == "running":
                        self.activity[-1]["status"] = "failed"
                    self._changed()
                if self.process:
                    with contextlib.suppress(RuntimeError):
                        self.process.answer(
                            key,
                            {
                                "success": False,
                                "contentItems": [
                                    {"type": "inputText", "text": str(exc)}
                                ],
                            },
                        )

    def _run_tool(self, key: Any, params: dict[str, Any]) -> None:
        with self.lock:
            root = self.root
            mode = self.mode
            process = self.process
            active = (
                self.busy
                and not self.stopping
                and params.get("threadId") == self.thread_id
            )
        if not active and process is not None:
            with contextlib.suppress(RuntimeError):
                process.answer(
                    key,
                    {
                        "success": False,
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": "This turn is no longer active.",
                            }
                        ],
                    },
                )
            return
        if root is None or process is None:
            return
        tool = str(params.get("tool", ""))
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        with self.lock:
            self.phase = (
                "Finding tools"
                if tool == "kicad_search"
                else str(arguments.get("name", "Using KiCadFlow"))
            )
            self._changed()
            entry = {"id": uuid.uuid4().hex, "tool": self.phase, "status": "running"}
            self.activity.append(entry)
            self.activity = self.activity[-100:]
        document_call = tool == "kicad_call" and any(
            key in arguments.get("arguments", {})
            for key in ("path", "schematic_path", "board_path")
        )
        status = (
            self.project_status()
            if self.project_status and document_call
            else {"ready": True}
        )
        if not status.get("ready"):
            reply = {
                "success": False,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": self._editor_error(status),
                    }
                ],
            }
        else:
            reply = invoke(tool, arguments, root, read_only=mode == "ask")
        with self.lock:
            entry["status"] = "completed" if reply.get("success") else "failed"
            self._changed()
        with contextlib.suppress(RuntimeError):
            process.answer(key, reply)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self.lock:
            owner = params.get("threadId")
            if owner is not None and owner != self.thread_id:
                return
            if method == "disconnected":
                self.busy = False
                self._loaded = False
                self.phase = "Codex disconnected"
                self.account = None
                self.login_id = ""
                self.login_info = {}
            elif method == "account/updated":
                threading.Thread(
                    target=self._refresh_account, args=(self.process,), daemon=True
                ).start()
            elif method == "account/login/completed":
                if params.get("loginId") != self.login_id:
                    self._login_results = {str(params.get("loginId", "")): params}
                    return
                self.login_id = ""
                self.login_info = {}
                self.phase = "Signed in" if params.get("success") else "Sign-in failed"
                if params.get("success"):
                    threading.Thread(
                        target=self._refresh_account, args=(self.process,), daemon=True
                    ).start()
                else:
                    self.error = str(params.get("error") or "Sign-in did not complete")
            elif method == "item/agentMessage/delta":
                identity = str(params.get("itemId", ""))
                message = next((m for m in self.messages if m["id"] == identity), None)
                if message is None:
                    message = {"id": identity, "role": "assistant", "text": ""}
                    self.messages.append(message)
                message["text"] = (message["text"] + str(params.get("delta", "")))[
                    :64000
                ]
            elif method == "turn/started":
                self.turn_id = str(params.get("turn", {}).get("id", ""))
            elif method == "turn/completed":
                turn = params.get("turn", {})
                self.busy = False
                self.pending.clear()
                self.phase = str(turn.get("status", "Finished")).capitalize()
                error = turn.get("error")
                if error:
                    self.error = str(error.get("message", error))
                self._save()
            elif method == "thread/tokenUsage/updated":
                self.usage = params.get("tokenUsage", {}).get("total", {})
            elif method == "error":
                error = params.get("error", {})
                self.error = str(error.get("message", "Agent error"))
            elif method == "item/completed":
                self._save()
            else:
                return
            self._changed()

    def _cache(self) -> Path | None:
        if self.root is None:
            return None
        identity = hashlib.sha256(str(self.root).encode()).hexdigest()
        return Path.home() / ".kicad-flow" / "chat" / (identity + ".json")

    def _save(self) -> None:
        path = self._cache()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "thread_id": self.thread_id,
                        "model": self.model,
                        "messages": self.messages[-100:],
                        "busy": self.busy,
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            self.error = "Conversation could not be saved locally"

    def _restore(self) -> None:
        path = self._cache()
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid conversation")
            saved = data.get("messages", [])
            if not isinstance(saved, list):
                raise ValueError("Invalid messages")
            self.messages = [
                {"id": m["id"], "role": m["role"], "text": m["text"]}
                for m in saved[-100:]
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("id"), str)
                and isinstance(m.get("text"), str)
            ]
            self.thread_id = str(data.get("thread_id") or "")
            self.model = str(data.get("model") or "")
            if data.get("busy"):
                self.phase = "Previous turn was interrupted; inspect before continuing"
        except (OSError, ValueError, TypeError):
            self.error = "Saved conversation unavailable; start a new chat"

    def close(self) -> None:
        """Stop this owned agent process; keep account credentials with Codex."""
        if self.process:
            self.process.close()
