"""Canonical JSON-backed Tileset Specification contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import json
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Direction = Literal["N", "E", "S", "W"]
TileSemantic = Literal["surface", "network", "transition", "object"]
NormalizeMode = Literal["nearest", "area", "pixel_preserving"]
CropPolicy = Literal["center", "top_left"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ArtDirection(_ContractModel):
    style_id: str = "handheld_tactical_rpg_v1"
    perspective: str = "top-down orthographic"
    visual_rules: tuple[str, ...] = ()
    palette_direction: str = "controlled color with readable value separation"

    @field_validator("style_id", "perspective", "palette_direction")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("art direction text must not be empty")
        return value


class MaterialSpec(_ContractModel):
    material_id: str
    feature_scale: str = "medium"
    palette_hints: tuple[str, ...] = ()
    accepted_visual_vocabulary: tuple[str, ...] = ()
    generation_rules: tuple[str, ...] = ()

    @field_validator("material_id")
    @classmethod
    def material_id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("material_id must not be empty")
        return value


class EdgeContract(_ContractModel):
    edge_id: str = "shared_surface_edge"
    role: str = "surface"
    shared_edge_rule: str = "same base material continues across the cell edge"
    safe_zone_ratio: float = Field(default=0.08, ge=0.0, le=0.5)
    forbidden_artifacts: tuple[str, ...] = ("strong grid line", "gutter", "text")


class NetworkContract(_ContractModel):
    network_id: str
    network_type: Literal["road", "river"] = "road"
    allowed_topologies: tuple[str, ...]
    forbidden_topologies: tuple[str, ...] = ()
    connector_width_ratio: float = Field(default=0.22, gt=0.0, le=1.0)
    connector_rule: str = "connectors meet the exact cell edge center"

    @field_validator("allowed_topologies")
    @classmethod
    def topology_list_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("network contract requires at least one topology")
        return tuple(dict.fromkeys(value))


class TileSpec(_ContractModel):
    id: str
    cell: tuple[int, int]
    semantic: TileSemantic
    material: str | None = None
    network: str | None = None
    connectors: tuple[Direction, ...] = ()
    description: str
    variation_strength: str | None = None

    @field_validator("id", "description")
    @classmethod
    def text_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tile text must not be empty")
        return value

    @field_validator("cell")
    @classmethod
    def cell_is_pair(cls, value: tuple[int, int]) -> tuple[int, int]:
        if len(value) != 2 or any(index < 0 for index in value):
            raise ValueError("tile cell must contain two non-negative indexes")
        return value


class GenerationSpec(_ContractModel):
    adapter: str = "mcp"
    generator: str = "image_generation"
    model: str | None = None
    requested_width: int = Field(default=1024, ge=64)
    requested_height: int = Field(default=1024, ge=64)
    max_attempts: int = Field(default=3, ge=1, le=10)
    seed: int = Field(default=42, ge=0)
    negative_constraints: tuple[str, ...] = ()


class PostprocessSpec(_ContractModel):
    crop_policy: CropPolicy = "center"
    normalize_mode: NormalizeMode = "nearest"
    style_regularization_enabled: bool = False
    style_regularization_palette_budget: int | None = Field(default=None, ge=4, le=64)
    style_regularization_cluster_cleanup: bool = False


class GenerationRequest(_ContractModel):
    task: str
    intent: str
    output_contract: dict[str, object]
    art_direction: dict[str, object]
    palette_direction: str
    shared_surface_contract: dict[str, object] | None = None
    shared_edge_contract: dict[str, object] | None = None
    feature_scale_contract: dict[str, str] = Field(default_factory=dict)
    material_contracts: dict[str, dict[str, object]] = Field(default_factory=dict)
    network_contracts: dict[str, dict[str, object]] = Field(default_factory=dict)
    tile_manifest: list[dict[str, object]]
    global_consistency_rules: tuple[str, ...] = ()
    strict_negative_constraints: tuple[str, ...] = ()
    split_contract: dict[str, object]
    final_instruction: str


class TilesetSpec(_ContractModel):
    version: str = "1"
    tileset_id: str
    tile_size_px: int = Field(default=64, ge=1)
    grid_columns: int = Field(ge=1, le=64)
    grid_rows: int = Field(ge=1, le=64)
    art_direction: ArtDirection
    materials: dict[str, MaterialSpec] = Field(default_factory=dict)
    shared_edge_contract: EdgeContract | None = None
    network_contracts: dict[str, NetworkContract] = Field(default_factory=dict)
    tiles: tuple[TileSpec, ...]
    generation: GenerationSpec = Field(default_factory=GenerationSpec)
    postprocess: PostprocessSpec = Field(default_factory=PostprocessSpec)

    @field_validator("version", "tileset_id")
    @classmethod
    def ids_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tileset identifiers must not be empty")
        return value

    @model_validator(mode="after")
    def validate_tiles(self) -> "TilesetSpec":
        expected_cells = {(column, row) for row in range(self.grid_rows) for column in range(self.grid_columns)}
        seen_ids: set[str] = set()
        seen_cells: set[tuple[int, int]] = set()
        for tile in self.tiles:
            if tile.id in seen_ids:
                raise ValueError(f"duplicate tile id: {tile.id}")
            if tile.cell in seen_cells:
                raise ValueError(f"duplicate cell: {tile.cell}")
            if tile.cell not in expected_cells:
                raise ValueError(f"tile cell outside grid: {tile.cell}")
            if tile.material is not None and tile.material not in self.materials:
                raise ValueError(f"unknown tile material: {tile.material}")
            if tile.network is not None and tile.network not in self.network_contracts:
                raise ValueError(f"unknown tile network: {tile.network}")
            seen_ids.add(tile.id)
            seen_cells.add(tile.cell)
        if seen_cells != expected_cells:
            missing = sorted(expected_cells - seen_cells)
            raise ValueError(f"incomplete grid; missing cells: {missing}")
        return self

    @classmethod
    def from_json_file(cls, path: Path) -> "TilesetSpec":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("tileset spec JSON must contain an object")
        return cls.model_validate(value)

    def write_json(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path
