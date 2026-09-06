"""Deterministic palette quantization."""

import numpy as np
from PIL import Image


def quantize_palette(
    image: Image.Image,
    budget: int,
    seed: int = 42,
    palette_colors: tuple[tuple[int, int, int], ...] | None = None,
) -> Image.Image:
    """Quantize only binary-visible RGB pixels; transparent RGB never enters the palette."""
    del seed  # Pillow's median cut path is deterministic for a fixed image.
    rgba = np.asarray(image.convert("RGBA"))
    alpha = np.where(rgba[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    visible = alpha != 0
    result_rgb = np.zeros_like(rgba[:, :, :3], dtype=np.uint8)
    if not visible.any():
        return Image.fromarray(np.dstack([result_rgb, alpha]).astype(np.uint8), mode="RGBA")

    visible_rgb = Image.fromarray(rgba[:, :, :3][visible].reshape(1, -1, 3), mode="RGB")
    if palette_colors is None:
        quantized = visible_rgb.quantize(colors=budget, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    else:
        if not palette_colors:
            raise ValueError("palette_colors must not be empty")
        palette_image = Image.new("P", (1, 1), 0)
        flattened = [channel for color in palette_colors for channel in color]
        filler = list(palette_colors[-1])
        flattened.extend(filler * ((768 - len(flattened) + 2) // 3))
        flattened = flattened[:768]
        palette_image.putpalette(flattened)
        quantized = visible_rgb.quantize(palette=palette_image, dither=Image.Dither.NONE).convert("RGB")
    result_rgb[visible] = np.asarray(quantized).reshape(-1, 3)
    result = np.dstack([result_rgb, alpha])
    return Image.fromarray(result.astype(np.uint8), mode="RGBA")


def extract_palette(image: Image.Image, budget: int) -> tuple[tuple[int, int, int], ...]:
    """Return the deterministic colors actually used by a quantized image."""
    quantized = quantize_palette(image, budget=budget)
    colors = sorted({pixel[:3] for pixel in quantized.convert("RGBA").getdata() if pixel[3] != 0})
    return tuple(colors)


def palette_preview(image: Image.Image) -> Image.Image:
    """Return a compact swatch strip for debug and GUI preview."""
    opaque = [pixel for pixel in image.convert("RGBA").getdata() if pixel[3] != 0]
    colors = sorted(set(opaque))
    preview = Image.new("RGBA", (max(1, len(colors)) * 16, 16), (0, 0, 0, 0))
    for index, color in enumerate(colors):
        preview.paste(color, (index * 16, 0, index * 16 + 16, 16))
    return preview
