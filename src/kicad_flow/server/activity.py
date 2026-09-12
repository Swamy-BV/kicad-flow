"""Record every MCP tool call to a JSONL activity log.

The live monitor (:mod:`kicad_flow.monitor`) runs as a *separate* process, so
it can't see into the stdio MCP server directly. This middleware bridges them:
it appends one JSON line per primitive operation -- name, arguments, the file
it touched, what came back, and duration -- to a log the monitor tails for its
activity feed and to learn which design is currently being edited. A `batch`
request is therefore displayed as its individual operations, not its transport
wrapper.

The record has to carry the tool's ANSWER, not just that it was called.
"check_sheet took 1.3s" is not reviewable; "check_sheet found 0 violations" is.
So each line carries the outcome, any error text, and a digest of the scalar
values the tool returned (parts, nets, errors, violations, ...) -- see
:func:`_digest`. Calls that remain active for two seconds get a temporary
``running`` record with the same call id as their completion. Full arguments,
results and file revisions go to local ``replay.jsonl`` files which the monitor
never reads.

The log is best-effort: a logging failure never disturbs the tool call.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar
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
_MAX_REPLAY_LOG = 64_000_000
_SLOW_CALL_SECONDS = 2.0


def _roll(target: Path, limit: int = _MAX_LOG) -> None:
    """Rename the log aside once it passes *limit* bytes.

    One generation is kept, as ``<name>.1``, so a run that has just finished is
    still readable. Failure is ignored: a log that cannot be rolled is a log
    that keeps growing, which is better than a tool call that fails.
    """
    try:
        if target.stat().st_size < limit:
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


def replay_log_path(activity: Path | None = None) -> Path:
    """Global full replay log beside the compact activity log."""
    source = activity or activity_log_path()
    return source.with_name("replay.jsonl")


def project_log_path(project_dir: Path) -> Path:
    """A project's own copy of the feed, ``<project>/logs/mcp.jsonl``.

    The global log interleaves every project the server has ever touched, which
    is what the monitor wants (one live feed) and the worst thing to hand
    someone reviewing a single board. So each project keeps its own record
    alongside its other artefacts. It goes under ``logs/`` rather than loose in
    the project so a ``.gitignore`` can drop the whole folder in one line.
    """
    return project_dir / "logs" / "mcp.jsonl"


def project_replay_log_path(project_dir: Path) -> Path:
    """Full local call records, kept out of the monitor's web stream."""
    return project_dir / "logs" / "replay.jsonl"


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

_NestedLogger = Callable[[str, dict[str, Any], Any, float], None]
_NESTED_LOGGER: ContextVar[_NestedLogger | None] = ContextVar(
    "kicad_flow_nested_activity_logger", default=None
)


def record_nested_tool(name: str, arguments: dict[str, Any], result: Any,
                       elapsed_ms: float) -> None:
    """Record one primitive invoked inside a transport such as ``batch``.

    A context-local callback keeps the transport independent of any particular
    logger instance and remains safe when an HTTP server handles concurrent
    requests. Outside an activity-middleware call this is deliberately a no-op.
    """
    logger = _NESTED_LOGGER.get()
    if logger is not None:
        logger(name, arguments, result, elapsed_ms)


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
    its length under ``<key>_n``. Finding kinds and a bounded sample are retained
    so a review can identify what an agent was asked to correct. ``ok``
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
        elif k == "kind_counts" and isinstance(v, dict):
            out[k] = {str(kind)[:80]: count for kind, count in list(v.items())[:16]
                      if isinstance(count, int)}
        elif k in ("findings", "findings_sample") and isinstance(v, list):
            if k == "findings":
                out["findings_n"] = len(v)
            out["findings_sample"] = [
                {key: value[:180] if isinstance(value, str) else value
                 for key, value in item.items()
                 if key in {"kind", "severity", "message", "first", "second",
                            "sheet", "x", "y"}
                 and isinstance(value, str | bool | int | float)}
                for item in v[:3] if isinstance(item, dict)
            ]
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


def _result_data(result: Any) -> Any:
    """The complete structured result for the local replay log."""
    data = getattr(result, "structured_content", None)
    return data if data is not None else {"is_error": bool(
        getattr(result, "is_error", False)
    )}


