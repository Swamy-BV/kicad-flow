"""Record every MCP tool call to a JSONL activity log.

The live monitor (:mod:`kicad_flow.monitor`) runs as a *separate* process, so
it can't see into the stdio MCP server directly. This middleware bridges them:
it appends one JSON line per tool call -- name, arguments, the file it touched,
what came back, and duration -- to a log the monitor tails for its activity
feed and to learn which design is currently being edited.

The record has to carry the tool's ANSWER, not just that it was called.
"check_sheet took 1.3s" is not reviewable; "check_sheet found 0 violations" is.
So each line carries the outcome, any error text, and a digest of the scalar
values the tool returned (parts, nets, errors, violations, ...) -- see
:func:`_digest`.

The log is best-effort: a logging failure never disturbs the tool call.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

# Args whose value is a path we render; the monitor follows the newest one.
_PATH_ARGS = ("schematic_path", "board_path", "path", "project_dir")
# Args naming a project FOLDER outright, rather than a file inside one.
_DIR_ARGS = ("project_dir",)


#: Roll a log over at this size. It is append-only and nothing trimmed it:
#: one found in use had reached 61.7 MB and 134,137 records, and the monitor
#: had to read all of them before it could show anything.
_MAX_LOG = 8_000_000


def _roll(target: Path) -> None:
    """Rename the log aside once it passes :data:`_MAX_LOG`.

    One generation is kept, as ``<name>.1``, so a run that has just finished is
    still readable. Failure is ignored: a log that cannot be rolled is a log
    that keeps growing, which is better than a tool call that fails.
    """
    try:
        if target.stat().st_size < _MAX_LOG:
            return
    except OSError:
        return
    with contextlib.suppress(OSError):
        previous = target.with_suffix(target.suffix + ".1")
        previous.unlink(missing_ok=True)
        target.rename(previous)


def activity_log_path() -> Path:
    """Where tool calls are logged (``$KICAD_FLOW_ACTIVITY`` or the default)."""
    env = os.environ.get("KICAD_FLOW_ACTIVITY")
    return Path(env) if env else Path.home() / ".kicad-flow" / "activity.jsonl"


def project_log_path(project_dir: Path) -> Path:
    """A project's own copy of the feed, ``<project>/logs/mcp.jsonl``.

    The global log interleaves every project the server has ever touched, which
    is what the monitor wants (one live feed) and the worst thing to hand
    someone reviewing a single board. So each project keeps its own record
    alongside its other artefacts. It goes under ``logs/`` rather than loose in
    the project so a ``.gitignore`` can drop the whole folder in one line.
    """
    return project_dir / "logs" / "mcp.jsonl"


def _project_of(args: dict[str, Any] | None) -> Path | None:
    """The project folder a tool call names, if it names one at all."""
    if not isinstance(args, dict):
        return None
    for key in _DIR_ARGS:
        if args.get(key):
            return Path(str(args[key]))
    for key in ("schematic_path", "board_path", "path"):
        if args.get(key):
            return Path(str(args[key])).parent
    return None


def _summarize(args: dict[str, Any] | None) -> tuple[str, str]:
    """Return ``(path, compact-args)`` for one tool call's arguments."""
    if not isinstance(args, dict):
        return "", ""
    path = next((str(args[k]) for k in _PATH_ARGS if args.get(k)), "")
    parts = []
    for k, v in args.items():
        if k in _PATH_ARGS:
            continue
        s = str(v)
        parts.append(f"{k}={s if len(s) <= 30 else s[:29] + '…'}")
        if len(parts) >= 4:
            break
    return path, ", ".join(parts)


# One argument's full value is worth keeping for review; an unbounded one is
# not. Cap per value and overall so one call cannot bloat the log.
_ARG_CHARS = 600
_ARGS_CHARS = 2400
# Scalars are the interesting part of a result; a list is worth its length.
_DIGEST_KEYS = 14
_ERROR_CHARS = 400


