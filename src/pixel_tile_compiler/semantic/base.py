"""Provider contracts and semantic result types."""

from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from pixel_tile_compiler.analysis.structural import StructuralAnalysis
from pixel_tile_compiler.config import CompilerConfig


@dataclass(frozen=True)
class SemanticRegion:
    id: int
    semantic: str
    importance: float
    preserve_edges: bool
    preserve_texture: bool
    texture_priority: float
    merge_candidates: tuple[int, ...]
    discard_micro_detail: bool


@dataclass(frozen=True)
class SemanticAnalysis:
    tile_type: str
    tile_mode: str
    texture_density: float
    contrast_strength: float
    edge_strength: float
    regions: tuple[SemanticRegion, ...]
    provider_name: str = "rule"


class SemanticProvider(Protocol):
    """Semantic layer boundary; no MCP transport is required by core."""

    def analyze(
        self,
        image: Image.Image,
        structural_data: StructuralAnalysis,
        config: CompilerConfig,
    ) -> SemanticAnalysis:
        ...


class SemanticProviderError(RuntimeError):
    """Raised when an optional semantic provider cannot return valid data."""
