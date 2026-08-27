"""Character-oriented pixelization helpers."""

from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps


def nearest_pixelize(source: Image.Image, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Resize an image with nearest-neighbor sampling for detail preservation."""
    if size[0] < 1 or size[1] < 1:
        raise ValueError("pixelization size must be positive")
    return source.convert("RGBA").resize(size, Image.Resampling.NEAREST)


def add_edge_margin(image: Image.Image, margin: int = 2) -> Image.Image:
    """Add transparent padding only where visible pixels touch the canvas edge."""
    if margin < 0:
        raise ValueError("margin must be non-negative")

    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None or margin == 0:
        return source.copy()

    left = margin if bbox[0] == 0 else 0
    top = margin if bbox[1] == 0 else 0
    right = margin if bbox[2] == source.width else 0
    bottom = margin if bbox[3] == source.height else 0
    return ImageOps.expand(source, border=(left, top, right, bottom), fill=(0, 0, 0, 0))


def add_outline(image: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    """Add a one-pixel outside outline while preserving existing visible pixels."""
    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    dilated = alpha.filter(ImageFilter.MaxFilter(3))
    mask = Image.new("L", source.size)
    mask.putdata(
        [255 if dilated_value > 0 and alpha_value == 0 else 0 for dilated_value, alpha_value in zip(dilated.getdata(), alpha.getdata())]
    )
    outline = Image.new("RGBA", source.size, color)
    return Image.composite(outline, source, mask)
