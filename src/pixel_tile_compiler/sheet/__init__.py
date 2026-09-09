"""Generated-sheet structural validation, splitting, and normalization."""

from .normalizer import normalize_sheet, normalize_tile
from .alpha_projection import SpriteSheetCell, SpriteSheetSplitResult, alpha_occupancy_mask, split_sprite_sheet
from .component_split import (
    ComponentCell,
    ComponentInfo,
    ComponentRowMethod,
    ComponentSplitResult,
    analyze_component_split,
    decode_mask_rle,
    encode_mask_rle,
    render_component_split_overlay,
    restore_owner_labels_from_cells,
)
from .splitter import GridSplitResult, GridSplitter, SplitTile
from .validator import SheetValidationReport, validate_sheet_structure

__all__ = [
    "GridSplitResult",
    "GridSplitter",
    "SheetValidationReport",
    "SplitTile",
    "SpriteSheetCell",
    "SpriteSheetSplitResult",
    "ComponentCell",
    "ComponentInfo",
    "ComponentRowMethod",
    "ComponentSplitResult",
    "analyze_component_split",
    "alpha_occupancy_mask",
    "decode_mask_rle",
    "encode_mask_rle",
    "render_component_split_overlay",
    "restore_owner_labels_from_cells",
    "normalize_sheet",
    "normalize_tile",
    "split_sprite_sheet",
    "validate_sheet_structure",
]
