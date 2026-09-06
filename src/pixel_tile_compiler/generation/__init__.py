"""Specification and image-generation request contracts."""

from .adapter import GeneratedImage, GenerationUnavailableError, ImageGenerationAdapter, McpImageGenerationAdapter
from .presets import build_dirt_road_network_spec, build_grass_surface_spec, generate_network_tileset_spec
from .request_compiler import GenerationRequestCompiler
from .spec import (
    ArtDirection,
    EdgeContract,
    GenerationRequest,
    GenerationSpec,
    MaterialSpec,
    NetworkContract,
    PostprocessSpec,
    TileSpec,
    TilesetSpec,
)

__all__ = [
    "ArtDirection",
    "EdgeContract",
    "GeneratedImage",
    "GenerationRequest",
    "GenerationRequestCompiler",
    "GenerationSpec",
    "GenerationUnavailableError",
    "ImageGenerationAdapter",
    "McpImageGenerationAdapter",
    "MaterialSpec",
    "NetworkContract",
    "PostprocessSpec",
    "TileSpec",
    "TilesetSpec",
    "build_dirt_road_network_spec",
    "build_grass_surface_spec",
    "generate_network_tileset_spec",
]
