"""Deterministic terrain interpretation used by default and as fallback."""

from PIL import Image

from pixel_tile_compiler.analysis.structural import StructuralAnalysis, StructuralRegion
from pixel_tile_compiler.config import CompilerConfig

from .base import SemanticAnalysis, SemanticRegion


def classify_color(mean_rgb: tuple[int, int, int]) -> str:
    """Classify broad SRPG map material from its representative color."""
    red, green, blue = mean_rgb
    if blue > red * 1.15 and blue > green * 1.05:
        return "water"
    if green > red * 1.12 and green > blue * 1.05:
        return "grass"
    if red > blue * 1.35 and green > blue * 1.15:
        return "soil"
    if max(mean_rgb) < 70:
        return "shadow"
    if max(mean_rgb) - min(mean_rgb) < 24:
        return "stone"
    return "ground"


def _importance(region: StructuralRegion, total_area: int) -> float:
    area_weight = region.area / max(total_area, 1)
    return max(0.05, min(1.0, area_weight * 1.5 + region.edge_density * 0.7 + region.saliency * 0.3))


class RuleBasedSemanticProvider:
    """No-network semantic provider that always returns a valid analysis."""

    def analyze(
        self,
        image: Image.Image,
        structural_data: StructuralAnalysis,
        config: CompilerConfig,
    ) -> SemanticAnalysis:
        del image
        total_area = structural_data.region_map.size
        semantic_regions = tuple(
            SemanticRegion(
                id=region.id,
                semantic=classify_color(region.mean_rgb),
                importance=_importance(region, total_area),
                preserve_edges=region.edge_density >= 0.08 or not region.touches_border,
                preserve_texture=region.contrast >= 0.08,
                texture_priority=max(0.05, min(1.0, region.contrast)),
                merge_candidates=tuple(region.neighbor_regions),
                discard_micro_detail=True,
            )
            for region in structural_data.regions
        )
        if semantic_regions:
            tile_type = max(semantic_regions, key=lambda value: value.importance).semantic
        else:
            tile_type = "unknown"
        texture = sum(value.texture_priority for value in semantic_regions) / max(len(semantic_regions), 1)
        edge = sum(value.importance for value in semantic_regions if value.preserve_edges) / max(len(semantic_regions), 1)
        return SemanticAnalysis(
            tile_type=tile_type,
            tile_mode=config.tile_mode,
            texture_density=max(0.0, min(1.0, texture)),
            contrast_strength=0.5,
            edge_strength=max(0.0, min(1.0, edge)),
            regions=semantic_regions,
            provider_name="rule",
        )
