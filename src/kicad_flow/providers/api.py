"""Abstract contracts for part catalogues and project-local CAD assets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .types import (
    FabricationCapabilities,
    FabricationProfile,
    FabricationSelection,
    ImportedLibrary,
    ProviderPart,
    ProviderStatus,
)


class FabricationProvider(ABC):
    """A source of fabrication facts, independent of any CAD backend."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The stable provider name accepted by MCP tools."""

    @abstractmethod
    def capabilities(self) -> FabricationCapabilities:
        """Return exact caller choices and their source provenance."""

    @abstractmethod
    def resolve(self, selection: FabricationSelection) -> FabricationProfile:
        """Validate explicit choices and return their applicable constraints."""


class PartsProvider(ABC):
    """A searchable source of purchasable electronic parts."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The stable provider name accepted by the MCP tools."""

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Report whether the local catalogue can be queried."""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        package: str = "",
        manufacturer: str = "",
        assembly_type: str = "",
        min_stock: int = 0,
    ) -> list[ProviderPart]:
        """Find parts using only explicit caller-supplied filters."""


class ProjectLibrary(ABC):
    """Install downloaded CAD bundles into one project's local libraries."""

    @abstractmethod
    def import_bundle(
        self,
        name: str,
        source: str,
        source_path: Path,
        *,
        source_url: str = "",
        overwrite: bool = False,
    ) -> ImportedLibrary:
        """Import one extracted directory or ZIP into the project."""

    @abstractmethod
    def libraries(self) -> list[ImportedLibrary]:
        """List bundles recorded in the project manifest."""


__all__ = ["FabricationProvider", "PartsProvider", "ProjectLibrary"]
