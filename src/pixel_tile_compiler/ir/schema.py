"""Pydantic Tile IR contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GlobalStyle(BaseModel):
    """Global pixel-art style decisions."""

    model_config = ConfigDict(extra="forbid")

    texture_density: float = Field(ge=0.0, le=1.0)
    contrast_strength: float = Field(ge=0.0, le=1.0)
    edge_strength: float = Field(ge=0.0, le=1.0)
    dithering: Literal["off", "minimal", "ordered"]
    outline_mode: Literal["off", "selective"]
    seam_mode: Literal["off", "inspect", "correct"]


class RegionIR(BaseModel):
    """A region's semantic and reduction priorities."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    semantic: str
    importance: float = Field(ge=0.0, le=1.0)
    area_ratio: float = Field(ge=0.0, le=1.0)
    dominant_color: tuple[int, int, int]
    preserve_edges: bool
    preserve_texture: bool
    texture_priority: float = Field(ge=0.0, le=1.0)
    merge_candidates: list[int]
    discard_micro_detail: bool


class MapContext(BaseModel):
    """Optional MAP-level context retained alongside a single tile IR."""

    model_config = ConfigDict(extra="forbid")

    tile_x: int = Field(ge=0)
    tile_y: int = Field(ge=0)
    north_semantic: str | None = None
    south_semantic: str | None = None
    east_semantic: str | None = None
    west_semantic: str | None = None
    global_palette_id: str | None = None
    local_brightness_target: float = Field(ge=0.0, le=1.0)
    local_texture_target: float = Field(ge=0.0, le=1.0)


class TileIR(BaseModel):
    """Stable intermediate representation between analysis and rasterization."""

    model_config = ConfigDict(extra="forbid")

    version: str = "0.1"
    width: Literal[64] = 64
    height: Literal[64] = 64
    tile_type: str
    tile_mode: Literal["repeatable", "directional", "object"]
    palette_budget: int = Field(ge=4, le=32)
    global_style: GlobalStyle
    regions: list[RegionIR]
    map_context: MapContext | None = None
