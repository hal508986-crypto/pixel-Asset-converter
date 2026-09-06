"""Equal-grid mechanical splitter."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .normalizer import CropPolicy, normalize_sheet, normalize_tile
from .validator import validate_sheet_structure


@dataclass(frozen=True)
class SplitTile:
    row: int
    column: int
    image: Image.Image
    source_box: tuple[int, int, int, int]


@dataclass(frozen=True)
class GridSplitResult:
    normalized_sheet: Image.Image
    crop_box: tuple[int, int, int, int]
    normalization_method: str
    tiles: tuple[SplitTile, ...]
    validation: object


class GridSplitter:
    def split(
        self,
        image: Image.Image,
        columns: int,
        rows: int,
        crop_policy: CropPolicy = "center",
        tile_size: int = 64,
        normalize_mode: str = "nearest",
    ) -> GridSplitResult:
        report = validate_sheet_structure(image, columns, rows)
        if report.status == "rejected":
            raise ValueError("sheet rejected: " + "; ".join(report.issues))
        normalized = normalize_sheet(image, columns, rows, crop_policy)
        cell_width = normalized.image.width // columns
        cell_height = normalized.image.height // rows
        tiles: list[SplitTile] = []
        for row in range(rows):
            for column in range(columns):
                box = (
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
                tiles.append(
                    SplitTile(
                        row=row,
                        column=column,
                        image=normalize_tile(normalized.image.crop(box), tile_size, normalize_mode),
                        source_box=box,
                    )
                )
        return GridSplitResult(normalized.image, normalized.crop_box, normalized.method, tuple(tiles), report)
