"""JLCPCB rigid-FR4 fabrication facts from a dated local snapshot."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..api import FabricationProvider
from ..types import (
    FabricationCapabilities,
    FabricationProfile,
    FabricationSelection,
    ManufacturingLimits,
)


def _same(left: float, right: float) -> bool:
    return math.isclose(left, right, abs_tol=1e-9)


def _choice(value: float, choices: list[Any], name: str) -> float:
    numeric = [float(item) for item in choices]
    for item in numeric:
        if _same(value, item):
            return item
    raise ValueError(f"unsupported JLCPCB {name} {value:g}; choose {numeric}")


class JlcpcbFabricationProvider(FabricationProvider):
    """Resolve explicit rigid-FR4 choices into provider-neutral limits."""

    def __init__(self) -> None:
        """Load the checked-in capability snapshot."""
        loaded = json.loads(
            Path(__file__).with_name("capabilities.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(loaded, dict):
            raise ValueError("JLCPCB capabilities root must be an object")
        self._data: dict[str, Any] = loaded

    @property
    def name(self) -> str:
        """The provider registry name."""
        return "jlcpcb"

    def capabilities(self) -> FabricationCapabilities:
        """Return exact choices from the dated local capability snapshot."""
        data = self._data
        layers = tuple(int(item) for item in data["supported_layers"])
        outer = tuple(
            (count, tuple(float(value) for value in data["outer_copper_oz"][
                "2" if count == 2 else "multilayer"
            ]))
            for count in layers
        )
        return FabricationCapabilities(
            provider=self.name,
            board_types=("rigid_fr4",),
            materials=("FR-4",),
            layers=layers,
            thicknesses=tuple(float(item) for item in data["thickness_mm"]),
            outer_copper_oz=outer,
            inner_copper_oz=tuple(
                float(item) for item in data["inner_copper_oz"]
            ),
            finishes=tuple(str(item) for item in data["finishes"]),
            soldermask_colors=tuple(
                str(item) for item in data["soldermask_colors"]
            ),
            outline_processes=("routed",),
            tiers=("recommended", "minimum"),
            source_url=str(data["source_url"]),
            retrieved_at=str(data["retrieved_at"]),
        )

    def resolve(self, selection: FabricationSelection) -> FabricationProfile:
        """Validate the selected process and calculate its applicable limits."""
        data = self._data
        if selection.board_type.lower() != "rigid_fr4":
            raise ValueError("initial JLCPCB profile supports rigid_fr4 only")
        if selection.material.upper() not in ("FR4", "FR-4"):
            raise ValueError("initial JLCPCB profile supports FR-4 only")
        supported = [int(item) for item in data["supported_layers"]]
        if selection.layers not in supported:
            raise ValueError(
                f"JLCPCB profile layer count must be one of {supported}"
            )
        thickness = _choice(
            selection.thickness, data["thickness_mm"], "thickness"
        )
        group = "2" if selection.layers == 2 else "multilayer"
        outer = _choice(
            selection.outer_copper_oz,
            data["outer_copper_oz"][group],
            "outer copper weight",
        )
        inner: float | None = None
        if selection.layers > 2:
            if selection.inner_copper_oz is None:
                raise ValueError("multilayer JLCPCB profile needs inner_copper_oz")
            inner = _choice(
                selection.inner_copper_oz,
                data["inner_copper_oz"],
                "inner copper weight",
            )
        elif selection.inner_copper_oz is not None:
            raise ValueError("a 2-layer board has no inner copper weight")

        finishes = {str(item).lower(): str(item) for item in data["finishes"]}
        finish = finishes.get(selection.finish.lower())
        if finish is None:
            raise ValueError(
                f"unsupported JLCPCB finish {selection.finish!r}; "
                f"choose {list(finishes.values())}"
            )
        if finish.startswith("HASL") and (
            selection.layers >= 6 or thickness <= 0.4
        ):
            raise ValueError(
                "JLCPCB does not offer HASL for 6+ layers or 0.4 mm boards"
            )
        colors = [str(item) for item in data["soldermask_colors"]]
        color = selection.soldermask_color.lower()
        if color not in colors:
            raise ValueError(
                f"unsupported JLCPCB soldermask color {color!r}; choose {colors}"
            )
        if selection.outline_process.lower() != "routed":
            raise ValueError("initial JLCPCB profile supports routed outlines only")
        tier = selection.tier.lower()
        if tier not in ("recommended", "minimum"):
            raise ValueError("tier must be 'recommended' or 'minimum'")
        if selection.impedance_control:
            if selection.layers < 4:
                raise ValueError("JLCPCB controlled impedance requires 4+ layers")
            if not _same(outer, 1.0):
                raise ValueError(
                    "initial controlled-impedance profile requires 1 oz outer copper"
                )

        width = float(data["track_width_spacing_mm"][group][f"{outer:g}"])
        via = data["via_mm"][tier]
        # KiCad's board-wide annular setting is specifically the VIA annular
        # width.  JLC's larger 0.18/0.25 mm figures describe PTH component
        # pads, not vias.  Applying them here made JLC's own preferred
        # 0.35/0.20 mm via impossible: its radial ring is 0.075 mm.
        annular = (float(via["diameter"]) - float(via["drill"])) / 2
        mask = data["soldermask_bridge_mm"]
        if outer >= 2.0:
            mask_bridge = float(mask["two_oz"])
        elif color in ("black", "white"):
            mask_bridge = float(mask["one_oz_black_white"])
        else:
            mask_bridge = float(mask["one_oz_color"])
        maximum = data["maximum_size_mm"][group]
        resolved = FabricationSelection(
            board_type="rigid_fr4",
            layers=selection.layers,
            thickness=thickness,
            material="FR-4",
            outer_copper_oz=outer,
            inner_copper_oz=inner,
            finish=finish,
            soldermask_color=color,
            outline_process="routed",
            impedance_control=selection.impedance_control,
            tier=tier,
        )
        limits = ManufacturingLimits(tuple({
            "min_clearance": width,
            "min_track_width": width,
            "min_via_diameter": float(via["diameter"]),
            "min_via_drill": float(via["drill"]),
            "min_annular_width": annular,
            "min_hole_clearance": float(data["hole_clearance_mm"]),
            "min_hole_to_hole": float(data["hole_to_hole_mm"]),
            "min_copper_edge_clearance": float(
                data["routed_edge_clearance_mm"]
            ),
            "min_silk_clearance": float(data["silkscreen_pad_clearance_mm"]),
            "min_text_height": float(data["silkscreen_text_height_mm"]),
            "min_text_thickness": float(data["silkscreen_line_width_mm"]),
            "min_groove_width": float(data["non_plated_slot_width_mm"]),
            "solder_mask_to_copper_clearance": float(
                data["soldermask_to_copper_mm"]
            ),
            "min_solder_mask_bridge": mask_bridge,
        }.items()))
        notes = (
            "Through-vias only; blind and buried vias are not enabled.",
            "The annular-width limit applies to vias. PTH/NPTH pad annular "
            "rings and plated-slot subtype checks need future hole APIs.",
            "Impedance control records eligibility; select a published JLC "
            "stackup before claiming an impedance value.",
        )
        minimum = data["minimum_size_mm"]
        return FabricationProfile(
            provider=self.name,
            selection=resolved,
            limits=limits,
            minimum_size=(float(minimum[0]), float(minimum[1])),
            maximum_size=(float(maximum[0]), float(maximum[1])),
            source_url=str(data["source_url"]),
            retrieved_at=str(data["retrieved_at"]),
            notes=notes,
        )


__all__ = ["JlcpcbFabricationProvider"]
