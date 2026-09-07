"""Shared-trim preparation and compilation helpers for character animations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.sheet.normalizer import normalize_sheet


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

    def __post_init__(self) -> None:
        if self.frame_count < 1:
            raise ValueError("frame_count must be positive")
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
        if self.fit_within[0] + self.outline_width * 2 > self.canvas_size[0]:
            raise ValueError("fit width does not fit canvas with outline")
        if self.fit_within[1] + self.outline_width * 2 + self.bottom_margin > self.canvas_size[1]:
            raise ValueError("fit height and bottom margin do not fit canvas with outline")


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
        }


def split_horizontal_sheet(
    image: Image.Image,
    frame_count: int = 4,
    *,
    remainder_policy: Literal["center_crop", "error"] = "center_crop",
) -> tuple[tuple[Image.Image, ...], tuple[int, int, int, int], tuple[int, int]]:
    """Split a horizontal sheet into equal source cells without resizing them."""
    source = image.convert("RGBA")
    if frame_count < 1:
        raise ValueError("frame_count must be positive")
    if source.width % frame_count and remainder_policy == "error":
        raise ValueError("sheet width must be divisible by frame_count")
    normalized = normalize_sheet(source, frame_count, 1, crop_policy="center")
    cell_width = normalized.image.width // frame_count
    frames = tuple(
        normalized.image.crop((index * cell_width, 0, (index + 1) * cell_width, normalized.image.height))
        for index in range(frame_count)
    )
    return frames, normalized.crop_box, normalized.image.size


def _binary_alpha(image: Image.Image, threshold: int) -> np.ndarray:
    alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
    return alpha >= threshold


def _remove_small_components(mask: np.ndarray, min_area: int) -> tuple[np.ndarray, int]:
    """Remove 8-connected components smaller than ``min_area``."""
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
    mask = _binary_alpha(source, alpha_threshold)
    if remove_isolated_components:
        mask, _ = _remove_small_components(mask, min_component_area_px)
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
                    int(np.count_nonzero(_binary_alpha(frame, config.alpha_threshold)))
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
    """Split a horizontal sheet and align all frames against one layout."""
    config = config or CharacterAnimationConfig()
    frames, crop_box, normalized_size = split_horizontal_sheet(
        image,
        config.frame_count,
        remainder_policy=config.remainder_policy,
    )
    result = align_character_frames(frames, config)
    return CharacterAnimationResult(
        result.aligned_frames,
        result.frame_reports,
        result.union_bbox,
        result.scale,
        crop_box,
        normalized_size,
        config,
    )


@dataclass(frozen=True)
class CharacterAnimationCompileResult:
    output_root: Path
    aligned_sheet_path: Path
    report_path: Path
    frame_paths: tuple[Path, ...]
    compiled_sheet_path: Path
    preview_8x_path: Path


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
    """Prepare and compile every animation frame with the shared layout."""
    from pixel_tile_compiler.config import CanvasSpec, CompilerConfig
    from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

    source = Path(source)
    output_root = Path(output_root)
    config = config or CharacterAnimationConfig()
    with Image.open(source) as opened:
        prepared = prepare_character_animation_sheet(opened, config)
    output_root.mkdir(parents=True, exist_ok=True)
    aligned_sheet_path = save_png(prepared.output_sheet, output_root / "aligned_sheet.png")
    report_path = save_json(prepared.report_as_dict(), output_root / "bbox_report.json")

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
        compiled_sheet_path,
        preview_8x_path,
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
