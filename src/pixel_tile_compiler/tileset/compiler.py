"""Experiment runner for material-exemplar driven tilesets."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.palette import extract_palette

from .edge_contract import edge_profile
from .evaluator import (
    assemble_contract_map,
    assemble_image,
    tileset_metrics,
    validate_contract_adjacency,
    validate_edge_profiles,
)
from .material import analyze_material
from .patches import PatchDatabase
from .source_tile import SourceTile, SourceTileSpec, SourceTileSynthesizer, TilesetConfig
from .validator import SourceValidationReport, validate_material_source


@dataclass(frozen=True)
class TilesetBuildResult:
    output_root: Path
    source_tiles: tuple[SourceTile, ...]
    pixel_tile_paths: tuple[Path, ...]
    map_path: Path
    comparison_path: Path
    metrics_path: Path
    validation_path: Path
    metrics: dict[str, Any]


@dataclass(frozen=True)
class _CompiledTile:
    spec: SourceTileSpec
    image: Image.Image


class TilesetSourceCompiler:
    """Build a reusable source tileset and then delegate pixelization to the existing compiler."""

    def build(self, source: Path, config: TilesetConfig) -> TilesetBuildResult:
        source = Path(source)
        material_source = load_image(source)
        return self.build_image(material_source, config, source_name=source)

    def build_image(self, source: Image.Image, config: TilesetConfig, source_name: Path | str = "<memory>") -> TilesetBuildResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        material_source = source.convert("RGBA")
        save_png(material_source, root / f"{config.material}_master.png")
        report = validate_material_source(material_source, palette_budget=config.palette_budget, material=config.material)
        save_json(report.as_dict(), root / "material_validation.json")
        analysis = report.analysis
        save_json(analysis.as_dict(), root / "material_analysis.json")

        database = PatchDatabase.from_image(
            material_source,
            patch_size=config.patch_size,
            overlap=config.patch_overlap,
            seed=config.seed,
        )
        self._save_patch_debug(database, root / "debug")
        synthesizer = SourceTileSynthesizer(database, config, material_source)
        source_tiles = synthesizer.synthesize()
        self._save_source_tiles(source_tiles, root)
        self._save_edge_debug(synthesizer.edge_strips, root / "debug")

        shared_palette = self._shared_palette(source_tiles, config) if config.shared_palette else None
        compiled_tiles = self._compile_pixel_tiles(source_tiles, config, root, shared_palette)
        pixel_tile_paths = tuple(root / "pixel_tiles" / f"{tile.spec.tile_id}.png" for tile in compiled_tiles)
        pixel_by_id = {tile.spec.tile_id: tile for tile in compiled_tiles}

        contract_grid = assemble_contract_map(compiled_tiles, config.map_columns, config.map_rows, seed=config.seed)
        compatible_map = validate_contract_adjacency(contract_grid)
        map_image = assemble_image(contract_grid, image_getter=lambda tile: tile.image)
        map_path = save_png(map_image, root / "source_compiler_tileset.png")
        save_json(
            {
                "columns": config.map_columns,
                "rows": config.map_rows,
                "tiles": [
                    {
                        "x": x,
                        "y": y,
                        "tile_id": tile.spec.tile_id,
                        "edge_contract": tile.spec.edge_contract.as_dict(),
                    }
                    for y, row in enumerate(contract_grid)
                    for x, tile in enumerate(row)
                ],
            },
            root / "map_layout.json",
        )
        self._save_contract_debug(contract_grid, root / "debug")

        single_repeat = self._repeat_image(compiled_tiles[0].image, config.map_columns, config.map_rows)
        independent_grid = self._independent_grid(compiled_tiles, config)
        independent = assemble_image(independent_grid, image_getter=lambda tile: tile.image)
        single_path = save_png(single_repeat, root / "single_repeat.png")
        independent_path = save_png(independent, root / "independent_variants.png")
        comparison = Image.new("RGBA", (map_image.width * 3, map_image.height), (0, 0, 0, 255))
        comparison.paste(single_repeat, (0, 0))
        comparison.paste(independent, (map_image.width, 0))
        comparison.paste(map_image, (map_image.width * 2, 0))
        comparison_path = save_png(comparison, root / "comparison.png")

        source_contracts = validate_edge_profiles(source_tiles)
        pixel_contracts = validate_edge_profiles(compiled_tiles)
        metrics_config = config.as_dict()
        # The artifact itself is reproducible even when callers choose a
        # different output directory for a second run.
        metrics_config.pop("output_root", None)
        metrics = {
            "source": str(source_name),
            "config": metrics_config,
            "source_validation": report.as_dict(),
            "palette": {
                "shared": config.shared_palette,
                "budget": config.palette_budget,
                "colors": [list(color) for color in (shared_palette or ())],
            },
            "single_repeat": tileset_metrics(single_repeat, config.map_columns, config.map_rows, [compiled_tiles[0].spec.tile_id] * (config.map_columns * config.map_rows), pixel_contracts),
            "independent_variants": tileset_metrics(independent, config.map_columns, config.map_rows, [tile.spec.tile_id for row in independent_grid for tile in row], pixel_contracts),
            "source_compiler_tileset": tileset_metrics(map_image, config.map_columns, config.map_rows, [tile.spec.tile_id for row in contract_grid for tile in row], pixel_contracts),
            "contract_validation": {
                "compatible_map": compatible_map,
                "source": source_contracts,
                "pixel": pixel_contracts,
                "pixel_edge_risk": pixel_contracts.get("risk_score", 0.0),
            },
        }
        metrics_path = save_json(metrics, root / "metrics.json")
        return TilesetBuildResult(
            output_root=root,
            source_tiles=source_tiles,
            pixel_tile_paths=pixel_tile_paths,
            map_path=map_path,
            comparison_path=comparison_path,
            metrics_path=metrics_path,
            validation_path=root / "material_validation.json",
            metrics=metrics,
        )

    @staticmethod
    def _shared_palette(source_tiles: tuple[SourceTile, ...], config: TilesetConfig) -> tuple[tuple[int, int, int], ...]:
        width = max(tile.image.width for tile in source_tiles)
        height = max(tile.image.height for tile in source_tiles)
        montage = Image.new("RGBA", (width * len(source_tiles), height), (0, 0, 0, 255))
        for index, tile in enumerate(source_tiles):
            montage.paste(tile.image, (index * width, 0))
        return extract_palette(montage, budget=config.palette_budget)

    @staticmethod
    def _compile_pixel_tiles(
        source_tiles: tuple[SourceTile, ...],
        config: TilesetConfig,
        root: Path,
        shared_palette: tuple[tuple[int, int, int], ...] | None,
    ) -> tuple[_CompiledTile, ...]:
        compiled: list[_CompiledTile] = []
        for tile in source_tiles:
            artifact_root = root / "pixel_artifacts" / tile.spec.tile_id
            compiler_config = CompilerConfig(
                output_root=artifact_root,
                palette_budget=config.palette_budget,
                palette_colors=shared_palette,
                tile_mode="directional",
                seed=tile.spec.seed,
                debug_enabled=config.debug_enabled,
            )
            result = PixelTileCompiler().compile_image(tile.image, compiler_config, source_name=tile.spec.tile_id)
            pixel = Image.open(result.final_path).convert("RGBA")
            save_png(pixel, root / "pixel_tiles" / f"{tile.spec.tile_id}.png")
            compiled.append(_CompiledTile(tile.spec, pixel.copy()))
        return tuple(compiled)

    @staticmethod
    def _save_source_tiles(source_tiles: tuple[SourceTile, ...], root: Path) -> None:
        for tile in source_tiles:
            save_png(tile.image, root / "source_tiles" / f"{tile.spec.tile_id}.png")
            save_json(tile.spec.as_dict(), root / "source_tiles" / f"{tile.spec.tile_id}.json")

    @staticmethod
    def _save_patch_debug(database: PatchDatabase, debug_root: Path) -> None:
        for index, patch in enumerate(database.patches[:16]):
            save_png(patch.image, debug_root / "patch_samples" / f"patch_{index:02d}.png")
            save_json(patch.metadata.__dict__, debug_root / "patch_samples" / f"patch_{index:02d}.json")

    @staticmethod
    def _save_edge_debug(strips: dict[str, Any], debug_root: Path) -> None:
        for edge_id, edge_set in sorted(strips.items()):
            safe_name = edge_id.replace("/", "_")
            for side in ("north", "east", "south", "west"):
                save_png(getattr(edge_set, side), debug_root / "edge_strips" / f"{safe_name}_{side}.png")

    @staticmethod
    def _save_contract_debug(grid: list[list[_CompiledTile]], debug_root: Path) -> None:
        for y, row in enumerate(grid):
            for x, tile in enumerate(row):
                image = tile.image.convert("RGBA").copy()
                draw = ImageDraw.Draw(image)
                contract = tile.spec.edge_contract
                draw.text((2, 2), f"N:{contract.north}", fill=(255, 255, 255, 255))
                draw.text((2, 14), f"E:{contract.east}", fill=(255, 255, 255, 255))
                draw.text((2, 26), f"S:{contract.south}", fill=(255, 255, 255, 255))
                draw.text((2, 38), f"W:{contract.west}", fill=(255, 255, 255, 255))
                save_png(image, debug_root / "contracts" / f"map_{x:02d}_{y:02d}.png")

    @staticmethod
    def _repeat_image(image: Image.Image, columns: int, rows: int) -> Image.Image:
        return assemble_image([[type("RepeatTile", (), {"image": image})() for _x in range(columns)] for _y in range(rows)])

    @staticmethod
    def _independent_grid(tiles: tuple[_CompiledTile, ...], config: TilesetConfig) -> list[list[_CompiledTile]]:
        rng = random.Random(config.seed + 1000)
        shuffled = list(tiles)
        rng.shuffle(shuffled)
        return [[shuffled[(y * config.map_columns + x) % len(shuffled)] for x in range(config.map_columns)] for y in range(config.map_rows)]
