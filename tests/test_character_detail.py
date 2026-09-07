import pytest
from PIL import Image

from pixel_tile_compiler.pixelizer.character_detail import (
    character_detail_profile,
    simplify_character_detail,
)


def _detail_fixture() -> Image.Image:
    image = Image.new("RGBA", (9, 9), (17, 23, 31, 0))
    for y in range(2, 7):
        for x in range(2, 7):
            image.putpixel((x, y), (100, 100, 100, 255))
    image.putpixel((4, 4), (110, 110, 110, 255))
    image.putpixel((5, 5), (255, 0, 0, 255))
    image.putpixel((7, 4), (110, 110, 110, 255))
    return image


def test_detailed_is_pixel_identical_and_returns_a_copy() -> None:
    source = _detail_fixture()

    result = simplify_character_detail(source, "detailed")

    assert result is not source
    assert result.tobytes() == source.tobytes()


def test_balanced_preserves_alpha_and_transparent_rgb() -> None:
    source = _detail_fixture()

    result = simplify_character_detail(source, "balanced")

    assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert result.getpixel((0, 0)) == source.getpixel((0, 0))


def test_low_contrast_interior_component_is_replaced_by_an_existing_color() -> None:
    source = _detail_fixture()

    result = simplify_character_detail(source, "balanced")

    assert result.getpixel((4, 4)) == (100, 100, 100, 255)
    assert result.getpixel((4, 4))[:3] in {pixel[:3] for pixel in source.getdata() if pixel[3] != 0}


def test_high_contrast_and_silhouette_boundary_components_are_protected() -> None:
    source = _detail_fixture()

    result = simplify_character_detail(source, "sparse")

    assert result.getpixel((5, 5)) == (255, 0, 0, 255)
    assert result.getpixel((7, 4)) == (110, 110, 110, 255)


def test_detail_simplification_is_deterministic_and_rejects_unknown_levels() -> None:
    source = _detail_fixture()

    first = simplify_character_detail(source, "sparse")
    second = simplify_character_detail(source, "sparse")

    assert first.tobytes() == second.tobytes()

    with pytest.raises(ValueError, match="character detail level"):
        simplify_character_detail(source, "unknown")


def test_detail_thresholds_scale_with_native_canvas_short_side() -> None:
    assert character_detail_profile("balanced", canvas_size=(64, 64)).max_low_contrast_component_area == 2
    assert character_detail_profile("balanced", canvas_size=(128, 128)).max_low_contrast_component_area == 8
    assert character_detail_profile("balanced", canvas_size=(128, 96)).max_low_contrast_component_area == 5
    assert character_detail_profile("sparse", canvas_size=(128, 128)).max_low_contrast_component_area == 16
