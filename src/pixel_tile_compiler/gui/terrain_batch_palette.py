"""Backward-compatible import surface for terrain palette code."""

from pixel_tile_compiler.palette_contract import (
    extract_final_palette,
    fixed_palette_config,
    metadata_palette_matches_final,
    normalize_palette,
    palette_id,
    validate_reference_palette,
)

__all__ = [
    "extract_final_palette",
    "fixed_palette_config",
    "metadata_palette_matches_final",
    "normalize_palette",
    "palette_id",
    "validate_reference_palette",
]
