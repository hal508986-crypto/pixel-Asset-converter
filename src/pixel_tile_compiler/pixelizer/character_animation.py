"""Shared-trim preparation and compilation helpers for character animations."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from shutil import copyfile
from typing import Literal

import numpy as np
from PIL import Image

from pixel_tile_compiler.io.exporter import save_json, save_png
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

    def __post_init__(self) -> None:
        if self.frame_count < 1:
            raise ValueError("frame_count must be positive")
        if self.split_mode not in {"fixed_grid", "alpha_gap_auto", "hybrid"}:
            raise ValueError("split_mode must be fixed_grid, alpha_gap_auto, or hybrid")
        if self.grid_columns is not None and self.grid_columns < 1:
            raise ValueError("grid_columns must be positive")
        if self.grid_rows is not None and self.grid_rows < 1:
            raise ValueError("grid_rows must be positive")
        if min(self.canvas_size) < 1 or min(self.fit_within) < 1:
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

    def as_dict(self, *, union_bbox: AlphaBoundingBox | None, scale: float, config: CharacterAnimationConfig) -> dict[str, object]:
        alpha_values = [0, 255]
        return {
            "frame_id": self.frame_id,
            "source_size": list(self.source_size),
            "visible_bbox": self.bbox.as_dict() if self.bbox else None,
            "union_bbox": union_bbox.as_dict() if union_bbox else None,
            "visible_pixel_count": self.visible_pixel_count,
            "removed_isolated_pixel_count": self.removed_isolated_pixel_count,
            "scale": scale,
            "placed_bbox": self.placed_bbox.as_dict() if self.placed_bbox else None,
            "anchor_x": config.anchor_x,
            "anchor_y": config.anchor_y,
            "clipped": self.clipped,
            "alpha_values": alpha_values,
            "status": self.status,
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
        return {
            "frame_count": len(self.aligned_frames),
            "common_scale": self.scale,
            "common_anchor": {
                "x": self.config.anchor_x,
                "y": self.config.anchor_y,
                "bottom_margin_px": self.config.bottom_margin,
            },
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


def align_character_frames(
    frames: tuple[Image.Image, ...] | list[Image.Image],
    config: CharacterAnimationConfig | None = None,
) -> CharacterAnimationResult:
    """Measure, union-trim, uniformly scale, and anchor animation frames."""
    config = config or CharacterAnimationConfig(frame_count=len(frames))
    if len(frames) != config.frame_count:
        raise ValueError("frame count does not match CharacterAnimationConfig")
    if not frames:
        raise ValueError("at least one frame is required")
    source_frames = tuple(frame.convert("RGBA") for frame in frames)
    source_size = source_frames[0].size
    if any(frame.size != source_size for frame in source_frames):
        raise ValueError("all animation frames must have the same size")

    cleaned_frames: list[Image.Image] = []
    frame_reports: list[CharacterAnimationFrameReport] = []
    union_bbox: AlphaBoundingBox | None = None
    for index, frame in enumerate(source_frames):
        cleaned, bbox = analyze_frame_alpha(
            frame,
            alpha_threshold=config.alpha_threshold,
            remove_isolated_components=config.remove_isolated_components,
            min_component_area_px=config.min_component_area_px,
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
                            )[0]
                        )
                    )
                    - _visible_pixel_count(cleaned)
                ),
                status="ready" if bbox is not None else "empty",
            )
        )
    if union_bbox is None:
        return CharacterAnimationResult(
            tuple(Image.new("RGBA", config.canvas_size, (0, 0, 0, 0)) for _ in source_frames),
            tuple(frame_reports),
            None,
            1.0,
            (0, 0, source_size[0], source_size[1]),
            source_size,
            config,
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
    )


def prepare_character_animation_sheet(
    image: Image.Image,
    config: CharacterAnimationConfig | None = None,
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
    result = align_character_frames(split.frames, alignment_config)
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
) -> CharacterAnimationCompileResult:
    """Write one complete animation artifact set into an empty staging root."""
    from pixel_tile_compiler.config import CanvasSpec, CompilerConfig
    from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

    with Image.open(source) as opened:
        prepared = prepare_character_animation_sheet(opened, config)
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
    "align_character_frames",
    "analyze_frame_alpha",
    "compile_character_animation_sheet",
    "prepare_character_animation_sheet",
    "split_horizontal_sheet",
]
