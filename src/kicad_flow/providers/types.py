"""Provider-neutral nouns for manufacturer part catalogues.

These records describe procurement facts.  They deliberately know nothing
about KiCad symbols, footprints, boards, or BOM layout; a provider can answer
which parts exist without deciding how a design uses one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriceBreak:
    """One unit-price tier from a parts provider."""

    quantity: int
    unit_price: float

    def as_dict(self) -> dict[str, int | float]:
        """Return a JSON-ready price tier."""
        return {"quantity": self.quantity, "unit_price": self.unit_price}


@dataclass(frozen=True)
class ProviderPart:
    """One purchasable part normalized across provider catalogues."""

    provider: str
    provider_part_number: str
    manufacturer_part_number: str
    manufacturer: str
    description: str
    package: str
    category: str
    subcategory: str
    assembly_type: str
    stock: int
    datasheet_url: str = ""
    product_url: str = ""
    prices: tuple[PriceBreak, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return all normalized catalogue fields as JSON."""
        return {
            "provider": self.provider,
            "provider_part_number": self.provider_part_number,
            "manufacturer_part_number": self.manufacturer_part_number,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "package": self.package,
            "category": self.category,
            "subcategory": self.subcategory,
            "assembly_type": self.assembly_type,
            "stock": self.stock,
            "datasheet_url": self.datasheet_url,
            "product_url": self.product_url,
            "prices": [price.as_dict() for price in self.prices],
        }


@dataclass(frozen=True)
class ProviderStatus:
    """Observable state of one local parts catalogue."""

    provider: str
    available: bool
    database_path: str
    size_bytes: int = 0
    modified_at: str = ""
    part_count: int | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return status without pretending unavailable values are zero."""
        out: dict[str, Any] = {
            "provider": self.provider,
            "available": self.available,
            "database_path": self.database_path,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
        }
        if self.part_count is not None:
            out["part_count"] = self.part_count
        if self.error:
            out["error"] = self.error
        return out


@dataclass(frozen=True)
class FabricationSelection:
    """Explicit caller choices needed to resolve manufacturing capabilities."""

    board_type: str
    layers: int
    thickness: float
    material: str
    outer_copper_oz: float
    inner_copper_oz: float | None
    finish: str
    soldermask_color: str
    outline_process: str
    impedance_control: bool
    tier: str

    def as_dict(self) -> dict[str, Any]:
        """Return the selections without adding inferred choices."""
        return {
            "board_type": self.board_type,
            "layers": self.layers,
            "thickness": self.thickness,
            "material": self.material,
            "outer_copper_oz": self.outer_copper_oz,
            "inner_copper_oz": self.inner_copper_oz,
            "finish": self.finish,
            "soldermask_color": self.soldermask_color,
            "outline_process": self.outline_process,
            "impedance_control": self.impedance_control,
            "tier": self.tier,
        }


@dataclass(frozen=True)
class ManufacturingLimits:
    """Provider facts that can be projected onto a board's neutral limits."""

    values: tuple[tuple[str, float], ...]

    def as_dict(self) -> dict[str, float]:
        """Return limits by neutral board-limit name."""
        return dict(self.values)


@dataclass(frozen=True)
class FabricationProfile:
    """One resolved and provenance-bearing manufacturing contract."""

    provider: str
    selection: FabricationSelection
    limits: ManufacturingLimits
    minimum_size: tuple[float, float]
    maximum_size: tuple[float, float]
    source_url: str
    retrieved_at: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the complete resolved profile as JSON."""
        return {
            "provider": self.provider,
            "selection": self.selection.as_dict(),
            "limits": self.limits.as_dict(),
            "minimum_size": list(self.minimum_size),
            "maximum_size": list(self.maximum_size),
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ImportedLibrary:
    """One external CAD bundle installed into one project."""

    name: str
    source: str
    source_url: str
    symbol_libraries: tuple[str, ...]
    footprint_libraries: tuple[str, ...]
    models: tuple[str, ...]
    unresolved_model_references: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return library nicknames, model paths, and source provenance."""
        return {
            "name": self.name,
            "source": self.source,
            "source_url": self.source_url,
            "symbol_libraries": list(self.symbol_libraries),
            "footprint_libraries": list(self.footprint_libraries),
            "models": list(self.models),
            "unresolved_model_references": list(
                self.unresolved_model_references
            ),
        }


__all__ = [
    "FabricationProfile",
    "FabricationSelection",
    "ImportedLibrary",
    "ManufacturingLimits",
    "PriceBreak",
    "ProviderPart",
    "ProviderStatus",
]
