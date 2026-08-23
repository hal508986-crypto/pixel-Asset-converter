"""Compact, repeatable Compile Probe and Map Probe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageEnhance

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pixel_grammar.metrics import compute_map_metrics, compute_tile_metrics, grammar_score
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


@dataclass(frozen=True)
class CompileProbeResult:
    output_root: Path
    pixel_tile_paths: tuple[Path, ...]
    map_path: Path
    metrics_path: Path
    metrics: dict[str, float]


def run_compile_probe(
    source: Image.Image,
    output_root: Path,
    material_id: str,
    variants: int = 8,
    palette_budget: int = 28,
    seed: int = 42,
    map_columns: int = 10,
    map_rows: int = 10,
) -> CompileProbeResult:
    """Compile deterministic source variants and assemble a variant-aware map."""
    if variants < 1:
        raise ValueError("variants must be positive")
    root = Path(output_root)
    source_root = root / "source_tiles"
    pixel_root = root / "pixel_tiles"
    compiler_root = root / "_compiler"
    source_root.mkdir(parents=True, exist_ok=True)
    pixel_root.mkdir(parents=True, exist_ok=True)
    image = source.convert("RGB")
    compiler = PixelTileCompiler()
    pixel_paths: list[Path] = []
    tile_metrics: list[dict[str, object]] = []
    for variant in range(variants):
        variant_image = _variant(image, variant, seed)
        source_path = save_png(variant_image, source_root / f"source_v{variant + 1:02d}.png")
        config = CompilerConfig(
            output_root=compiler_root / f"variant_{variant + 1:02d}",
            palette_budget=palette_budget,
            tile_mode="repeatable",
            semantic_provider="rule",
            seam_mode="off",
            repeat_opt_enabled=False,
            dither="off",
            background_mode="color",
            background_color="#000000",
            work_size=128,
            smoothing_enabled=False,
            seed=seed + variant,
            debug_enabled=False,
        )
        result = compiler.compile_image(variant_image, config, source_name=f"{material_id}:v{variant + 1:02d}")
        pixel_path = pixel_root / f"pixel_v{variant + 1:02d}.png"
        with Image.open(result.final_path) as final:
            save_png(final.convert("RGBA"), pixel_path)
            tile_metrics.append(compute_tile_metrics(final, target="64x64", semantic_role="surface"))
        pixel_paths.append(pixel_path)
    map_image = Image.new("RGBA", (map_columns * 64, map_rows * 64), (0, 0, 0, 255))
    for y in range(map_rows):
        for x in range(map_columns):
            index = (x * 7 + y * 11 + seed) % len(pixel_paths)
            with Image.open(pixel_paths[index]) as tile:
                map_image.paste(tile.convert("RGBA"), (x * 64, y * 64))
    map_path = save_png(map_image, root / "map_probe.png")
    map_metrics = compute_map_metrics(map_image, tile_size=64)
    average_tile_score = float(np.mean([float(item["single_tile_score"]) for item in tile_metrics])) if tile_metrics else 0.0
    palette_usage = max((int(item["palette_usage"]) for item in tile_metrics), default=0)
    compile_score = max(0.0, min(1.0, average_tile_score))
    map_score = max(0.0, min(1.0, float(map_metrics["map_readability_score"])))
    metrics: dict[str, float] = {
        "palette_usage": palette_usage,
        "compile_readability_score": round(float(np.mean([float(item["readability_score"]) for item in tile_metrics])), 6),
        "compile_semantic_fitness_score": round(float(np.mean([float(item["semantic_fitness_score"]) for item in tile_metrics])), 6),
        "edge_continuity_score": round(float(np.mean([float(item["edge_continuity_score"]) for item in tile_metrics])), 6),
        "compile_fitness_score": round(compile_score, 6),
        "map_edge_discontinuity_score": float(map_metrics["map_edge_discontinuity_score"]),
        "grid_visibility_score": float(map_metrics["grid_visibility_score"]),
        "periodicity_risk": float(map_metrics["map_periodicity_risk"]),
        "map_readability_score": float(map_metrics["map_readability_score"]),
        "map_fitness_score": round(map_score, 6),
        "variants": variants,
        "palette_budget": palette_budget,
    }
    metrics["grammar_score"] = grammar_score(tile_metrics[0], map_metrics) if tile_metrics else 0.0
    metrics_path = save_json(metrics, root / "metrics.json")
    return CompileProbeResult(root, tuple(pixel_paths), map_path, metrics_path, metrics)


def _variant(image: Image.Image, index: int, seed: int) -> Image.Image:
    """Make a stable candidate variant without changing its material identity."""
    width, height = image.size
    offset_x = (index * 17 + seed) % max(1, width)
    offset_y = (index * 23 + seed * 3) % max(1, height)
    shifted = ImageChops.offset(image, offset_x, offset_y)
    brightness = 0.96 + ((index * 7 + seed) % 9) / 100.0
    contrast = 0.97 + ((index * 5 + seed) % 7) / 100.0
    shifted = ImageEnhance.Brightness(shifted).enhance(brightness)
    shifted = ImageEnhance.Contrast(shifted).enhance(contrast)
    crop_size = min(width, height)
    left = (index * 11 + seed) % max(1, width - crop_size + 1)
    top = (index * 13 + seed) % max(1, height - crop_size + 1)
    return shifted.crop((left, top, left + crop_size, top + crop_size)).resize((256, 256), Image.Resampling.BICUBIC)
