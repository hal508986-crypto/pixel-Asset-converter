"""Orchestrate the deterministic image-to-tile compilation stages."""

import hashlib
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
from pixel_tile_compiler.ir.schema import MapContext
from pixel_tile_compiler.ir.validator import validate_tile_ir
from pixel_tile_compiler.pixelizer.character import add_outline, fit_character_to_canvas, nearest_pixelize
from pixel_tile_compiler.pixelizer.character_detail import simplify_character_detail
from pixel_tile_compiler.pixelizer.color_conditioning import apply_color_conditioning
from pixel_tile_compiler.pixelizer.palette import palette_preview, quantize_palette
from pixel_tile_compiler.pixelizer.spatial import region_aware_pixelize
from pixel_tile_compiler.preprocess.background import apply_background
from pixel_tile_compiler.preprocess.normalize import normalize_image
from pixel_tile_compiler.preprocess.smoothing import smooth_image
from pixel_tile_compiler.semantic.base import SemanticProviderError
from pixel_tile_compiler.semantic.mcp_provider import McpSemanticProvider
from pixel_tile_compiler.semantic.rule_based import RuleBasedSemanticProvider
from pixel_tile_compiler.tile.repeat_preview import make_tiled_preview
from pixel_tile_compiler.tile.repeatability import measure_repeatability, optimize_repeatability
from pixel_tile_compiler.tile.seam import SeamMetrics, measure_seams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompilationMetrics:
    actual_palette_count: int
    isolated_pixel_count: int
    micro_cluster_count: int
    horizontal_seam_score: float | None
    vertical_seam_score: float | None
    center_dominance_score: float | None
    periodicity_risk_score: float | None
    edge_continuity_score: float | None
    corner_seam_score: float | None
    repeatability_optimization_applied: bool


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


def _palette_colors(image: Image.Image) -> list[list[int]]:
    colors = {
        tuple(int(channel) for channel in pixel[:3])
        for pixel in image.convert("RGBA").getdata()
        if pixel[3] != 0
    }
    return [list(color) for color in sorted(colors)]


