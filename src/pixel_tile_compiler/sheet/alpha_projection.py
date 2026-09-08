"""Alpha-projection based sprite-sheet grid detection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .normalizer import normalize_sheet


SplitMode = Literal["fixed_grid", "alpha_gap_auto", "row_alpha_gap", "hybrid"]
Band = tuple[int, int]
SourceBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class SpriteSheetCell:
    """One detected or fixed logical cell in source-image coordinates."""

    index: int
    row: int
    column: int
    source_box: SourceBox
    content_box: SourceBox
    visible_pixel_count: int
    occupied_ratio: float
    valid: bool
    logical_origin: tuple[int, int] | None = None
    registration_translation: tuple[int, int] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "row": self.row,
            "column": self.column,
            "source_box": list(self.source_box),
            "content_box": list(self.content_box),
            "width": self.source_box[2] - self.source_box[0],
            "height": self.source_box[3] - self.source_box[1],
            "visible_pixel_count": self.visible_pixel_count,
            "occupied_ratio": self.occupied_ratio,
            "valid": self.valid,
            "logical_origin": list(self.logical_origin) if self.logical_origin is not None else None,
            "registration_translation": (
                list(self.registration_translation) if self.registration_translation is not None else None
            ),
        }


@dataclass(frozen=True)
class SpriteSheetSplitResult:
    """Source cells plus explainable detection diagnostics."""

    requested_mode: SplitMode
    detected_mode: Literal["fixed_grid", "alpha_projection", "row_alpha_gap"]
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
    row_bands: tuple[tuple[int, Band, tuple[Band, ...]], ...] = ()
    boundary_crossings: tuple[dict[str, object], ...] = ()
    attempted_modes: tuple[str, ...] = ()
    failure_reasons: tuple[str, ...] = ()

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def report_as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 3,
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
            "row_bands": [
                {"row": row, "y_range": list(y_range), "x_bands": [list(band) for band in x_bands]}
                for row, y_range, x_bands in self.row_bands
            ],
            "boundary_crossings": [dict(crossing) for crossing in self.boundary_crossings],
            "attempted_modes": list(self.attempted_modes),
            "failure_reasons": list(self.failure_reasons),
            "expected_frame_count": self.rows * self.columns,
            "detected_frame_count": self.frame_count,
            "quality_status": "warning" if self.fallback_used or self.boundary_crossings or self.issues else "passed",
            "warnings": list(self.issues),
        }


def alpha_occupancy_mask(
    image: Image.Image,
    *,
    alpha_threshold: int = 16,
    remove_small_components: bool = True,
    min_component_area_px: int = 3,
    protected_mask: Image.Image | None = None,
) -> tuple[np.ndarray, int]:
    """Build a binary alpha mask and return the number of removed pixels."""
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")
    if min_component_area_px < 1:
        raise ValueError("min_component_area_px must be positive")
    rgba = image.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    protected = _as_protected_mask(protected_mask, rgba.size)
    mask = (alpha >= alpha_threshold) | (protected & (alpha > 0))
    if not remove_small_components:
        return mask, 0
    return _remove_small_components(mask, min_component_area_px, protected_mask=protected)


def _as_protected_mask(mask: Image.Image | None, size: tuple[int, int]) -> np.ndarray:
    if mask is None:
        return np.zeros((size[1], size[0]), dtype=bool)
    if mask.size != size:
        raise ValueError("protected mask must have the same size as the source image")
    return np.asarray(mask.convert("L"), dtype=np.uint8) > 0


def _remove_small_components(
    mask: np.ndarray,
    min_area: int,
    *,
    protected_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
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
        component_protected = protected_mask is not None and any(
            bool(protected_mask[y, x]) for y, x in component
        )
        if len(component) >= min_area or component_protected:
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


def _grid_boundaries(bands: tuple[Band, ...], extent: int) -> tuple[Band, ...]:
    """Turn detected band counts into equal source windows without moving pixels."""
    if not bands:
        return ()
    count = len(bands)
    return tuple(
        (
            round(index * extent / count),
            round((index + 1) * extent / count),
        )
        for index in range(count)
    )


def _mask_crosses_boundary(mask: np.ndarray, boundary: int, *, axis: int) -> bool:
    """Return whether an 8-connected visible component crosses one grid boundary."""
    if axis == 0:
        before = mask[:, boundary - 1]
        after = mask[:, boundary]
    else:
        before = mask[boundary - 1, :]
        after = mask[boundary, :]
    return bool(
        np.any(before & after)
        or np.any(before[:-1] & after[1:])
        or np.any(before[1:] & after[:-1])
    )


def _boundary_crossing_record(
    mask: np.ndarray,
    boundary: int,
    *,
    axis: int,
    row: int | None = None,
    frame: int | None = None,
) -> dict[str, object] | None:
    """Describe visible pixels touching an unsafe 8-neighbor boundary."""
    if boundary <= 0 or (boundary >= mask.shape[1] if axis == 0 else boundary >= mask.shape[0]):
        return None
    if axis == 0:
        before = mask[:, boundary - 1]
        after = mask[:, boundary]
        touching = int(np.count_nonzero(before | after))
        direction = "vertical"
    else:
        before = mask[boundary - 1, :]
        after = mask[boundary, :]
        touching = int(np.count_nonzero(before | after))
        direction = "horizontal"
    if not _mask_crosses_boundary(mask, boundary, axis=axis):
        return None
    return {
        "direction": direction,
        "coordinate": boundary,
        "row": row,
        "frame": frame,
        "visible_pixel_count": touching,
    }


def _choose_gap_boundary(
    mask: np.ndarray,
    left_end: int,
    right_start: int,
    *,
    axis: int,
    row: int | None = None,
) -> int:
    """Choose the closest safe integer boundary inside one transparent gap."""
    if right_start <= left_end:
        raise ValueError("可視帯の順序が不正です")
    center = (left_end + right_start) // 2
    extent = mask.shape[1] if axis == 0 else mask.shape[0]
    candidates = sorted(
        range(max(1, left_end), min(extent - 1, right_start) + 1),
        key=lambda value: (abs(value - center), value),
    )
    for candidate in candidates:
        if not _mask_crosses_boundary(mask, candidate, axis=axis):
            return candidate
    raise ValueError(
        f"{('X' if axis == 0 else 'Y')}方向の透明ガター内に安全な境界がありません"
    )


def _grid_boundary_issues(
    mask: np.ndarray,
    grid_bands: tuple[Band, ...],
    *,
    axis: int,
    axis_name: str,
) -> tuple[str, ...]:
    """Reject equal windows whose internal boundary cuts through visible content."""
    issues: list[str] = []
    for grid_band in grid_bands[1:]:
        boundary = grid_band[0]
        if _mask_crosses_boundary(mask, boundary, axis=axis):
            issues.append(
                f"{axis_name}方向の等分境界{boundary}pxが可視maskを横切ります"
            )
    return tuple(issues)


def _make_cells(
    source: Image.Image,
    boxes: tuple[SourceBox, ...],
    *,
    columns: int,
    alpha_threshold: int,
    remove_small_components: bool,
    min_component_area_px: int,
    require_nonempty_each_cell: bool,
    content_boxes: tuple[SourceBox, ...] | None = None,
    logical_origins: tuple[tuple[int, int] | None, ...] | None = None,
    registration_translations: tuple[tuple[int, int] | None, ...] | None = None,
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
                content_box=content_boxes[index] if content_boxes is not None else box,
                visible_pixel_count=visible_count,
                occupied_ratio=visible_count / area,
                valid=visible_count > 0 or not require_nonempty_each_cell,
                logical_origin=(logical_origins[index] if logical_origins is not None else None),
                registration_translation=(
                    registration_translations[index] if registration_translations is not None else None
                ),
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
        content_boxes=tuple(boxes),
    )
    mask, _ = alpha_occupancy_mask(
        source,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
    )
    boundary_crossings = tuple(
        crossing
        for axis, bands in ((0, x_bands), (1, y_bands))
        for band in bands[1:]
        for crossing in (_boundary_crossing_record(mask, band[0], axis=axis),)
        if crossing is not None
    )
    issues = tuple(
        f"セル{cell.index + 1}が空です"
        for cell in cells
        if not cell.valid
    )
    crossing_issues = tuple(
        f"{crossing['direction']}方向の境界{crossing['coordinate']}pxが可視maskを横切ります"
        for crossing in boundary_crossings
    )
    issues = (*issues, *crossing_issues)
    if fallback_used and fallback_reason:
        issues = (fallback_reason, *issues)
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
        confidence=0.0 if fallback_used or boundary_crossings else 1.0,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        issues=issues,
        alpha_threshold=alpha_threshold,
        empty_column_threshold=0,
        empty_row_threshold=0,
        min_gutter_width_px=0,
        detection_overlay=_render_overlay(source, tuple(boxes), cells, boundary_crossings=boundary_crossings),
        boundary_crossings=boundary_crossings,
    )


def _coalesce_bands_to_expected_count(
    bands: tuple[Band, ...],
    expected_count: int,
    *,
    min_gutter_width_px: int,
    axis_name: str,
) -> tuple[Band, ...]:
    """Join only an unambiguous near-gap when projection sees one extra band."""
    if len(bands) <= expected_count:
        return bands
    work = list(bands)
    while len(work) > expected_count:
        gaps = [work[index + 1][0] - work[index][1] for index in range(len(work) - 1)]
        smallest = min(gaps)
        if smallest > min_gutter_width_px + 1 or gaps.count(smallest) != 1:
            raise ValueError(
                f"{axis_name}方向の可視帯が期待数に一致しません"
                f"（期待{expected_count}、検出{len(work)}。曖昧な隙間です）"
            )
        index = gaps.index(smallest)
        work[index : index + 2] = [(work[index][0], work[index + 1][1])]
    return tuple(work)


def _row_grid(
    source: Image.Image,
    *,
    columns: int,
    rows: int,
    alpha_threshold: int,
    remove_small_components: bool,
    min_component_area_px: int,
    empty_column_threshold: int,
    empty_row_threshold: int,
    min_gutter_width_px: int,
    require_nonempty_each_cell: bool,
    requested_mode: SplitMode,
) -> SpriteSheetSplitResult:
    """Split each row from its own alpha bands while preserving source coverage."""
    if columns < 1 or rows < 1:
        raise ValueError("row split columns and rows must be positive")
    mask, _ = alpha_occupancy_mask(
        source,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
    )
    y_bands = _projection_bands(
        mask,
        empty_threshold=empty_row_threshold,
        min_gutter_width_px=min_gutter_width_px,
        axis=1,
    )
    y_bands = _coalesce_bands_to_expected_count(
        y_bands,
        rows,
        min_gutter_width_px=min_gutter_width_px,
        axis_name="Y",
    )
    if len(y_bands) != rows:
        raise ValueError(f"Y方向の可視帯が期待数に一致しません（期待{rows}、検出{len(y_bands)}）")

    row_x_bands: list[tuple[Band, ...]] = []
    for row, (y_start, y_end) in enumerate(y_bands):
        bands = _projection_bands(
            mask[y_start:y_end, :],
            empty_threshold=empty_column_threshold,
            min_gutter_width_px=min_gutter_width_px,
            axis=0,
        )
        bands = _coalesce_bands_to_expected_count(
            bands,
            columns,
            min_gutter_width_px=min_gutter_width_px,
            axis_name=f"行{row + 1}のX",
        )
        if len(bands) != columns:
            raise ValueError(
                f"行{row + 1}を{columns}コマに分割できませんでした"
                f"（検出{len(bands)}コマ）"
            )
        row_x_bands.append(bands)

    y_boundaries = [0]
    for previous, current in zip(y_bands, y_bands[1:]):
        y_boundaries.append(
            _choose_gap_boundary(mask, previous[1], current[0], axis=1)
        )
    y_boundaries.append(source.height)

    boxes: list[SourceBox] = []
    content_boxes: list[SourceBox] = []
    logical_origins: list[tuple[int, int]] = []
    registration_translations: list[tuple[int, int]] = []
    row_reports: list[tuple[int, Band, tuple[Band, ...]]] = []
    boundary_crossings: list[dict[str, object]] = []
    for row, (y_start, y_end) in enumerate(y_bands):
        bands = row_x_bands[row]
        x_boundaries = [0]
        extraction_row_mask = mask[y_boundaries[row]:y_boundaries[row + 1], :]
        for previous, current in zip(bands, bands[1:]):
            x_boundaries.append(
                _choose_gap_boundary(extraction_row_mask, previous[1], current[0], axis=0)
            )
        x_boundaries.append(source.width)
        for boundary_index, boundary in enumerate(x_boundaries[1:-1], start=1):
            crossing = _boundary_crossing_record(
                extraction_row_mask,
                boundary,
                axis=0,
                row=row,
                frame=row * columns + boundary_index,
            )
            if crossing is not None:
                boundary_crossings.append(crossing)
        row_reports.append((row, (y_start, y_end), bands))
        for column, (x_start, x_end) in enumerate(bands):
            boxes.append((x_boundaries[column], y_boundaries[row], x_boundaries[column + 1], y_boundaries[row + 1]))
            content_boxes.append((x_start, y_start, x_end, y_end))
            logical_origin = (
                round(column * source.width / columns),
                round(row * source.height / rows),
            )
            logical_origins.append(logical_origin)
            registration_translations.append(
                (x_boundaries[column] - logical_origin[0], y_boundaries[row] - logical_origin[1])
            )

    box_tuple = tuple(boxes)
    cells = _make_cells(
        source,
        box_tuple,
        columns=columns,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
        require_nonempty_each_cell=require_nonempty_each_cell,
        content_boxes=tuple(content_boxes),
        logical_origins=tuple(logical_origins),
        registration_translations=tuple(registration_translations),
    )
    crossing_issues = tuple(
        f"{crossing['direction']}方向の境界{crossing['coordinate']}pxが可視maskを横切ります"
        for crossing in boundary_crossings
    )
    issues = (
        *(f"セル{cell.index + 1}が空です" for cell in cells if not cell.valid),
        *crossing_issues,
    )
    return SpriteSheetSplitResult(
        requested_mode=requested_mode,
        detected_mode="row_alpha_gap",
        rows=rows,
        columns=columns,
        frames=tuple(source.crop(box) for box in box_tuple),
        cells=cells,
        x_bands=(),
        y_bands=y_bands,
        source_size=source.size,
        normalized_size=source.size,
        crop_box=(0, 0, source.width, source.height),
        confidence=0.0 if issues else 1.0,
        fallback_used=False,
        fallback_reason=None,
        issues=issues,
        alpha_threshold=alpha_threshold,
        empty_column_threshold=empty_column_threshold,
        empty_row_threshold=empty_row_threshold,
        min_gutter_width_px=min_gutter_width_px,
        detection_overlay=_render_overlay(
            source,
            box_tuple,
            cells,
            boundary_crossings=tuple(boundary_crossings),
        ),
        row_bands=tuple(row_reports),
        boundary_crossings=tuple(boundary_crossings),
        attempted_modes=("row_alpha_gap",),
    )


def _auto_grid(
    source: Image.Image,
    *,
    columns: int,
    rows: int,
    enforce_expected_count: bool,
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
    if enforce_expected_count and len(x_bands) != columns:
        issues.append(f"列数が期待値と異なります（期待{columns}、検出{len(x_bands)}）")
    if enforce_expected_count and len(y_bands) != rows:
        issues.append(f"行数が期待値と異なります（期待{rows}、検出{len(y_bands)}）")
    if _ratio([end - start for start, end in x_bands]) > max_cell_size_variance_ratio:
        issues.append("列方向のセル幅のばらつきが大きすぎます")
    if _ratio([end - start for start, end in y_bands]) > max_cell_size_variance_ratio:
        issues.append("行方向のセル高さのばらつきが大きすぎます")

    columns = len(x_bands)
    rows = len(y_bands)
    content_boxes = _cell_boxes(x_bands, y_bands) if columns and rows else ()
    grid_x_bands = _grid_boundaries(x_bands, source.width)
    grid_y_bands = _grid_boundaries(y_bands, source.height)
    issues.extend(_grid_boundary_issues(mask, grid_x_bands, axis=0, axis_name="X"))
    issues.extend(_grid_boundary_issues(mask, grid_y_bands, axis=1, axis_name="Y"))
    boxes = _cell_boxes(grid_x_bands, grid_y_bands) if columns and rows else ()
    cells = _make_cells(
        source,
        boxes,
        columns=max(1, columns),
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_small_components,
        min_component_area_px=min_component_area_px,
        require_nonempty_each_cell=require_nonempty_each_cell,
        content_boxes=content_boxes,
    )
    if any(not cell.valid for cell in cells):
        issues.append("検出したセルに空のセルがあります")
    if issues:
        raise ValueError("alpha_gap_autoの検出に失敗しました: " + "; ".join(issues))

    max_width = max(end - start for start, end in grid_x_bands)
    max_height = max(end - start for start, end in grid_y_bands)
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
    if mode not in {"fixed_grid", "alpha_gap_auto", "row_alpha_gap", "hybrid"}:
        raise ValueError("split mode must be fixed_grid, alpha_gap_auto, row_alpha_gap, or hybrid")
    if empty_column_threshold < 0 or empty_row_threshold < 0:
        raise ValueError("empty band thresholds must be non-negative")
    if min_gutter_width_px < 1:
        raise ValueError("min_gutter_width_px must be positive")
    if max_cell_size_variance_ratio < 1.0:
        raise ValueError("max_cell_size_variance_ratio must be at least 1")
    source = image.convert("RGBA")
    if mode == "fixed_grid":
        return replace(_fixed_grid(
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
        ), attempted_modes=("fixed_grid",))
    if mode == "row_alpha_gap":
        return _row_grid(
            source,
            columns=columns,
            rows=rows,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            empty_column_threshold=empty_column_threshold,
            empty_row_threshold=empty_row_threshold,
            min_gutter_width_px=min_gutter_width_px,
            require_nonempty_each_cell=require_nonempty_each_cell,
            requested_mode=mode,
        )

    if mode == "alpha_gap_auto":
        return replace(_auto_grid(
            source,
            columns=columns,
            rows=rows,
            enforce_expected_count=False,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            empty_column_threshold=empty_column_threshold,
            empty_row_threshold=empty_row_threshold,
            min_gutter_width_px=min_gutter_width_px,
            max_cell_size_variance_ratio=max_cell_size_variance_ratio,
            require_nonempty_each_cell=require_nonempty_each_cell,
            requested_mode=mode,
        ), attempted_modes=("alpha_gap_auto",))

    attempts = ["alpha_gap_auto"]
    failures: list[str] = []
    try:
        result = _auto_grid(
            source,
            columns=columns,
            rows=rows,
            enforce_expected_count=True,
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
        return replace(result, attempted_modes=tuple(attempts))
    except ValueError as exc:
        failures.append(str(exc))

    attempts.append("row_alpha_gap")
    try:
        result = _row_grid(
            source,
            columns=columns,
            rows=rows,
            alpha_threshold=alpha_threshold,
            remove_small_components=remove_small_components,
            min_component_area_px=min_component_area_px,
            empty_column_threshold=empty_column_threshold,
            empty_row_threshold=empty_row_threshold,
            min_gutter_width_px=min_gutter_width_px,
            require_nonempty_each_cell=require_nonempty_each_cell,
            requested_mode=mode,
        )
        return replace(result, attempted_modes=tuple(attempts), failure_reasons=tuple(failures))
    except ValueError as exc:
        failures.append(str(exc))

    attempts.append("fixed_grid")
    return replace(_fixed_grid(
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
            fallback_reason=" / ".join(failures),
        ), attempted_modes=tuple(attempts), failure_reasons=tuple(failures))


def _render_overlay(
    source: Image.Image,
    boxes: tuple[SourceBox, ...],
    cells: tuple[SpriteSheetCell, ...],
    *,
    boundary_crossings: tuple[dict[str, object], ...] = (),
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
    for crossing in boundary_crossings:
        coordinate = int(crossing["coordinate"])
        if crossing["direction"] == "vertical":
            draw.line((coordinate, 0, coordinate, overlay.height - 1), fill=(255, 70, 70, 255), width=2)
        else:
            draw.line((0, coordinate, overlay.width - 1, coordinate), fill=(255, 70, 70, 255), width=2)
    return overlay


__all__ = [
    "Band",
    "SplitMode",
    "SpriteSheetCell",
    "SpriteSheetSplitResult",
    "alpha_occupancy_mask",
    "split_sprite_sheet",
]
