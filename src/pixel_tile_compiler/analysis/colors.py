"""Small color helpers kept separate from region analysis."""

import numpy as np


def color_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    """Return a stable Euclidean RGB distance for local palette decisions."""
    return float(np.linalg.norm(np.asarray(first, dtype=np.float32) - np.asarray(second, dtype=np.float32)))
