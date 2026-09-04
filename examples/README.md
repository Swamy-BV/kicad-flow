# Examples

Six designs, each one a script in [`scripts/`](scripts/) that builds a folder
next to it. **Every one reports 0 ERC errors and 0 warnings.**

The scripts are the source; the folders are output. Rebuild any of them:

```bash
python examples/scripts/fc_via_mcp.py
```

| example | script | files | nets | what it shows |
| --- | --- | --- | --- | --- |
| [`mcp_sheet`](mcp_sheet/) | `draw_via_mcp.py` | 1 | 5 | the smallest one: a 5 V → 3.3 V regulator |
| [`usbc_3v3`](usbc_3v3/) | `usbc_via_mcp.py` | 1 | 14 | USB-C in, both CC pulldowns, polyfuse then TVS, one ground rail |
| [`dual_buffer`](dual_buffer/) | `dual_buffer_via_mcp.py` | 1 | 10 | an LM358 — a multi-unit symbol, three units sharing one reference |
| [`usb_ttl4`](usb_ttl4/) | `usb_ttl4_via_mcp.py` | 1 | 55 | an FT4232H four-channel USB-to-TTL adapter, 64 pins |
| [`multisheet`](multisheet/) | `multisheet_via_mcp.py` | 4 | 8 | a hierarchy: signals cross by port, power crosses by name |
| [`fc`](fc/) | `fc_via_mcp.py` | 5 | 76 | an STM32F405 flight controller, crossing sheets by global label |

## How to read them

Every script drives the MCP server and nothing else. It **chooses every
coordinate itself** and asks the server only for facts — where a pin is, what a
symbol's box measures, what the sheet actually connects. There is no autoplacer
and no wire router to reach for.

Each one ends by asking the sheet what it became, rather than assuming:

```python
report = await call("check_sheet", path=root)   # violations, by part and pin
nets   = await call("list_nets",   path=root)   # what is ACTUALLY connected
```

That pairing is the point. `check_sheet` catches the electrical half; the render
catches how it reads. Both are needed, and the second is still a human looking
at a picture.

Where a layout comes out badly, that is the finding — not a reason to reach past
the API. The comments in each script say which choices were deliberate and which
were mistakes worth keeping a note of.

## What each one taught

- **`dual_buffer`** — an LM358's three units share one reference and are placed
  one call at a time. Before `unit=` existed, all eight pins came back at one
  unit's coordinates and produced a silently wrong netlist.
- **`multisheet`** — power crosses a sheet boundary **by name** and signals
  cross **by port**. The power page has no ports at all despite feeding both
  other pages.
- **`fc`** — with twenty-two signals between pages, ports would turn the root
  into a wiring diagram of its own. Global labels say the same thing in one
  symbol, which is how real flight controller schematics are drawn.

## Other scripts

`drone_frame.py` and `jlc_lookup.py` are board-side utilities, not schematic
examples.
