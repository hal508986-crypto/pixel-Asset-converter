from PIL import Image

from pixel_tile_compiler.tile.seam import measure_seams


def test_seam_metrics_are_zero_for_matching_edges():
    image = Image.new("RGBA", (64, 64), (80, 100, 120, 255))

    metrics = measure_seams(image, tile_mode="repeatable")

    assert metrics.horizontal_seam_score == 0.0
    assert metrics.vertical_seam_score == 0.0


def test_seam_metrics_are_nullable_for_non_repeatable_tiles():
    image = Image.new("RGBA", (64, 64), (80, 100, 120, 255))

    metrics = measure_seams(image, tile_mode="object")

    assert metrics.horizontal_seam_score is None
    assert metrics.vertical_seam_score is None
