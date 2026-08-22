"""Minimal dithering hook."""

from PIL import Image


def apply_dithering(image: Image.Image, mode: str = "minimal") -> Image.Image:
    """Keep default output cluster-friendly; ordered dithering is intentionally deferred."""
    del mode
    return image.convert("RGBA").copy()
