"""Shared-trim preparation and compilation helpers for character animations."""

from __future__ import annotations

import json
import hashlib
import math
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from shutil import copyfile
from typing import Literal, Mapping

import numpy as np
from PIL import Image

from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pixelizer.palette import extract_palette
from pixel_tile_compiler.sheet.alpha_projection import (
    SplitMode,
    alpha_occupancy_mask,
    split_sprite_sheet,
)


@dataclass(frozen=True)
class AlphaBoundingBox:
    """An image bounding box using Pillow's right/bottom-exclusive convention."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    def expand(self, padding: int, size: tuple[int, int]) -> "AlphaBoundingBox":
        if padding < 0:
            raise ValueError("padding must be non-negative")
        width, height = size
        return AlphaBoundingBox(
            max(0, self.left - padding),
            max(0, self.top - padding),
            min(width, self.right + padding),
            min(height, self.bottom + padding),
        )

    def union(self, other: "AlphaBoundingBox") -> "AlphaBoundingBox":
        return AlphaBoundingBox(
            min(self.left, other.left),
            min(self.top, other.top),
            max(self.right, other.right),
            max(self.bottom, other.bottom),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class CharacterAnimationTransform:
    """One affine nearest-neighbor transform shared by every animation frame."""

    source_origin: tuple[float, float]
    output_origin: tuple[float, float]
    scale: float
    frame_offsets: tuple[tuple[int, int], ...] = ()
    sampling_rounding: str = "floor(v + 0.5)"

    def __post_init__(self) -> None:
        if self.sampling_rounding != "floor(v + 0.5)":
            raise ValueError("sampling_rounding must be floor(v + 0.5)")
        if not math.isfinite(float(self.scale)) or self.scale <= 0:
            raise ValueError("animation scale must be greater than 0")
        for name, point in (("source_origin", self.source_origin), ("output_origin", self.output_origin)):
            if not _valid_finite_point(point):
                raise ValueError(f"{name} must contain two finite coordinates")
        for offset in self.frame_offsets:
            if not _valid_integer_pair(offset):
                raise ValueError("frame_offsets must contain integer pairs")

    def offset_for(self, frame_index: int) -> tuple[int, int]:
        if frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        if not self.frame_offsets or frame_index >= len(self.frame_offsets):
            return (0, 0)
        return self.frame_offsets[frame_index]

    def map_source_point(
        self,
        x: float | tuple[float, float],
        y: float | None = None,
        *,
        frame_index: int = 0,
    ) -> tuple[int, int]:
        if y is None:
            if not isinstance(x, tuple) or len(x) != 2:
                raise ValueError("source point must contain x and y")
            x, y = x
        offset_x, offset_y = self.offset_for(frame_index)
        source_x, source_y = self.source_origin
        output_x, output_y = self.output_origin
        return (
            _round_half_up(output_x + self.scale * (float(x) - source_x) + offset_x),
            _round_half_up(output_y + self.scale * (float(y) - source_y) + offset_y),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_origin": [self.source_origin[0], self.source_origin[1]],
            "output_origin": [self.output_origin[0], self.output_origin[1]],
            "scale": self.scale,
            "frame_offsets": [list(offset) for offset in self.frame_offsets],
            "sampling_rounding": self.sampling_rounding,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CharacterAnimationTransform":
        try:
            source_origin = tuple(float(item) for item in value["source_origin"])  # type: ignore[index]
            output_origin = tuple(float(item) for item in value["output_origin"])  # type: ignore[index]
            scale = float(value["scale"])  # type: ignore[arg-type]
            raw_offsets = value.get("frame_offsets", [])
            frame_offsets = tuple(tuple(int(item) for item in offset) for offset in raw_offsets)  # type: ignore[union-attr]
            sampling_rounding = str(value.get("sampling_rounding", "floor(v + 0.5)"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid animation transform") from exc
        return cls(source_origin, output_origin, scale, frame_offsets, sampling_rounding)


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _valid_positive_size(size: object) -> bool:
    return (
        isinstance(size, tuple)
        and len(size) == 2
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 1
            for value in size
        )
    )


def _valid_finite_point(point: object) -> bool:
    return (
        isinstance(point, (tuple, list))
        and len(point) == 2
        and all(math.isfinite(float(value)) for value in point)
    )


def _valid_integer_pair(pair: object) -> bool:
    return (
        isinstance(pair, (tuple, list))
        and len(pair) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in pair)
    )


def resolve_action_scale(
    *,
    target_reference_length: float | None,
    source_reference_length: float | None,
) -> float:
    """Calibrate an action from explicit reference lengths; never infer a body size."""
    if target_reference_length is None or source_reference_length is None:
        raise ValueError("動作間の縮尺には基準長を明示してください")
    if (
        not math.isfinite(float(target_reference_length))
        or not math.isfinite(float(source_reference_length))
        or target_reference_length <= 0
        or source_reference_length <= 0
    ):
        raise ValueError("基準長は正の有限値で指定してください")
    return float(target_reference_length) / float(source_reference_length)


def save_character_animation_transform(
    transform: CharacterAnimationTransform,
    path: Path,
) -> Path:
    """Persist a resolved transform for reuse by another action."""
    return save_json({"transform": transform.as_dict()}, Path(path))


def load_character_animation_transform(path: Path) -> CharacterAnimationTransform:
    """Load only the explicit transform contract from a report or transform file."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        value = payload.get("transform", payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"transformを読み込めません: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("transform must be a JSON object")
    return CharacterAnimationTransform.from_dict(value)


