# Net colors

Use `set_net_classes` to choose independent PCB and schematic colors. Colors
belong to named classes; assign one net or a group through persistent patterns.
Use the board path in the same project as the root schematic: both share the
sibling `.kicad_pro` file. This API currently requires a board document.

```json
{
  "path": "controller.kicad_pcb",
  "classes": [
    {"name": "Power", "pcb_color": "#E53935", "schematic_color": "#E53935"},
    {"name": "Data", "pcb_color": "#1565C0", "schematic_color": "#1565C0"}
  ]
}
```

Colors accept `#RRGGBB` or `#RRGGBBAA`. Omitted or `null` leaves an existing
color unchanged; `""` clears it to the inherited/theme default. Zero alpha also
clears the color. Updates preserve omitted routing dimensions and other classes.
Read colors back with `list_net_classes`.

Read `list_net_class_patterns`, then pass the complete desired list to
`set_net_class_patterns`:

```json
{
  "path": "controller.kicad_pcb",
  "patterns": [
    {"pattern": "^VBUS$", "net_class": "Power"},
    {"pattern": "^USB_.*$", "net_class": "Data"}
  ]
}
```

**This replaces all patterns.** Include existing entries to retain them; an
empty list clears patterns. KiCad supports wildcard and regular-expression
matching. For exact names, anchor and escape regex characters: `^VBUS$`,
`^/SCL$`, or `^DATA\[0\]$` (double backslashes in JSON). Use the full sheet path
from `list_nets` for local nets. All matching classes apply, with KiCad resolving
property priority; graphical assignments and explicit board assignments remain
independent. Class assignment affects routing rules as well as appearance.

`assign_net_classes` sets explicit board memberships. KiCad's schematic editor
can rebuild those from graphical assignments, so use patterns for persistent
project-wide coloring. `list_board_nets(include_rules=true)` reports KiCad's
resolved `class_colors`, along with routing dimensions. Individual PCB net-color
overrides can supersede class colors and are not edited by these tools.

## Display

- **PCB editor:** Appearance → Nets → net display options → **All** colors
  tracks, pads, vias and zones. **Ratsnest** colors only airwires; **None** hides
  net colors. Use a named class instead of relying on a Default-class PCB color.
- **Schematic:** class colors apply to wires, or their highlight when the
  editor's *Highlight netclass colors* preference is enabled. Explicit wire
  styling can override the class color. Reopen an already-open project after
  external edits if KiCad has not reloaded its settings.
- **Renders:** color schematic exports use these colors; monochrome exports
  remain monochrome. Physical PCB 3D renders still show copper, mask and silk
  materials. PCB plot/dashboard rendering does not gain a net-color overlay.

See KiCad's [PCB display documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#net-and-net-class-controls)
and [schematic net classes](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#schematic-netclasses).

Run `python examples/scripts/net_colors.py` for color set/clear/preserve,
atomic failures, pattern replacement, native color resolution and schematic
render examples under `out/net-colors/`.
