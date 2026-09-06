"""Deterministic sheet and tile normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image


CropPolicy = Literal["center", "top_left"]
NormalizeMode = Literal["nearest", "area", "pixel_preserving"]


@dataclass(frozen=True)
class SheetNormalization:
    image: Image.Image
    crop_box: tuple[int, int, int, int]
    method: str


def normalize_sheet(
    image: Image.Image,
    columns: int,
    rows: int,
    crop_policy: CropPolicy = "center",
) -> SheetNormalization:
    if columns < 1 or rows < 1:
        raise ValueError("grid dimensions must be positive")
    source = image.convert("RGBA")
    target_width = source.width - (source.width % columns)
    target_height = source.height - (source.height % rows)
    if target_width < columns or target_height < rows:
        raise ValueError("sheet is smaller than the requested logical grid")
    left = (source.width - target_width) // 2 if crop_policy == "center" else 0
    top = (source.height - target_height) // 2 if crop_policy == "center" else 0
    box = (left, top, left + target_width, top + target_height)
    return SheetNormalization(source.crop(box), box, f"deterministic_{crop_policy}_crop")


def normalize_tile(image: Image.Image, tile_size: int = 64, mode: NormalizeMode = "nearest") -> Image.Image:
    if tile_size < 1:
        raise ValueError("tile_size must be positive")
    source = image.convert("RGBA")
    if mode in {"nearest", "pixel_preserving"}:
        resampling = Image.Resampling.NEAREST
    elif mode == "area":
        resampling = Image.Resampling.BOX if source.width >= tile_size and source.height >= tile_size else Image.Resampling.BICUBIC
    else:
        raise ValueError(f"unsupported tile normalization mode: {mode}")
    return source.resize((tile_size, tile_size), resampling)
