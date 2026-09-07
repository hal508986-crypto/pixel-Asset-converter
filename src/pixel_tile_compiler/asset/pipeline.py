"""Generation-first sheet processing and asset packaging pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import shutil
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.asset.acceptance import validate_tileset_contract
from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.map.metrics import measure_map_metrics
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.palette import extract_palette
from pixel_tile_compiler.tile.seam import measure_seams
from pixel_tile_compiler.transition_network.graph import NetworkTopology, NetworkTopologyResolver
from pixel_tile_compiler.transition_network.road_graph import default_road_graph

from pixel_tile_compiler.generation.adapter import GeneratedImage
from pixel_tile_compiler.generation.request_compiler import GenerationRequestCompiler
from pixel_tile_compiler.generation.spec import TilesetSpec
from pixel_tile_compiler.sheet.splitter import GridSplitResult, GridSplitter

from .manifest import AssetTileRecord, TilesetManifest


@dataclass(frozen=True)
class AssetPackageResult:
    output_root: Path
    manifest_path: Path
    validation_path: Path
    split_preview_path: Path
    repeat_preview_path: Path | None
    network_preview_path: Path | None
    validation: Any


@dataclass(frozen=True)
class CompiledAssetPackageResult:
    """Artifacts from the explicit high-resolution-cell compile path."""

    output_root: Path
    manifest_path: Path
    validation_path: Path
    map_path: Path
    comparison_path: Path
    preview_4x_path: Path
    settings_path: Path
    validation: dict[str, object]


def process_generated_sheet(
    spec: TilesetSpec,
    image_path: Path,
    output_root: Path,
    generated: GeneratedImage | None = None,
) -> AssetPackageResult:
    """Process an existing generated sheet without mutating the raw input."""
    image_path = Path(image_path)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    generated = generated or GeneratedImage.from_path(image_path, generator="existing_generated_sheet")
    request = GenerationRequestCompiler().compile(spec)
    save_json(request.model_dump(mode="json"), output_root / "generation_request.json")
    save_json(generated.as_dict(), output_root / "generation_manifest.json")
    spec.write_json(output_root / "tileset_spec.json")
    raw_path = output_root / "sheet_raw.png"
    if image_path.suffix.lower() == ".png":
        shutil.copy2(image_path, raw_path)
    else:
        with Image.open(image_path) as source:
            source.save(raw_path, format="PNG")
    with Image.open(image_path) as source:
        split = GridSplitter().split(
            source,
            spec.grid_columns,
            spec.grid_rows,
            crop_policy=spec.postprocess.crop_policy,
            tile_size=spec.tile_size_px,
            normalize_mode=spec.postprocess.normalize_mode,
        )
    save_png(split.normalized_sheet, output_root / "sheet_normalized.png")
    save_json(
        {"crop_box": list(split.crop_box), "method": split.normalization_method},
        output_root / "sheet_normalization.json",
    )
    tiles_root = output_root / "tiles"
    tiles_root.mkdir(parents=True, exist_ok=True)
    spec_tiles = sorted(spec.tiles, key=lambda item: (item.cell[1], item.cell[0]))
    records: list[AssetTileRecord] = []
    tile_metrics: list[dict[str, object]] = []
    images_by_id: dict[str, Image.Image] = {}
    for tile_spec, split_tile in zip(spec_tiles, split.tiles):
        tile_path = save_png(split_tile.image, tiles_root / f"{tile_spec.id}.png")
        images_by_id[tile_spec.id] = split_tile.image.copy()
        records.append(
            AssetTileRecord(
                id=tile_spec.id,
                row=tile_spec.cell[1],
                column=tile_spec.cell[0],
                file=str(tile_path.relative_to(output_root)).replace("\\", "/"),
                semantic=tile_spec.semantic,
                material=tile_spec.material,
                network=tile_spec.network,
                connectors=tile_spec.connectors,
            )
        )
        seam = measure_seams(split_tile.image, tile_mode="repeatable")
        tile_metrics.append({"id": tile_spec.id, "seams": asdict(seam)})
    manifest = TilesetManifest(
        tileset_id=spec.tileset_id,
        tile_size_px=spec.tile_size_px,
        sheet={"columns": spec.grid_columns, "rows": spec.grid_rows},
        status="provisional",
        tiles=tuple(records),
    )
    manifest.validate_complete()
    manifest_path = save_json(manifest.model_dump(mode="json"), output_root / "manifest.json")
    validation = {
        "status": split.validation.status,
        "sheet": split.validation.as_dict(),
        "tile_count": len(records),
        "tile_size_px": spec.tile_size_px,
        "tile_metrics": tile_metrics,
        "issues": [],
        "warnings": list(split.validation.warnings),
    }
    validation_path = save_json(validation, output_root / "validation" / "report.json")
    previews_root = output_root / "previews"
    previews_root.mkdir(parents=True, exist_ok=True)
    split_preview = _contact_preview(images_by_id, records, spec.grid_columns, spec.grid_rows)
    split_preview_path = save_png(split_preview, previews_root / "split_preview.png")
    repeat_preview_path: Path | None = None
    network_preview_path: Path | None = None
    if any(tile.semantic == "surface" for tile in spec.tiles):
        repeat_preview = _repeat_preview(images_by_id, records, seed=spec.generation.seed)
        repeat_preview_path = save_png(repeat_preview, previews_root / "repeat_preview.png")
        validation["repeat_metrics"] = asdict(measure_map_metrics(repeat_preview, 8, 8, spec.tile_size_px))
        save_json(validation, validation_path)
        save_png(repeat_preview, previews_root / "map_preview.png")
    if any(tile.semantic == "network" for tile in spec.tiles):
        network_preview = _network_preview(images_by_id, records, spec.tile_size_px)
        network_preview_path = save_png(network_preview, previews_root / "network_preview.png")
        validation["network_metrics"] = asdict(measure_map_metrics(network_preview, 10, 10, spec.tile_size_px))
        save_json(validation, validation_path)
        save_png(network_preview, previews_root / "map_preview.png")
    return AssetPackageResult(output_root, manifest_path, validation_path, split_preview_path, repeat_preview_path, network_preview_path, validation)


def compile_generated_sheet(
    spec: TilesetSpec,
    image_path: Path,
    output_root: Path,
    *,
    palette_budget: int = 16,
    debug_enabled: bool = False,
) -> CompiledAssetPackageResult:
    """Compile high-resolution sheet cells into a verified 64px package.

    The existing :func:`process_generated_sheet` remains the split-only
    compatibility path.  This function is intentionally explicit: it keeps
    each original cell at its source resolution until ``PixelTileCompiler``
    performs analysis and pixelization, then writes a separate package.
    """

    image_path = Path(image_path)
    output_root = Path(output_root)
    if spec.tile_size_px != 64:
        raise ValueError("compiled sheet path requires tile_size_px=64")
    if not 4 <= palette_budget <= 64:
        raise ValueError("palette_budget must be between 4 and 64")
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"compiled sheet output is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    source_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    generated = GeneratedImage.from_path(image_path, generator="existing_generated_sheet")
    request = GenerationRequestCompiler().compile(spec)
    save_json(request.model_dump(mode="json"), output_root / "generation_request.json")
    save_json(generated.as_dict(), output_root / "generation_manifest.json")
    spec.write_json(output_root / "tileset_spec.json")
    raw_path = output_root / "sheet_raw.png"
    if image_path.suffix.lower() == ".png":
        shutil.copy2(image_path, raw_path)
    else:
        with Image.open(image_path) as source:
            source.save(raw_path, format="PNG")

    splitter = GridSplitter()
    with Image.open(image_path) as opened:
        sheet_image = opened.convert("RGBA").copy()
    split = splitter.split(
        sheet_image,
        spec.grid_columns,
        spec.grid_rows,
        crop_policy=spec.postprocess.crop_policy,
        tile_size=spec.tile_size_px,
        normalize_mode=spec.postprocess.normalize_mode,
    )
    source_cells = splitter.extract_source_cells(
        sheet_image,
        spec.grid_columns,
        spec.grid_rows,
        crop_policy=spec.postprocess.crop_policy,
    )
    save_png(split.normalized_sheet, output_root / "sheet_normalized.png")
    save_json(
        {"crop_box": list(split.crop_box), "method": split.normalization_method},
        output_root / "sheet_normalization.json",
    )

    spec_tiles = sorted(spec.tiles, key=lambda item: (item.cell[1], item.cell[0]))
    if len(spec_tiles) != len(source_cells) or len(spec_tiles) != len(split.tiles):
        raise ValueError("sheet cell count does not match TilesetSpec")

    source_cells_root = output_root / "source_cells"
    source_images: dict[str, Image.Image] = {}
    source_paths: dict[str, Path] = {}
    for tile_spec, source_cell in zip(spec_tiles, source_cells):
        path = save_png(source_cell.image, source_cells_root / f"{tile_spec.id}.png")
        source_paths[tile_spec.id] = path
        source_images[tile_spec.id] = source_cell.image.copy()

    cell_width, cell_height = source_cells[0].image.size
    palette_montage = Image.new("RGBA", (cell_width * spec.grid_columns, cell_height * spec.grid_rows), (0, 0, 0, 0))
    for tile_spec, source_cell in zip(spec_tiles, source_cells):
        palette_montage.paste(source_cell.image, (tile_spec.cell[0] * cell_width, tile_spec.cell[1] * cell_height))
    shared_palette = extract_palette(palette_montage, budget=palette_budget)

    tiles_root = output_root / "tiles"
    artifacts_root = output_root / "compile_artifacts"
    final_images: dict[str, Image.Image] = {}
    records: list[AssetTileRecord] = []
    tile_metrics: list[dict[str, object]] = []
    for tile_spec, source_cell in zip(spec_tiles, source_cells):
        artifact_root = artifacts_root / tile_spec.id
        config = CompilerConfig(
            output_root=artifact_root,
            palette_budget=palette_budget,
            palette_colors=shared_palette,
            tile_mode="directional",
            repeat_opt_enabled=False,
            dither="off",
            seed=spec.generation.seed,
            debug_enabled=debug_enabled,
        )
        compiled = PixelTileCompiler().compile_image(
            source_cell.image,
            config,
            source_name=f"{image_path}::cell:{tile_spec.id}",
        )
        with Image.open(compiled.final_path) as opened:
            final_image = opened.convert("RGBA").copy()
        final_path = save_png(final_image, tiles_root / f"{tile_spec.id}.png")
        final_images[tile_spec.id] = final_image
        rgba = final_image.convert("RGBA")
        visible = [pixel[:3] for pixel in rgba.getdata() if pixel[3] != 0]
        alpha_values = tuple(sorted({pixel[3] for pixel in rgba.getdata()}))
        records.append(
            AssetTileRecord(
                id=tile_spec.id,
                row=tile_spec.cell[1],
                column=tile_spec.cell[0],
                file=str(final_path.relative_to(output_root)).replace("\\", "/"),
                semantic=tile_spec.semantic,
                material=tile_spec.material,
                network=tile_spec.network,
                connectors=tile_spec.connectors,
                source_box=source_cell.source_box,
                source_sha256=hashlib.sha256(source_paths[tile_spec.id].read_bytes()).hexdigest(),
                final_sha256=hashlib.sha256(final_path.read_bytes()).hexdigest(),
                palette_count=len(set(visible)),
                alpha_values=alpha_values,
            )
        )
        tile_metrics.append({"id": tile_spec.id, "seams": asdict(measure_seams(final_image, tile_mode="directional"))})

    manifest = TilesetManifest(
        tileset_id=spec.tileset_id,
        tile_size_px=spec.tile_size_px,
        sheet={"columns": spec.grid_columns, "rows": spec.grid_rows},
        status="provisional",
        tiles=tuple(records),
    )
    manifest.validate_complete()
    manifest_path = save_json(manifest.model_dump(mode="json"), output_root / "manifest.json")
    validation = validate_tileset_contract(
        spec_tiles,
        source_images,
        final_images,
        spec.network_contracts,
        palette_budget=palette_budget,
    )
    validation.update(
        {
            "sheet": split.validation.as_dict(),
            "tile_count": len(records),
            "tile_metrics": tile_metrics,
            "human_review": {
                "required": True,
                "automatic_aesthetic_approval": False,
                "items": ["terrain style", "boundary naturalness", "map readability"],
            },
        }
    )
    validation_path = save_json(validation, output_root / "validation" / "report.json")

    compiled_map = Image.new("RGBA", (spec.grid_columns * spec.tile_size_px, spec.grid_rows * spec.tile_size_px), (0, 0, 0, 0))
    split_map = Image.new("RGBA", (spec.grid_columns * spec.tile_size_px, spec.grid_rows * spec.tile_size_px), (0, 0, 0, 0))
    split_by_cell = {(tile.column, tile.row): tile.image.convert("RGBA") for tile in split.tiles}
    for tile_spec in spec_tiles:
        position = (tile_spec.cell[0] * spec.tile_size_px, tile_spec.cell[1] * spec.tile_size_px)
        compiled_map.paste(final_images[tile_spec.id], position)
        split_map.paste(split_by_cell[tile_spec.cell], position)
    map_path = save_png(compiled_map, output_root / "map_compiled.png")
    save_png(split_map, output_root / "comparison" / "split_only_map.png")
    comparison = Image.new("RGBA", (compiled_map.width * 2, compiled_map.height), (0, 0, 0, 255))
    comparison.paste(split_map, (0, 0))
    comparison.paste(compiled_map, (compiled_map.width, 0))
    comparison_path = save_png(comparison, output_root / "comparison" / "split_only_vs_compiled.png")
    preview_4x_path = save_png(
        compiled_map.resize((compiled_map.width * 4, compiled_map.height * 4), Image.Resampling.NEAREST),
        output_root / "previews" / "map_compiled_4x_nearest.png",
    )

    settings = {
        "compile_path": "high_resolution_cell_to_pixel_tile",
        "source_sheet": {
            "path": str(image_path),
            "sha256": source_hash,
            "dimensions": list(generated.raw_dimensions),
        },
        "grid": {"columns": spec.grid_columns, "rows": spec.grid_rows, "tile_size_px": 64},
        "crop_policy": spec.postprocess.crop_policy,
        "normalization_mode_for_split_only_comparison": spec.postprocess.normalize_mode,
        "shared_palette": {"enabled": True, "budget": palette_budget, "colors": [list(color) for color in shared_palette]},
        "compiler": {
            "tile_mode": "directional",
            "repeat_opt_enabled": False,
            "seed": spec.generation.seed,
            "debug_enabled": debug_enabled,
        },
        "cells": [
            {"id": tile.id, "column": tile.column, "row": tile.row, "source_box": list(tile.source_box or ())}
            for tile in records
        ],
        "adoption": {"status": "provisional_not_approved", "requires_human_review": True},
    }
    settings_path = save_json(settings, output_root / "compile_settings.json")
    return CompiledAssetPackageResult(
        output_root,
        manifest_path,
        validation_path,
        map_path,
        comparison_path,
        preview_4x_path,
        settings_path,
        validation,
    )


def validate_asset_package(output_root: Path) -> dict[str, object]:
    """Validate an already packaged asset directory without regenerating it."""
    output_root = Path(output_root)
    issues: list[str] = []
    manifest_path = output_root / "manifest.json"
    if not manifest_path.exists():
        return {"status": "rejected", "issues": ["manifest.json is missing"], "warnings": []}
    try:
        manifest = TilesetManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest.validate_complete()
    except (OSError, ValueError) as exc:
        return {"status": "rejected", "issues": [str(exc)], "warnings": []}
    for tile in manifest.tiles:
        path = output_root / tile.file
        if not path.exists():
            issues.append(f"missing tile file: {tile.file}")
            continue
        with Image.open(path) as image:
            if image.size != (manifest.tile_size_px, manifest.tile_size_px):
                issues.append(f"invalid tile dimensions: {tile.file}")
        if tile.final_sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != tile.final_sha256:
            issues.append(f"final tile hash mismatch: {tile.file}")
    compiled_spec_path = output_root / "tileset_spec.json"
    source_cells_root = output_root / "source_cells"
    if compiled_spec_path.exists() and source_cells_root.exists():
        try:
            spec = TilesetSpec.from_json_file(compiled_spec_path)
            source_images: dict[str, Image.Image] = {}
            final_images: dict[str, Image.Image] = {}
            for tile in manifest.tiles:
                source_path = source_cells_root / f"{tile.id}.png"
                final_path = output_root / tile.file
                if not source_path.exists() or not final_path.exists():
                    continue
                if tile.source_sha256 and hashlib.sha256(source_path.read_bytes()).hexdigest() != tile.source_sha256:
                    issues.append(f"source cell hash mismatch: {tile.id}")
                with Image.open(source_path) as source, Image.open(final_path) as final:
                    source_images[tile.id] = source.convert("RGBA").copy()
                    final_images[tile.id] = final.convert("RGBA").copy()
            settings_path = output_root / "compile_settings.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
            raw_path = output_root / "sheet_raw.png"
            expected_raw_hash = settings.get("source_sheet", {}).get("sha256")
            if expected_raw_hash and raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != expected_raw_hash:
                issues.append("raw sheet hash mismatch")
            palette_budget = int(settings.get("shared_palette", {}).get("budget", 16))
            gate = validate_tileset_contract(
                spec.tiles,
                source_images,
                final_images,
                spec.network_contracts,
                palette_budget=palette_budget,
            )
            if gate["status"] != "accepted":
                issues.append("compiled tile contract gate rejected the package")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(f"compiled package validation failed: {exc}")
    status = "rejected" if issues else "accepted"
    return {"status": status, "issues": issues, "warnings": [], "tile_count": len(manifest.tiles)}


def _contact_preview(
    images_by_id: dict[str, Image.Image],
    records: list[AssetTileRecord],
    columns: int,
    rows: int,
) -> Image.Image:
    tile_size = next(iter(images_by_id.values())).width
    output = Image.new("RGBA", (columns * tile_size, rows * tile_size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(output)
    for record in records:
        output.paste(images_by_id[record.id], (record.column * tile_size, record.row * tile_size))
        draw.rectangle((record.column * tile_size, record.row * tile_size, record.column * tile_size + 8, record.row * tile_size + 8), fill=(0, 0, 0, 120))
    return output


def _repeat_preview(images_by_id: dict[str, Image.Image], records: list[AssetTileRecord], seed: int) -> Image.Image:
    tile_size = next(iter(images_by_id.values())).width
    output = Image.new("RGBA", (8 * tile_size, 8 * tile_size), (0, 0, 0, 255))
    rng = random.Random(seed)
    ids = [record.id for record in records if record.semantic == "surface"] or [record.id for record in records]
    for y in range(8):
        for x in range(8):
            output.paste(images_by_id[rng.choice(ids)], (x * tile_size, y * tile_size))
    return output


def _network_preview(images_by_id: dict[str, Image.Image], records: list[AssetTileRecord], tile_size: int) -> Image.Image:
    lookup = {
        (record.network, tuple(record.connectors)): record.id
        for record in records
        if record.semantic == "network"
    }
    graph = default_road_graph(10, 10)
    resolved = NetworkTopologyResolver().resolve_grid(graph)
    output = Image.new("RGBA", (10 * tile_size, 10 * tile_size), (0, 0, 0, 255))
    for point, cell in resolved.items():
        topology = () if cell.topology is NetworkTopology.EMPTY else tuple(cell.topology.value)
        tile_id = lookup.get(("road", topology))
        if tile_id is None:
            raise ValueError(f"network preview has no asset for topology {cell.topology.value}")
        output.paste(images_by_id[tile_id], (point.x * tile_size, point.y * tile_size))
    return output
