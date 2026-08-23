"""High-resolution source tile contracts and synthesis."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .edge_contract import EdgeContract
from .patches import PatchDatabase
from .quilting import ImageQuilter, MinimumErrorSeamSolver


@dataclass
class TilesetConfig:
    """Configuration for a material-source tileset experiment."""

    output_root: Path = field(default_factory=lambda: Path("experiment/grass_tileset"))
    material: str = "grass"
    patch_size: int = 256
    patch_overlap: int = 64
    edge_types: int = 3
    strip_width: int = 96
    variants: int = 12
    source_tile_size: int = 512
    palette_budget: int = 24
    shared_palette: bool = True
    map_columns: int = 10
    map_rows: int = 10
    seed: int = 42
    debug_enabled: bool = True

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        if self.material not in {"grass", "forest_canopy"}:
            raise ValueError("material must be grass or forest_canopy")
        if self.patch_size < 2 or not 0 <= self.patch_overlap < self.patch_size:
            raise ValueError("patch_size/patch_overlap are invalid")
        if not 1 <= self.edge_types <= 8:
            raise ValueError("edge_types must be between 1 and 8")
        if self.strip_width < 1 or self.strip_width * 2 > self.source_tile_size:
            raise ValueError("strip_width must fit twice inside source_tile_size")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if self.source_tile_size < 64:
            raise ValueError("source_tile_size must be at least 64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.map_columns < 1 or self.map_rows < 1:
            raise ValueError("map_columns and map_rows must be positive")

    def as_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["output_root"] = str(self.output_root)
        return values


@dataclass(frozen=True)
class SourceTileSpec:
    tile_id: str
    edge_contract: EdgeContract
    seed: int

    def as_dict(self) -> dict[str, object]:
        return {
            "tile_id": self.tile_id,
            "edge_contract": self.edge_contract.as_dict(),
            "seed": self.seed,
        }


@dataclass(frozen=True)
class SourceTile:
    spec: SourceTileSpec
    image: Image.Image


@dataclass(frozen=True)
class EdgeStripSet:
    north: Image.Image
    east: Image.Image
    south: Image.Image
    west: Image.Image


class SourceTileSynthesizer:
    """Create contract-bearing sources from one patch database."""

    def __init__(self, database: PatchDatabase, config: TilesetConfig, source: Image.Image) -> None:
        self.database = database
        self.config = config
        self.source = source.convert("RGBA")
        self.edge_ids = tuple(f"{config.material}_{chr(ord('A') + index)}" for index in range(config.edge_types))
        self.edge_strips = self._build_edge_strips()

    def synthesize(self) -> tuple[SourceTile, ...]:
        tiles: list[SourceTile] = []
        for index in range(self.config.variants):
            spec = SourceTileSpec(
                tile_id=f"tile_{index:02d}",
                edge_contract=self._contract_for(index),
                seed=self.config.seed + index,
            )
            quilter = ImageQuilter(self.database, seed=spec.seed, seam_solver=MinimumErrorSeamSolver())
            center = quilter.synthesize((self.config.source_tile_size, self.config.source_tile_size))
            image = self._apply_edge_strips(center, spec.edge_contract)
            tiles.append(SourceTile(spec, image))
        return tuple(tiles)

    def _contract_for(self, index: int) -> EdgeContract:
        count = len(self.edge_ids)
        # Keep one universally connectable variant per edge family.  These
        # anchors make small variant counts (for example 8 with 3 families)
        # assemblable; the remaining variants add directional combinations.
        if index < count:
            edge_id = self.edge_ids[index]
            return EdgeContract(north=edge_id, east=edge_id, south=edge_id, west=edge_id)
        pattern = index - count
        off_diagonal = [
            (self.edge_ids[north], self.edge_ids[west])
            for west in range(count)
            for north in range(count)
            if north != west
        ]
        duplicate_pairs = [
            (self.edge_ids[index % count], self.edge_ids[(index + 1) % count])
            for index in range(count)
        ]
        north, west = (off_diagonal + duplicate_pairs)[pattern % (len(off_diagonal) + len(duplicate_pairs))]
        east = west
        south = north
        return EdgeContract(north=north, east=east, south=south, west=west)

    def _build_edge_strips(self) -> dict[str, EdgeStripSet]:
        strips: dict[str, EdgeStripSet] = {}
        width = self.config.source_tile_size
        strip = self.config.strip_width
        for index, edge_id in enumerate(self.edge_ids):
            horizontal_line = _sample_line(self.source, "horizontal", index, len(self.edge_ids), width)
            vertical_line = _sample_line(self.source, "vertical", index, len(self.edge_ids), width)
            north = _make_strip(self.source, (width, strip), index, len(self.edge_ids), "north")
            south = _make_strip(self.source, (width, strip), index, len(self.edge_ids), "south")
            east = _make_strip(self.source, (strip, width), index, len(self.edge_ids), "east")
            west = _make_strip(self.source, (strip, width), index, len(self.edge_ids), "west")
            north_array = np.asarray(north).copy()
            south_array = np.asarray(south).copy()
            east_array = np.asarray(east).copy()
            west_array = np.asarray(west).copy()
            north_array[0, :, :3] = horizontal_line
            south_array[-1, :, :3] = horizontal_line
            west_array[:, 0, :3] = vertical_line
            east_array[:, -1, :3] = vertical_line
            strips[edge_id] = EdgeStripSet(
                Image.fromarray(north_array, mode="RGBA"),
                Image.fromarray(east_array, mode="RGBA"),
                Image.fromarray(south_array, mode="RGBA"),
                Image.fromarray(west_array, mode="RGBA"),
            )
        return strips

    def _apply_edge_strips(self, center: Image.Image, contract: EdgeContract) -> Image.Image:
        result = np.asarray(center.convert("RGBA")).copy()
        strip = self.config.strip_width
        solver = MinimumErrorSeamSolver()
        for side, strip_image in (
            ("north", self.edge_strips[contract.north].north),
            ("east", self.edge_strips[contract.east].east),
            ("south", self.edge_strips[contract.south].south),
            ("west", self.edge_strips[contract.west].west),
        ):
            overlay = np.asarray(strip_image.convert("RGBA"))
            if side in {"north", "south"}:
                existing = result[:strip] if side == "north" else result[-strip:]
                error = np.square(existing[:, :, :3].astype(np.float32) - overlay[:, :, :3].astype(np.float32)).mean(axis=2)
                seam = solver.horizontal(error)
                blended = _horizontal_seam(existing, overlay, seam)
                if side == "north":
                    result[:strip] = blended
                else:
                    result[-strip:] = np.flip(_horizontal_seam(np.flip(existing, axis=0), np.flip(overlay, axis=0), solver.horizontal(np.flip(error, axis=0))), axis=0)
            else:
                existing = result[:, :strip] if side == "west" else result[:, -strip:]
                error = np.square(existing[:, :, :3].astype(np.float32) - overlay[:, :, :3].astype(np.float32)).mean(axis=2)
                seam = solver.vertical(error)
                blended = _vertical_seam(existing, overlay, seam)
                if side == "west":
                    result[:, :strip] = blended
                else:
                    result[:, -strip:] = np.flip(_vertical_seam(np.flip(existing, axis=1), np.flip(overlay, axis=1), solver.vertical(np.flip(error, axis=1))), axis=1)
        _force_contract_boundary(result, contract, self.edge_strips)
        return Image.fromarray(result, mode="RGBA")


def _sample_line(image: Image.Image, orientation: str, index: int, count: int, length: int) -> np.ndarray:
    source = np.asarray(image.convert("RGB"))
    position = min(source.shape[0 if orientation == "horizontal" else 1] - 1, (index + 1) * source.shape[0 if orientation == "horizontal" else 1] // (count + 1))
    if orientation == "horizontal":
        line = source[position, :, :]
    else:
        line = source[:, position, :]
    return np.asarray(Image.fromarray(line.reshape(1, -1, 3) if orientation == "horizontal" else line.reshape(-1, 1, 3), mode="RGB").resize((length, 1) if orientation == "horizontal" else (1, length), Image.Resampling.BILINEAR)).reshape(-1, 3)


def _make_strip(source: Image.Image, size: tuple[int, int], index: int, count: int, side: str) -> Image.Image:
    width, height = source.size
    cx = min(width - 1, (index + 1) * width // (count + 1))
    cy = min(height - 1, (index + 1) * height // (count + 1))
    crop = source.crop((max(0, cx - width // 4), max(0, cy - height // 4), min(width, cx + width // 4 + 1), min(height, cy + height // 4 + 1)))
    return crop.resize(size, Image.Resampling.BILINEAR).convert("RGBA")


def _horizontal_seam(existing: np.ndarray, overlay: np.ndarray, seam: list[int]) -> np.ndarray:
    output = existing.copy()
    for column, cut in enumerate(seam):
        output[: cut + 1, column] = overlay[: cut + 1, column]
    return output


def _vertical_seam(existing: np.ndarray, overlay: np.ndarray, seam: list[int]) -> np.ndarray:
    output = existing.copy()
    for row, cut in enumerate(seam):
        output[row, : cut + 1] = overlay[row, : cut + 1]
    return output


def _force_contract_boundary(result: np.ndarray, contract: EdgeContract, strips: dict[str, EdgeStripSet]) -> None:
    result[0, :, :] = np.asarray(strips[contract.north].north)[0, :, :]
    result[-1, :, :] = np.asarray(strips[contract.south].south)[-1, :, :]
    result[:, 0, :] = np.asarray(strips[contract.west].west)[:, 0, :]
    result[:, -1, :] = np.asarray(strips[contract.east].east)[:, -1, :]
