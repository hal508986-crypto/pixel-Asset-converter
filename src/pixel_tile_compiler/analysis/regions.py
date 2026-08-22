"""SLIC region-map generation with a deterministic fallback."""

from typing import Any

import numpy as np
from PIL import Image
from skimage.segmentation import slic


def generate_region_map(
    image: Image.Image,
    segments: int = 128,
    compactness: float = 10.0,
    seed: int = 42,
) -> np.ndarray:
    """Generate a 2-D integer region map using SLIC."""
    del seed  # SLIC is deterministic for the fixed input and parameters used here.
    rgb = np.asarray(image.convert("RGB"))
    requested = max(2, min(segments, rgb.shape[0] * rgb.shape[1] // 16))
    try:
        labels = slic(
            rgb,
            n_segments=requested,
            compactness=compactness,
            start_label=0,
            channel_axis=-1,
            convert2lab=True,
        )
        return labels.astype(np.int32)
    except Exception:
        # The fallback preserves the core guarantee when optional CV algorithms fail.
        grid = max(1, int((rgb.shape[0] * rgb.shape[1] / requested) ** 0.5))
        yy, xx = np.indices(rgb.shape[:2])
        columns = (rgb.shape[1] + grid - 1) // grid
        return ((yy // grid) * columns + (xx // grid)).astype(np.int32)
