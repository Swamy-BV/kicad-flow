"""The server's instructions, one file per concern, joined here.

Instructions are injected into every conversation before the client has called
anything, so they are the first and often the only thing a caller reads. They
were a single 220-line string covering schematic, board and manufacturing at
once, and it drifted: the schematic third described a tool surface that had
been deleted, and nobody noticed because nothing in the file belonged to
anybody.

So the text is split by the thing it talks about. Each part sits beside the
tools it describes and is accurate or says plainly that it is not, and this
module does nothing but put them in order.

The order is the order the work happens in.
"""

from __future__ import annotations

from .pcb import PCB
from .schematic import SCHEMATIC

#: The whole instruction text handed to :class:`~fastmcp.FastMCP`.
INSTRUCTIONS = SCHEMATIC + PCB

__all__ = ["INSTRUCTIONS", "PCB", "SCHEMATIC"]
