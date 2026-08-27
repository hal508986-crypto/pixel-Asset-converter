from __future__ import annotations

import colorsys

import pytest
from PIL import Image

from pixel_tile_compiler.config import ColorConditioningConfig
from pixel_tile_compiler.pixelizer.color_conditioning import apply_color_conditioning


def _saturation(pixel: tuple[int, int, int, int]) -> float:
    return colorsys.rgb_to_hsv(pixel[0] / 255, pixel[1] / 255, pixel[2] / 255)[1]


def test_identity_conditioning_preserves_pixels_and_alpha() -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(80, 180, 60, 255), (20, 40, 80, 0)])

    result = apply_color_conditioning(image, ColorConditioningConfig())

    assert result.mode == "RGBA"
    assert list(result.getdata()) == list(image.getdata())


def test_conditioning_changes_rgb_but_preserves_alpha_and_caps_saturation() -> None:
    image = Image.new("RGBA", (3, 1))
    image.putdata([(80, 180, 60, 255), (20, 40, 80, 128), (220, 10, 20, 0)])
    profile = ColorConditioningConfig(
        brightness_scale=0.8,
        saturation_scale=0.7,
        saturation_cap=0.35,
    )

    result = apply_color_conditioning(image, profile)

    assert [pixel[3] for pixel in result.getdata()] == [255, 128, 0]
    assert result.getpixel((0, 0))[:3] != image.getpixel((0, 0))[:3]
    assert _saturation(result.getpixel((0, 0))) <= 0.35 + 1 / 255
    assert result.getpixel((1, 0))[:3] != image.getpixel((1, 0))[:3]
    assert result.getpixel((2, 0))[:3] == image.getpixel((2, 0))[:3]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"brightness_scale": 0}, "brightness_scale"),
        ({"saturation_scale": -0.1}, "saturation_scale"),
        ({"saturation_cap": -0.1}, "saturation_cap"),
        ({"saturation_cap": 1.1}, "saturation_cap"),
    ],
)
def test_conditioning_rejects_invalid_profile(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ColorConditioningConfig(**kwargs)
