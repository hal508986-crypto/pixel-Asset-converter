from PIL import Image

from pixel_tile_compiler.tile.repeatability import (
    measure_repeatability,
    optimize_repeatability,
)


def make_periodic_risk_tile() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (45, 90, 45, 255))
    for y in range(20, 44):
        for x in range(20, 44):
            image.putpixel((x, y), (170, 125, 45, 255))
    for y in range(64):
        image.putpixel((0, y), (20, 60, 30, 255))
        image.putpixel((63, y), (100, 80, 35, 255))
    return image


def test_repeatability_metrics_include_center_and_wrap_scores():
    metrics = measure_repeatability(make_periodic_risk_tile(), tile_mode="repeatable", edge_band=6)

    assert metrics.center_dominance_score > 0
    assert metrics.periodicity_risk_score > 0
    assert metrics.edge_continuity_score < 1
    assert metrics.corner_seam_score >= 0


def test_repeatability_optimization_keeps_palette_and_reduces_risk():
    image = make_periodic_risk_tile()
    before = measure_repeatability(image, tile_mode="repeatable", edge_band=6)

    optimized = optimize_repeatability(
        image,
        tile_mode="repeatable",
        strength=1.0,
        edge_band=6,
        center_suppression_strength=0.8,
    )
    after = measure_repeatability(optimized, tile_mode="repeatable", edge_band=6)

    assert optimized.size == (64, 64)
    assert set(optimized.getdata()) <= set(image.getdata())
    assert after.edge_continuity_score >= before.edge_continuity_score
    assert after.periodicity_risk_score < before.periodicity_risk_score


def test_repeatability_optimization_is_inactive_for_non_repeatable_tiles():
    image = make_periodic_risk_tile()

    optimized = optimize_repeatability(
        image,
        tile_mode="object",
        strength=1.0,
        edge_band=6,
        center_suppression_strength=1.0,
    )

    assert optimized.tobytes() == image.tobytes()
