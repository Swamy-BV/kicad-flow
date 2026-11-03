"""Small native chat window; the design itself stays in the KiCad editors."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any
from urllib.parse import urlparse

from kicad_flow.agent import AgentSession
from kicad_flow.agent.companion import CompanionService
from kicad_flow.backend import DesktopProject


class CompanionWindow:
    """Keep a resizable chat window beside a live native KiCad project."""

    def __init__(self, project: str | None = None) -> None:
        """Build the small window, then connect asynchronously without inference."""
        self.root = tk.Tk(className="KiCadFlow")
        self.root.title("KiCadFlow · Live chat")
        self.root.geometry(f"390x660+{max(0, self.root.winfo_screenwidth() - 414)}+60")
        self.root.minsize(340, 460)
        self.root.configure(background="#17212e")
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.desktop = DesktopProject()
        self.session = AgentSession(self.desktop.open, self.desktop.status)
        self.events: queue.Queue[tuple[Any, Any, str]] = queue.Queue()
        self.service = CompanionService(
            self.session, lambda: self.events.put((self._focus, None, ""))
        )
        self.posting = False
        self.closing = False
        self.revision = ""
        self.state: dict[str, Any] = {}
        self.local_error = ""
        self.messages_key = ""
        self.questions_key = ""
        self.models: dict[str, str] = {}
        self.checking_editor = False
        self.next_editor_check = 0.0
        self._build()
        self.root.after(120, self._tick)

        def start() -> None:
            if project:
                self.session.select(str(Path(project).expanduser().resolve()))
            self.session.connect()

        self._work(start)

    def _build(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        configure: Callable[..., Any] = style.configure
        configure("TFrame", background="#17212e")
        configure(
            "TLabel", background="#17212e", foreground="#dfe8f1", font=("Segoe UI", 10)
        )
        configure("Muted.TLabel", foreground="#9aaabd", font=("Segoe UI", 9))
        configure("TButton", font=("Segoe UI", 9), padding=(7, 4))
        configure("TCheckbutton", background="#17212e", foreground="#9aaabd")
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text="KiCadFlow", font=("Segoe UI", 12, "bold")).pack(
            side="left"
        )
        self.pin = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            header,
            text="Pin",
            variable=self.pin,
            command=lambda: self.root.attributes("-topmost", self.pin.get()),
        ).pack(side="right")
        self.account_button = ttk.Button(
            header, text="Connecting…", command=self._account
        )
        self.account_button.pack(side="right", padx=(0, 8))
        project_row = ttk.Frame(shell)
        project_row.pack(fill="x", pady=(10, 2))
        self.project_label = ttk.Label(project_row, text="Choose your project")
        self.project_label.pack(side="left", fill="x", expand=True)
        self.open_button = ttk.Button(project_row, text="Open…", command=self._project)
        self.open_button.pack(side="right")
        self.editor_label = ttk.Label(
            shell, text="Changes appear in KiCad", style="Muted.TLabel"
        )
        self.editor_label.pack(anchor="w", pady=(0, 8))
        self.messages = ScrolledText(
            shell,
            wrap="word",
            borderwidth=0,
            background="#101923",
            foreground="#e0e8f2",
            insertbackground="#e0e8f2",
            padx=12,
            pady=12,
            font=("Segoe UI", 10),
            state="disabled",
            takefocus=True,
        )
        self.messages.pack(fill="both", expand=True)
        self.messages.tag_configure("user", foreground="#9aaabd", spacing1=12)
        self.messages.tag_configure("assistant", foreground="#a5dfb9", spacing1=12)
        self.messages.tag_configure("body", spacing1=5, spacing3=10)
        self.question_frame = ttk.Frame(shell)
        self.question_frame.pack(fill="x")
        self.activity = ttk.Label(shell, text="", style="Muted.TLabel", wraplength=355)
        self.activity.pack(fill="x", pady=(6, 4))
        self.prompt = tk.Text(
            shell,
            height=4,
            wrap="word",
            background="#253345",
            foreground="#f1f5fa",
            insertbackground="#f1f5fa",
            relief="flat",
            padx=9,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.prompt.pack(fill="x")
        self.prompt.bind("<Control-Return>", lambda _event: self._send())
        controls = ttk.Frame(shell)
        controls.pack(fill="x", pady=(7, 3))
        self.mode = ttk.Combobox(
            controls, state="readonly", width=9, values=("Edit project", "Ask only")
        )
        self.mode.set("Edit project")
        self.mode.pack(side="left")
        self.model = ttk.Combobox(controls, state="readonly", width=13)
        self.model.pack(side="left", fill="x", expand=True, padx=5)
        self.stop_button = ttk.Button(
            controls, text="Stop", command=lambda: self._work(self.session.stop)
        )
        self.stop_button.pack(side="right")
        self.send_button = ttk.Button(controls, text="Send", command=self._send)
        self.send_button.pack(side="right", padx=(0, 4))
        self.phase = ttk.Label(
            shell,
            text="Connecting to your account…",
            style="Muted.TLabel",
            wraplength=355,
        )
        self.phase.pack(fill="x", pady=(3, 0))
        self.error_label = ttk.Label(
            shell, text="", foreground="#ffb2a7", wraplength=355
        )
        self.error_label.pack(fill="x")

    def _work(
        self, action: Callable[[], Any], callback: Callable[[Any], None] | None = None
    ) -> None:
        if self.posting:
            return
        self.posting = True
        self.local_error = ""

        def execute() -> None:
            try:
                self.events.put((callback, action(), ""))
            except Exception as exc:
                self.events.put((None, None, str(exc)))

        threading.Thread(target=execute, daemon=True).start()

    def _project(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Open KiCad project",
            filetypes=[
                ("KiCad project", "*.kicad_pro"),
                ("KiCad schematic", "*.kicad_sch"),
                ("KiCad PCB", "*.kicad_pcb"),
            ],
        )
        if filename:
            self.phase.configure(text="Opening native KiCad editors…")
            self._work(lambda: self.session.select(filename))

    def _account(self) -> None:
        if self.state.get("login"):
            self._open_login(self.state["login"])
        elif not self.state.get("connected"):
            self._work(self.session.connect)
        elif not self.state.get("account"):
            self._work(self.session.login, self._open_login)
        else:
            self._work(self.session.connect)

    def _open_login(self, info: dict[str, Any]) -> None:
        url = str(info.get("auth_url", ""))
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in (
            "auth.openai.com",
            "chatgpt.com",
        ):
            webbrowser.open(url)

    def _send(self) -> str:
        text = self.prompt.get("1.0", "end").strip()
        if text and not self.posting and not self.state.get("busy"):
            model = self.models.get(self.model.get(), "")
            mode = "ask" if self.mode.get() == "Ask only" else "edit"
            self._work(
                lambda: self.session.send(text, model, mode),
                lambda _value: self.prompt.delete("1.0", "end"),
            )
        return "break"

    def _focus(self, _value: Any = None) -> None:
        self.root.deiconify()
        self.root.lift()
        self.prompt.focus_set()

    def _questions(self) -> None:
        pending = self.state.get("pending", [])
        signature = str(pending)
        if signature == self.questions_key:
            return
        self.questions_key = signature
        for child in self.question_frame.winfo_children():
            child.destroy()
        for request in pending:
            entries: dict[str, ttk.Entry] = {}
            for question in request["params"].get("questions", []):
                ttk.Label(
                    self.question_frame, text=question["question"], wraplength=350
                ).pack(fill="x", pady=(4, 0))
                entry = ttk.Entry(self.question_frame)
                entry.pack(fill="x")
                entries[question["id"]] = entry

            def reply(
                key: str = request["id"], fields: dict[str, ttk.Entry] = entries
            ) -> None:
                answers = {identity: field.get() for identity, field in fields.items()}
                self._work(lambda: self.session.respond(key, answers))

            ttk.Button(self.question_frame, text="Reply", command=reply).pack(
                anchor="e"
            )

    def _render(self) -> None:
        state = self.state
        busy = bool(state.get("busy") or self.posting)
        self.open_button.configure(state="disabled" if busy else "normal")
        if state.get("busy"):
            self.mode.set("Ask only" if state.get("mode") == "ask" else "Edit project")
        ready = (
            state.get("account")
            and state.get("connected")
            and state.get("project")
            and (self.mode.get() == "Ask only" or state.get("editor", {}).get("ready"))
        )
        self.send_button.configure(state="normal" if ready and not busy else "disabled")
        self.stop_button.configure(state="normal" if state.get("busy") else "disabled")
        self.mode.configure(state="disabled" if busy else "readonly")
        self.model.configure(state="disabled" if busy else "readonly")
        self.account_button.configure(state="disabled" if busy else "normal")
        account = state.get("account") or {}
        self.account_button.configure(
            text=(
                "Continue sign-in"
                if state.get("login")
                else "ChatGPT linked"
                if account.get("type") == "chatgpt"
                else "API key account"
                if account
                else "Sign in"
                if state.get("connected")
                else "Connect"
            )
        )
        self.project_label.configure(
            text=state.get("project_name") or "Choose your project"
        )
        documents = state.get("editor", {}).get("documents", [])
        self.editor_label.configure(
            text=" · ".join(
                "Schematic open" if doc["kind"] == "schematic" else "PCB open"
                for doc in documents
            )
            or "Changes appear in KiCad"
        )
        catalogue = {
            m.get("displayName") or m["model"]: m["model"]
            for m in state.get("models", [])
        }
        if catalogue != self.models:
            self.models = catalogue
            self.model.configure(values=list(catalogue))
            self.model.set(
                next(
                    (
                        label
                        for label, model in catalogue.items()
                        if model == state.get("model")
                    ),
                    "",
                )
            )
        messages = state.get("messages", [])
        signature = str(messages)
        if signature != self.messages_key:
            follow = self.messages.yview()[1] >= 0.97
            self.messages.configure(state="normal")
            self.messages.delete("1.0", "end")
            if not messages:
                self.messages.insert(
                    "end", "Chat with your KiCad project\n", "assistant"
                )
                self.messages.insert(
                    "end",
                    "Open a project and describe a change. "
                    "Watch it happen in the native editor.\n",
                    "body",
                )
            for message in messages:
                self.messages.insert(
                    "end",
                    "You\n" if message["role"] == "user" else "KiCadFlow\n",
                    message["role"],
                )
                self.messages.insert("end", message["text"] + "\n", "body")
            self.messages.configure(state="disabled")
            if follow:
                self.messages.see("end")
            self.messages_key = signature
        activity = state.get("activity", [])
        self.activity.configure(
            text=(
                activity[-1]["tool"] + " · " + activity[-1]["status"]
                if activity
                else ""
            )
        )
        self.phase.configure(text=state.get("phase") or "Open a project to start")
        self.error_label.configure(text=self.local_error or state.get("error", ""))
        self._questions()

    def _tick(self) -> None:
        if not self.root.winfo_exists():
            return
        while True:
            try:
                callback, result, error = self.events.get_nowait()
            except queue.Empty:
                break
            if callback != self._focus:
                self.posting = False
            if error:
                self.local_error = error
            if callback:
                callback(result)
            try:
                if not self.root.winfo_exists():
                    return
            except tk.TclError:
                return
        current = self.session.snapshot(self.revision)
        if current.get("changed"):
            self.state = current
            self.revision = str(current["revision"])
        if (
            not self.closing
            and not self.checking_editor
            and time.monotonic() >= self.next_editor_check
        ):
            self.checking_editor = True
            self.next_editor_check = time.monotonic() + 2

            def inspect() -> None:
                try:
                    self.session.refresh_editor()
                finally:
                    self.checking_editor = False

            threading.Thread(target=inspect, daemon=True).start()
        if not self.closing:
            self._render()
        self.root.after(150, self._tick)

    def close(self) -> None:
        """Stop the app connection, keeping all native KiCad windows open."""
        if self.closing:
            return
        self.closing = True
        self.root.withdraw()

        def finish() -> None:
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                self.session.stop()
            self.session.close()
            self.service.close()
            self.desktop.close()
            self.events.put((lambda _result: self.root.destroy(), None, ""))

        threading.Thread(target=finish, daemon=True).start()

    def run(self) -> None:
        """Run the native event loop until the user closes this chat window."""
        self.root.mainloop()
