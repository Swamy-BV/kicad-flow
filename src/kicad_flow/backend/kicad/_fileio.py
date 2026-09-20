"""Atomic file publication tolerant of brief Windows preview read handles."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace_file(source: Path, target: Path) -> None:
    """Retry Windows sharing/access conflicts for at most half a second.

    Keep the old file intact until atomic replacement succeeds. Persistent
    permissions and locks still raise the original filesystem error.
    """
    deadline = time.monotonic() + 0.5
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if (os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(0.01)
