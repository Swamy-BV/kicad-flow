"""Write the design to disk after every call that changes it.

A sheet stays open in the server between calls and used to reach disk only on
`save_sheet`. That is why the monitor looked dead: it renders the FILE, and
until a save there was no file to render -- measured, `new_sheet` followed by
five `add_components` left nothing on disk at all. An agent could place 300
parts and the preview would not move.

Two costs decide the shape of this. Saving is 7.2 ms on a 60-part A3 page;
rendering the same page is 530 ms, seventy times more. So the file is written
after EVERY change, and the monitor renders at whatever rate it can manage --
polling every 0.6 s and coalescing, so a burst of calls produces one picture of
the latest state rather than a queue of stale ones. Rendering per call was
never the right target: `led_digits` makes 150 calls, which at 530 ms each
would be 80 seconds of rendering for a build that takes 22.

It never affects the call. A save that fails -- KiCad holding the file, a
read-only directory -- is swallowed, exactly as the activity log is: the design
in memory is still correct and `save_sheet` will report the problem properly
when the caller asks for it.

Set ``KICAD_FLOW_AUTOSAVE=0`` to turn it off.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

#: Tool-name prefixes that change a design. `save_*` is excluded because it
#: has just written; `check_`, `get_`, `list_`, `find_` and `what_` read.
_WRITES = ("add_", "move_", "remove_", "rotate_", "mirror_", "set_", "place_",
           "flip_", "refill_", "new_", "batch")


def _enabled() -> bool:
    """Whether autosave is on. Unset means on."""
    return os.environ.get("KICAD_FLOW_AUTOSAVE", "on").strip().lower() not in (
        "0", "off", "false", "no", "")


class AutosaveMiddleware(Middleware):
    """Persist the design after each successful write, best-effort."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        """Run the tool, then put what it changed on disk."""
        result = await call_next(context)
        if not _enabled():
            return result
        name = getattr(context.message, "name", "") or ""
        if not name.startswith(_WRITES):
            return result
        data = getattr(result, "structured_content", None)
        if not isinstance(data, dict) or data.get("ok") is not True:
            return result       # a refusal changed nothing worth writing
        for path in _paths_in(getattr(context.message, "arguments", None)):
            with contextlib.suppress(Exception):
                _save(path)
        return result


def _paths_in(arguments: Any) -> list[str]:
    """Every design path named by a call, including inside a `batch`."""
    if not isinstance(arguments, dict):
        return []
    found: list[str] = []
    path = arguments.get("path")
    if isinstance(path, str):
        found.append(path)
    for op in arguments.get("ops") or []:            # batch carries its own
        if isinstance(op, dict):
            inner = (op.get("args") or {}).get("path")
            if isinstance(inner, str) and inner not in found:
                found.append(inner)
    return found


def _save(path: str) -> None:
    """Write one open design, if it is one this server has."""
    from . import tools_board, tools_schematic

    if path.endswith(".kicad_sch"):
        registry, key = tools_schematic._OPEN, tools_schematic._key(path)
    elif path.endswith(".kicad_pcb"):
        registry, key = tools_board._OPEN, tools_board._key(path)
    else:
        return
    design = registry.get(key)
    if design is not None:
        design.save()


__all__ = ["AutosaveMiddleware"]
