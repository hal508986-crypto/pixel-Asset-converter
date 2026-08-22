"""MAP-level continuity measurements."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .models import MapMetrics


def _boundary_texture(luminance: np.ndarray, axis: int, index: int, band: int = 4) -> float:
    if axis == 1:
        first = luminance[:, max(0, index - band):index]
        second = luminance[:, index:min(luminance.shape[1], index + band)]
    else:
        first = luminance[max(0, index - band):index, :]
        second = luminance[index:min(luminance.shape[0], index + band), :]
    if first.size == 0 or second.size == 0:
        return 0.0
    return abs(float(first.std()) - float(second.std())) / 255.0


def measure_map_metrics(image: Image.Image, columns: int, rows: int, tile_size: int = 64) -> MapMetrics:
    """Measure color, brightness, and texture discontinuity at grid boundaries."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    luminance = rgb.mean(axis=2)
    color_scores: list[float] = []
    brightness_scores: list[float] = []
    texture_scores: list[float] = []
    width, height = image.size
    for tile_x in range(1, columns):
        boundary = tile_x * tile_size
        if boundary >= width:
            continue
        left = rgb[:, boundary - 1, :]
        right = rgb[:, boundary, :]
        color_scores.append(float(np.linalg.norm(left - right, axis=1).mean() / 441.6729559))
        brightness_scores.append(float(np.abs(luminance[:, boundary - 1] - luminance[:, boundary]).mean() / 255.0))
        texture_scores.append(_boundary_texture(luminance, axis=1, index=boundary))
    for tile_y in range(1, rows):
        boundary = tile_y * tile_size
        if boundary >= height:
            continue
        top = rgb[boundary - 1, :, :]
        bottom = rgb[boundary, :, :]
        color_scores.append(float(np.linalg.norm(top - bottom, axis=1).mean() / 441.6729559))
        brightness_scores.append(float(np.abs(luminance[boundary - 1, :] - luminance[boundary, :]).mean() / 255.0))
        texture_scores.append(_boundary_texture(luminance, axis=0, index=boundary))
    color = float(np.mean(color_scores)) if color_scores else 0.0
    brightness = float(np.mean(brightness_scores)) if brightness_scores else 0.0
    texture = float(np.mean(texture_scores)) if texture_scores else 0.0
    visibility = max(0.0, min(1.0, color * 0.5 + brightness * 0.3 + texture * 0.2))
    return MapMetrics(color, brightness, texture, visibility)
