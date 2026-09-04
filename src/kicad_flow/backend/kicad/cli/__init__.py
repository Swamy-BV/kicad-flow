"""``kicad-cli``, behind one class.

:class:`~.commands.KiCadCLI` has a method per command the tool offers; ``_run``
is the subprocess plumbing under it and is private. Nothing outside this
package should assemble an argv or start a process -- import :data:`cli` and
call a method.

    from kicad_flow.backend.kicad.cli import cli
    report = cli.drc("board.kicad_pcb", refill=True)

:class:`KiCadCliError` is public because it is the failure contract: it is what
every caller catches, and it subclasses ``RuntimeError`` so a caller that would
rather not name a KiCad type does not have to.
"""

from __future__ import annotations

from ._run import KiCadCliError
from .commands import MODEL_FORMATS, KiCadCLI, cli

__all__ = ["MODEL_FORMATS", "KiCadCLI", "KiCadCliError", "cli"]
