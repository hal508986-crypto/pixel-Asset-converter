"""Noise reduction that preserves larger visual boundaries."""

import cv2
import numpy as np
from PIL import Image


def smooth_image(
    image: Image.Image,
    enabled: bool = True,
    diameter: int = 7,
    sigma_color: float = 40,
    sigma_space: float = 40,
) -> Image.Image:
    """Apply a deterministic bilateral filter while preserving alpha."""
    rgba = np.asarray(image.convert("RGBA"))
    if not enabled:
        return image.convert("RGBA").copy()
    rgb = cv2.bilateralFilter(rgba[:, :, :3], diameter, sigma_color, sigma_space)
    result = np.dstack([rgb, rgba[:, :, 3]])
    return Image.fromarray(result.astype(np.uint8), mode="RGBA")
