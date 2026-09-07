"""Character-oriented pixelization helpers."""

from __future__ import annotations

from typing import NamedTuple

from PIL import Image, ImageFilter, ImageOps


def nearest_pixelize(source: Image.Image, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Resize an image with nearest-neighbor sampling for detail preservation."""
    if size[0] < 1 or size[1] < 1:
        raise ValueError("pixelization size must be positive")
    return source.convert("RGBA").resize(size, Image.Resampling.NEAREST)


class CharacterLayout(NamedTuple):
    """Resolved character frame and bottom anchor in output pixels."""

    frame_width: int
    frame_height: int
    bottom_margin: int


def resolve_character_layout(
    canvas: object,
    *,
    frame_width: int | None = None,
    frame_height: int | None = None,
    bottom_margin: int | None = None,
) -> CharacterLayout:
    """Resolve the 64px reference layout for any output canvas."""
    canvas_size = getattr(canvas, "size", canvas)
    canvas_width, canvas_height = (int(canvas_size[0]), int(canvas_size[1]))
    resolved_width = frame_width if frame_width is not None else _scale_half_up(54, canvas_width, 64)
    resolved_height = frame_height if frame_height is not None else _scale_half_up(54, canvas_height, 64)
    resolved_bottom = bottom_margin if bottom_margin is not None else _scale_half_up(7, canvas_height, 64)
    if min(resolved_width, resolved_height) < 1 or resolved_bottom < 0:
        raise ValueError("character layout dimensions must be valid")
    return CharacterLayout(resolved_width, resolved_height, resolved_bottom)


def _scale_half_up(value: int, target: int, reference: int) -> int:
    return (value * target + reference // 2) // reference


def fit_character_to_canvas(
    source: Image.Image,
    canvas_size: tuple[int, int] = (64, 64),
    frame_size: tuple[int, int] = (54, 54),
    bottom_margin: int = 7,
    outline_width: int = 0,
) -> Image.Image:
    """Fit visible pixels into a centered, bottom-anchored output frame."""
    canvas_width, canvas_height = canvas_size
    frame_width, frame_height = frame_size
    if min(canvas_width, canvas_height, frame_width, frame_height) < 1:
        raise ValueError("canvas and frame dimensions must be positive")
    if bottom_margin < 0 or outline_width < 0:
        raise ValueError("bottom_margin and outline_width must be non-negative")
    if frame_width + outline_width * 2 > canvas_width:
        raise ValueError("character frame width does not fit the canvas")
    if frame_height + outline_width * 2 + bottom_margin > canvas_height:
        raise ValueError("character frame height and bottom margin do not fit the canvas")

    rgba = source.convert("RGBA")
    binary_alpha = rgba.getchannel("A").point(lambda value: 255 if value >= 128 else 0)
    bbox = binary_alpha.getbbox()
    output = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    if bbox is None:
        return output

    cropped = rgba.crop(bbox)
    cropped.putalpha(binary_alpha.crop(bbox))
    available_width = frame_width - outline_width * 2
    available_height = frame_height - outline_width * 2
    scale = min(available_width / cropped.width, available_height / cropped.height)
    resized_size = (
        max(1, min(available_width, round(cropped.width * scale))),
        max(1, min(available_height, round(cropped.height * scale))),
    )
    resized = cropped.resize(resized_size, Image.Resampling.NEAREST)
    left = (canvas_width - resized.width) // 2
    top = canvas_height - bottom_margin - outline_width - resized.height
    output.alpha_composite(resized, (left, top))
    return output


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
