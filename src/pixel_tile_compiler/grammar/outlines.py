"""Selective outline hook retained for future tile-specific rules."""

from PIL import Image


def apply_selective_outlines(image: Image.Image, enabled: bool = False) -> Image.Image:
    """Return the image unchanged in the conservative MVP grammar."""
    del enabled
    return image.convert("RGBA").copy()
