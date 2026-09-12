# PCB text alignment

Use one text item for a multiline note. `justify` aligns its individual lines;
`vertical_justify` anchors the whole block. Both default to `center`, preserving
existing calls. For a note starting at its top-left anchor:

```json
{
  "path": "board.kicad_pcb",
  "texts": [{
    "x": 20, "y": 10,
    "text": "POWER INPUT\n5V\nGND",
    "layer": "F.SilkS", "size": 1.0,
    "justify": "left", "vertical_justify": "top"
  }]
}
```

Pass this to `add_board_texts`. JSON `\n` represents a newline; `\\n` is literal
backslash-n. Horizontal choices are `left`, `center`, `right`; vertical choices
are `top`, `center`, `bottom`. The reply includes the applied alignment, size,
rotation and mirror setting. Size must be positive.

Alignment uses the text's local frame, rotating and mirroring with it. Keep
coordinates in board space. For readable back-side silkscreen, select
`B.SilkS` and `mirror: true`; inspect with `render_board_layout(side="bottom")`.

For columns or custom line spacing, submit separate items with explicit anchors
in the same list. Spaces inside a string do not provide reliable column stops.
Multiline text uses KiCad's native line spacing and does not wrap automatically.
The saved alignment uses KiCad's [text effects format](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_text_effects).

Run `python examples/scripts/pcb_text.py` for front/back renders covering unequal
line widths, all horizontal/vertical alignments, rotation, default behavior and
invalid-list refusal. Crosses in `out/pcb-text/top.png` and `bottom.png` mark the
authored anchors.
