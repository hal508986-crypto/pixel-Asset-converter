"""Serializable manifest contracts for packaged tilesets."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetTileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    file: str
    semantic: str
    material: str | None = None
    network: str | None = None
    connectors: tuple[str, ...] = ()


class TilesetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tileset_id: str
    tile_size_px: int = Field(ge=1)
    sheet: dict[str, int]
    status: Literal["provisional", "approved"] = "provisional"
    tiles: tuple[AssetTileRecord, ...]

    def validate_complete(self) -> None:
        columns = int(self.sheet["columns"])
        rows = int(self.sheet["rows"])
        cells = {(tile.column, tile.row) for tile in self.tiles}
        expected = {(column, row) for row in range(rows) for column in range(columns)}
        if cells != expected:
            raise ValueError("manifest cell count does not match the declared sheet grid")
        if len({tile.id for tile in self.tiles}) != len(self.tiles):
            raise ValueError("manifest contains duplicate tile ids")
