"""Register schematic tools and preserve existing imports.

Implementations live in :mod:`.schematic_tools` by responsibility.
"""

from .schematic_tools.components import (
    add_components,
    get_component,
    get_fields,
    get_pin,
    list_components,
    mirror_components,
    move_components,
    move_fields,
    next_ref,
    remove_components,
    remove_fields,
    rotate_components,
    set_fields,
)
from .schematic_tools.connections import _move_label as _move_label
from .schematic_tools.connections import _remove_label as _remove_label
from .schematic_tools.connections import _rotate_label as _rotate_label
from .schematic_tools.connections import (
    add_junctions,
    add_labels,
    add_no_connects,
    add_power,
    add_power_flags,
    add_wires,
    list_labels,
    list_wires,
    move_labels,
    move_wires,
    remove_junctions,
    remove_labels,
    remove_no_connects,
    remove_wires,
    rotate_labels,
)
from .schematic_tools.hierarchy import add_sheets, add_texts, move_sheets, remove_sheets
from .schematic_tools.inspection import _findings_result as _findings_result
from .schematic_tools.inspection import (
    check_sheet,
    check_sheet_layout,
    inspect_schematic_scene,
    list_nets,
    measure_schematic_placement,
    what_is_at,
)
from .schematic_tools.models import FieldRef as FieldRef
from .schematic_tools.models import FieldShift as FieldShift
from .schematic_tools.models import FieldValue as FieldValue
from .schematic_tools.models import LabelShift as LabelShift
from .schematic_tools.models import LabelTarget as LabelTarget
from .schematic_tools.models import LabelTurn as LabelTurn
from .schematic_tools.models import NewFlag as NewFlag
from .schematic_tools.models import NewLabel as NewLabel
from .schematic_tools.models import NewPart as NewPart
from .schematic_tools.models import NewPower as NewPower
from .schematic_tools.models import NewSheetBox as NewSheetBox
from .schematic_tools.models import NewSheetPort as NewSheetPort
from .schematic_tools.models import PartFlip as PartFlip
from .schematic_tools.models import PartMove as PartMove
from .schematic_tools.models import PartTurn as PartTurn
from .schematic_tools.models import QuarterTurn as QuarterTurn
from .schematic_tools.models import Segment as Segment
from .schematic_tools.models import SheetMove as SheetMove
from .schematic_tools.models import SheetNote as SheetNote
from .schematic_tools.models import Spot as Spot
from .schematic_tools.models import WireEnds as WireEnds
from .schematic_tools.models import WireShift as WireShift
from .schematic_tools.models import _StrictModel as _StrictModel
from .schematic_tools.rendering import _pages as _pages
from .schematic_tools.rendering import render_schematic
from .schematic_tools.session import _OPEN as _OPEN
from .schematic_tools.session import _atomic_items as _atomic_items
from .schematic_tools.session import _blank as _blank
from .schematic_tools.session import _counted as _counted
from .schematic_tools.session import _fail as _fail
from .schematic_tools.session import _key as _key
from .schematic_tools.session import _sheet as _sheet
from .schematic_tools.session import new_sheet, save_sheet
from .schematic_tools.symbols import find_symbol, symbol_pins

__all__ = [
    "add_components",
    "add_junctions",
    "add_labels",
    "add_no_connects",
    "add_power",
    "add_power_flags",
    "add_sheets",
    "add_texts",
    "add_wires",
    "check_sheet",
    "check_sheet_layout",
    "find_symbol",
    "get_component",
    "get_fields",
    "get_pin",
    "inspect_schematic_scene",
    "list_components",
    "list_labels",
    "list_nets",
    "list_wires",
    "measure_schematic_placement",
    "mirror_components",
    "move_components",
    "move_fields",
    "move_labels",
    "move_sheets",
    "move_wires",
    "new_sheet",
    "next_ref",
    "remove_components",
    "remove_fields",
    "remove_junctions",
    "remove_labels",
    "remove_no_connects",
    "remove_sheets",
    "remove_wires",
    "render_schematic",
    "rotate_components",
    "rotate_labels",
    "save_sheet",
    "set_fields",
    "symbol_pins",
    "what_is_at",
]
