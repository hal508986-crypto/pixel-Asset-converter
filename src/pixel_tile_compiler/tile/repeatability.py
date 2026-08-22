"""Deterministic repeatable-tile optimization and periodicity metrics.

The optimizer only reassigns pixels to colors already present in the tile.  It
does not blur, interpolate, or invent alpha values, so the pixel-art contract
survives the wrap-aware correction.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class RepeatabilityMetrics:
    """Scores used to inspect repeatable-tile risk.

    ``edge_continuity_score`` is better when higher.  The other three scores
    are risk/seam scores where lower is better.
    """

    center_dominance_score: float | None
    periodicity_risk_score: float | None
    edge_continuity_score: float | None
    corner_seam_score: float | None


def _opaque_rgb(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    return rgba[:, :, :3].astype(np.float32), rgba[:, :, 3]


def _normalized_rgb_distance(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.linalg.norm(first.astype(np.float32) - second.astype(np.float32)) / (255.0 * 3.0**0.5))


def _center_bounds(height: int, width: int) -> tuple[int, int, int, int]:
    center_height = max(8, int(round(height * 0.40)))
    center_width = max(8, int(round(width * 0.40)))
    y0 = (height - center_height) // 2
    x0 = (width - center_width) // 2
    return y0, y0 + center_height, x0, x0 + center_width


def _mean_opaque(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    mask = alpha >= 128
    if not np.any(mask):
        return np.zeros(3, dtype=np.float32)
    return rgb[mask].mean(axis=0)


def _dominant_color(rgb: np.ndarray, alpha: np.ndarray) -> tuple[int, int, int] | None:
    pixels = [tuple(int(value) for value in pixel) for pixel in rgb[alpha >= 128]]
    if not pixels:
        return None
    return Counter(pixels).most_common(1)[0][0]


def _edge_continuity(rgb: np.ndarray, alpha: np.ndarray) -> float:
    height, width = alpha.shape
    horizontal_mask = (alpha[:, 0] >= 128) & (alpha[:, width - 1] >= 128)
    vertical_mask = (alpha[0, :] >= 128) & (alpha[height - 1, :] >= 128)
    distances: list[float] = []
    if np.any(horizontal_mask):
        distances.append(float(np.linalg.norm(rgb[horizontal_mask, 0] - rgb[horizontal_mask, -1], axis=1).mean() / (255.0 * 3.0**0.5)))
    if np.any(vertical_mask):
        distances.append(float(np.linalg.norm(rgb[0, vertical_mask] - rgb[-1, vertical_mask], axis=1).mean() / (255.0 * 3.0**0.5)))
    if not distances:
        return 1.0
    return max(0.0, min(1.0, 1.0 - sum(distances) / len(distances)))


def _corner_seam(rgb: np.ndarray, alpha: np.ndarray) -> float:
    height, width = alpha.shape
    pairs = (
        ((0, 0), (0, width - 1)),
        ((height - 1, 0), (height - 1, width - 1)),
        ((0, 0), (height - 1, 0)),
        ((0, width - 1), (height - 1, width - 1)),
    )
    distances = []
    for (ay, ax), (by, bx) in pairs:
        if alpha[ay, ax] >= 128 and alpha[by, bx] >= 128:
            distances.append(_normalized_rgb_distance(rgb[ay, ax], rgb[by, bx]))
    return round(sum(distances) / len(distances), 6) if distances else 0.0


def measure_repeatability(
    image: Image.Image,
    tile_mode: str = "repeatable",
    edge_band: int = 6,
) -> RepeatabilityMetrics:
    """Measure center concentration and wrap continuity for a tile.

    The metric is deliberately a cheap deterministic approximation.  It is a
    decision aid for comparing compiler output, not a claim of perceptual
    equivalence.
    """
    if tile_mode != "repeatable":
        return RepeatabilityMetrics(None, None, None, None)
    del edge_band  # reserved for a future multi-pixel continuity metric
    rgb, alpha = _opaque_rgb(image)
    height, width = alpha.shape
    y0, y1, x0, x1 = _center_bounds(height, width)
    center_rgb = rgb[y0:y1, x0:x1]
    center_alpha = alpha[y0:y1, x0:x1]
    outer_mask = np.ones((height, width), dtype=bool)
    outer_mask[y0:y1, x0:x1] = False
    outer_rgb = rgb[outer_mask]
    outer_alpha = alpha[outer_mask]
    center_mean = _mean_opaque(center_rgb, center_alpha)
    outer_mean = _mean_opaque(outer_rgb.reshape(-1, 1, 3), outer_alpha.reshape(-1, 1))
    contrast = _normalized_rgb_distance(center_mean, outer_mean)

    center_dominant = _dominant_color(center_rgb, center_alpha)
    if center_dominant is None:
        center_concentration = 0.0
    else:
        center_concentration = float(
            np.all(center_rgb == np.asarray(center_dominant, dtype=np.float32), axis=2)[center_alpha >= 128].mean()
        )
    center_dominance = max(0.0, min(1.0, 0.65 * contrast + 0.35 * center_concentration))
    edge_continuity = _edge_continuity(rgb, alpha)
    corner_seam = _corner_seam(rgb, alpha)
    periodicity_risk = max(0.0, min(1.0, 0.60 * center_dominance + 0.30 * (1.0 - edge_continuity) + 0.10 * corner_seam))
    return RepeatabilityMetrics(
        center_dominance_score=round(center_dominance, 6),
        periodicity_risk_score=round(periodicity_risk, 6),
        edge_continuity_score=round(edge_continuity, 6),
        corner_seam_score=corner_seam,
    )


def _palette(image: Image.Image) -> list[np.ndarray]:
    rgb, alpha = _opaque_rgb(image)
    colors = sorted({tuple(int(value) for value in pixel) for pixel in rgb[alpha >= 128]})
    return [np.asarray(color, dtype=np.float32) for color in colors]


def _nearest_palette(target: np.ndarray, palette: Iterable[np.ndarray]) -> np.ndarray:
    candidates = list(palette)
    if not candidates:
        return np.asarray(target, dtype=np.float32)
    return min(candidates, key=lambda color: float(np.linalg.norm(color - target)))


def _pair_target(
    rgb: np.ndarray,
    left: tuple[int, int],
    right: tuple[int, int],
) -> np.ndarray:
    ly, lx = left
    ry, rx = right
    candidates = [rgb[ly, lx], rgb[ry, rx]]
    for y, x in (left, right):
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny = min(rgb.shape[0] - 1, max(0, y + dy))
            nx = min(rgb.shape[1] - 1, max(0, x + dx))
            candidates.append(rgb[ny, nx])
    unique_candidates = {tuple(int(value) for value in candidate) for candidate in candidates}
    return min(
        (np.asarray(candidate, dtype=np.float32) for candidate in unique_candidates),
        key=lambda candidate: float(np.linalg.norm(candidate - rgb[ly, lx]) + np.linalg.norm(candidate - rgb[ry, rx])),
    )


def _correct_wrap_edges(rgba: np.ndarray, strength: float, edge_band: int) -> None:
    """Correct corresponding edge strips with existing palette colors only."""
    if strength <= 0:
        return
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3]
    height, width = alpha.shape
    band = max(1, min(min(height, width) // 2, int(round(edge_band * strength))))
    for offset in range(band):
        for y in range(height):
            if alpha[y, offset] >= 128 and alpha[y, width - 1 - offset] >= 128:
                left = rgb[y, offset]
                right = rgb[y, width - 1 - offset]
                if not np.array_equal(left, right):
                    target = _pair_target(rgb, (y, offset), (y, width - 1 - offset))
                    rgb[y, offset] = target
                    rgb[y, width - 1 - offset] = target
        for x in range(width):
            if alpha[offset, x] >= 128 and alpha[height - 1 - offset, x] >= 128:
                top = rgb[offset, x]
                bottom = rgb[height - 1 - offset, x]
                if not np.array_equal(top, bottom):
                    target = _pair_target(rgb, (offset, x), (height - 1 - offset, x))
                    rgb[offset, x] = target
                    rgb[height - 1 - offset, x] = target
    rgba[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)


def _suppress_center_landmark(rgba: np.ndarray, strength: float, palette: list[np.ndarray]) -> None:
    """Reduce only the outer ring of the center's dominant color cluster."""
    if strength <= 0:
        return
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3]
    height, width = alpha.shape
    y0, y1, x0, x1 = _center_bounds(height, width)
    center = rgb[y0:y1, x0:x1]
    center_alpha = alpha[y0:y1, x0:x1]
    dominant = _dominant_color(center, center_alpha)
    if dominant is None:
        return
    dominant_array = np.asarray(dominant, dtype=np.float32)
    ring = max(1, min(4, int(round(4 * strength))))
    expanded_y0 = max(0, y0 - ring)
    expanded_y1 = min(height, y1 + ring)
    expanded_x0 = max(0, x0 - ring)
    expanded_x1 = min(width, x1 + ring)
    surrounding = rgb[expanded_y0:expanded_y1, expanded_x0:expanded_x1]
    surrounding_alpha = alpha[expanded_y0:expanded_y1, expanded_x0:expanded_x1]
    outer_mask = np.ones(surrounding_alpha.shape, dtype=bool)
    outer_mask[y0 - expanded_y0 : y1 - expanded_y0, x0 - expanded_x0 : x1 - expanded_x0] = False
    surrounding_pixels = surrounding[outer_mask & (surrounding_alpha >= 128)]
    if len(surrounding_pixels) == 0:
        return
    reference = surrounding_pixels.mean(axis=0)
    target = _nearest_palette((1.0 - strength * 0.75) * dominant_array + strength * 0.75 * reference, palette)
    for y in range(y0, y1):
        for x in range(x0, x1):
            if not np.array_equal(rgb[y, x], dominant_array) or alpha[y, x] < 128:
                continue
            near_border = (y - y0 < ring) or (y1 - 1 - y < ring) or (x - x0 < ring) or (x1 - 1 - x < ring)
            if near_border:
                rgb[y, x] = target
    rgba[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)


def optimize_repeatability(
    image: Image.Image,
    tile_mode: str = "repeatable",
    strength: float = 0.5,
    edge_band: int = 6,
    center_suppression_strength: float = 0.4,
) -> Image.Image:
    """Apply palette-preserving wrap correction for repeatable tiles only."""
    if tile_mode != "repeatable":
        return image.convert("RGBA").copy()
    if not 0.0 <= strength <= 1.0:
        raise ValueError("repeatability strength must be between 0 and 1")
    if not 0.0 <= center_suppression_strength <= 1.0:
        raise ValueError("center_suppression_strength must be between 0 and 1")
    if edge_band < 1:
        raise ValueError("edge_band must be at least 1")
    rgba = np.asarray(image.convert("RGBA")).copy()
    palette = _palette(image)
    _correct_wrap_edges(rgba, strength, edge_band)
    _suppress_center_landmark(rgba, center_suppression_strength * strength, palette)
    rgba[:, :, 3] = np.where(rgba[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    rgba[rgba[:, :, 3] == 0, :3] = 0
    return Image.fromarray(rgba, mode="RGBA")
