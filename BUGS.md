# Known bugs

Open defects in the API and the repo. Layout faults inside a particular example
are **not** tracked here: the examples are inputs, and a crowded block on one
sheet is not an API defect.

## Format

Every entry carries the same seven fields. `Measured` is the rule that matters:
it is a command or a result that was **run**, so the bug can be re-measured
rather than re-argued. An entry without one is a suspicion, not a bug.

```
### ID — one-line summary
Area · Severity · Status · Where
Symptom    what a caller sees
Measured   the observation, verbatim
Cause      why, in one or two lines
Fix        what would close it
```

**Severity** — `high` silently produces a wrong result; `medium` fails loudly
or misleads; `low` cosmetic or documentation.
**Status** — `open`, or `fixed in <commit>` (then delete it; do not leave it
ticked).

| ID | Summary | Area | Sev |
| --- | --- | --- | --- |
| [SCH-1](#sch-1--four-calls-ignore-the-unit-they-were-given) | Four calls ignore the unit they were given | schematic | high |
| [SCH-2](#sch-2--a-hierarchical-label-is-always-an-input) | A hierarchical label is always an input | schematic | high |
| [SCH-3](#sch-3--mirror-does-not-move-a-parts-fields-rotate-does) | `mirror` does not move a part's fields, `rotate` does | schematic | medium |
| [SCH-4](#sch-4--every-uuid-is-fresh-on-every-build) | Every UUID is fresh on every build | schematic | high |
| [SCH-5](#sch-5--the-root-reads-child-sheets-from-disk) | The root reads child sheets from disk | schematic | high |
| [SCH-6](#sch-6--find_symbols-reaches-past-libkicadlibrary) | `find_symbols` reaches past the library's public API | schematic | medium |
| [SCH-7](#sch-7--sheetsave-bypasses-the-atomic-writer) | `Sheet.save` bypasses the atomic writer | schematic | medium |
| [SCH-8](#sch-8--a-part-level-erc-finding-has-no-ref) | A part-level ERC finding has no ref | schematic | low |
| [SCH-9](#sch-9--label-refuses-a-justification-its-contract-offers) | `label` refuses a justification its contract offers | schematic | low |
| [SCH-10](#sch-10--the-primitive-count-in-apipy-is-wrong) | The primitive count in `api.py` is wrong | docs | low |
| [MCP-1](#mcp-1--no-tool-docstring-says-what-key-its-payload-uses) | No tool docstring says what key its payload uses | docs | low |
| [PCB-2](#pcb-2--a-rotated-or-flipped-footprint-does-not-match-its-library-copy) | A rotated or flipped footprint does not match its library copy | pcb | medium |

---

### SCH-1 — Four calls ignore the unit they were given
`schematic` · **high** · open · `backend/kicad/schematic/sheet.py:695,709,728` + `pin` at 732

**Symptom** `place` documents that "every call below that takes a *ref* takes a
*unit* alongside it". Four do not — `pin`, `set_field`, `move_field` and
`fields` resolve through `_require(ref)`, which defaults to unit 1. `get_pin`
has no `unit` argument at all, unlike its six sibling tools.

**Measured** an LM358 placed as its three units:

    s.pin("U1", "8")  ->  LookupError: U1 has no pin '8'; it has 1, 2, 3

Pin 8 is `V+`, it is on unit 3, and it is on the sheet. `check` resolves that
same pin correctly, so the sheet knows where it is.

**Cause** `_require(ref)` called without the caller's `unit`.

**Fix** Thread `unit` through all four, and add it to the `get_pin` tool. The
error message is the worse half — it asserts the pin does not exist.

---

### SCH-2 — A hierarchical label is always an input
`schematic` · **high** · open · `backend/kicad/schematic/sheet.py:798`

**Symptom** `label` writes `(shape input)` unconditionally and takes no shape
argument, while `add_sheet` takes a `kind` per port. Nothing reconciles them,
so the parent's declared direction and the child's label disagree, silently.

**Measured** the committed `multisheet` output: the root declares
`(pin "OUT_A" output)` and `(pin "IN_A" input)`; both child sheets contain
`4 x (shape input)` — all eight labels, whatever the parent said. ERC reports
0 errors and 0 warnings. Every hierarchical label in every design renders as
an inward arrow.

**Cause** The shape is hardcoded.

**Fix** A `shape` argument on `label`, defaulting to `input` so nothing
already drawn changes.

---

### SCH-3 — `mirror` does not move a part's fields, `rotate` does
`schematic` · **medium** · open · `backend/kicad/schematic/sheet.py:661` `rotate`, `:677` `mirror`

**Symptom** Two routes to the same placement give different sheets, and the
mirror route can print a part's reference over its own pins.

**Measured** `Connector:USB_C_Receptacle_USB2.0_14P`, whose body hangs
22.86 mm below its origin. Reference offset from the part position:

    place(...) then mirror("J1","x")  ->  dy = -20.32   (inside the body)
    place(..., mirror="x")            ->  dy = -26.67   (clear above it)

The mirrored body's top edge is at -22.86, so the first route puts the text
over the pins.

**Cause** `_layout_fields` takes a `mirror` argument and uses it; `place` and
`rotate` call it and `mirror` does not.

**Fix** Call `_layout_fields` from `mirror`. Merging `move`/`rotate`/`mirror`
into one placement call would remove the class of bug rather than the instance.

---

### SCH-4 — Every UUID is fresh on every build
`schematic` · **high** · open · `backend/kicad/schematic/sheet.py` `_uid()`

**Symptom** A rebuilt sheet cannot be diffed against its committed form, so
nothing can answer "did this change alter the output?" — the question a
regression check exists to ask, and the examples are the only check here.

**Measured** two consecutive identical builds of the smallest example:

    164 lines differ
    134 of them are (uuid ...)
     30 of them are (path "/<root-uuid>" ...)

100% of the diff between two identical runs is UUID churn.

**Cause** `_uid()` is `uuid.uuid4()`, called at 10 sites — every part, wire,
junction, label, sheet and no-connect.

**Fix** Derive them: UUIDv5 over a stable per-element identity under a fixed
namespace. Preserving them on load fixes only the edit case, not a rebuild.

---

### SCH-5 — The root reads child sheets from disk
`schematic` · **high** · open · `backend/kicad/schematic/sheet.py` `_scratch`

**Symptom** `list_nets`, `check_sheet` and `render_schematic` on a root report
the *last saved* state of every child. An unsaved child is reported as though
its parts were not there, with nothing said.

**Measured** a root with one child, `R2` and its wire added to the child in
memory and not saved:

    child in memory has parts: ['R1', 'R2']
    root nets():  [('/Child/SIG', ['R1.1']), ('unconnected-(R1-Pad2)', ['R1.2'])]
    root check(): 2 errors

**Cause** `_scratch` writes only the sheet it was called on; children resolve
by filename off disk.

**Fix** Flush every open sheet before these three calls, or report which
children were read from disk so the caller can tell.

---

### SCH-6 — `find_symbols` reaches past the library's public API
`schematic` · **medium** · open · `backend/kicad/schematic/sheet.py:376-377`

**Symptom** The only private-member access anywhere in the schematic layer.
Separately, the contract is wrong: the ABC says "matching *query*, by id,
description or keyword"; the implementation matches the id alone.

**Measured** `find_symbols("operational amplifier")` returns 0 results;
`find_symbols("LM358")` returns 2.

**Cause** It calls `library._libraries_in` and `library._library_symbol_names`
and reimplements the walk. The public `library.search_symbols` did this and was
deleted as unreachable in `229cbf5`.

**Fix** Recover `search_symbols` from `229cbf5^` rather than rewriting it, and
correct the ABC docstring to say what the search actually matches.

---

### SCH-7 — `Sheet.save` bypasses the atomic writer
`schematic` · **medium** · open · `backend/kicad/schematic/sheet.py:355`

**Symptom** The atomic-write protection does not cover the primary schematic
write path. It exists because of an observed `Permission denied` while KiCad
held a file open.

**Measured** `sheet.py:355` is `self._path.write_text(...)`, while
`backend/kicad/_fileio.py:86` `save_tree` documents itself as "the single place
a `.kicad_sch` is written". Two write paths; the primary one is not atomic.

**Cause** `save` predates or ignores `fileio.save_tree`.

**Fix** Route `save` through `_fileio.save_tree`. That module now has no
caller at all -- it survived the `fab/` deletion only because it is this fix. `_scratch` at line 876 is a
throwaway and can stay as it is.

---

### SCH-8 — A part-level ERC finding has no ref
`schematic` · **low** · open · `backend/kicad/schematic/sheet.py` `check`

**Symptom** Findings that sit on a symbol's origin rather than a pin come back
with `ref=""`, against a tool whose whole point is naming findings by part.

**Measured** a three-unit LM358: 11 of 13 findings resolved to `ref.pin`; the
two part-level ones (`missing_unit`, `missing_input_pin`) returned `ref=""`.

**Cause** `check` indexes pin positions only.

**Fix** Index part origins as well.

---

### SCH-9 — `label` refuses a justification its contract offers
`schematic` · **low** · open · `schematic/api.py` `label`

**Symptom** The ABC says a local label "also takes `bottom` and reads above the
wire"; passing it fails.

**Measured** `s.label(10, 10, "N", kind="local", justify="bottom")` ->
`ValueError: justify must be 'left' or 'right', not 'bottom'`.

**Cause** The implementation appends `bottom` itself for local labels, so the
behaviour is right and only the wording invites a call that fails.

**Fix** Reword the docstring, or accept `bottom` as a no-op for local labels.

---

### SCH-10 — The primitive count in `api.py` is wrong
`docs` · **low** · open · `schematic/api.py:1`

**Symptom** The module docstring opens "The whole schematic contract: sixteen
primitives".

**Measured** `Sheet` has **31** abstract members — 28 methods and 3 properties.
Count with `__isabstractmethod__`; grepping for the decorator counts the
`from abc import` line and gives 32.

**Cause** Stale after several rounds of adding and removing primitives.

**Fix** Say 31, or say "the primitives" and stop carrying a number that has
been wrong three times.

---

### MCP-1 — No tool docstring says what key its payload uses
`docs` · **low** · open · `server/tools_schematic.py` `server/tools_board.py`

**Symptom** A caller has to guess the name of the list in a reply, and the
tool name is a misleading guide. `list_components` returns its parts under
`"parts"`; `list_footprints` returns `"footprints"`. Not one of the 58
docstrings names the key it replies with, so the only way to learn it is to
call the tool and look -- which an agent can do, but only after acting on the
empty list it got first.

**Measured** while writing `led_digits_via_mcp.py`, which read
`list_components(...)["components"]`, got `[]` from a sheet holding two parts,
and concluded `remove_component` was broken. It was not:

    add_component R1, add_component R2
    list_components  -> {"ok": true, "count": 2, "parts": [...]}
                        ["components"] -> []          <- the guess
    remove_component R1 -> {"ok": true, "removed": "R1"}
    on disk             -> ['R2']                     <- it worked

**Cause** The return shape is written once in the code and nowhere in the
contract. `Args:` is documented on every tool; `Returns:` on none.

**Fix** Either name the key in each docstring, or make it uniform. Every reply
already carries `count`, which is the one key that never needs guessing -- so
the cheapest fix is one line in the server instructions saying to use it, and
the honest one is a `Returns:` line per tool.

---

### PCB-2 — A rotated or flipped footprint does not match its library copy
`pcb` · **medium** · open · `backend/kicad/pcb/board.py` (`place`, `rotate`, `flip`)

**Symptom** DRC reports `lib_footprint_mismatch` once for every part that is
rotated, on the back, or both. The board is electrically right -- pad positions
were checked against pcbnew -- but the warning reads exactly like real library
drift, so a genuine one would not stand out.

**Measured** four placements of one footprint on an otherwise empty board:

    place(FP, "J1",  5,  5, rotation=0)              -> clean
    place(FP, "J2", 15,  5, rotation=90)             -> mismatch
    place(FP, "J3",  5, 15, rotation=0,  side="B")   -> mismatch
    place(FP, "J4", 15, 15, rotation=90, side="B")   -> mismatch

3 of 4. `led_matrix` carries 14 across its six boards; every one of them is a
part that is turned or on the back.

**Cause** Not the geometry. A rotated footprint's stored pads and `property`
`at` nodes are byte-identical to the library copy -- only the footprint's own
`(at x y 90)` differs. What is left is how the turn is expressed: KiCad appears
to fold the footprint rotation into each property's `at` ANGLE, so it expects
`(at 0 -2.38 90)` where this writes the library's `(at 0 -2.38 0)`. That last
step is inference, not measurement -- it has not been checked against a board
KiCad itself wrote.

**Fix** Confirm the convention against a KiCad-authored file first. If it
holds, add the footprint rotation to each property `at` angle in `place`,
`rotate` and `flip`, and take it back off in `fields`.

---
