from PIL import Image

from pixel_tile_compiler.ir.schema import GlobalStyle, RegionIR, TileIR
from pixel_tile_compiler.pixelizer.spatial import region_aware_pixelize


def test_region_aware_pixelize_produces_fixed_size_without_smoothing():
    source = Image.new("RGBA", (16, 16), (25, 90, 40, 255))
    for y in range(4, 12):
        for x in range(4, 12):
            source.putpixel((x, y), (130, 80, 40, 255))
    region_map = [[0 for _ in range(16)] for _ in range(16)]
    for y in range(4, 12):
        for x in range(4, 12):
            region_map[y][x] = 1
    ir = TileIR(
        tile_type="ground",
        tile_mode="repeatable",
        palette_budget=16,
        global_style=GlobalStyle(
            texture_density=0.4,
            contrast_strength=0.5,
            edge_strength=0.5,
            dithering="off",
            outline_mode="off",
            seam_mode="inspect",
        ),
        regions=[
            RegionIR(
                id=0,
                semantic="grass",
                importance=0.5,
                area_ratio=0.75,
                dominant_color=(25, 90, 40),
                preserve_edges=False,
                preserve_texture=True,
                texture_priority=0.3,
                merge_candidates=[],
                discard_micro_detail=True,
            ),
            RegionIR(
                id=1,
                semantic="road",
                importance=1.0,
                area_ratio=0.25,
                dominant_color=(130, 80, 40),
                preserve_edges=True,
                preserve_texture=False,
                texture_priority=0.1,
                merge_candidates=[],
                discard_micro_detail=True,
            ),
        ],
    )

    result = region_aware_pixelize(source, region_map, ir)

    assert result.size == (64, 64)
    assert result.mode == "RGBA"
    assert result.getpixel((32, 32))[:3] == (130, 80, 40)
