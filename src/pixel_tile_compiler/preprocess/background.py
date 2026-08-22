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


def apply_background(
    image: Image.Image,
    mode: str = "alpha",
    color: Optional[str] = None,
    tolerance: int = 12,
) -> Image.Image:
    """Return an RGBA image with a binary alpha mask."""
    array = np.asarray(image.convert("RGBA")).copy()
    rgb = array[:, :, :3].astype(np.int16)
    if mode == "color":
        background = np.asarray(_parse_color(color), dtype=np.int16)
        mask = np.linalg.norm(rgb - background, axis=2) <= tolerance
        array[:, :, 3] = np.where(mask, 0, 255).astype(np.uint8)
    elif mode == "auto":
        corners = np.asarray(
            [rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]], dtype=np.float32
        )
        background = corners.mean(axis=0)
        mask = np.linalg.norm(rgb - background, axis=2) <= tolerance
        array[:, :, 3] = np.where(mask, 0, 255).astype(np.uint8)
    else:
        array[:, :, 3] = np.where(array[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    return Image.fromarray(array, mode="RGBA")
