"""Parts and fabrication contracts with the built-in provider registry."""

from __future__ import annotations

from .api import FabricationProvider, PartsProvider, ProjectLibrary
from .jlcpcb import JlcpcbFabricationProvider, JlcpcbPartsProvider
from .types import (
    FabricationCapabilities,
    FabricationProfile,
    FabricationSelection,
    ImportedLibrary,
    ManufacturingLimits,
    PriceBreak,
    ProviderPart,
    ProviderStatus,
)


def fabrication_provider(name: str) -> FabricationProvider:
    """Construct a fabrication provider through the neutral contract."""
    normalized = name.strip().lower()
    if normalized == "jlcpcb":
        return JlcpcbFabricationProvider()
    raise LookupError(f"unknown fabrication provider {name!r}; available: jlcpcb")


def parts_provider(name: str) -> PartsProvider:
    """Construct the named provider without leaking its implementation."""
    normalized = name.strip().lower()
    if normalized == "jlcpcb":
        return JlcpcbPartsProvider()
    raise LookupError(f"unknown parts provider {name!r}; available: jlcpcb")


def part_provider_names() -> tuple[str, ...]:
    """Return the providers compiled into this installation."""
    return ("jlcpcb",)


__all__ = [
    "FabricationCapabilities",
    "FabricationProfile",
    "FabricationProvider",
    "FabricationSelection",
    "ImportedLibrary",
    "ManufacturingLimits",
    "PartsProvider",
    "PriceBreak",
    "ProjectLibrary",
    "ProviderPart",
    "ProviderStatus",
    "fabrication_provider",
    "part_provider_names",
    "parts_provider",
]
