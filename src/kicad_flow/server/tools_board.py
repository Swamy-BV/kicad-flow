"""Register board tools and preserve existing imports.

Implementations live in :mod:`.board_tools` by responsibility.
"""

from .board_tools.copper import _provider_via_refusal as _provider_via_refusal
from .board_tools.copper import _zone_value as _zone_value
from .board_tools.copper import (
    add_tracks,
    add_vias,
    add_zones,
    list_copper,
    refill_zones,
    remove_copper,
)
from .board_tools.footprints import (
    find_footprint,
    flip_footprints,
    footprint_pads,
    get_footprint,
    get_footprint_fields,
    get_pad,
    list_footprints,
    measure_placement,
    move_footprint_fields,
    move_footprints,
    place_footprints,
    remove_footprints,
    rotate_footprints,
    set_footprint_fields,
    set_pad_nets,
)
from .board_tools.graphics import (
    add_board_texts,
    add_graphics,
    list_graphics,
    move_graphics,
    remove_graphics,
)
from .board_tools.inspection import (
    list_board_nets,
    measure_routes,
    query_board_region,
    unrouted_connections,
    what_is_on_board,
)
from .board_tools.models import ArcGraphic as ArcGraphic
from .board_tools.models import BoardRuleSpec as BoardRuleSpec
from .board_tools.models import CircleGraphic as CircleGraphic
from .board_tools.models import FootprintFieldShift as FootprintFieldShift
from .board_tools.models import FootprintFieldValue as FootprintFieldValue
from .board_tools.models import FootprintFlip as FootprintFlip
from .board_tools.models import FootprintMove as FootprintMove
from .board_tools.models import FootprintTurn as FootprintTurn
from .board_tools.models import GraphicMove as GraphicMove
from .board_tools.models import GraphicSpec as GraphicSpec
from .board_tools.models import LineGraphic as LineGraphic
from .board_tools.models import NetClassAssignmentSpec as NetClassAssignmentSpec
from .board_tools.models import NetClassSpec as NetClassSpec
from .board_tools.models import NetPairSpec as NetPairSpec
from .board_tools.models import NewBoardText as NewBoardText
from .board_tools.models import NewFootprint as NewFootprint
from .board_tools.models import NewTrack as NewTrack
from .board_tools.models import NewVia as NewVia
from .board_tools.models import NewZone as NewZone
from .board_tools.models import NumericConstraintSpec as NumericConstraintSpec
from .board_tools.models import PadNet as PadNet
from .board_tools.models import PlacementCandidate as PlacementCandidate
from .board_tools.models import PolygonGraphic as PolygonGraphic
from .board_tools.models import RectangleGraphic as RectangleGraphic
from .board_tools.models import StackupLayerSpec as StackupLayerSpec
from .board_tools.models import _GraphicBase as _GraphicBase
from .board_tools.models import _StrictModel as _StrictModel
from .board_tools.rendering import render_board, render_board_layout
from .board_tools.rules import (
    assign_net_classes,
    get_board_limits,
    get_stackup,
    list_board_constraints,
    list_net_class_assignments,
    list_net_classes,
    set_board_constraints,
    set_board_layers,
    set_board_limits,
    set_net_classes,
    set_stackup,
)
from .board_tools.session import _ERRORS as _ERRORS
from .board_tools.session import _OPEN as _OPEN
from .board_tools.session import _atomic_items as _atomic_items
from .board_tools.session import _blank as _blank
from .board_tools.session import _board as _board
from .board_tools.session import _fail as _fail
from .board_tools.session import _key as _key
from .board_tools.session import new_board, save_board
from .board_tools.validation import check_board

__all__ = [
    "add_board_texts",
    "add_graphics",
    "add_tracks",
    "add_vias",
    "add_zones",
    "assign_net_classes",
    "check_board",
    "find_footprint",
    "flip_footprints",
    "footprint_pads",
    "get_board_limits",
    "get_footprint",
    "get_footprint_fields",
    "get_pad",
    "get_stackup",
    "list_board_constraints",
    "list_board_nets",
    "list_copper",
    "list_footprints",
    "list_graphics",
    "list_net_class_assignments",
    "list_net_classes",
    "measure_placement",
    "measure_routes",
    "move_footprint_fields",
    "move_footprints",
    "move_graphics",
    "new_board",
    "place_footprints",
    "query_board_region",
    "refill_zones",
    "remove_copper",
    "remove_footprints",
    "remove_graphics",
    "render_board",
    "render_board_layout",
    "rotate_footprints",
    "save_board",
    "set_board_constraints",
    "set_board_layers",
    "set_board_limits",
    "set_footprint_fields",
    "set_net_classes",
    "set_pad_nets",
    "set_stackup",
    "unrouted_connections",
    "what_is_on_board",
]
