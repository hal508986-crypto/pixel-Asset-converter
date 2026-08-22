"""Repeatable tile seam inspection."""

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class SeamMetrics:
    horizontal_seam_score: float | None
    vertical_seam_score: float | None


def measure_seams(image: Image.Image, tile_mode: str = "repeatable") -> SeamMetrics:
    """Measure opposite edge RGB distances; non-repeatable tiles return nulls."""
    if tile_mode != "repeatable":
        return SeamMetrics(None, None)
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    horizontal = float(np.linalg.norm(rgb[:, 0] - rgb[:, -1], axis=1).mean())
    vertical = float(np.linalg.norm(rgb[0, :] - rgb[-1, :], axis=1).mean())
    return SeamMetrics(round(horizontal, 4), round(vertical, 4))
