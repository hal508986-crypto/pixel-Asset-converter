"""Generated-sheet structural validation, splitting, and normalization."""

from .normalizer import normalize_sheet, normalize_tile
from .alpha_projection import SpriteSheetCell, SpriteSheetSplitResult, alpha_occupancy_mask, split_sprite_sheet
from .splitter import GridSplitResult, GridSplitter, SplitTile
from .validator import SheetValidationReport, validate_sheet_structure

__all__ = [
    "GridSplitResult",
    "GridSplitter",
    "SheetValidationReport",
    "SplitTile",
    "SpriteSheetCell",
    "SpriteSheetSplitResult",
    "alpha_occupancy_mask",
    "normalize_sheet",
    "normalize_tile",
    "split_sprite_sheet",
    "validate_sheet_structure",
]
