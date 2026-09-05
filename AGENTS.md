# AGENTS.md

An MCP server for authoring KiCad schematics and boards. `README.md` is the
shape of it.

## Commands

```
python -m ruff check .                      # lint; --fix to apply
python -m mypy                              # strict, src/ only
python examples/scripts/fc.py       # build an example, end to end
python -m kicad_flow.server --http          # the server on a real socket
```

Lint and types must pass before a commit. **There is no unit suite: the examples
are the check.** Run both before a change lands. Each goes through
`Client(mcp)` and nothing else, so the same runs exercise the tool layer and
the contracts underneath it.

## The rule that matters

**Simple APIs, maximum flexibility.** Every call does one thing, takes scalars,
and returns what it made. The caller composes them.

The line to hold is between a **fact** and a **decision**:

- A **fact** belongs in the API. Where a pin is once its part is rotated and
  mirrored. What the sheet actually connects. Which rule a sheet violates and at
  which pin. These are arithmetic or observation, the caller cannot get them
  without the file format, and getting them wrong is silent.
- A **decision** belongs to the caller. Where a part goes. Which way a wire
  turns. Which net gets a label. What the sheet should look like.

That line has been drawn in the wrong place twice, both times by adding
something helpful:

- 13,845 lines of autoplacer, floorplanner, wire router and design document were
  deleted because each one decided something and a caller who disagreed had
  nowhere to say so.
- Junctions, instance blocks and label anchoring were left to the caller because
  they looked like drawing choices. They are file-format mechanics, every one of
  them failed silently, and they cost more than the algorithms did.

When unsure: if two reasonable callers would want different answers, it is a
decision — expose a parameter and pick a sane default. If there is only one
correct answer and the caller cannot compute it, it is a fact — do it for them.

**Do not add inference.** A default that guesses from surrounding context is
worse than one that is plainly wrong half the time and takes an argument. The
caller can see what they meant; the layer cannot.

**Every schematic write takes a LIST.** `add_components`, `add_wires`,
`move_components` -- one item is a list of one, so there is no singular form to
choose between and no second way to do anything. This is the one place the
"takes scalars" rule is bent, and it buys two things: the call count an agent
pays drops by a factor of 20 or more, and every element stays a typed model, so
a misspelled key is rejected by the schema naming the exact index before
anything runs.

It bends the rule; it does not cross the line. The caller still supplies every
coordinate, and there is nothing expressible in a list of N that is not
expressible as N calls. Nothing is decided.

**Placement cannot share a call with wiring.** A wire is drawn to a coordinate
`add_components` REPORTS, so the parts have to land and answer first. Two
calls, always: place, read the pins out of the reply, then wire.

The board side has not been through this yet and still takes scalars; `batch`
runs its primitives N at a time until it has.

## Hard rules

**The abstraction holds, on both sides.** `schematic/api.py` and `pcb/api.py`
are ABCs naming no file format and no tool; `backend/kicad/` is the only code
that knows a `.kicad_sch` from a `.kicad_pcb`. Nothing above imports it and it
imports nothing above — the MCP layer talks to `Sheet` and `Board` and to
nothing else. That containment kept the `instances` and multi-unit bugs to one
file. The board side did not have it until recently, and the cost was a cycle:
`backend` reaching up into `pcb/` while `pcb/` reached down into `backend`.

**Millimetres and the 1.27 mm grid.** Every coordinate a caller gives is snapped.
A wire end and a pin that differ by a fraction of a millimetre are not connected
and nothing on the sheet says so.

**Never edit an example to make a change look good.** The examples are inputs.
If one comes out worse, the code is wrong or the trade is worth stating — the
example is neither.

**Standard library plus FastMCP.** No third-party file-format library; KiCad's
own s-expressions are written directly, so output tracks the installed KiCad.

## How to work here

**Measure, do not assert.** Every number in a docstring, commit message or reply
is something that was run. Three separate claims this project has made were
wrong because they were reasoned rather than measured — most recently that
rotation points a global label, which two renders showed it does not.

**Look at the output.** `check_sheet` catches the electrical half. It cannot see
a label printed over a pin number, a wire drawn through text, or a page that is
correct and unreadable. Render it and look. Every readability fault so far was
found that way and by nothing else.

**KiCad accepts a great deal in silence.** A missing `instances` block, a label
shape in the wrong slot, a child sheet named by a path that resolves nowhere —
all parse, open, render, and connect nothing. `list_nets` on the root is the
only thing that tells you what a design really is.

**Report what is still wrong.** A page that half works is described as half
working, with the numbers. A project that records only its wins cannot be
reasoned about.

## Layout

```
src/kicad_flow/
  schematic/            the sheet contract, and nothing else
    api.py              the ABC: 31 members, no tool named
    types.py            Point, Pin, Part, SymbolDef, Net, Finding, SheetRef
  pcb/                  the board contract, and nothing else
    api.py              the ABC: 34 members, no tool named
    types.py            Point, Pad, Footprint, Track, Via, Zone, Net, Finding
  backend/              the only code that knows a KiCad file from any other
    __init__.py         create/load -- the one place a concrete class is named
    kicad/              _sexpr, cli, _library, _fileio, render
    kicad/schematic/    KiCadSheet(Sheet), netlist
    kicad/pcb/          KiCadBoard(Board), library, _runner
  server/               FastMCP tools: 28 schematic + 30 board = 58
  monitor/              the live view and the tool-call log; its own process
examples/scripts/       fc and led_digits, both through MCP calls alone
```

Neither contract package imports a backend -- measured: `import
kicad_flow.schematic` and `import kicad_flow.pcb` each load none.
