"""Analysis-resolution normalization."""

from PIL import Image


def normalize_image(image: Image.Image, work_size: int = 256) -> Image.Image:
    """Resize to the analysis canvas without performing final pixelization."""
    normalized = image.convert("RGBA")
    if normalized.size == (work_size, work_size):
        return normalized.copy()
    return normalized.resize((work_size, work_size), Image.Resampling.LANCZOS)
