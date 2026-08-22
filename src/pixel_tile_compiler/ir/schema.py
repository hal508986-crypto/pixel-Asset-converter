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