def _full_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Every argument, each value capped -- what a row expands to show."""
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    budget = _ARGS_CHARS
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, default=str)
        if len(s) > _ARG_CHARS:
            s = s[:_ARG_CHARS] + f"… (+{len(s) - _ARG_CHARS} chars)"
        budget -= len(s)
        if budget < 0:
            out["…"] = f"{len(args) - len(out)} more argument(s) elided"
            break
        out[k] = s
    return out


def _digest(data: Any) -> dict[str, Any]:
    """The scalar answers in a tool's result -- what made the call worth making.

    Numbers and short strings are kept as they are; a list or dict is kept as
    its length under ``<key>_n``, since "conflicts: 3" is the reviewable fact
    and the three conflicts themselves belong in the tool's own output. ``ok``
    and ``error`` are promoted to the top level of the record, so they are
    skipped here.
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k in ("ok", "error") or len(out) >= _DIGEST_KEYS:
            continue
        if isinstance(v, bool | int | float):
            out[k] = v
        elif isinstance(v, str):
            if 0 < len(v) <= 80:
                out[k] = v
        elif isinstance(v, list | dict):
            out[f"{k}_n"] = len(v)
    return out


def _outcome(result: Any) -> tuple[bool, str, dict[str, Any]]:
    """``(ok, error, digest)`` for a tool result.

    ``is_error`` alone is not the answer: it is set only when a tool RAISED, and
    almost nothing here does -- the house style is to catch and return
    ``{"ok": False, "error": ...}``. Reading only ``is_error`` logged every
    handled failure as a success, which is exactly the kind of thing someone
    reviewing a run needs to see.
    """
    if bool(getattr(result, "is_error", False)):
        return False, "", {}
    data = getattr(result, "structured_content", None)
    if not isinstance(data, dict):
        return True, "", {}
    ok = bool(data.get("ok", True))
    error = str(data.get("error", ""))[:_ERROR_CHARS]
    return ok, error, _digest(data)


class ActivityMiddleware(Middleware):
    """Append each tool call to the activity JSONL for the live monitor."""

    def __init__(self, log_path: Path | None = None) -> None:
        """Log to *log_path* (default: :func:`activity_log_path`)."""
        self._log = log_path or activity_log_path()
        # The project the run is working on, remembered across calls. Most calls
        # name no project at all -- searching parts, symbols and footprints is
        # the bulk of a real run and none of it carries a path -- so attributing
        # only the calls that do would scatter a session between the project log
        # and nowhere. The last project seen owns the ones in between.
        self._project: Path | None = None
        # One id per server process. Under stdio the host spawns a server per
        # session, so this is the run -- what lets the monitor group a feed
        # into "this build" instead of one endless stream.
        self._run = uuid.uuid4().hex[:8]
        with contextlib.suppress(OSError):
            self._log.parent.mkdir(parents=True, exist_ok=True)

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        """Time the tool call and append a record to the activity log."""
        msg = context.message
        name = str(getattr(msg, "name", "?"))
        arguments = getattr(msg, "arguments", None)
        path, args = _summarize(arguments)
        project = _project_of(arguments)
        start = time.perf_counter()
        ok, error = True, ""
        digest: dict[str, Any] = {}
        try:
            result = await call_next(context)
            ok, error, digest = _outcome(result)
            return result
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
            raise
        finally:
            # Resolved after the call: `new_sheet` is handed a path whose
            # folder does not exist until `save_sheet` runs.
            if project is not None and project.is_dir():
                self._project = project
            self._append(
                {
                    "t": time.time(),
                    "run": self._run,
                    "tool": name,
                    "args": args,
                    "argv": _full_args(arguments),
                    "path": path,
                    "project": str(self._project) if self._project else "",
                    "ok": ok,
                    "error": error,
                    "result": digest,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )

    def _append(self, record: dict[str, Any]) -> None:
        """Append to the global feed, and to the project's own if one is known.

        Both, not either: the monitor tails the global log and would go blank
        if a run wrote only to its project.
        """
        line = json.dumps(record) + "\n"
        targets = [self._log]
        if self._project is not None:
            targets.append(project_log_path(self._project))
        for target in targets:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                _roll(target)
                with target.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass  # never let logging break a tool call
