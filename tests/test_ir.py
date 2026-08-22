from pixel_tile_compiler.ir.schema import GlobalStyle, RegionIR, TileIR


def make_region() -> RegionIR:
    return RegionIR(
        id=1,
        semantic="grass",
        importance=0.8,
        area_ratio=1.0,
        dominant_color=(50, 120, 60),
        preserve_edges=True,
        preserve_texture=True,
        texture_priority=0.4,
        merge_candidates=[],
        discard_micro_detail=True,
    )


def test_tile_ir_validates_contract_and_round_trips_json():
    ir = TileIR(
        tile_type="grass",
        tile_mode="repeatable",
        palette_budget=16,
        global_style=GlobalStyle(
            texture_density=0.45,
            contrast_strength=0.5,
            edge_strength=0.35,
            dithering="minimal",
            outline_mode="selective",
            seam_mode="inspect",
        ),
        regions=[make_region()],
    )

    restored = TileIR.model_validate_json(ir.model_dump_json())

    assert restored.width == 64
    assert restored.palette_budget == 16
    assert restored.regions[0].semantic == "grass"


def test_tile_ir_rejects_palette_outside_spec_range():
    try:
        TileIR(
            tile_type="grass",
            tile_mode="repeatable",
            palette_budget=3,
            global_style=GlobalStyle(
                texture_density=0.4,
                contrast_strength=0.5,
                edge_strength=0.3,
                dithering="off",
                outline_mode="off",
                seam_mode="off",
            ),
            regions=[make_region()],
        )
    except ValueError:
        return
    raise AssertionError("palette_budget=3 must be rejected")
