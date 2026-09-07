"""Alpha-projection based sprite-sheet grid detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .normalizer import normalize_sheet


SplitMode = Literal["fixed_grid", "alpha_gap_auto", "hybrid"]
Band = tuple[int, int]
SourceBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class SpriteSheetCell:
    """One detected or fixed logical cell in source-image coordinates."""

    index: int
    row: int
    column: int
    source_box: SourceBox
    visible_pixel_count: int
    occupied_ratio: float
    valid: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "row": self.row,
            "column": self.column,
            "source_box": list(self.source_box),
            "width": self.source_box[2] - self.source_box[0],
            "height": self.source_box[3] - self.source_box[1],
            "visible_pixel_count": self.visible_pixel_count,
            "occupied_ratio": self.occupied_ratio,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class SpriteSheetSplitResult:
    """Source cells plus explainable detection diagnostics."""

    requested_mode: SplitMode
    detected_mode: Literal["fixed_grid", "alpha_projection"]
    rows: int
    columns: int
    frames: tuple[Image.Image, ...]
    cells: tuple[SpriteSheetCell, ...]
    x_bands: tuple[Band, ...]
    y_bands: tuple[Band, ...]
    source_size: tuple[int, int]
    normalized_size: tuple[int, int]
    crop_box: SourceBox
    confidence: float
    fallback_used: bool
    fallback_reason: str | None
    issues: tuple[str, ...]
    alpha_threshold: int
    empty_column_threshold: int
    empty_row_threshold: int
    min_gutter_width_px: int
    detection_overlay: Image.Image

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def report_as_dict(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "detected_mode": self.detected_mode,
            "rows": self.rows,
            "columns": self.columns,
            "frame_count": self.frame_count,
            "source_size": list(self.source_size),
            "normalized_size": list(self.normalized_size),
            "crop_box": list(self.crop_box),
            "confidence": self.confidence,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "issues": list(self.issues),
            "alpha_threshold": self.alpha_threshold,
            "empty_column_threshold": self.empty_column_threshold,
            "empty_row_threshold": self.empty_row_threshold,
            "min_gutter_width_px": self.min_gutter_width_px,
            "x_bands": [list(band) for band in self.x_bands],
            "y_bands": [list(band) for band in self.y_bands],
            "cells": [cell.as_dict() for cell in self.cells],
        }


def alpha_occupancy_mask(
    image: Image.Image,
    *,
    alpha_threshold: int = 16,
    remove_small_components: bool = True,
    min_component_area_px: int = 3,
) -> tuple[np.ndarray, int]:
    """Build a binary alpha mask and return the number of removed pixels."""
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")
    if min_component_area_px < 1:
        raise ValueError("min_component_area_px must be positive")
    alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
    mask = alpha >= alpha_threshold
    if not remove_small_components:
        return mask, 0
    return _remove_small_components(mask, min_component_area_px)


def _remove_small_components(mask: np.ndarray, min_area: int) -> tuple[np.ndarray, int]:
    if min_area <= 1 or not mask.any():
        return mask.copy(), 0

    height, width = mask.shape
    kept = np.zeros_like(mask, dtype=bool)
    visited = np.zeros_like(mask, dtype=bool)
    removed_pixels = 0
    for start_y, start_x in np.argwhere(mask):
        y0, x0 = int(start_y), int(start_x)
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        component: list[tuple[int, int]] = []
        visited[y0, x0] = True
        while stack:
            y, x = stack.pop()
            component.append((y, x))
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    if not mask[next_y, next_x] or visited[next_y, next_x]:
                        continue
                    visited[next_y, next_x] = True
                    stack.append((next_y, next_x))
        if len(component) >= min_area:
            for y, x in component:
                kept[y, x] = True
        else:
            removed_pixels += len(component)
    return kept, removed_pixels


def _raw_bands(projection: np.ndarray, empty_threshold: int) -> list[Band]:
    occupied = projection > empty_threshold
    bands: list[Band] = []
    start: int | None = None
    for index, is_occupied in enumerate(occupied.tolist() + [False]):
        if is_occupied and start is None:
            start = index
        elif not is_occupied and start is not None:
            bands.append((start, index))
            start = None
    return bands


def _merge_short_gaps(bands: list[Band], min_gutter_width_px: int) -> list[Band]:
    if len(bands) < 2:
        return bands
    merged: list[Band] = [bands[0]]
    for start, end in bands[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end < min_gutter_width_px:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return merged


def _projection_bands(
    mask: np.ndarray,
    *,
    empty_threshold: int,
    min_gutter_width_px: int,
    axis: int,
) -> tuple[Band, ...]:
    projection = mask.sum(axis=axis)
    return tuple(_merge_short_gaps(_raw_bands(projection, empty_threshold), min_gutter_width_px))


def _ratio(values: list[int]) -> float:
    if not values:
        return float("inf")
    smallest = min(values)
    return float("inf") if smallest <= 0 else max(values) / smallest


def _cell_boxes(
    x_bands: tuple[Band, ...],
    y_bands: tuple[Band, ...],
) -> tuple[SourceBox, ...]:
    return tuple(
        (x_start, y_start, x_end, y_end)
        for y_start, y_end in y_bands
        for x_start, x_end in x_bands
    )


def _make_cells(
    source: Image.Image,
    boxes: tuple[SourceBox, ...],
    *,
    columns: int,
    alpha_threshold: int,
    remove_small_components: bool,
    min_component_area_px: int,
    require_nonempty_each_cell: bool,
) -> tuple[SpriteSheetCell, ...]:
    cells: list[SpriteSheetCell] = []
    for index, box in enumerate(boxes):
        row, column = divmod(index, columns)
        cell = source.crop(box)
        mask, _ = alpha_occupancy_mask(
            cell,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
        )
        visible_count = int(np.count_nonzero(mask))
        area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
        cells.append(
            SpriteSheetCell(
                index=index,
                row=row,
                column=column,
                source_box=box,
                visible_pixel_count=visible_count,
                occupied_ratio=visible_count / area,
                valid=visible_count > 0 or not require_nonempty_each_cell,
            )
        )
    return tuple(cells)


def _fixed_grid(
    source: Image.Image,
    *,
    columns: int,
    rows: int,
    alpha_threshold: int,
    remove_small_components: bool,
    min_component_area_px: int,
    require_nonempty_each_cell: bool,
    remainder_policy: Literal["center_crop", "error"],
    requested_mode: SplitMode,
    fallback_used: bool,
    fallback_reason: str | None,
) -> SpriteSheetSplitResult:
    if columns < 1 or rows < 1:
        raise ValueError("grid columns and rows must be positive")
    if remainder_policy not in {"center_crop", "error"}:
        raise ValueError("remainder_policy must be center_crop or error")
    if remainder_policy == "error" and (source.width % columns or source.height % rows):
        raise ValueError("sheet dimensions must be divisible by the fixed grid")
    normalized = normalize_sheet(source, columns, rows, crop_policy="center")
    cell_width = normalized.image.width // columns
    cell_height = normalized.image.height // rows
    frames: list[Image.Image] = []
    boxes: list[SourceBox] = []
    for row in range(rows):
        for column in range(columns):
            local_box = (
                column * cell_width,
                row * cell_height,
                (column + 1) * cell_width,
                (row + 1) * cell_height,
            )
            frames.append(normalized.image.crop(local_box))
            boxes.append(
                (
                    normalized.crop_box[0] + local_box[0],
                    normalized.crop_box[1] + local_box[1],
                    normalized.crop_box[0] + local_box[2],
                    normalized.crop_box[1] + local_box[3],
                )
            )
    x_bands = tuple(
        (normalized.crop_box[0] + column * cell_width, normalized.crop_box[0] + (column + 1) * cell_width)
        for column in range(columns)
    )
    y_bands = tuple(
        (normalized.crop_box[1] + row * cell_height, normalized.crop_box[1] + (row + 1) * cell_height)
        for row in range(rows)
    )
    cells = _make_cells(
        source,
        tuple(boxes),
        columns=columns,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
        require_nonempty_each_cell=require_nonempty_each_cell,
    )
    issues = tuple(
        f"セル{cell.index + 1}が空です"
        for cell in cells
        if not cell.valid
    )
    return SpriteSheetSplitResult(
        requested_mode=requested_mode,
        detected_mode="fixed_grid",
        rows=rows,
        columns=columns,
        frames=tuple(frames),
        cells=cells,
        x_bands=x_bands,
        y_bands=y_bands,
        source_size=source.size,
        normalized_size=normalized.image.size,
        crop_box=normalized.crop_box,
        confidence=1.0,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        issues=issues,
        alpha_threshold=alpha_threshold,
        empty_column_threshold=0,
        empty_row_threshold=0,
        min_gutter_width_px=0,
        detection_overlay=_render_overlay(source, tuple(boxes), cells),
    )


def _auto_grid(
    source: Image.Image,
    *,
    alpha_threshold: int,
    remove_small_components: bool,
    min_component_area_px: int,
    empty_column_threshold: int,
    empty_row_threshold: int,
    min_gutter_width_px: int,
    max_cell_size_variance_ratio: float,
    require_nonempty_each_cell: bool,
    requested_mode: SplitMode,
) -> SpriteSheetSplitResult:
    mask, _ = alpha_occupancy_mask(
        source,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
    )
    x_bands = _projection_bands(
        mask,
        empty_threshold=empty_column_threshold,
        min_gutter_width_px=min_gutter_width_px,
        axis=0,
    )
    y_bands = _projection_bands(
        mask,
        empty_threshold=empty_row_threshold,
        min_gutter_width_px=min_gutter_width_px,
        axis=1,
    )
    issues: list[str] = []
    if not x_bands or not y_bands:
        issues.append("可視領域を検出できませんでした")
    if len(x_bands) == 1 and len(y_bands) == 1:
        issues.append("透明ガターからグリッド境界を検出できませんでした")
    if _ratio([end - start for start, end in x_bands]) > max_cell_size_variance_ratio:
        issues.append("列方向のセル幅のばらつきが大きすぎます")
    if _ratio([end - start for start, end in y_bands]) > max_cell_size_variance_ratio:
        issues.append("行方向のセル高さのばらつきが大きすぎます")

    columns = len(x_bands)
    rows = len(y_bands)
    boxes = _cell_boxes(x_bands, y_bands) if columns and rows else ()
    cells = _make_cells(
        source,
        boxes,
        columns=max(1, columns),
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
        require_nonempty_each_cell=require_nonempty_each_cell,
    )
    if any(not cell.valid for cell in cells):
        issues.append("検出したセルに空のセルがあります")
    if issues:
        raise ValueError("alpha_gap_autoの検出に失敗しました: " + "; ".join(issues))

    max_width = max(end - start for start, end in x_bands)
    max_height = max(end - start for start, end in y_bands)
    frames: list[Image.Image] = []
    for box in boxes:
        cropped = source.crop(box)
        padded = Image.new("RGBA", (max_width, max_height), (0, 0, 0, 0))
        padded.alpha_composite(cropped, (0, 0))
        frames.append(padded)
    confidence = min(
        1.0,
        1.0 / max(
            _ratio([end - start for start, end in x_bands]),
            _ratio([end - start for start, end in y_bands]),
        ),
    )
    return SpriteSheetSplitResult(
        requested_mode=requested_mode,
        detected_mode="alpha_projection",
        rows=rows,
        columns=columns,
        frames=tuple(frames),
        cells=cells,
        x_bands=x_bands,
        y_bands=y_bands,
        source_size=source.size,
        normalized_size=(max_width, max_height),
        crop_box=(0, 0, source.width, source.height),
        confidence=confidence,
        fallback_used=False,
        fallback_reason=None,
        issues=(),
        alpha_threshold=alpha_threshold,
        empty_column_threshold=empty_column_threshold,
        empty_row_threshold=empty_row_threshold,
        min_gutter_width_px=min_gutter_width_px,
        detection_overlay=_render_overlay(source, boxes, cells),
    )


def split_sprite_sheet(
    image: Image.Image,
    *,
    mode: SplitMode = "fixed_grid",
    columns: int = 4,
    rows: int = 1,
    alpha_threshold: int = 16,
    remove_small_components: bool = True,
    min_component_area_px: int = 3,
    empty_column_threshold: int = 2,
    empty_row_threshold: int = 2,
    min_gutter_width_px: int = 2,
    max_cell_size_variance_ratio: float = 1.25,
    require_nonempty_each_cell: bool = True,
    remainder_policy: Literal["center_crop", "error"] = "center_crop",
) -> SpriteSheetSplitResult:
    """Split a regular sprite sheet with fixed, alpha, or hybrid detection."""
    if mode not in {"fixed_grid", "alpha_gap_auto", "hybrid"}:
        raise ValueError("split mode must be fixed_grid, alpha_gap_auto, or hybrid")
    if empty_column_threshold < 0 or empty_row_threshold < 0:
        raise ValueError("empty band thresholds must be non-negative")
    if min_gutter_width_px < 1:
        raise ValueError("min_gutter_width_px must be positive")
    if max_cell_size_variance_ratio < 1.0:
        raise ValueError("max_cell_size_variance_ratio must be at least 1")
    source = image.convert("RGBA")
    if mode == "fixed_grid":
        return _fixed_grid(
            source,
            columns=columns,
            rows=rows,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            require_nonempty_each_cell=require_nonempty_each_cell,
            remainder_policy=remainder_policy,
            requested_mode=mode,
            fallback_used=False,
            fallback_reason=None,
        )
    try:
        return _auto_grid(
            source,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            empty_column_threshold=empty_column_threshold,
            empty_row_threshold=empty_row_threshold,
            min_gutter_width_px=min_gutter_width_px,
            max_cell_size_variance_ratio=max_cell_size_variance_ratio,
            require_nonempty_each_cell=require_nonempty_each_cell,
            requested_mode=mode,
        )
    except ValueError as exc:
        if mode != "hybrid":
            raise
        return _fixed_grid(
            source,
            columns=columns,
            rows=rows,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            require_nonempty_each_cell=require_nonempty_each_cell,
            remainder_policy=remainder_policy,
            requested_mode=mode,
            fallback_used=True,
            fallback_reason=str(exc),
        )


def _render_overlay(
    source: Image.Image,
    boxes: tuple[SourceBox, ...],
    cells: tuple[SpriteSheetCell, ...],
) -> Image.Image:
    overlay = source.convert("RGBA").copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = ImageFont.load_default()
    for cell, box in zip(cells, boxes):
        color = (60, 220, 120, 255) if cell.valid else (240, 80, 80, 255)
        draw.rectangle(box, outline=color, width=max(1, min(3, overlay.width // 256 + 1)))
        label = f"F{cell.index + 1}"
        text_x = min(max(0, box[0] + 2), max(0, overlay.width - 20))
        text_y = min(max(0, box[1] + 2), max(0, overlay.height - 12))
        draw.rectangle((text_x, text_y, text_x + 20, text_y + 10), fill=(0, 0, 0, 160))
        draw.text((text_x + 2, text_y), label, fill=(255, 255, 255, 255), font=font)
    return overlay


__all__ = [
    "Band",
    "SplitMode",
    "SpriteSheetCell",
    "SpriteSheetSplitResult",
    "alpha_occupancy_mask",
    "split_sprite_sheet",
]
