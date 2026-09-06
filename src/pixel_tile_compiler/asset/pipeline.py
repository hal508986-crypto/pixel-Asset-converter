"""Generation-first sheet processing and asset packaging pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
import shutil
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.map.metrics import measure_map_metrics
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
