"""Alpha and flat-background handling."""

from typing import Optional

import numpy as np
from PIL import Image


def _parse_color(color: Optional[str]) -> tuple[int, int, int]:
    if not color:
        raise ValueError("背景色が指定されていません")
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError("背景色は #RRGGBB 形式で指定してください")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError("背景色は #RRGGBB 形式で指定してください") from exc


def _connected_background_mask(
    rgb: np.ndarray,
    visible: np.ndarray,
    backgrounds: tuple[tuple[int, int, int], ...],
    tolerance: int,
) -> np.ndarray:
    """Return only border-connected pixels close to one of the background colors."""
    height, width = visible.shape
    background_mask = np.zeros((height, width), dtype=bool)
    candidate = np.zeros_like(visible)
    for background in backgrounds:
        color = np.asarray(background, dtype=np.int16)
        candidate |= visible & (np.linalg.norm(rgb - color, axis=2) <= tolerance)
    seeds = np.zeros_like(candidate)
    seeds[0, :] = candidate[0, :]
    seeds[-1, :] |= candidate[-1, :]
    seeds[:, 0] |= candidate[:, 0]
    seeds[:, -1] |= candidate[:, -1]

    stack = [(int(y), int(x)) for y, x in np.argwhere(seeds)]
    while stack:
        y, x = stack.pop()
        if background_mask[y, x] or not candidate[y, x]:
            continue
        background_mask[y, x] = True
        for next_y in range(max(0, y - 1), min(height, y + 2)):
            for next_x in range(max(0, x - 1), min(width, x + 2)):
                if (next_y, next_x) != (y, x):
                    stack.append((next_y, next_x))
    return background_mask


def apply_background(
    image: Image.Image,
    mode: str = "alpha",
    color: Optional[str] = None,
    tolerance: int = 12,
) -> Image.Image:
    """Resolve alpha/background with a binary mask and border-connected removal."""
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    array = np.asarray(image.convert("RGBA")).copy()
    rgb = array[:, :, :3].astype(np.int16)
    visible = array[:, :, 3] >= 128
    if mode == "color":
        backgrounds = (_parse_color(color),)
        removed = _connected_background_mask(rgb, visible, backgrounds, tolerance)
        array[:, :, 3] = np.where(visible & ~removed, 255, 0).astype(np.uint8)
    elif mode == "auto":
        if not visible.all():
            array[:, :, 3] = np.where(visible, 255, 0).astype(np.uint8)
            return Image.fromarray(array, mode="RGBA")
        border_points = (
            [(0, x) for x in range(rgb.shape[1])]
            + [(rgb.shape[0] - 1, x) for x in range(rgb.shape[1])]
            + [(y, 0) for y in range(1, rgb.shape[0] - 1)]
            + [(y, rgb.shape[1] - 1) for y in range(1, rgb.shape[0] - 1)]
        )
        exact_backgrounds = tuple(
            tuple(int(channel) for channel in rgb[y, x])
            for y, x in border_points
            if visible[y, x]
        )
        if tolerance == 0:
            backgrounds = exact_backgrounds
        else:
            backgrounds = tuple(
                tuple((int(channel) // 4) * 4 for channel in rgb[y, x])
                for y, x in border_points
                if visible[y, x]
            )
        removed = _connected_background_mask(rgb, visible, tuple(dict.fromkeys(backgrounds)), tolerance)
        array[:, :, 3] = np.where(visible & ~removed, 255, 0).astype(np.uint8)
    else:
        if mode != "alpha":
            raise ValueError("background mode must be alpha, auto, or color")
        array[:, :, 3] = np.where(visible, 255, 0).astype(np.uint8)
    return Image.fromarray(array, mode="RGBA")
