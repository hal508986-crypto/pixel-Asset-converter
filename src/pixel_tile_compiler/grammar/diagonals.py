"""Conservative diagonal cleanup for the MVP."""

from PIL import Image


def cleanup_diagonals(image: Image.Image) -> Image.Image:
    """Keep artist-defined diagonals intact; isolated pixels are handled separately."""
    return image.convert("RGBA").copy()