@dataclass(frozen=True)
class CharacterAnimationConfig:
    """Deterministic preparation settings for a horizontal character sheet."""

    frame_count: int = 4
    split_mode: SplitMode = "fixed_grid"
    grid_columns: int | None = None
    grid_rows: int | None = None
    canvas_size: tuple[int, int] = (64, 64)
    fit_within: tuple[int, int] = (54, 54)
    bottom_margin: int = 6
    alpha_threshold: int = 16
    remove_isolated_components: bool = True
    min_component_area_px: int = 3
    padding_px: int = 1
    remainder_policy: Literal["center_crop", "error"] = "center_crop"
    anchor_x: Literal["center"] = "center"
    anchor_y: Literal["foot"] = "foot"
    outline_width: int = 0
    empty_column_threshold: int = 2
    empty_row_threshold: int = 2
    min_gutter_width_px: int = 2
    max_cell_size_variance_ratio: float = 1.25
    require_nonempty_each_cell: bool = True
    placement_mode: Literal["legacy_foot", "preserve_motion"] = "legacy_foot"
    source_origin: tuple[float, float] | None = None
    output_origin: tuple[float, float] | None = None
    scale_override: float | None = None
    frame_offsets: tuple[tuple[int, int], ...] | None = None
    shared_palette_enabled: bool = False
    allow_empty_frames: bool = False

    def __post_init__(self) -> None:
        if self.frame_count < 1:
            raise ValueError("frame_count must be positive")
        if self.split_mode not in {"fixed_grid", "alpha_gap_auto", "hybrid"}:
            raise ValueError("split_mode must be fixed_grid, alpha_gap_auto, or hybrid")
        if self.grid_columns is not None and self.grid_columns < 1:
            raise ValueError("grid_columns must be positive")
        if self.grid_rows is not None and self.grid_rows < 1:
            raise ValueError("grid_rows must be positive")
        if not _valid_positive_size(self.canvas_size) or not _valid_positive_size(self.fit_within):
            raise ValueError("canvas_size and fit_within must be positive")
        if self.bottom_margin < 0:
            raise ValueError("bottom_margin must be non-negative")
        if not 0 <= self.alpha_threshold <= 255:
            raise ValueError("alpha_threshold must be between 0 and 255")
        if self.min_component_area_px < 1:
            raise ValueError("min_component_area_px must be positive")
        if self.padding_px < 0:
            raise ValueError("padding_px must be non-negative")
        if self.outline_width < 0:
            raise ValueError("outline_width must be non-negative")
        if self.empty_column_threshold < 0 or self.empty_row_threshold < 0:
            raise ValueError("empty band thresholds must be non-negative")
        if self.min_gutter_width_px < 1:
            raise ValueError("min_gutter_width_px must be positive")
        if self.max_cell_size_variance_ratio < 1.0:
            raise ValueError("max_cell_size_variance_ratio must be at least 1")
        if self.remainder_policy not in {"center_crop", "error"}:
            raise ValueError("remainder_policy must be center_crop or error")
        if self.placement_mode not in {"legacy_foot", "preserve_motion"}:
            raise ValueError("placement_mode must be legacy_foot or preserve_motion")
        for name, point in (("source_origin", self.source_origin), ("output_origin", self.output_origin)):
            if point is not None and not _valid_finite_point(point):
                raise ValueError(f"{name} must contain two finite coordinates")
        if self.scale_override is not None and (
            not math.isfinite(float(self.scale_override)) or self.scale_override <= 0
        ):
            raise ValueError("scale_override must be a positive finite number")
        if self.frame_offsets is not None:
            for offset in self.frame_offsets:
                if not _valid_integer_pair(offset):
                    raise ValueError("frame_offsets must contain integer pairs")
        if self.fit_within[0] + self.outline_width * 2 > self.canvas_size[0]:
            raise ValueError("fit width does not fit canvas with outline")
        if self.fit_within[1] + self.outline_width * 2 + self.bottom_margin > self.canvas_size[1]:
            raise ValueError("fit height and bottom margin do not fit canvas with outline")

    @property
    def grid_size(self) -> tuple[int, int]:
        """Return the explicit grid used by fixed mode and hybrid fallback."""
        return (
            self.grid_columns if self.grid_columns is not None else self.frame_count,
            self.grid_rows if self.grid_rows is not None else 1,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the serializable animation settings used for a report."""
        return {
            "frame_count": self.frame_count,
            "split_mode": self.split_mode,
            "grid_columns": self.grid_columns,
            "grid_rows": self.grid_rows,
            "canvas_size": list(self.canvas_size),
            "fit_within": list(self.fit_within),
            "bottom_margin": self.bottom_margin,
            "alpha_threshold": self.alpha_threshold,
            "remove_isolated_components": self.remove_isolated_components,
            "min_component_area_px": self.min_component_area_px,
            "padding_px": self.padding_px,
            "remainder_policy": self.remainder_policy,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
            "outline_width": self.outline_width,
            "empty_column_threshold": self.empty_column_threshold,
            "empty_row_threshold": self.empty_row_threshold,
            "min_gutter_width_px": self.min_gutter_width_px,
            "max_cell_size_variance_ratio": self.max_cell_size_variance_ratio,
            "require_nonempty_each_cell": self.require_nonempty_each_cell,
            "placement_mode": self.placement_mode,
            "source_origin": list(self.source_origin) if self.source_origin is not None else None,
            "output_origin": list(self.output_origin) if self.output_origin is not None else None,
            "scale_override": self.scale_override,
            "frame_offsets": [list(offset) for offset in self.frame_offsets] if self.frame_offsets is not None else None,
            "shared_palette_enabled": self.shared_palette_enabled,
            "allow_empty_frames": self.allow_empty_frames,
        }


@dataclass(frozen=True)
class CharacterAnimationFrameReport:
    frame_id: str
    source_size: tuple[int, int]
    bbox: AlphaBoundingBox | None
    visible_pixel_count: int
    removed_isolated_pixel_count: int
    status: Literal["ready", "empty"]
    placed_bbox: AlphaBoundingBox | None = None
    clipped: bool = False
    offset: tuple[int, int] = (0, 0)
    protected_pixel_count: int | None = None
    protected_pixel_lost: bool | None = None

    def as_dict(self, *, union_bbox: AlphaBoundingBox | None, scale: float, config: CharacterAnimationConfig) -> dict[str, object]:
        alpha_values = [0, 255]
        anchor_x: object = config.anchor_x
        anchor_y: object = config.anchor_y
        if config.placement_mode == "preserve_motion":
            anchor_x = "explicit"
            anchor_y = "explicit"
        return {
            "frame_id": self.frame_id,
            "source_size": list(self.source_size),
            "visible_bbox": self.bbox.as_dict() if self.bbox else None,
            "union_bbox": union_bbox.as_dict() if union_bbox else None,
            "visible_pixel_count": self.visible_pixel_count,
            "removed_isolated_pixel_count": self.removed_isolated_pixel_count,
            "scale": scale,
            "placed_bbox": self.placed_bbox.as_dict() if self.placed_bbox else None,
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "clipped": self.clipped,
            "alpha_values": alpha_values,
            "status": self.status,
            "offset": list(self.offset),
            "protected_pixel_count": self.protected_pixel_count,
            "protected_pixel_lost": self.protected_pixel_lost,
        }


@dataclass(frozen=True)
class CharacterAnimationResult:
    aligned_frames: tuple[Image.Image, ...]
    frame_reports: tuple[CharacterAnimationFrameReport, ...]
    union_bbox: AlphaBoundingBox | None
    scale: float
    crop_box: tuple[int, int, int, int]
    normalized_sheet_size: tuple[int, int]
    config: CharacterAnimationConfig
    split_report: dict[str, object] = field(default_factory=dict)
    detection_overlay: Image.Image | None = None
    transform: CharacterAnimationTransform | None = None
    shared_palette: tuple[tuple[int, int, int], ...] | None = None
    warnings: tuple[str, ...] = ()
    protected_masks: tuple[Image.Image | None, ...] = ()

    @property
    def output_sheet(self) -> Image.Image:
        output = Image.new(
            "RGBA",
            (self.config.canvas_size[0] * len(self.aligned_frames), self.config.canvas_size[1]),
            (0, 0, 0, 0),
        )
        for index, frame in enumerate(self.aligned_frames):
            output.alpha_composite(frame, (index * self.config.canvas_size[0], 0))
        return output

    def report_as_dict(self) -> dict[str, object]:
        if self.transform is None:
            common_anchor: dict[str, object] = {
                "x": self.config.anchor_x,
                "y": self.config.anchor_y,
                "bottom_margin_px": self.config.bottom_margin,
            }
        else:
            common_anchor = {
                "x": self.transform.output_origin[0],
                "y": self.transform.output_origin[1],
                "source_origin": list(self.transform.source_origin),
                "mode": "preserve_motion",
            }
        return {
            "schema_version": 2,
            "frame_count": len(self.aligned_frames),
            "common_scale": self.scale,
            "common_anchor": common_anchor,
            "output_frame_size": list(self.config.canvas_size),
            "output_sheet_size": [self.output_sheet.width, self.output_sheet.height],
            "normalized_sheet_size": list(self.normalized_sheet_size),
            "crop_box": list(self.crop_box),
            "union_bbox": self.union_bbox.as_dict() if self.union_bbox else None,
            "frames": [
                report.as_dict(union_bbox=self.union_bbox, scale=self.scale, config=self.config)
                for report in self.frame_reports
            ],
            "sprite_sheet_split": self.split_report,
            "placement_mode": self.config.placement_mode,
            "transform": self.transform.as_dict() if self.transform is not None else None,
            "shared_palette": {
                "enabled": self.config.shared_palette_enabled,
                "colors": [list(color) for color in self.shared_palette] if self.shared_palette is not None else None,
                "color_count": len(self.shared_palette) if self.shared_palette is not None else None,
            },
            "warnings": list(self.warnings),
            "config": self.config.as_dict(),
        }


def split_horizontal_sheet(
    image: Image.Image,
    frame_count: int = 4,
    *,
    remainder_policy: Literal["center_crop", "error"] = "center_crop",
) -> tuple[tuple[Image.Image, ...], tuple[int, int, int, int], tuple[int, int]]:
    """Split a horizontal sheet into equal source cells without resizing them."""
    if frame_count < 1:
        raise ValueError("frame_count must be positive")
    result = split_sprite_sheet(
        image,
        mode="fixed_grid",
        columns=frame_count,
        rows=1,
        remainder_policy=remainder_policy,
    )
    return result.frames, result.crop_box, result.normalized_size


def analyze_frame_alpha(
    image: Image.Image,
    *,
    alpha_threshold: int = 16,
    remove_isolated_components: bool = True,
    min_component_area_px: int = 3,
    protected_mask: Image.Image | None = None,
) -> tuple[Image.Image, AlphaBoundingBox | None]:
    """Return a binary-alpha frame and its stable visible bounding box."""
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")
    if min_component_area_px < 1:
        raise ValueError("min_component_area_px must be positive")
    source = image.convert("RGBA")
    mask, _ = alpha_occupancy_mask(
        source,
        alpha_threshold=alpha_threshold,
        remove_small_components=remove_isolated_components,
        min_component_area_px=min_component_area_px,
        protected_mask=protected_mask,
    )
    bbox = None
    if mask.any():
        ys, xs = np.where(mask)
        bbox = AlphaBoundingBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    rgba = np.asarray(source).copy()
    rgba[:, :, 3] = np.where(mask, 255, 0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA"), bbox


def _visible_pixel_count(image: Image.Image) -> int:
    return int(np.count_nonzero(np.asarray(image.getchannel("A"), dtype=np.uint8)))


def _place_frame(
    resized: Image.Image,
    *,
    canvas_size: tuple[int, int],
    bottom_margin: int,
    outline_width: int,
) -> tuple[Image.Image, AlphaBoundingBox | None, bool]:
    output = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    visible_bbox = resized.getchannel("A").getbbox()
    if visible_bbox is None:
        return output, None, False
    target_bottom = canvas_size[1] - bottom_margin - outline_width
    left = (canvas_size[0] - resized.width) // 2
    top = target_bottom - visible_bbox[3]
    placed = AlphaBoundingBox(
        left + visible_bbox[0],
        top + visible_bbox[1],
        left + visible_bbox[2],
        top + visible_bbox[3],
    )
    clipped = (
        placed.left < 0
        or placed.top < 0
        or placed.right > canvas_size[0]
        or placed.bottom > canvas_size[1]
    )
    if clipped:
        raise ValueError("aligned character frame would be clipped")
    output.alpha_composite(resized, (left, top))
    return output, placed, False


def _resolve_frame_offsets(
    frame_count: int,
    offsets: tuple[tuple[int, int], ...] | None,
) -> tuple[tuple[int, int], ...]:
    if offsets is None:
        return tuple((0, 0) for _ in range(frame_count))
    if len(offsets) not in {0, frame_count}:
        raise ValueError("frame_offsets must contain one pair per animation frame")
    return tuple(offsets) if offsets else tuple((0, 0) for _ in range(frame_count))


def _resolve_preserve_transform(
    frames: tuple[Image.Image, ...],
    reports: tuple[CharacterAnimationFrameReport, ...],
    config: CharacterAnimationConfig,
    transform: CharacterAnimationTransform | None,
) -> CharacterAnimationTransform:
    if transform is not None:
        if transform.frame_offsets and len(transform.frame_offsets) not in {len(frames)}:
            raise ValueError("animation transform frame count does not match frames")
        resolved = transform
    else:
        if config.source_origin is None or config.output_origin is None:
            raise ValueError("移動保存モードではソース原点と出力原点を明示してください")
        offsets = _resolve_frame_offsets(len(frames), config.frame_offsets)
        scale = config.scale_override
        if scale is None:
            scale = _fit_preserve_scale(
                reports,
                source_origin=config.source_origin,
                output_origin=config.output_origin,
                offsets=offsets,
                canvas_size=config.canvas_size,
            )
        resolved = CharacterAnimationTransform(
            tuple(float(value) for value in config.source_origin),
            tuple(float(value) for value in config.output_origin),
            float(scale),
            offsets,
        )
    _validate_transform_fits(reports, resolved, config.canvas_size)
    return resolved


def _fit_preserve_scale(
    reports: tuple[CharacterAnimationFrameReport, ...],
    *,
    source_origin: tuple[float, float],
    output_origin: tuple[float, float],
    offsets: tuple[tuple[int, int], ...],
    canvas_size: tuple[int, int],
) -> float:
    output_width, output_height = canvas_size
    origin_x, origin_y = output_origin
    if not (0 <= origin_x <= output_width and 0 <= origin_y <= output_height):
        raise ValueError("出力原点はCanvas内の境界座標で指定してください")
    candidates: list[float] = []
    source_x, source_y = source_origin
    for report, offset in zip(reports, offsets):
        if report.bbox is None:
            continue
        left, top, right, bottom = report.bbox.left, report.bbox.top, report.bbox.right, report.bbox.bottom
        extents = (
            (left - source_x, right - source_x, origin_x, output_width - origin_x, offset[0]),
            (top - source_y, bottom - source_y, origin_y, output_height - origin_y, offset[1]),
        )
        for low, high, before, after, shift in extents:
            if low < 0:
                candidates.append((before - shift) / -low)
            if high > 0:
                candidates.append((after - shift) / high)
    positive = [candidate for candidate in candidates if candidate > 0 and math.isfinite(candidate)]
    return min(positive) if positive else 1.0


def _transformed_bbox(
    bbox: AlphaBoundingBox,
    transform: CharacterAnimationTransform,
    frame_index: int,
) -> tuple[float, float, float, float]:
    offset_x, offset_y = transform.offset_for(frame_index)
    source_x, source_y = transform.source_origin
    output_x, output_y = transform.output_origin
    return (
        output_x + transform.scale * (bbox.left - source_x) + offset_x,
        output_y + transform.scale * (bbox.top - source_y) + offset_y,
        output_x + transform.scale * (bbox.right - source_x) + offset_x,
        output_y + transform.scale * (bbox.bottom - source_y) + offset_y,
    )


def _validate_transform_fits(
    reports: tuple[CharacterAnimationFrameReport, ...],
    transform: CharacterAnimationTransform,
    canvas_size: tuple[int, int],
) -> None:
    width, height = canvas_size
    issues: list[str] = []
    for index, report in enumerate(reports):
        if report.bbox is None:
            continue
        left, top, right, bottom = _transformed_bbox(report.bbox, transform, index)
        if left < 0:
            issues.append(f"F{index + 1}: left {math.floor(left)}px")
        if top < 0:
            issues.append(f"F{index + 1}: top {math.floor(top)}px")
        if right > width:
            issues.append(f"F{index + 1}: right needs {math.ceil(right - width)}px")
        if bottom > height:
            issues.append(f"F{index + 1}: bottom needs {math.ceil(bottom - height)}px")
    if issues:
        raise ValueError("指定した戦闘アニメーション変換では見切れます: " + "; ".join(issues))


def _sample_nearest(
    image: Image.Image,
    *,
    transform: CharacterAnimationTransform,
    frame_index: int,
    canvas_size: tuple[int, int],
) -> Image.Image:
    """Sample directly from the source using one affine phase for every frame."""
    source = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    output_width, output_height = canvas_size
    output_x, output_y = transform.output_origin
    source_x, source_y = transform.source_origin
    offset_x, offset_y = transform.offset_for(frame_index)
    x_centers = np.arange(output_width, dtype=np.float64) + 0.5
    y_centers = np.arange(output_height, dtype=np.float64) + 0.5
    source_x_indices = np.floor((x_centers - output_x - offset_x) / transform.scale + source_x).astype(int)
    source_y_indices = np.floor((y_centers - output_y - offset_y) / transform.scale + source_y).astype(int)
    valid_x = (source_x_indices >= 0) & (source_x_indices < source.shape[1])
    valid_y = (source_y_indices >= 0) & (source_y_indices < source.shape[0])
    output = np.zeros((output_height, output_width, 4), dtype=np.uint8)
    if valid_x.any() and valid_y.any():
        output[np.ix_(valid_y, valid_x)] = source[
            source_y_indices[valid_y][:, None],
            source_x_indices[valid_x][None, :],
        ]
    return Image.fromarray(output, mode="RGBA")


def _render_protected_mask(
    mask: Image.Image | None,
    *,
    transform: CharacterAnimationTransform,
    frame_index: int,
    canvas_size: tuple[int, int],
) -> Image.Image | None:
    if mask is None:
        return None
    source = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    source.putalpha(mask.convert("L"))
    return _sample_nearest(
        source,
        transform=transform,
        frame_index=frame_index,
        canvas_size=canvas_size,
    )


def _outline_rgb(color: str) -> tuple[int, int, int] | None:
    if color == "off":
        return None
    if color == "black":
        return (0, 0, 0)
    if color == "white":
        return (255, 255, 255)
    raise ValueError("outline_color must be off, black, or white")


def _resolve_shared_palette(
    frames: tuple[Image.Image, ...],
    *,
    budget: int,
    outline_color: str,
    palette_colors: tuple[tuple[int, int, int], ...] | None,
) -> tuple[tuple[int, int, int], ...]:
    """Resolve one visible-RGB palette for the complete action."""
    if palette_colors is not None:
        if not palette_colors:
            raise ValueError("palette_colors must not be empty")
        if len(palette_colors) > budget:
            raise ValueError("palette_colors cannot exceed palette_budget")
        resolved = tuple(tuple(int(channel) for channel in color) for color in palette_colors)
    else:
        combined = Image.new(
            "RGBA",
            (max(frame.width for frame in frames), sum(frame.height for frame in frames)),
            (0, 0, 0, 0),
        )
        top = 0
        for frame in frames:
            combined.alpha_composite(frame.convert("RGBA"), (0, top))
            top += frame.height
        outline_rgb = _outline_rgb(outline_color)
        resolved = extract_palette(
            combined,
            budget=max(1, budget - (1 if outline_rgb is not None else 0)),
        )
    outline_rgb = _outline_rgb(outline_color)
    if outline_rgb is not None and outline_rgb not in resolved:
        if len(resolved) < budget:
            resolved = (*resolved, outline_rgb)
        else:
            resolved = (*resolved[:-1], outline_rgb)
    return tuple(resolved)


def _image_palette_colors(path: Path) -> list[list[int]]:
    with Image.open(path) as opened:
        return [
            list(color)
            for color in sorted(
                {pixel[:3] for pixel in opened.convert("RGBA").getdata() if pixel[3] != 0}
            )
        ]


def _image_alpha_values(path: Path) -> list[int]:
    with Image.open(path) as opened:
        return sorted(set(opened.convert("RGBA").getchannel("A").getdata()))


def _image_bbox(path: Path) -> list[int] | None:
    with Image.open(path) as opened:
        bbox = opened.convert("RGBA").getchannel("A").getbbox()
    return list(bbox) if bbox is not None else None


def align_character_frames(
    frames: tuple[Image.Image, ...] | list[Image.Image],
    config: CharacterAnimationConfig | None = None,
    *,
    protected_masks: tuple[Image.Image | None, ...] | list[Image.Image | None] | None = None,
    transform: CharacterAnimationTransform | None = None,
) -> CharacterAnimationResult:
    """Align frames through the legacy foot mode or an explicit motion transform."""
    config = config or CharacterAnimationConfig(frame_count=len(frames))
    if len(frames) != config.frame_count:
        raise ValueError("frame count does not match CharacterAnimationConfig")
    if not frames:
        raise ValueError("at least one frame is required")
    source_frames = tuple(frame.convert("RGBA") for frame in frames)
    source_size = source_frames[0].size
    if any(frame.size != source_size for frame in source_frames):
        raise ValueError("all animation frames must have the same size")
    if protected_masks is None:
        source_protected_masks: tuple[Image.Image | None, ...] = tuple(None for _ in source_frames)
    else:
        if len(protected_masks) != len(source_frames):
            raise ValueError("protected_masks must contain one mask per animation frame")
        source_protected_masks = tuple(
            None if mask is None else mask.convert("L") for mask in protected_masks
        )
        if any(mask is not None and mask.size != source_size for mask in source_protected_masks):
            raise ValueError("protected masks must have the same size as the source frames")

    cleaned_frames: list[Image.Image] = []
    frame_reports: list[CharacterAnimationFrameReport] = []
    union_bbox: AlphaBoundingBox | None = None
    for index, (frame, protected_mask) in enumerate(zip(source_frames, source_protected_masks)):
        cleaned, bbox = analyze_frame_alpha(
            frame,
            alpha_threshold=config.alpha_threshold,
            remove_isolated_components=config.remove_isolated_components,
            min_component_area_px=config.min_component_area_px,
            protected_mask=protected_mask,
        )
        cleaned_frames.append(cleaned)
        if bbox is not None:
            union_bbox = bbox if union_bbox is None else union_bbox.union(bbox)
        frame_reports.append(
            CharacterAnimationFrameReport(
                frame_id=f"F{index + 1}",
                source_size=source_size,
                bbox=bbox,
                visible_pixel_count=_visible_pixel_count(cleaned),
                removed_isolated_pixel_count=(
                    int(
                        np.count_nonzero(
                            alpha_occupancy_mask(
                            frame,
                            alpha_threshold=config.alpha_threshold,
                            remove_small_components=False,
                            min_component_area_px=config.min_component_area_px,
                            protected_mask=protected_mask,
                        )[0]
                        )
                    )
                    - _visible_pixel_count(cleaned)
                ),
                status="ready" if bbox is not None else "empty",
                offset=(0, 0),
                protected_pixel_count=(
                    int(np.count_nonzero(np.asarray(protected_mask, dtype=np.uint8)))
                    if protected_mask is not None
                    else None
                ),
                protected_pixel_lost=False if protected_mask is not None and bbox is not None else None,
            )
        )
    if union_bbox is None:
        if config.placement_mode == "preserve_motion" and not config.allow_empty_frames:
            raise ValueError("移動保存モードでは空のframeを既定で許可しません")
        return CharacterAnimationResult(
            tuple(Image.new("RGBA", config.canvas_size, (0, 0, 0, 0)) for _ in source_frames),
            tuple(frame_reports),
            None,
            1.0,
            (0, 0, source_size[0], source_size[1]),
            source_size,
            config,
            transform=transform,
            protected_masks=tuple(None for _ in source_frames),
        )

    if config.placement_mode == "preserve_motion":
        reports = tuple(frame_reports)
        resolved_transform = _resolve_preserve_transform(
            tuple(cleaned_frames), reports, config, transform
        )
        aligned_frames: list[Image.Image] = []
        aligned_protected_masks: list[Image.Image | None] = []
        updated_reports: list[CharacterAnimationFrameReport] = []
        warnings: list[str] = []
        for index, (cleaned, report, source_mask) in enumerate(
            zip(cleaned_frames, frame_reports, source_protected_masks)
        ):
            aligned = _sample_nearest(
                cleaned,
                transform=resolved_transform,
                frame_index=index,
                canvas_size=config.canvas_size,
            )
            aligned_mask = _render_protected_mask(
                source_mask,
                transform=resolved_transform,
                frame_index=index,
                canvas_size=config.canvas_size,
            )
            protected_count = report.protected_pixel_count
            protected_lost = (
                protected_count is not None
                and protected_count > 0
                and (aligned_mask is None or aligned_mask.getchannel("A").getbbox() is None)
            )
            if protected_lost:
                warnings.append(f"F{index + 1}: 保護領域が縮小で消失しました")
            aligned_frames.append(aligned)
            aligned_protected_masks.append(aligned_mask)
            updated_reports.append(
                replace(
                    report,
                    placed_bbox=(
                        AlphaBoundingBox(*aligned.getchannel("A").getbbox())
                        if aligned.getchannel("A").getbbox() is not None
                        else None
                    ),
                    clipped=False,
                    offset=resolved_transform.offset_for(index),
                    protected_pixel_lost=protected_lost if protected_count is not None else None,
                )
            )
        return CharacterAnimationResult(
            tuple(aligned_frames),
            tuple(updated_reports),
            union_bbox,
            resolved_transform.scale,
            (0, 0, source_size[0], source_size[1]),
            source_size,
            config,
            transform=resolved_transform,
            warnings=tuple(warnings),
            protected_masks=tuple(aligned_protected_masks),
        )

    union_bbox = union_bbox.expand(config.padding_px, source_size)
    scale = min(config.fit_within[0] / union_bbox.width, config.fit_within[1] / union_bbox.height)
    resized_size = (
        max(1, round(union_bbox.width * scale)),
        max(1, round(union_bbox.height * scale)),
    )
    aligned_frames: list[Image.Image] = []
    updated_reports: list[CharacterAnimationFrameReport] = []
    for cleaned, report in zip(cleaned_frames, frame_reports):
        cropped = cleaned.crop((union_bbox.left, union_bbox.top, union_bbox.right, union_bbox.bottom))
        resized = cropped.resize(resized_size, Image.Resampling.NEAREST)
        aligned, placed_bbox, clipped = _place_frame(
            resized,
            canvas_size=config.canvas_size,
            bottom_margin=config.bottom_margin,
            outline_width=config.outline_width,
        )
        aligned_frames.append(aligned)
        updated_reports.append(
            CharacterAnimationFrameReport(
                frame_id=report.frame_id,
                source_size=report.source_size,
                bbox=report.bbox,
                visible_pixel_count=report.visible_pixel_count,
                removed_isolated_pixel_count=report.removed_isolated_pixel_count,
                status=report.status,
                placed_bbox=placed_bbox,
                clipped=clipped,
                offset=(0, 0),
                protected_pixel_count=report.protected_pixel_count,
                protected_pixel_lost=report.protected_pixel_lost,
            )
        )
    return CharacterAnimationResult(
        tuple(aligned_frames),
        tuple(updated_reports),
        union_bbox,
        scale,
        (0, 0, source_size[0], source_size[1]),
        source_size,
        config,
        protected_masks=tuple(None for _ in source_frames),
    )


def prepare_character_animation_sheet(
    image: Image.Image,
    config: CharacterAnimationConfig | None = None,
    *,
    protected_masks: tuple[Image.Image | None, ...] | list[Image.Image | None] | None = None,
    transform: CharacterAnimationTransform | None = None,
) -> CharacterAnimationResult:
    """Split a regular sheet and align all frames against one layout."""
    config = config or CharacterAnimationConfig()
    columns, rows = config.grid_size
    split = split_sprite_sheet(
        image,
        mode=config.split_mode,
        columns=columns,
        rows=rows,
        alpha_threshold=config.alpha_threshold,
        remove_small_components=config.remove_isolated_components,
        min_component_area_px=config.min_component_area_px,
        empty_column_threshold=config.empty_column_threshold,
        empty_row_threshold=config.empty_row_threshold,
        min_gutter_width_px=config.min_gutter_width_px,
        max_cell_size_variance_ratio=config.max_cell_size_variance_ratio,
        require_nonempty_each_cell=config.require_nonempty_each_cell,
        remainder_policy=config.remainder_policy,
    )
    alignment_config = replace(config, frame_count=split.frame_count)
    result = align_character_frames(
        split.frames,
        alignment_config,
        protected_masks=protected_masks,
        transform=transform,
    )
    return replace(
        result,
        crop_box=split.crop_box,
        normalized_sheet_size=split.normalized_size,
        split_report=split.report_as_dict(),
        detection_overlay=split.detection_overlay,
    )


@dataclass(frozen=True)
class CharacterAnimationCompileResult:
    output_root: Path
    aligned_sheet_path: Path
    report_path: Path
    frame_paths: tuple[Path, ...]
    final_frame_paths: tuple[Path, ...]
    compiled_sheet_path: Path
    preview_8x_path: Path
    detection_overlay_path: Path | None


_MANAGED_ANIMATION_OUTPUTS = (
    "aligned_sheet.png",
    "bbox_report.json",
    "compiled",
    "compiled_sheet.png",
    "compiled_sheet_8x.png",
    "detection_overlay.png",
    "final_frames",
)


def compile_character_animation_sheet(
    source: Path,
    output_root: Path,
    *,
    config: CharacterAnimationConfig | None = None,
    palette_budget: int = 24,
    character_detail_level: str = "balanced",
    outline_color: str = "off",
    debug_enabled: bool = False,
    palette_colors: tuple[tuple[int, int, int], ...] | None = None,
    shared_palette: tuple[tuple[int, int, int], ...] | None = None,
    protected_masks: tuple[Image.Image | None, ...] | list[Image.Image | None] | None = None,
    transform: CharacterAnimationTransform | None = None,
) -> CharacterAnimationCompileResult:
    """Prepare and compile every animation frame with transactional output replacement."""
    source = Path(source)
    output_root = Path(output_root)
    config = config or CharacterAnimationConfig()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name or 'output'}.staging-",
            dir=str(output_root.parent.resolve()),
        )
    )
    try:
        staged_result = _compile_character_animation_to_root(
            source,
            staging_root,
            config=config,
            palette_budget=palette_budget,
            character_detail_level=character_detail_level,
            outline_color=outline_color,
            debug_enabled=debug_enabled,
            palette_colors=palette_colors,
            shared_palette=shared_palette,
            protected_masks=protected_masks,
            transform=transform,
        )
        final_result = _relocate_compile_result(staged_result, staging_root, output_root)
        _rewrite_staged_metadata_paths(staging_root, output_root)
        _replace_output_root(staging_root, output_root)
        return final_result
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def _compile_character_animation_to_root(
    source: Path,
    output_root: Path,
    *,
    config: CharacterAnimationConfig,
    palette_budget: int,
    character_detail_level: str,
    outline_color: str,
    debug_enabled: bool,
    palette_colors: tuple[tuple[int, int, int], ...] | None,
    shared_palette: tuple[tuple[int, int, int], ...] | None,
    protected_masks: tuple[Image.Image | None, ...] | list[Image.Image | None] | None,
    transform: CharacterAnimationTransform | None,
) -> CharacterAnimationCompileResult:
    """Write one complete animation artifact set into an empty staging root."""
    from pixel_tile_compiler.config import CanvasSpec, CompilerConfig
    from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

    with Image.open(source) as opened:
        prepared = prepare_character_animation_sheet(
            opened,
            config,
            protected_masks=protected_masks,
            transform=transform,
        )
    if not 4 <= palette_budget <= 64:
        raise ValueError("palette_budget must be between 4 and 64")
    if palette_colors is not None and shared_palette is not None:
        raise ValueError("palette_colors and shared_palette cannot both be provided")
    requested_shared_palette = palette_colors if palette_colors is not None else shared_palette
    resolved_shared_palette = _resolve_shared_palette(
        prepared.aligned_frames,
        budget=palette_budget,
        outline_color=outline_color,
        palette_colors=requested_shared_palette,
    ) if config.shared_palette_enabled or requested_shared_palette is not None else None
    prepared = replace(prepared, shared_palette=resolved_shared_palette)
    output_root.mkdir(parents=True, exist_ok=True)
    aligned_sheet_path = save_png(prepared.output_sheet, output_root / "aligned_sheet.png")
    report_path = save_json(prepared.report_as_dict(), output_root / "bbox_report.json")
    detection_overlay_path = None
    if debug_enabled and prepared.detection_overlay is not None:
        detection_overlay_path = save_png(prepared.detection_overlay, output_root / "detection_overlay.png")

    frame_paths: list[Path] = []
    compiler = PixelTileCompiler()
    for index, frame in enumerate(prepared.aligned_frames):
        frame_root = output_root / "compiled" / f"F{index + 1}"
        compiler_config = CompilerConfig(
            output_root=frame_root,
            canvas=CanvasSpec(*config.canvas_size),
            palette_budget=palette_budget,
            tile_mode="object",
            repeat_opt_enabled=False,
            dither="off",
            background_mode="alpha",
            pixelization_mode="nearest",
            outline_color=outline_color,  # type: ignore[arg-type]
            character_detail_level=character_detail_level,  # type: ignore[arg-type]
            character_input_mode="pre_aligned",
            character_detail_scale_with_canvas=config.placement_mode == "legacy_foot",
            character_protected_mask=(
                prepared.protected_masks[index]
                if index < len(prepared.protected_masks)
                else None
            ),
            palette_colors=resolved_shared_palette,
            smoothing_enabled=False,
            debug_enabled=debug_enabled,
        )
        compiled = compiler.compile_image(frame, compiler_config, source_name=f"{source}::F{index + 1}")
        frame_paths.append(compiled.final_path)
    final_frames_root = output_root / "final_frames"
    final_frames_root.mkdir(parents=True, exist_ok=True)
    final_frame_paths = tuple(
        final_frames_root / f"F{index + 1}_final.png"
        for index in range(len(frame_paths))
    )
    for frame_path, final_frame_path in zip(frame_paths, final_frame_paths):
        copyfile(frame_path, final_frame_path)
    final_palette = sorted(
        {
            tuple(color)
            for final_frame_path in final_frame_paths
            for color in _image_palette_colors(final_frame_path)
        }
    )
    if len(final_palette) > palette_budget:
        raise RuntimeError("最終frame群の実paletteが上限を超えました")
    if resolved_shared_palette is not None and not set(final_palette).issubset(set(resolved_shared_palette)):
        raise RuntimeError("最終frame群の色が共有paletteの外へ出ました")
    report_payload = prepared.report_as_dict()
    report_payload["source_image"] = {
        "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "dimensions": list(Image.open(source).size),
    }
    report_payload["final_frames"] = [
        {
            "frame_id": f"F{index + 1}",
            "path": str(final_frame_path),
            "sha256": hashlib.sha256(final_frame_path.read_bytes()).hexdigest(),
            "size": list(Image.open(final_frame_path).size),
            "placed_bbox": _image_bbox(final_frame_path),
            "palette_colors": _image_palette_colors(final_frame_path),
            "alpha_values": _image_alpha_values(final_frame_path),
        }
        for index, final_frame_path in enumerate(final_frame_paths)
    ]
    report_payload["final_palette"] = {
        "colors": [list(color) for color in final_palette],
        "color_count": len(final_palette),
        "within_budget": len(final_palette) <= palette_budget,
        "alpha_policy": "binary",
    }
    save_json(report_payload, report_path)
    compiled_sheet = Image.new(
        "RGBA",
        (config.canvas_size[0] * len(frame_paths), config.canvas_size[1]),
        (0, 0, 0, 0),
    )
    for index, frame_path in enumerate(frame_paths):
        with Image.open(frame_path) as opened:
            compiled_sheet.alpha_composite(opened.convert("RGBA"), (index * config.canvas_size[0], 0))
    compiled_sheet_path = save_png(compiled_sheet, output_root / "compiled_sheet.png")
    preview_8x_path = save_png(
        compiled_sheet.resize((compiled_sheet.width * 8, compiled_sheet.height * 8), Image.Resampling.NEAREST),
        output_root / "compiled_sheet_8x.png",
    )
    return CharacterAnimationCompileResult(
        output_root,
        aligned_sheet_path,
        report_path,
        tuple(frame_paths),
        final_frame_paths,
        compiled_sheet_path,
        preview_8x_path,
        detection_overlay_path,
    )


def _replace_staged_value(value: object, old_prefix: str, new_prefix: str) -> object:
    if isinstance(value, str) and value.startswith(old_prefix):
        return new_prefix + value[len(old_prefix):]
    if isinstance(value, list):
        return [_replace_staged_value(item, old_prefix, new_prefix) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_staged_value(item, old_prefix, new_prefix)
            for key, item in value.items()
        }
    return value


def _rewrite_staged_metadata_paths(staging_root: Path, output_root: Path) -> None:
    """Keep compiler metadata pointing at the committed output location."""
    old_prefix = str(staging_root)
    new_prefix = str(output_root)
    for metadata_path in staging_root.glob("compiled/F*/metadata.json"):
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        save_json(_replace_staged_value(payload, old_prefix, new_prefix), metadata_path)


def _replace_output_root(staging_root: Path, output_root: Path) -> None:
    """Replace compiler-owned artifacts while preserving unrelated user files."""
    output_was_missing = not output_root.exists()
    output_root.mkdir(parents=True, exist_ok=True)
    backup_root = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name or 'output'}.previous-",
            dir=str(output_root.parent.resolve()),
        )
    )
    moved_old: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    replacement_succeeded = False
    restore_completed = False
    try:
        for name in _MANAGED_ANIMATION_OUTPUTS:
            destination = output_root / name
            staged = staging_root / name
            if destination.exists() or destination.is_symlink():
                backup = backup_root / name
                destination.rename(backup)
                moved_old.append((destination, backup))
            if staged.exists() or staged.is_symlink():
                staged.rename(destination)
                installed.append(destination)
        replacement_succeeded = True
    except BaseException:
        try:
            for destination in reversed(installed):
                _remove_output_path(destination)
            for destination, backup in reversed(moved_old):
                if backup.exists() or backup.is_symlink():
                    backup.rename(destination)
            if output_was_missing and output_root.exists() and not any(output_root.iterdir()):
                output_root.rmdir()
        except BaseException as restore_error:
            raise RuntimeError(
                "出力の入れ替えに失敗し、旧出力を復元できませんでした。"
                f"復旧用バックアップを保持しています: {backup_root}"
            ) from restore_error
        restore_completed = True
        raise
    finally:
        if replacement_succeeded or restore_completed:
            shutil.rmtree(backup_root, ignore_errors=True)


def _remove_output_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def _relocate_compile_result(
    result: CharacterAnimationCompileResult,
    staging_root: Path,
    output_root: Path,
) -> CharacterAnimationCompileResult:
    def relocate(path: Path | None) -> Path | None:
        return None if path is None else output_root / path.relative_to(staging_root)

    return replace(
        result,
        output_root=output_root,
        aligned_sheet_path=relocate(result.aligned_sheet_path),
        report_path=relocate(result.report_path),
        frame_paths=tuple(relocate(path) for path in result.frame_paths),
        final_frame_paths=tuple(relocate(path) for path in result.final_frame_paths),
        compiled_sheet_path=relocate(result.compiled_sheet_path),
        preview_8x_path=relocate(result.preview_8x_path),
        detection_overlay_path=relocate(result.detection_overlay_path),
    )


__all__ = [
    "AlphaBoundingBox",
    "CharacterAnimationCompileResult",
    "CharacterAnimationConfig",
    "CharacterAnimationFrameReport",
    "CharacterAnimationResult",
    "CharacterAnimationTransform",
    "align_character_frames",
    "analyze_frame_alpha",
    "compile_character_animation_sheet",
    "load_character_animation_transform",
    "prepare_character_animation_sheet",
    "resolve_action_scale",
    "save_character_animation_transform",
    "split_horizontal_sheet",
]
