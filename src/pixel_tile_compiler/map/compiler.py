"""MAP-first context-aware compilation and A/B/C experiment runner."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CompilerConfig, MapCompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.ir.schema import MapContext
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler, generate_baseline_bicubic_quantized

from .analysis import GlobalMapAnalysis, analyze_global_map, classify_map_material, render_region_map
from .metrics import measure_map_metrics
from .models import MapCompilationResult, MapExperimentResult, MapMetrics

Box = tuple[int, int, int, int]


def _validate_grid_index(value: int, limit: int, name: str) -> None:
    if not 0 <= value < limit:
        raise ValueError(f"{name} is outside the grid")


def compute_tile_box(
    image_size: tuple[int, int],
    columns: int,
    rows: int,
    tile_x: int,
    tile_y: int,
) -> Box:
    """Return a source-space tile box using integer grid edges."""
    _validate_grid_index(tile_x, columns, "tile_x")
    _validate_grid_index(tile_y, rows, "tile_y")
    width, height = image_size
    x0, x1 = (width * tile_x) // columns, (width * (tile_x + 1)) // columns
    y0, y1 = (height * tile_y) // rows, (height * (tile_y + 1)) // rows
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def compute_context_box(
    image_size: tuple[int, int],
    columns: int,
    rows: int,
    tile_x: int,
    tile_y: int,
    margin_tiles: int,
) -> Box:
    """Return a clipped context box; MAP edges never wrap."""
    if margin_tiles < 0:
        raise ValueError("margin_tiles must be non-negative")
    width, height = image_size
    _validate_grid_index(tile_x, columns, "tile_x")
    _validate_grid_index(tile_y, rows, "tile_y")
    x0 = (width * max(0, tile_x - margin_tiles)) // columns
    y0 = (height * max(0, tile_y - margin_tiles)) // rows
    x1 = (width * min(columns, tile_x + 1 + margin_tiles)) // columns
    y1 = (height * min(rows, tile_y + 1 + margin_tiles)) // rows
    return (
        max(0, x0),
        max(0, y0),
        min(width, max(x0 + 1, x1)),
        min(height, max(y0 + 1, y1)),
    )


def _mean_rgb(image: Image.Image) -> tuple[int, int, int]:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    return tuple(int(round(value)) for value in array.reshape(-1, 3).mean(axis=0))


def _box_to_analysis(box: Box, source_size: tuple[int, int], analysis_size: tuple[int, int]) -> Box:
    sx, sy = source_size
    ax, ay = analysis_size
    return (
        max(0, (box[0] * ax) // sx),
        max(0, (box[1] * ay) // sy),
        min(ax, max(1, (box[2] * ax) // sx)),
        min(ay, max(1, (box[3] * ay) // sy)),
    )


def _semantic_for_box(analysis: GlobalMapAnalysis, box: Box, source_size: tuple[int, int]) -> str:
    mapped = _box_to_analysis(box, source_size, analysis.normalized_image.size)
    sample = analysis.semantic_map[mapped[1]:mapped[3], mapped[0]:mapped[2]]
    if sample.size == 0:
        return "unknown"
    values, counts = np.unique(sample, return_counts=True)
    return str(values[int(np.argmax(counts))])


def _neighbor_box(
    source_size: tuple[int, int],
    columns: int,
    rows: int,
    tile_x: int,
    tile_y: int,
    side: str,
) -> Box | None:
    neighbors = {
        "west": (tile_x - 1, tile_y),
        "east": (tile_x + 1, tile_y),
        "north": (tile_x, tile_y - 1),
        "south": (tile_x, tile_y + 1),
    }
    neighbor_x, neighbor_y = neighbors[side]
    if not (0 <= neighbor_x < columns and 0 <= neighbor_y < rows):
        return None
    return compute_tile_box(source_size, columns, rows, neighbor_x, neighbor_y)


def _guidance(
    map_image: Image.Image,
    source_box: Box,
    source_size: tuple[int, int],
    analysis: GlobalMapAnalysis,
    columns: int,
    rows: int,
    tile_x: int,
    tile_y: int,
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for side in ("north", "south", "east", "west"):
        box = _neighbor_box(source_size, columns, rows, tile_x, tile_y, side)
        if box is None or box[2] <= box[0] or box[3] <= box[1]:
            values[side] = {"semantic": None, "dominant_colors": [], "edge_density": 0.0}
            continue
        neighbor = map_image.crop(box)
        mean = _mean_rgb(neighbor)
        edge_box = _box_to_analysis(box, source_size, analysis.edges.size)
        edge_pixels = np.asarray(analysis.edges)[edge_box[1]:edge_box[3], edge_box[0]:edge_box[2]]
        values[side] = {
            "semantic": _semantic_for_box(analysis, box, source_size),
            "dominant_colors": [list(mean)],
            "edge_density": float((edge_pixels > 0).mean()) if edge_pixels.size else 0.0,
        }
    return values


def _blend_edge_guidance(
    image: Image.Image,
    guidance: dict[str, dict[str, Any]],
    central_semantic: str,
    strength: float = 0.15,
) -> Image.Image:
    """Use neighbor colors as low-strength source guidance before central compilation."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32).copy()
    height, width = rgba.shape[:2]
    band = max(1, min(6, width // 16, height // 16))
    for side, region in (
        ("west", (slice(None), slice(0, band))),
        ("east", (slice(None), slice(width - band, width))),
        ("north", (slice(0, band), slice(None))),
        ("south", (slice(height - band, height), slice(None))),
    ):
        side_guidance = guidance.get(side, {})
        colors = side_guidance.get("dominant_colors", [])
        if not colors:
            continue
        target = np.asarray(colors[0], dtype=np.float32)
        pixels = rgba[region]
        semantic_factor = 1.0 if side_guidance.get("semantic") == central_semantic else 0.25
        side_strength = min(0.3, (strength + float(side_guidance.get("edge_density", 0.0)) * 0.08) * semantic_factor)
        pixels[:, :, :3] = pixels[:, :, :3] * (1.0 - side_strength) + target * side_strength
        rgba[region] = pixels
    return Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), mode="RGBA")


