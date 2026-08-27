"""Deterministic brightness and saturation conditioning."""

from __future__ import annotations

import numpy as np
from PIL import Image

from pixel_tile_compiler.config import ColorConditioningConfig


def apply_color_conditioning(image: Image.Image, config: ColorConditioningConfig) -> Image.Image:
    """Adjust opaque and semi-transparent RGB values while preserving alpha."""
    source = image.convert("RGBA")
    if (
        config.brightness_scale == 1.0
        and config.saturation_scale == 1.0
        and config.saturation_cap is None
    ):
        return source.copy()

    rgba = np.asarray(source, dtype=np.float32).copy()
    rgb = rgba[:, :, :3] / 255.0
    alpha = rgba[:, :, 3]
    hue, saturation, value = _rgb_to_hsv(rgb)
    value = np.clip(value * config.brightness_scale, 0.0, 1.0)
    saturation = np.clip(saturation * config.saturation_scale, 0.0, 1.0)
    if config.saturation_cap is not None:
        saturation = np.minimum(saturation, config.saturation_cap)
    conditioned = _hsv_to_rgb(hue, saturation, value)
    visible = alpha > 0
    rgba[visible, :3] = np.rint(conditioned[visible] * 255.0)
    return Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), mode="RGBA")


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    maximum = rgb.max(axis=-1)
    minimum = rgb.min(axis=-1)
    delta = maximum - minimum
    saturation = np.zeros_like(maximum)
    np.divide(delta, maximum, out=saturation, where=maximum != 0)

    hue = np.zeros_like(maximum)
    safe_delta = np.where(delta == 0, 1.0, delta)
    red = (maximum == rgb[:, :, 0]) & (delta != 0)
    green = (maximum == rgb[:, :, 1]) & (delta != 0)
    blue = (maximum == rgb[:, :, 2]) & (delta != 0)
    hue[red] = ((rgb[:, :, 1][red] - rgb[:, :, 2][red]) / safe_delta[red]) % 6.0
    hue[green] = ((rgb[:, :, 2][green] - rgb[:, :, 0][green]) / safe_delta[green]) + 2.0
    hue[blue] = ((rgb[:, :, 0][blue] - rgb[:, :, 1][blue]) / safe_delta[blue]) + 4.0
    hue = (hue / 6.0) % 1.0
    return hue, saturation, maximum


def _hsv_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    scaled_hue = hue * 6.0
    sector = np.floor(scaled_hue).astype(np.int8) % 6
    fraction = scaled_hue - np.floor(scaled_hue)
    p = value * (1.0 - saturation)
    q = value * (1.0 - fraction * saturation)
    t = value * (1.0 - (1.0 - fraction) * saturation)
    output = np.empty((*hue.shape, 3), dtype=np.float32)
    output[sector == 0] = np.stack((value, t, p), axis=-1)[sector == 0]
    output[sector == 1] = np.stack((q, value, p), axis=-1)[sector == 1]
    output[sector == 2] = np.stack((p, value, t), axis=-1)[sector == 2]
    output[sector == 3] = np.stack((p, q, value), axis=-1)[sector == 3]
    output[sector == 4] = np.stack((t, p, value), axis=-1)[sector == 4]
    output[sector == 5] = np.stack((value, p, q), axis=-1)[sector == 5]
    return output
