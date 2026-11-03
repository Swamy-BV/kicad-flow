"""Command-line access to the same project agent used by the GUI."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from .client import AgentClient


def _run(client: AgentClient, args: argparse.Namespace) -> int:
    if args.project:
        client.action("project", path=str(Path(args.project).expanduser().resolve()))
    state = client.action("connect")
    if not state.get("account"):
        raise ValueError("Run kicad-flow-agent login to link your Codex account first")
    printed = {m["id"]: m["text"] for m in state.get("messages", [])}
    client.action(
        "send",
        text=args.message,
        model=args.model or state["model"],
        mode="ask" if args.ask else "edit",
    )
    seen_tools: set[str] = set()
    while True:
        state = client.state()
        for entry in state.get("activity", []):
            identity = entry["id"] + entry["status"]
            if identity not in seen_tools:
                print(f"[{entry['status']}] {entry['tool']}", file=sys.stderr)
                seen_tools.add(identity)
        for message in state.get("messages", []):
            if message["role"] == "assistant":
                previous = printed.get(message["id"], "")
                text = message["text"]
                print(text[len(previous) :], end="", flush=True)
                printed[message["id"]] = text
        for question in state.get("pending", []):
            if not sys.stdin.isatty():
                client.action("stop")
                raise RuntimeError("Agent needs an answer; continue in the GUI")
            answers = {
                q["id"]: input("\n" + q["question"] + "\n> ")
                for q in question["params"]["questions"]
            }
            client.action("respond", id=question["id"], answers=answers)
        if not state.get("busy"):
            print()
            if state.get("error"):
                raise RuntimeError(state["error"])
            return 0
        time.sleep(0.3)


def main() -> int:
    """Run account linking, session inspection or a project agent turn."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="KiCadFlow project agent")
    parser.add_argument("--port", type=int, help="explicit local service port")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show account and selected project")
    commands.add_parser("connect", help="reuse the local Codex account")
    login = commands.add_parser("login", help="sign in through Codex")
    login.add_argument(
        "--device", action="store_true", help="show a device sign-in code"
    )
    login.add_argument("--no-open", action="store_true", help="print the sign-in URL")
    commands.add_parser("cancel-login", help="cancel unfinished sign-in")
    commands.add_parser("disconnect", help="disconnect this app from Codex")
    project = commands.add_parser(
        "project", help="select a project folder or KiCad file"
    )
    project.add_argument("path")
    run = commands.add_parser("run", help="describe a change and stream the result")
    run.add_argument("message")
    run.add_argument("--project")
    run.add_argument("--model", help="model identifier from the connected catalogue")
    run.add_argument("--ask", action="store_true", help="inspect without CAD writes")
    commands.add_parser("stop", help="interrupt the active turn")
    args = parser.parse_args()
    client: AgentClient | None = None
    try:
        client = AgentClient(args.port)
        client.ensure_running()
        if args.command == "run":
            return _run(client, args)
        result: dict[str, Any]
        if args.command == "status":
            state = client.state()
            result = {
                k: state[k]
                for k in (
                    "connected",
                    "installed",
                    "account",
                    "project",
                    "model",
                    "phase",
                    "busy",
                )
            }
        elif args.command == "project":
            client.action("project", path=str(Path(args.path).expanduser().resolve()))
            result = {"project": client.state()["project"]}
        elif args.command == "login":
            result = client.action("login", device=args.device)
            if not args.no_open:
                webbrowser.open(result["auth_url"])
        else:
            result = client.action(args.command)
            result = {k: v for k, v in result.items() if k not in ("csrf", "messages")}
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except KeyboardInterrupt:
        if client is not None and args.command == "run":
            client.action("stop")
        print("\nStopped.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
