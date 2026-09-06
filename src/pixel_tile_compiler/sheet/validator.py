"""Soft-failure structural validation for generated sheets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image


ValidationStatus = Literal["accepted", "warning", "rejected"]


@dataclass(frozen=True)
class SheetValidationReport:
    status: ValidationStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    dimensions: tuple[int, int]
    grid: tuple[int, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "dimensions": list(self.dimensions),
            "grid": {"columns": self.grid[0], "rows": self.grid[1]},
        }


def validate_sheet_structure(
    image: Image.Image,
    columns: int,
    rows: int,
    minimum_dimension: int = 64,
) -> SheetValidationReport:
    width, height = image.size
    issues: list[str] = []
    warnings: list[str] = []
    if columns < 1 or rows < 1:
        issues.append("grid dimensions must be positive")
    if width < minimum_dimension or height < minimum_dimension:
        issues.append("sheet dimensions are below the minimum")
    if width != height:
        issues.append("sheet must be square")
    if width % columns or height % rows:
        warnings.append("sheet dimensions are not divisible by the logical grid; deterministic crop is required")
    if width // max(1, columns) < 8 or height // max(1, rows) < 8:
        warnings.append("logical cells are unusually small")
    status: ValidationStatus = "rejected" if issues else "warning" if warnings else "accepted"
    return SheetValidationReport(status, tuple(issues), tuple(warnings), (width, height), (columns, rows))
