"""Global MAP analysis retained as context for local tile compilation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from collections import Counter

import numpy as np
from PIL import Image

from pixel_tile_compiler.analysis.edges import detect_edges
from pixel_tile_compiler.analysis.regions import generate_region_map
from pixel_tile_compiler.analysis.structural import analyze_regions
from pixel_tile_compiler.config import MapCompilerConfig
from pixel_tile_compiler.pixelizer.palette import extract_palette, quantize_palette
from pixel_tile_compiler.preprocess.smoothing import smooth_image
from pixel_tile_compiler.semantic.rule_based import RuleBasedSemanticProvider


@dataclass(frozen=True)
class GlobalMapAnalysis:
    """Global statistics and maps used by every tile in a MAP."""

    normalized_image: Image.Image
    edges: Image.Image
    region_map: np.ndarray
    semantic_map: np.ndarray
    palette: tuple[tuple[int, int, int], ...]
    dominant_colors: tuple[tuple[int, int, int], ...]
    brightness_mean: float
    brightness_std: float
    brightness_distribution: tuple[int, ...]
    texture_density: float
    edge_density: float
    semantic_distribution: dict[str, int]

    @property
    def palette_id(self) -> str:
        """Return a stable identifier for the shared palette."""
        payload = json.dumps(self.palette, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, object]:
        """Return JSON-safe global analysis without raster arrays."""
        return {
            "normalized_size": list(self.normalized_image.size),
            "palette": [list(color) for color in self.palette],
            "dominant_colors": [list(color) for color in self.dominant_colors],
            "palette_id": self.palette_id,
            "brightness_mean": self.brightness_mean,
            "brightness_std": self.brightness_std,
            "brightness_distribution": list(self.brightness_distribution),
            "texture_density": self.texture_density,
            "edge_density": self.edge_density,
            "region_count": int(len(np.unique(self.region_map))),
            "semantic_distribution": self.semantic_distribution,
        }


def normalize_map_image(image: Image.Image, max_edge: int = 512) -> Image.Image:
    """Resize a MAP while preserving its aspect ratio for global analysis."""
    source = image.convert("RGBA")
    width, height = source.size
    scale = min(1.0, max_edge / max(width, height))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    if target == source.size:
        return source.copy()
    return source.resize(target, Image.Resampling.LANCZOS)


def _texture_density(image: Image.Image) -> float:
    luminance = np.asarray(image.convert("RGB"), dtype=np.float32).mean(axis=2)
    horizontal = np.abs(np.diff(luminance, axis=1)).mean() if luminance.shape[1] > 1 else 0.0
    vertical = np.abs(np.diff(luminance, axis=0)).mean() if luminance.shape[0] > 1 else 0.0
    return max(0.0, min(1.0, float((horizontal + vertical) / 64.0)))


def classify_map_material(mean_rgb: tuple[int, int, int]) -> str:
    """Use a MAP-specific broad classifier for olive grass and warm road colors."""
    red, green, blue = mean_rgb
    if green >= red * 1.01 and green >= blue * 1.25:
        return "grass"
    if red >= green * 1.12 and red >= blue * 1.35:
        return "road"
    if max(mean_rgb) < 65:
        return "shadow"
    if max(mean_rgb) - min(mean_rgb) < 22:
        return "stone"
    return "soil"


def _dominant_colors(image: Image.Image, budget: int) -> tuple[tuple[int, int, int], ...]:
    quantized = quantize_palette(image, budget=budget)
    counts = Counter(pixel[:3] for pixel in quantized.convert("RGBA").getdata() if pixel[3] != 0)
    return tuple(color for color, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def analyze_global_map(image: Image.Image, config: MapCompilerConfig) -> GlobalMapAnalysis:
    """Run the global analysis once before any grid tile is compiled."""
    normalized = normalize_map_image(image)
    smooth = smooth_image(normalized, enabled=config.smoothing_enabled)
    edges = detect_edges(smooth)
    region_map = generate_region_map(smooth, seed=config.seed)
    structural = analyze_regions(smooth, region_map, edges)
    semantic = RuleBasedSemanticProvider().analyze(smooth, structural, _single_tile_config(config))
    semantic_map = np.full(region_map.shape, "unknown", dtype="<U16")
    structural_by_id = {region.id: region for region in structural.regions}
    for region in semantic.regions:
        source_region = structural_by_id.get(region.id)
        mean_rgb = source_region.mean_rgb if source_region is not None else (0, 0, 0)
        semantic_map[region_map == region.id] = classify_map_material(mean_rgb)

    rgb = np.asarray(smooth.convert("RGB"), dtype=np.float32)
    brightness = rgb.mean(axis=2) / 255.0
    brightness_distribution = tuple(int(value) for value in np.histogram(brightness, bins=10, range=(0.0, 1.0))[0])
    semantic_distribution: dict[str, int] = {}
    for label in semantic_map.ravel():
        semantic_distribution[str(label)] = semantic_distribution.get(str(label), 0) + 1
    return GlobalMapAnalysis(
        normalized_image=smooth,
        edges=edges,
        region_map=region_map,
        semantic_map=semantic_map,
        palette=extract_palette(smooth, budget=config.global_palette_budget),
        dominant_colors=_dominant_colors(smooth, budget=config.global_palette_budget),
        brightness_mean=float(brightness.mean()),
        brightness_std=float(brightness.std()),
        brightness_distribution=brightness_distribution,
        texture_density=_texture_density(smooth),
        edge_density=float((np.asarray(edges) > 0).mean()),
        semantic_distribution=semantic_distribution,
    )


def render_region_map(region_map: np.ndarray) -> Image.Image:
    """Render deterministic pseudo-colors for the global region debug artifact."""
    labels = sorted(int(value) for value in np.unique(region_map))
    output = np.zeros((*region_map.shape, 4), dtype=np.uint8)
    for label in labels:
        output[region_map == label] = (
            (label * 67 + 31) % 256,
            (label * 127 + 73) % 256,
            (label * 191 + 109) % 256,
            255,
        )
    return Image.fromarray(output, mode="RGBA")


def _single_tile_config(config: MapCompilerConfig):
    """Build the minimum semantic-provider config without coupling it to map output."""
    from pixel_tile_compiler.config import CompilerConfig

    return CompilerConfig(
        palette_budget=config.global_palette_budget,
        tile_mode=config.tile_mode,
        semantic_provider=config.semantic_provider,
        seed=config.seed,
        debug_enabled=False,
        smoothing_enabled=config.smoothing_enabled,
    )
