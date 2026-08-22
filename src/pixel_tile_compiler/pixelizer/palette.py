"""Deterministic palette quantization."""

import numpy as np
from PIL import Image


def quantize_palette(image: Image.Image, budget: int, seed: int = 42) -> Image.Image:
    """Quantize opaque pixels without introducing semi-transparent fringe pixels."""
    del seed  # Pillow's median cut path is deterministic for a fixed image.
    rgba = np.asarray(image.convert("RGBA"))
    alpha = np.where(rgba[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    rgb = Image.fromarray(rgba[:, :, :3], mode="RGB")
    quantized = rgb.quantize(colors=budget, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    result = np.dstack([np.asarray(quantized), alpha])
    result[alpha == 0, :3] = 0
    return Image.fromarray(result.astype(np.uint8), mode="RGBA")


def palette_preview(image: Image.Image) -> Image.Image:
    """Return a compact swatch strip for debug and GUI preview."""
    opaque = [pixel for pixel in image.convert("RGBA").getdata() if pixel[3] != 0]
    colors = sorted(set(opaque))
    preview = Image.new("RGBA", (max(1, len(colors)) * 16, 16), (0, 0, 0, 0))
    for index, color in enumerate(colors):
        preview.paste(color, (index * 16, 0, index * 16 + 16, 16))
    return preview
