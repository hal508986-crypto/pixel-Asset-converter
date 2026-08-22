"""Orchestrate the deterministic image-to-tile compilation stages."""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from pixel_tile_compiler.analysis.edges import detect_edges
from pixel_tile_compiler.analysis.regions import generate_region_map
from pixel_tile_compiler.analysis.structural import analyze_regions
from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.grammar.clusters import ClusterMetrics, cleanup_pixel_clusters
from pixel_tile_compiler.grammar.diagonals import cleanup_diagonals
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.ir.builder import build_tile_ir
from pixel_tile_compiler.ir.validator import validate_tile_ir
from pixel_tile_compiler.pixelizer.palette import palette_preview, quantize_palette
from pixel_tile_compiler.pixelizer.spatial import region_aware_pixelize
from pixel_tile_compiler.preprocess.background import apply_background
from pixel_tile_compiler.preprocess.normalize import normalize_image
from pixel_tile_compiler.preprocess.smoothing import smooth_image
from pixel_tile_compiler.semantic.base import SemanticProviderError
from pixel_tile_compiler.semantic.mcp_provider import McpSemanticProvider
from pixel_tile_compiler.semantic.rule_based import RuleBasedSemanticProvider
from pixel_tile_compiler.tile.repeat_preview import make_tiled_preview
from pixel_tile_compiler.tile.seam import SeamMetrics, measure_seams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompilationMetrics:
    actual_palette_count: int
    isolated_pixel_count: int
    micro_cluster_count: int
    horizontal_seam_score: float | None
    vertical_seam_score: float | None


@dataclass(frozen=True)
class CompilationResult:
    final_path: Path
    ir_path: Path
    debug_paths: dict[str, Path]
    metrics: CompilationMetrics
    metadata: dict[str, Any]


def generate_baseline_nearest(image: Image.Image, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Generate the nearest-neighbor baseline required for hypothesis comparison."""
    return image.convert("RGBA").resize(size, Image.Resampling.NEAREST)


def generate_baseline_bicubic_quantized(
    image: Image.Image,
    budget: int,
    size: tuple[int, int] = (64, 64),
) -> Image.Image:
    """Generate a bicubic-plus-quantization baseline."""
    resized = image.convert("RGBA").resize(size, Image.Resampling.BICUBIC)
    return quantize_palette(resized, budget=budget)


def _region_preview(region_map: np.ndarray) -> Image.Image:
    """Render deterministic pseudo-colors for the debug region artifact."""
    labels = sorted(int(value) for value in np.unique(region_map))
    colors = {
        label: ((label * 67 + 31) % 256, (label * 127 + 73) % 256, (label * 191 + 109) % 256, 255)
        for label in labels
    }
    output = np.zeros((*region_map.shape, 4), dtype=np.uint8)
    for label, color in colors.items():
        output[region_map == label] = color
    return Image.fromarray(output, mode="RGBA")


def _palette_count(image: Image.Image) -> int:
    return len({pixel[:3] for pixel in image.convert("RGBA").getdata() if pixel[3] != 0})


class PixelTileCompiler:
    """Compile one source image into a 64x64 SRPG map tile and artifacts."""

    def compile(self, source: Path, config: CompilerConfig) -> CompilationResult:
        """Run all MVP stages and export a complete artifact directory."""
        logging.getLogger().setLevel(logging.INFO)
        source = Path(source)
        output_dir = Path(config.output_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_paths: dict[str, Path] = {}

        loaded = load_image(source)
        normalized = normalize_image(loaded, work_size=config.work_size)
        normalized = apply_background(
            normalized,
            mode=config.background_mode,
            color=config.background_color,
            tolerance=config.background_tolerance,
        )
        smooth = smooth_image(normalized, enabled=config.smoothing_enabled)
        edges = detect_edges(smooth)
        region_map = generate_region_map(smooth, seed=config.seed)
        structural = analyze_regions(smooth, region_map, edges)

        provider_name = "rule"
        if config.semantic_provider == "mcp":
            provider = McpSemanticProvider(config.semantic_callable or (lambda **_kwargs: {}))
            try:
                semantic = provider.analyze(smooth, structural, config)
                provider_name = "mcp"
            except SemanticProviderError as exc:
                logger.warning("MCP semantic provider failed; using rule fallback: %s", exc)
                semantic = RuleBasedSemanticProvider().analyze(smooth, structural, config)
                provider_name = "rule-fallback"
        else:
            semantic = RuleBasedSemanticProvider().analyze(smooth, structural, config)

        ir = validate_tile_ir(build_tile_ir(structural, semantic, config))
        raw_pixelized = region_aware_pixelize(smooth, region_map, ir)
        quantized = quantize_palette(raw_pixelized, budget=config.palette_budget, seed=config.seed)
        cleaned, cluster_metrics = cleanup_pixel_clusters(quantized, min_cluster_size=2)
        cleaned = cleanup_diagonals(cleaned)
        final = cleaned
        seam: SeamMetrics = measure_seams(final, tile_mode=config.tile_mode)
        metrics = CompilationMetrics(
            actual_palette_count=_palette_count(final),
            isolated_pixel_count=cluster_metrics.isolated_pixel_count,
            micro_cluster_count=cluster_metrics.micro_cluster_count,
            horizontal_seam_score=seam.horizontal_seam_score,
            vertical_seam_score=seam.vertical_seam_score,
        )

        if config.debug_enabled:
            debug_images = {
                "01_normalized": normalized,
                "02_smooth": smooth,
                "03_edges": edges,
                "04_regions": _region_preview(region_map),
                "05_palette_preview": palette_preview(quantized),
                "06_raw_pixelized": raw_pixelized,
                "07_cluster_cleaned": cleaned,
                "08_tile_preview": make_tiled_preview(final),
            }
            for name, image in debug_images.items():
                debug_paths[name] = save_png(image, output_dir / "debug" / f"{name}.png")

        final_path = save_png(final, output_dir / "final.png")
        ir_path = save_json(ir.model_dump(mode="json"), output_dir / "ir.json")
        save_png(generate_baseline_nearest(normalized), output_dir / "baseline_nearest.png")
        save_png(
            generate_baseline_bicubic_quantized(normalized, config.palette_budget),
            output_dir / "baseline_bicubic_quantized.png",
        )
        metadata: dict[str, Any] = {
            "source": str(source),
            "config": config.as_dict(),
            "semantic_provider": provider_name,
            "metrics": asdict(metrics),
            "pipeline": [
                "load",
                "normalize",
                "background",
                "smoothing",
                "edges",
                "regions",
                "structural",
                "semantic",
                "ir",
                "pixelize",
                "palette",
                "clusters",
                "diagonals",
                "seam",
                "export",
            ],
        }
        save_json(metadata, output_dir / "metadata.json")
        return CompilationResult(final_path, ir_path, debug_paths, metrics, metadata)
