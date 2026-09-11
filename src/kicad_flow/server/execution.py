"""Serialize design access through autosave, including nested tool dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

_HIERARCHY = {"check_sheet", "check_sheet_layout", "list_nets", "render_schematic"}
_SLOW = _HIERARCHY | {"check_board", "render_board", "render_board_layout",
                       "find_symbol"}


@dataclass(frozen=True)
class _Scope:
    projects: frozenset[str]
    exclusive: bool = False


def _scope(name: str, args: dict[str, Any]) -> _Scope:
    if not isinstance(args, dict):
        return _Scope(frozenset())
    if name == "call_tool":
        return _scope(str(args.get("name", "")), args.get("arguments") or {})
    if name == "batch":
        ops = args.get("ops", [])
        if not isinstance(ops, list):
            return _Scope(frozenset())
        scopes = [_scope(str(op.get("tool", "")), op.get("args") or {})
                  for op in ops if isinstance(op, dict)]
        return _Scope(frozenset(p for s in scopes for p in s.projects),
                      any(s.exclusive for s in scopes))
    projects = set()
    for key in ("path", "schematic_path", "board_path", "project_dir"):
        value = args.get(key)
        if isinstance(value, str) and value:
            path = Path(value).resolve()
            projects.add(os.path.normcase(str(path if key == "project_dir"
                                              else path.parent)))
    return _Scope(frozenset(projects), name in _HIERARCHY)


class _Gate:
    """Claim all project scopes together, without lock-order deadlocks."""

    def __init__(self) -> None:
        self.mutex = threading.Lock()
        self.projects: set[str] = set()
        self.exclusive = False

    def acquire(self, scope: _Scope) -> bool:
        with self.mutex:
            if self.exclusive or (scope.exclusive and self.projects):
                return False
            if scope.projects & self.projects:
                return False
            self.projects.update(scope.projects)
            self.exclusive = scope.exclusive
            return True

    def release(self, scope: _Scope) -> None:
        with self.mutex:
            self.projects.difference_update(scope.projects)
            if scope.exclusive:
                self.exclusive = False


_gate = _Gate()
_held: ContextVar[_Scope | None] = ContextVar("design_scope", default=None)
_batch_progress: ContextVar[bool] = ContextVar("batch_progress", default=False)


class DesignAccessMiddleware(Middleware):
    """Hold project access until execution and autosave have both finished.

    Reads serialize with writes. Hierarchy queries exclude all design calls,
    since child sheets can live outside the root's directory. This coordinates
    this process only; it does not lock external KiCad editors or other servers.
    """

    async def on_call_tool(self, context: MiddlewareContext[Any],
                           call_next: CallNext[Any, Any]) -> Any:
        """Acquire once per outer call; nested primitives reuse that claim."""
        name = str(getattr(context.message, "name", ""))
        scope = _scope(name, getattr(context.message, "arguments", None) or {})
        held = _held.get()
        if held is not None:
            if not held.exclusive and (scope.exclusive
                                       or not scope.projects <= held.projects):
                raise RuntimeError("nested tool requested an unclaimed design")
            return await call_next(context)
        if not scope.projects and not scope.exclusive:
            return await call_next(context)
        while not _gate.acquire(scope):
            await asyncio.sleep(0.005)

        async def run() -> Any:
            token = _held.set(scope)
            try:
                return await call_next(context)
            finally:
                _held.reset(token)
                _gate.release(scope)

        task = asyncio.create_task(run())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A thread running a synchronous KiCad tool cannot be killed safely.
            # Keep its claim until it finishes, even if its caller disconnects.
            while not task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(task)
            if not task.cancelled():
                task.exception()  # consume a failure after client cancellation
            raise


class ProgressMiddleware(Middleware):
    """Report honest start/completion boundaries for slow native operations."""

    async def on_call_tool(self, context: MiddlewareContext[Any],
                           call_next: CallNext[Any, Any]) -> Any:
        """Send progress only when the client supplied a progress token."""
        name = str(getattr(context.message, "name", ""))
        if name == "batch":
            token = _batch_progress.set(True)
            try:
                return await call_next(context)
            finally:
                _batch_progress.reset(token)
        ctx = context.fastmcp_context
        enabled = ctx is not None and name in _SLOW and not _batch_progress.get()
        if enabled and ctx is not None:
            await ctx.report_progress(0, 1, f"Running {name}")
        result = await call_next(context)
        if enabled and ctx is not None:
            await ctx.report_progress(1, 1, f"Finished {name}; inspect the result")
        return result
