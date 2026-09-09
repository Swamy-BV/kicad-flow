"""Backends: the code that knows what a file on disk actually is.

Everything above this package talks to a contract -- :mod:`kicad_flow.schematic`
today, and :mod:`kicad_flow.pcb` once its abstraction exists. Everything that
knows a ``.kicad_sch`` from a ``.kicad_pcb``, that speaks KiCad's dialect of
S-expressions, or that shells out to ``kicad-cli``, lives under a backend and
nowhere else.

**Construction lives here, not in the contract.** :mod:`kicad_flow.schematic`
is the interface and the nouns; it imports no backend at all, so it can be read
and type-checked without one. Something still has to name a concrete class
eventually, and that is :func:`create` and :func:`load` -- one place, so a
second backend would be a change here and nowhere else.

That split is not tidiness. With the factories in the contract package the
import ran ``schematic -> backend.kicad -> sheet -> schematic``, a genuine
cycle: Python raised ``cannot import name 'PAPER' from partially initialized
module``, and before that mypy had quietly degraded ``Sheet`` to ``Any``, which
let ``KiCadSheet`` subclass nothing and reported no error at all.

There is one backend and one implementation of one contract, so this is not yet
a choice -- it is a **boundary**, and the boundary is the point: that
containment is what kept the ``instances`` and multi-unit bugs to a single file.

**The board side has not been through this.** :mod:`kicad_flow.pcb` is still 23
modules of pcbnew and kicad-cli with no interface over them, and it reaches into
:mod:`kicad_flow.backend.kicad` directly for ``cli``, ``render`` and ``sexpr``.
That import runs the wrong way -- a would-be contract importing a backend -- and
is the standing debt to clear when the board side gets its own ABC.
"""

from __future__ import annotations

from pathlib import Path

from kicad_flow.providers.api import ProjectLibrary

from .kicad import PAPER, create, create_board, load, load_board


def project_library(project_dir: str | Path) -> ProjectLibrary:
    """Construct the KiCad implementation of a project-local CAD library."""
    from .kicad.project_library import KiCadProjectLibrary

    return KiCadProjectLibrary(Path(project_dir))

__all__ = [
    "PAPER", "create", "create_board", "load", "load_board", "project_library"
]