def _make_grid_overlay(image: Image.Image, columns: int, rows: int, tile_size: int) -> Image.Image:
    overlay = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(overlay)
    for tile_x in range(1, columns):
        x = tile_x * tile_size
        draw.line((x, 0, x, image.height - 1), fill=(255, 255, 255, 220), width=1)
    for tile_y in range(1, rows):
        y = tile_y * tile_size
        draw.line((0, y, image.width - 1, y), fill=(255, 255, 255, 220), width=1)
    return overlay


def _nearest_palette_color(color: np.ndarray, palette: tuple[tuple[int, int, int], ...]) -> tuple[int, int, int]:
    candidates = np.asarray(palette, dtype=np.float32)
    distances = np.linalg.norm(candidates - color[:3], axis=1)
    return tuple(int(channel) for channel in candidates[int(np.argmin(distances))])


def correct_shared_boundaries(
    image: Image.Image,
    columns: int,
    rows: int,
    tile_size: int,
    palette: tuple[tuple[int, int, int], ...],
    semantic_map: np.ndarray | None = None,
    source_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Apply a small palette-preserving correction at shared MAP boundaries."""
    if not palette:
        return image.convert("RGBA").copy()
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    band = max(1, min(3, tile_size // 16))

    def semantic_matches(axis: str, boundary: int, index: int) -> bool:
        if semantic_map is None or source_size is None:
            return True
        source_width, source_height = source_size
        output_width, output_height = image.size
        if axis == "vertical":
            source_y = min(semantic_map.shape[0] - 1, (index * semantic_map.shape[0]) // output_height)
            left_x = min(semantic_map.shape[1] - 1, ((boundary - 1) * source_width // output_width) * semantic_map.shape[1] // source_width)
            right_x = min(semantic_map.shape[1] - 1, (boundary * source_width // output_width) * semantic_map.shape[1] // source_width)
            return semantic_map[source_y, left_x] == semantic_map[source_y, right_x]
        source_x = min(semantic_map.shape[1] - 1, (index * semantic_map.shape[1]) // output_width)
        top_y = min(semantic_map.shape[0] - 1, ((boundary - 1) * source_height // output_height) * semantic_map.shape[0] // source_height)
        bottom_y = min(semantic_map.shape[0] - 1, (boundary * source_height // output_height) * semantic_map.shape[0] // source_height)
        return semantic_map[top_y, source_x] == semantic_map[bottom_y, source_x]

    for tile_x in range(1, columns):
        boundary = tile_x * tile_size
        for y in range(rgba.shape[0]):
            if not semantic_matches("vertical", boundary, y):
                continue
            left = rgba[y, boundary - band:boundary, :3].astype(np.float32).mean(axis=0)
            right = rgba[y, boundary:boundary + band, :3].astype(np.float32).mean(axis=0)
            target = (left + right) / 2.0
            for offset in range(band):
                weight = 0.7 * (band - offset) / band
                for x in (boundary - 1 - offset, boundary + offset):
                    if 0 <= x < rgba.shape[1]:
                        current = rgba[y, x, :3].astype(np.float32)
                        adjusted = current * (1.0 - weight) + target * weight
                        rgba[y, x, :3] = _nearest_palette_color(adjusted, palette)
    for tile_y in range(1, rows):
        boundary = tile_y * tile_size
        for x in range(rgba.shape[1]):
            if not semantic_matches("horizontal", boundary, x):
                continue
            top = rgba[boundary - band:boundary, x, :3].astype(np.float32).mean(axis=0)
            bottom = rgba[boundary:boundary + band, x, :3].astype(np.float32).mean(axis=0)
            target = (top + bottom) / 2.0
            for offset in range(band):
                weight = 0.7 * (band - offset) / band
                for y in (boundary - 1 - offset, boundary + offset):
                    if 0 <= y < rgba.shape[0]:
                        current = rgba[y, x, :3].astype(np.float32)
                        adjusted = current * (1.0 - weight) + target * weight
                        rgba[y, x, :3] = _nearest_palette_color(adjusted, palette)
    return Image.fromarray(rgba, mode="RGBA")


class MapCompiler:
    """Compile one high-resolution MAP into a coherent tile set."""

    def compile(self, source: Path, config: MapCompilerConfig) -> MapCompilationResult:
        """Analyze the complete MAP, compile every central tile, and reassemble it."""
        source = Path(source)
        return self._compile_loaded(load_image(source), str(source), config, context_enabled=True)

    def compile_tile_with_context(
        self,
        map_image: Image.Image,
        tile_x: int,
        tile_y: int,
        context_margin: int,
        global_analysis: GlobalMapAnalysis,
        shared_palette: tuple[tuple[int, int, int], ...] | bool | None,
        *,
        columns: int = 4,
        rows: int = 5,
        tile_mode: str = "repeatable",
        seed: int = 42,
    ) -> Image.Image:
        """Compile only the central tile while using its surrounding MAP context."""
        if isinstance(shared_palette, bool):
            palette = global_analysis.palette if shared_palette else None
        else:
            palette = shared_palette
        palette_budget = max(16, min(32, len(palette or global_analysis.palette)))
        config = MapCompilerConfig(
            output_root=Path("map_context_api_output"),
            columns=columns,
            rows=rows,
            context_margin_tiles=context_margin,
            shared_palette_enabled=palette is not None,
            global_palette_budget=palette_budget,
            tile_mode=tile_mode,  # type: ignore[arg-type]
            seed=seed,
        )
        with TemporaryDirectory(prefix="pixel_tile_context_") as temporary:
            final_tile, _ = self._compile_one_tile(
                map_image=map_image,
                source_name="<map-memory>",
                source_size=map_image.size,
                tile_x=tile_x,
                tile_y=tile_y,
                config=config,
                analysis=global_analysis,
                context_enabled=True,
                artifacts_dir=Path(temporary) / "artifacts",
                tiles_dir=Path(temporary) / "tiles",
                palette=palette,
            )
            return final_tile.copy()

    def _compile_one_tile(
        self,
        map_image: Image.Image,
        source_name: str,
        source_size: tuple[int, int],
        tile_x: int,
        tile_y: int,
        config: MapCompilerConfig,
        analysis: GlobalMapAnalysis,
        context_enabled: bool,
        artifacts_dir: Path,
        tiles_dir: Path,
        palette: tuple[tuple[int, int, int], ...] | None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        source_box = compute_tile_box(source_size, config.columns, config.rows, tile_x, tile_y)
        context_active = context_enabled and config.context_margin_tiles > 0
        context_box = compute_context_box(
            source_size,
            config.columns,
            config.rows,
            tile_x,
            tile_y,
            config.context_margin_tiles if context_active else 0,
        )
        context_crop = map_image.crop(context_box)
        central = map_image.crop(source_box)
        central_semantic = classify_map_material(_mean_rgb(central))
        guidance = _guidance(
            map_image,
            source_box,
            source_size,
            analysis,
            config.columns,
            config.rows,
            tile_x,
            tile_y,
        ) if context_active else {
            side: {"semantic": None, "dominant_colors": [], "edge_density": 0.0}
            for side in ("north", "south", "east", "west")
        }
        if context_active:
            central = _blend_edge_guidance(central, guidance, central_semantic=central_semantic)
        central_mean = _mean_rgb(central)
        central_analysis_box = _box_to_analysis(source_box, source_size, analysis.normalized_image.size)
        central_pixels = np.asarray(analysis.normalized_image)[
            central_analysis_box[1]:central_analysis_box[3], central_analysis_box[0]:central_analysis_box[2]
        ]
        local_brightness = float(central_pixels[:, :, :3].mean() / 255.0) if central_pixels.size else 0.0
        context_pixels = np.asarray(context_crop.convert("RGB"), dtype=np.float32)
        local_texture = float(min(1.0, context_pixels.std() / 128.0)) if context_pixels.size else 0.0
        map_context = MapContext(
            tile_x=tile_x,
            tile_y=tile_y,
            north_semantic=guidance["north"]["semantic"],
            south_semantic=guidance["south"]["semantic"],
            east_semantic=guidance["east"]["semantic"],
            west_semantic=guidance["west"]["semantic"],
            global_palette_id=analysis.palette_id if palette is not None else None,
            local_brightness_target=max(0.0, min(1.0, local_brightness)),
            local_texture_target=max(0.0, min(1.0, local_texture)),
        )
        tile_name = f"tile_{tile_x:02d}_{tile_y:02d}"
        tile_config = CompilerConfig(
            output_root=artifacts_dir / tile_name,
            palette_budget=config.global_palette_budget,
            tile_mode=config.tile_mode,
            semantic_provider=config.semantic_provider,
            seed=config.seed,
            debug_enabled=config.debug_enabled,
            smoothing_enabled=config.smoothing_enabled,
            palette_colors=palette,
        )
        compiled = PixelTileCompiler().compile_image(
            central,
            tile_config,
            source_name=f"{source_name}::{tile_name}",
            map_context=map_context,
        )
        final_tile = Image.open(compiled.final_path).convert("RGBA")
        save_png(final_tile, tiles_dir / f"{tile_name}.png")
        layout_tile = {
            "x": tile_x,
            "y": tile_y,
            "source_bbox": list(source_box),
            "context_bbox": list(context_box),
            "semantic": _semantic_for_box(analysis, source_box, source_size) or classify_map_material(central_mean),
            "context_used": context_active,
            "neighbor_guidance": guidance,
            "metrics": asdict(compiled.metrics),
        }
        return final_tile.copy(), layout_tile

    def _compile_loaded(
        self,
        map_image: Image.Image,
        source_name: str,
        config: MapCompilerConfig,
        context_enabled: bool,
        output_root: Path | None = None,
    ) -> MapCompilationResult:
        root = Path(output_root or config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        tiles_dir = root / "tiles"
        artifacts_dir = root / "tile_artifacts"
        tiles_dir.mkdir(parents=True, exist_ok=True)
        analysis = analyze_global_map(map_image, config)
        save_json(analysis.to_dict(), root / "global_analysis.json")
        save_png(analysis.edges, root / "global_edges.png")
        save_png(render_region_map(analysis.region_map), root / "global_regions.png")
        shared_palette = analysis.palette if config.shared_palette_enabled else None
        tile_images: dict[tuple[int, int], Image.Image] = {}
        layout_tiles: list[dict[str, Any]] = []
        source_size = map_image.size
        for tile_y in range(config.rows):
            for tile_x in range(config.columns):
                final_tile, layout_tile = self._compile_one_tile(
                    map_image=map_image,
                    source_name=source_name,
                    source_size=source_size,
                    tile_x=tile_x,
                    tile_y=tile_y,
                    config=config,
                    analysis=analysis,
                    context_enabled=context_enabled,
                    artifacts_dir=artifacts_dir,
                    tiles_dir=tiles_dir,
                    palette=shared_palette,
                )
                tile_images[(tile_x, tile_y)] = final_tile
                layout_tiles.append(layout_tile)
        assembled = Image.new("RGBA", config.output_size, (0, 0, 0, 0))
        for tile_y in range(config.rows):
            for tile_x in range(config.columns):
                assembled.paste(tile_images[(tile_x, tile_y)], (tile_x * config.tile_size, tile_y * config.tile_size))
        if context_enabled and config.context_margin_tiles > 0 and shared_palette is not None:
            assembled = correct_shared_boundaries(
                assembled,
                columns=config.columns,
                rows=config.rows,
                tile_size=config.tile_size,
                palette=shared_palette,
                semantic_map=analysis.semantic_map,
                source_size=source_size,
            )
            for tile_y in range(config.rows):
                for tile_x in range(config.columns):
                    corrected_tile = assembled.crop(
                        (
                            tile_x * config.tile_size,
                            tile_y * config.tile_size,
                            (tile_x + 1) * config.tile_size,
                            (tile_y + 1) * config.tile_size,
                        )
                    )
                    save_png(corrected_tile, tiles_dir / f"tile_{tile_x:02d}_{tile_y:02d}.png")
        final_path = save_png(assembled, root / "map_compiled.png")
        metrics = measure_map_metrics(assembled, config.columns, config.rows, config.tile_size)
        layout = {
            "columns": config.columns,
            "rows": config.rows,
            "tile_size": config.tile_size,
            "source_size": list(source_size),
            "context_margin_tiles": config.context_margin_tiles if context_enabled else 0,
            "shared_palette_enabled": config.shared_palette_enabled,
            "tiles": sorted(layout_tiles, key=lambda tile: (tile["y"], tile["x"])),
        }
        save_json(layout, root / "map_layout.json")
        save_json(asdict(metrics), root / "metrics.json")
        save_png(_make_grid_overlay(assembled, config.columns, config.rows, config.tile_size), root / "grid_overlay.png")
        return MapCompilationResult(final_path, tiles_dir, layout, metrics, analysis.to_dict())


class MapExperimentRunner:
    """Produce the required global, independent, and context-aware comparison."""

    def run(self, source: Path, config: MapCompilerConfig) -> MapExperimentResult:
        source = Path(source)
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        map_image = load_image(source)
        save_png(map_image, root / "source.png")
        baseline = generate_baseline_bicubic_quantized(map_image, config.global_palette_budget, config.output_size)
        baseline_path = save_png(baseline, root / "baseline_global.png")
        compiler = MapCompiler()
        independent_config = replace(config, output_root=root / "independent", shared_palette_enabled=False)
        context_config = replace(config, output_root=root / "context")
        independent = compiler._compile_loaded(
            map_image,
            str(source),
            independent_config,
            context_enabled=False,
            output_root=independent_config.output_root,
        )
        context = compiler._compile_loaded(
            map_image,
            str(source),
            context_config,
            context_enabled=True,
            output_root=context_config.output_root,
        )
        independent_path = save_png(Image.open(independent.final_path), root / "independent_tiles.png")
        context_path = save_png(Image.open(context.final_path), root / "context_compiled.png")
        save_png(Image.open(context.final_path), root / "map_compiled.png")
        save_png(Image.open(context.final_path.parent / "grid_overlay.png"), root / "grid_overlay.png")
        comparison = Image.new("RGBA", (config.output_size[0] * 3, config.output_size[1]), (0, 0, 0, 255))
        comparison.paste(baseline, (0, 0))
        comparison.paste(Image.open(independent.final_path).convert("RGBA"), (config.output_size[0], 0))
        comparison.paste(Image.open(context.final_path).convert("RGBA"), (config.output_size[0] * 2, 0))
        comparison_path = save_png(comparison, root / "comparison.png")
        metrics = {
            "global_resize": asdict(measure_map_metrics(baseline, config.columns, config.rows, config.tile_size)),
            "independent_tiles": asdict(independent.metrics),
            "context_aware": asdict(context.metrics),
            "config": {
                "columns": config.columns,
                "rows": config.rows,
                "tile_size": config.tile_size,
                "context_margin_tiles": config.context_margin_tiles,
                "shared_palette_enabled": config.shared_palette_enabled,
                "global_palette_budget": config.global_palette_budget,
                "independent_shared_palette_enabled": False,
                "context_shared_palette_enabled": config.shared_palette_enabled,
            },
        }
        metrics_path = save_json(metrics, root / "metrics.json")
        return MapExperimentResult(root, baseline_path, independent_path, context_path, comparison_path, metrics_path, context)