def _revision(args: dict[str, Any] | None) -> dict[str, Any] | None:
    """Cheap file revision named by *args*, without reading the design."""
    if not isinstance(args, dict):
        return None
    value = next((args.get(key) for key in
                  ("schematic_path", "board_path", "path") if args.get(key)), None)
    if value is None:
        return None
    path = Path(str(value))
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


_QUALITY_FIELDS: dict[str, tuple[str, ...]] = {
    "check_sheet": ("errors", "warnings"),
    "check_sheet_layout": ("errors", "warnings"),
    "check_board": ("errors", "warnings"),
    "unrouted_connections": ("count",),
    "measure_schematic_placement": ("overlap_count", "page_violation_count"),
    "measure_placement": ("overlap_count", "edge_violation_count"),
}


def _metric_text(name: str, value: int | float) -> str:
    """Compact singular/plural metric text for the activity row."""
    label = name.removesuffix("_count").replace("_", " ")
    return f"{value} {label}"


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
        self._quality: dict[tuple[str, str], dict[str, int | float]] = {}
        self._failed_attempts: dict[str, int] = {}
        with contextlib.suppress(OSError):
            self._log.parent.mkdir(parents=True, exist_ok=True)

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        """Time the tool call and append a record to the activity log."""
        msg = context.message
        name = str(getattr(msg, "name", "?"))
        arguments = getattr(msg, "arguments", None)
        start = time.perf_counter()
        ok, error = True, ""
        digest: dict[str, Any] = {}
        nested = [0]
        call_id = uuid.uuid4().hex[:10]
        started_at = time.time()
        before = _revision(arguments)
        slow_logged = [False]
        raw_result: Any = None

        def log_nested(tool: str, argv: dict[str, Any], result: Any,
                       elapsed_ms: float) -> None:
            nested[0] += 1
            child_ok = bool(result.get("ok", True)) if isinstance(
                result, dict) else True
            child_error = (str(result.get("error", ""))[:_ERROR_CHARS]
                           if isinstance(result, dict) else "")
            self._record(tool, argv, child_ok, child_error, _digest(result),
                         elapsed_ms, raw_result=result)

        async def report_slow() -> None:
            await asyncio.sleep(_SLOW_CALL_SECONDS)
            slow_logged[0] = True
            self._record_start(call_id, name, arguments, started_at)

        parent_logger = _NESTED_LOGGER.get()
        wrapper = name in {"batch", "call_tool"}
        token = (_NESTED_LOGGER.set(log_nested)
                 if wrapper and parent_logger is None else None)
        slow_task = asyncio.create_task(report_slow())
        try:
            result = await call_next(context)
            ok, error, digest = _outcome(result)
            raw_result = _result_data(result)
            return result
        except asyncio.CancelledError:
            ok, error = False, "CancelledError: call cancelled"
            raw_result = {"ok": False, "error": error}
            raise
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
            raw_result = {"ok": False, "error": error}
            raise
        finally:
            slow_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await slow_task
            if token is not None:
                _NESTED_LOGGER.reset(token)
            # A non-empty batch has already emitted the actual primitives.
            # Keep a slow wrapper too, so its temporary running row completes.
            if (not wrapper or (parent_logger is None and nested[0] == 0)
                    or slow_logged[0]):
                elapsed = (time.perf_counter() - start) * 1000
                if parent_logger is not None:
                    parent_logger(name, arguments or {},
                                  {"ok": ok, "error": error, **digest}, elapsed)
                else:
                    self._record(
                        name, arguments, ok, error, digest, elapsed,
                        call_id=call_id, raw_result=raw_result, before=before,
                        started_at=started_at,
                    )

    def _record(self, name: str, arguments: dict[str, Any] | None, ok: bool,
                error: str, digest: dict[str, Any], elapsed_ms: float, *,
                call_id: str | None = None, raw_result: Any = None,
                before: dict[str, Any] | None = None,
                started_at: float | None = None) -> None:
        """Append one summarized tool record."""
        call_id = call_id or uuid.uuid4().hex[:10]
        path, args = _summarize(arguments)
        project = _project_of(arguments)
        # Resolved after the call: `new_sheet` may be handed a path whose
        # folder does not exist until the operation runs.
        if project is not None and project.is_dir():
            self._project = project
        quality = self._quality_change(name, path, digest)
        retry = self._retry(name, arguments, ok)
        record: dict[str, Any] = {
            "t": time.time(),
            "run": self._run,
            "call_id": call_id,
            "phase": "complete",
            "tool": name,
            "args": args,
            "argv": _full_args(arguments),
            "path": path,
            "project": str(self._project) if self._project else "",
            "ok": ok,
            "error": error,
            "result": digest,
            "ms": round(elapsed_ms, 1),
        }
        if quality is not None:
            record["quality"] = quality
        if retry is not None:
            record["retry"] = retry
        self._append(record)
        revision_before = before if before is not None else _revision(arguments)
        revision_after = _revision(arguments)
        self._append_replay({
            "t": record["t"],
            "started_at": started_at,
            "run": self._run,
            "call_id": call_id,
            "tool": name,
            "arguments": arguments or {},
            "result": raw_result if raw_result is not None else {
                "ok": ok, "error": error, **digest,
            },
            "ok": ok,
            "error": error,
            "ms": record["ms"],
            "revision_before": revision_before,
            "revision_after": revision_after,
            "changed": revision_before != revision_after,
        })

    def _record_start(self, call_id: str, name: str,
                      arguments: dict[str, Any] | None, started_at: float) -> None:
        """Expose only calls still running after the slow-call threshold."""
        path, args = _summarize(arguments)
        self._append({
            "t": started_at,
            "run": self._run,
            "call_id": call_id,
            "phase": "running",
            "tool": name,
            "args": args,
            "path": path,
            "project": str(self._project) if self._project else "",
            "ok": True,
            "error": "",
            "result": {},
        })

    def _retry(self, name: str, arguments: dict[str, Any] | None,
               ok: bool) -> dict[str, Any] | None:
        """Describe an exact retry after failure; ordinary repeats stay quiet."""
        encoded = json.dumps(arguments or {}, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(f"{name}\0{encoded}".encode()).hexdigest()
        previous = self._failed_attempts.get(fingerprint, 0)
        if ok:
            self._failed_attempts.pop(fingerprint, None)
            return ({"attempt": previous + 1, "recovered": True}
                    if previous else None)
        attempt = previous + 1
        self._failed_attempts[fingerprint] = attempt
        return {"attempt": attempt, "recovered": False} if attempt > 1 else None

    def _quality_change(self, name: str, path: str,
                        digest: dict[str, Any]) -> dict[str, str] | None:
        """Summarize measured quality changes without adding another feed row."""
        fields = _QUALITY_FIELDS.get(name)
        if fields is None:
            return None
        current = {
            field: value for field in fields
            if isinstance((value := digest.get(field)), int | float)
            and not isinstance(value, bool)
        }
        if not current:
            return None
        key = (path or str(self._project or ""), name)
        previous = self._quality.get(key)
        self._quality[key] = current
        if previous is None:
            nonzero = [(field, value) for field, value in current.items() if value]
            if not nonzero:
                return None
            return {
                "state": "attention",
                "summary": ", ".join(_metric_text(field, value)
                                     for field, value in nonzero),
            }
        changes = [(field, previous.get(field, 0), value)
                   for field, value in current.items()
                   if previous.get(field, 0) != value]
        if not changes:
            return None
        rose = any(after > before_value for _, before_value, after in changes)
        state = "degraded" if rose else (
            "clean" if not any(current.values()) else "improved"
        )
        return {
            "state": state,
            "summary": ", ".join(
                f"{field.removesuffix('_count').replace('_', ' ')} "
                f"{before_value}→{after}"
                for field, before_value, after in changes
            ),
        }

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

    def _append_replay(self, record: dict[str, Any]) -> None:
        """Write complete call data locally; the monitor never reads this file."""
        targets = [replay_log_path(self._log)]
        if self._project is not None:
            targets.append(project_replay_log_path(self._project))
        line = json.dumps(record, default=str) + "\n"
        for target in dict.fromkeys(targets):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                _roll(target, _MAX_REPLAY_LOG)
                with target.open("a", encoding="utf-8") as stream:
                    stream.write(line)
            except OSError:
                pass
