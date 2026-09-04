r"""Writing project files that another process may be reading at the same time.

Every edit rewrites a whole ``.kicad_sch``, and the file is not ours alone while
we do it: the live monitor re-renders the active sheet through ``kicad-cli``
whenever it changes, and KiCad itself may have it open. A plain
``Path.write_text`` truncates the file and then fills it, which loses both ways
on Windows -- the reader can observe a half-written sheet, and the open handle
can make the truncate fail outright with ``PermissionError``:

    ToolError: Error calling tool 'assign_lcsc':
    [Errno 13] Permission denied: '...\\rc_filter.kicad_sch'

:func:`write_text_atomic` fixes both. The new content goes to a temporary file
in the same directory and is swapped in with :func:`os.replace`, so a reader
sees either the whole old file or the whole new one, never a torn middle. The
swap is then retried briefly, because on Windows replacing a file that another
process holds open fails until it lets go -- and a render only holds it for a
moment.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from kicad_flow.backend.kicad import _sexpr as sexpr
from kicad_flow.backend.kicad._sexpr import Node

# Backoff between attempts to swap the new file in (seconds, ~3.5 s in total).
# A monitor render holds the sheet open only while kicad-cli reads it, so the
# window is short; the long tail is there for a slow first render.
_RETRY_DELAYS = (0.0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0)


def write_text_atomic(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically, retrying if a reader holds it open.

    The content lands in a sibling temporary file and is swapped in with
    :func:`os.replace`, so a concurrent reader never sees a partially written
    file. The swap is retried for a few seconds to ride out a reader that has
    the destination open (the monitor rendering the sheet, or KiCad).

    Args:
        path: Destination file.
        text: Full new contents.
        encoding: Text encoding (KiCad files are UTF-8).

    Raises:
        OSError: If the swap still fails after the retry budget. The message
            names the likely holder so the cause is not a bare errno.
    """
    path = Path(path)
    # A dotted name with a .tmp suffix so a stray temp file is neither picked up
    # by a *.kicad_sch scan nor mistaken for a project file; the pid keeps two
    # processes writing the same sheet from clobbering each other's temp.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())

    last: OSError | None = None
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:  # Windows: destination open elsewhere
            last = exc
        except OSError as exc:
            last = exc
            break

    with contextlib.suppress(OSError):
        tmp.unlink()
    raise OSError(
        f"could not write {path}: another process is holding it open "
        f"(the live monitor rendering it, or KiCad). Close it and retry. "
        f"Underlying error: {last}"
    ) from last


def save_tree(path: str | Path, root: Node) -> None:
    """Serialize an S-expression tree over *path* atomically.

    The single place a ``.kicad_sch`` is written, so every edit path gets the
    same crash- and reader-safety without repeating the serialize/write pair.
    """
    write_text_atomic(path, sexpr.dumps(root) + "\n")