def _source_sha256(source_name: Path | str) -> str | None:
    source_path = Path(source_name)
    if not source_path.is_file():
        return None
    try:
        return hashlib.sha256(source_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _outline_rgba(color: str) -> tuple[int, int, int, int] | None:
    if color == "off":
        return None
    if color == "black":
        return (0, 0, 0, 255)
    if color == "white":
        return (255, 255, 255, 255)
    raise ValueError("outline_color must be off, black, or white")


def _pixelize_source(
    background_resolved: Image.Image,
    smooth: Image.Image,
    region_map: np.ndarray,
    ir,
    config: CompilerConfig,
) -> Image.Image:
    """Select the spatial pixelizer without changing the analysis contract."""
    if config.pixelization_mode == "nearest":
        if config.tile_mode == "object":
            if config.character_input_mode == "pre_aligned":
                return nearest_pixelize(background_resolved, config.canvas.size)
            fitted = fit_character_to_canvas(
                background_resolved,
                canvas_size=config.canvas.size,
                frame_size=(config.character_layout.frame_width, config.character_layout.frame_height),
                bottom_margin=config.character_layout.bottom_margin,
                outline_width=1 if config.outline_color != "off" else 0,
            )
            return nearest_pixelize(fitted, config.canvas.size)
        return nearest_pixelize(background_resolved, config.canvas.size)
    return region_aware_pixelize(smooth, region_map, ir)


def _clean_pixelized(
    image: Image.Image,
    config: CompilerConfig,
) -> tuple[Image.Image, ClusterMetrics]:
    """Keep artist-defined character clusters while retaining terrain cleanup."""
    if config.pixelization_mode == "nearest":
        return image.convert("RGBA"), ClusterMetrics()
    cleaned, cluster_metrics = cleanup_pixel_clusters(image, min_cluster_size=2)
    return cleanup_diagonals(cleaned), cluster_metrics


class PixelTileCompiler:
    """Compile one source image into a 64x64 SRPG map tile and artifacts."""

    def compile(self, source: Path, config: CompilerConfig) -> CompilationResult:
        """Run all MVP stages and export a complete artifact directory."""
        source = Path(source)
        return self.compile_image(load_image(source), config, source_name=source)

    def compile_image(
        self,
        image: Image.Image,
        config: CompilerConfig,
        source_name: Path | str = "<memory>",
        map_context: MapContext | None = None,
    ) -> CompilationResult:
        """Compile an in-memory image, optionally retaining MAP context in its IR."""
        logging.getLogger().setLevel(logging.INFO)
        output_dir = Path(config.output_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_paths: dict[str, Path] = {}

        loaded = image.convert("RGBA").copy()
        source_hash = _source_sha256(source_name)
        background_resolved = apply_background(
            loaded,
            mode=config.background_mode,
            color=config.background_color,
            tolerance=config.background_tolerance,
        )
        normalized = normalize_image(background_resolved, work_size=config.work_size)
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

        ir = build_tile_ir(structural, semantic, config)
        if map_context is not None:
            ir = ir.model_copy(update={"map_context": map_context})
        ir = validate_tile_ir(ir)
        raw_pixelized = _pixelize_source(background_resolved, smooth, region_map, ir, config)
        color_conditioned = apply_color_conditioning(raw_pixelized, config.color_conditioning)
        outline_color = _outline_rgba(config.outline_color)
        quantize_budget = config.palette_budget
        quantize_palette_colors = config.palette_colors
        if outline_color is not None:
            outline_rgb = outline_color[:3]
            if quantize_palette_colors is None:
                quantize_budget = max(1, config.palette_budget - 1)
            elif outline_rgb not in quantize_palette_colors:
                if len(quantize_palette_colors) < config.palette_budget:
                    quantize_palette_colors = (*quantize_palette_colors, outline_rgb)
                else:
                    quantize_palette_colors = (*quantize_palette_colors[:-1], outline_rgb)
        if config.quantize_enabled:
            quantized = quantize_palette(
                color_conditioned,
                budget=quantize_budget,
                seed=config.seed,
                palette_colors=quantize_palette_colors,
            )
        else:
            quantized = raw_pixelized.convert("RGBA")
        character_detail_applied = config.tile_mode == "object"
        character_detail = (
            simplify_character_detail(quantized, config.character_detail_level, canvas_size=config.canvas.size)
            if character_detail_applied
            else quantized
        )
        cleaned, cluster_metrics = _clean_pixelized(character_detail, config)
        repeatability_before = measure_repeatability(cleaned, tile_mode=config.tile_mode, edge_band=config.repeat_opt_edge_band)
        repeatability_applied = bool(config.tile_mode == "repeatable" and config.repeat_opt_enabled)
        if repeatability_applied:
            optimized = optimize_repeatability(
                cleaned,
                tile_mode=config.tile_mode,
                strength=config.repeat_opt_strength,
                edge_band=config.repeat_opt_edge_band,
                center_suppression_strength=config.center_suppression_strength,
            )
            optimized, optimized_cluster_metrics = cleanup_pixel_clusters(optimized, min_cluster_size=2)
            final = cleanup_diagonals(optimized)
            cluster_metrics = optimized_cluster_metrics
        else:
            final = cleaned
        if outline_color is not None:
            final = add_outline(final, outline_color)
        repeatability_after = measure_repeatability(final, tile_mode=config.tile_mode, edge_band=config.repeat_opt_edge_band)
        seam: SeamMetrics = measure_seams(final, tile_mode=config.tile_mode)
        metrics = CompilationMetrics(
            actual_palette_count=_palette_count(final),
            isolated_pixel_count=cluster_metrics.isolated_pixel_count,
            micro_cluster_count=cluster_metrics.micro_cluster_count,
            horizontal_seam_score=seam.horizontal_seam_score,
            vertical_seam_score=seam.vertical_seam_score,
            center_dominance_score=repeatability_after.center_dominance_score,
            periodicity_risk_score=repeatability_after.periodicity_risk_score,
            edge_continuity_score=repeatability_after.edge_continuity_score,
            corner_seam_score=repeatability_after.corner_seam_score,
            repeatability_optimization_applied=repeatability_applied,
        )

        if config.debug_enabled:
            debug_images = {
                "01_normalized": normalized,
                "02_smooth": smooth,
                "03_edges": edges,
                "04_regions": _region_preview(region_map),
                "05_palette_preview": palette_preview(quantized),
                "06_raw_pixelized": raw_pixelized,
                "06_color_conditioned": color_conditioned,
                **({"07_character_detail": character_detail} if character_detail_applied else {}),
                "07_cluster_cleaned": cleaned,
                "08_repeat_optimized": final,
                "08_tile_preview": make_tiled_preview(final),
                "09_tile_preview": make_tiled_preview(final),
            }
            if outline_color is not None:
                debug_images["10_outline"] = final
            for name, image in debug_images.items():
                debug_paths[name] = save_png(image, output_dir / "debug" / f"{name}.png")

        final_path = save_png(final, output_dir / "final.png")
        ir_path = save_json(ir.model_dump(mode="json"), output_dir / "ir.json")
        save_png(generate_baseline_nearest(normalized, size=config.canvas.size), output_dir / "baseline_nearest.png")
        save_png(
            generate_baseline_bicubic_quantized(normalized, config.palette_budget, size=config.canvas.size),
            output_dir / "baseline_bicubic_quantized.png",
        )
        metadata: dict[str, Any] = {
            "source": str(source_name),
            "source_image": {
                "path": str(source_name),
                "sha256": source_hash,
                "dimensions": [int(loaded.width), int(loaded.height)],
            },
            "config": config.as_dict(),
            "output_canvas": {"width": config.width, "height": config.height},
            "analysis_canvas": {"width": config.work_size, "height": config.work_size},
            "character_layout": {
                "frame_width": config.character_layout.frame_width,
                "frame_height": config.character_layout.frame_height,
                "bottom_margin": config.character_layout.bottom_margin,
                "reference_canvas": [64, 64],
            },
            "native_resolution": config.tile_mode == "object",
            "semantic_provider": provider_name,
            "metrics": asdict(metrics),
                "palette_budget_scope": "visible_rgb",
                "character_detail": {
                    "level": config.character_detail_level,
                    "applied": character_detail_applied,
                    "alpha_policy": "preserve_exactly",
                },
            "repeatability_before": asdict(repeatability_before),
            "repeatability_after": asdict(repeatability_after),
            "repeatability_optimization": {
                "enabled": config.repeat_opt_enabled,
                "applied": repeatability_applied,
            },
            "transformation": {
                "pixelization_mode": config.pixelization_mode,
                "palette_budget": config.palette_budget,
                "actual_palette_count": metrics.actual_palette_count,
                "palette_colors": _palette_colors(final),
                "repeat_opt_enabled": config.repeat_opt_enabled,
                "repeat_opt_applied": repeatability_applied,
            },
            "pipeline": [
                "load",
                "background",
                "normalize",
                "smoothing",
                "edges",
                "regions",
                "structural",
                "semantic",
                "ir",
                "pixelize",
                "color_conditioning",
                "palette",
                *(["character_detail"] if character_detail_applied else []),
                "clusters",
                "diagonals",
                "outline",
                "repeatability",
                "seam",
                "export",
            ],
        }
        save_json(metadata, output_dir / "metadata.json")
        return CompilationResult(final_path, ir_path, debug_paths, metrics, metadata)
