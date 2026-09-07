"""Build Tile IR from structural and semantic analyses."""

from pixel_tile_compiler.analysis.structural import StructuralAnalysis
from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.semantic.base import SemanticAnalysis

from .schema import GlobalStyle, RegionIR, TileIR


def build_tile_ir(
    structural: StructuralAnalysis,
    semantic: SemanticAnalysis,
    config: CompilerConfig,
) -> TileIR:
    """Create a validated IR while retaining region geometry in the side-channel map."""
    total_area = max(structural.region_map.size, 1)
    structural_by_id = {region.id: region for region in structural.regions}
    regions = []
    for semantic_region in semantic.regions:
        source = structural_by_id.get(semantic_region.id)
        if source is None:
            continue
        regions.append(
            RegionIR(
                id=semantic_region.id,
                semantic=semantic_region.semantic,
                importance=semantic_region.importance,
                area_ratio=source.area / total_area,
                dominant_color=source.mean_rgb,
                preserve_edges=semantic_region.preserve_edges,
                preserve_texture=semantic_region.preserve_texture,
                texture_priority=semantic_region.texture_priority,
                merge_candidates=list(semantic_region.merge_candidates),
                discard_micro_detail=semantic_region.discard_micro_detail,
            )
        )
    return TileIR(
        width=config.width,
        height=config.height,
        tile_type=semantic.tile_type,
        tile_mode=config.tile_mode,
        palette_budget=config.palette_budget,
        global_style=GlobalStyle(
            texture_density=semantic.texture_density,
            contrast_strength=semantic.contrast_strength,
            edge_strength=semantic.edge_strength,
            dithering=config.dither,
            outline_mode="selective" if semantic.edge_strength > 0.1 else "off",
            seam_mode=config.seam_mode,
        ),
        regions=regions,
    )
