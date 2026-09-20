"""Operator-configured MCP batch sizes; primitive contracts stay unrestricted."""

from __future__ import annotations

import os
from typing import Annotated, TypeVar

from pydantic import Field


def _batch_limit() -> int:
    value = os.environ.get("KICAD_FLOW_BATCH_LIMIT", "5")
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError("KICAD_FLOW_BATCH_LIMIT must be a positive integer") from exc
    if limit < 1:
        raise ValueError("KICAD_FLOW_BATCH_LIMIT must be a positive integer")
    return limit


# Resolve once, so validation and advertised schemas agree for this server process.
BATCH_LIMIT = _batch_limit()
_Item = TypeVar("_Item")
BatchItems = Annotated[list[_Item], Field(max_length=BATCH_LIMIT)]
