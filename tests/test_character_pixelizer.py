from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.config import CompilerConfig, compiler_config_for_purpose
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.character import (
    add_edge_margin,
    add_outline,
    fit_character_to_canvas,
    nearest_pixelize,
)


def test_nearest_pixelize_uses_nearest_sampling() -> None:
    source = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    for y in range(2):
        for x in range(2):
            source.putpixel((x, y), (255, 0, 0, 255))
            source.putpixel((x + 2, y), (0, 255, 0, 255))
            source.putpixel((x, y + 2), (0, 0, 255, 255))
            source.putpixel((x + 2, y + 2), (255, 255, 0, 255))

    result = nearest_pixelize(source, (2, 2))

    assert result.size == (2, 2)
    assert list(result.getdata()) == [
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (255, 255, 0, 255),
    ]


def test_add_edge_margin_pads_only_sides_touched_by_visible_pixels() -> None:
    source = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    for y in range(2):
        for x in range(2):
            source.putpixel((x, y), (80, 140, 220, 255))

    result = add_edge_margin(source, margin=2)

    assert result.size == (6, 6)
    assert result.getchannel("A").getbbox() == (2, 2, 4, 4)
    assert result.getpixel((2, 2)) == (80, 140, 220, 255)
    assert result.getpixel((5, 5)) == (0, 0, 0, 0)


@pytest.mark.parametrize("color", [(0, 0, 0, 255), (255, 255, 255, 255)])
def test_add_outline_preserves_opaque_pixels_and_adds_one_pixel_border(color: tuple[int, int, int, int]) -> None:
    source = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
    for y in range(1, 4):
        for x in range(1, 4):
            source.putpixel((x, y), (30, 120, 220, 255))

    result = add_outline(source, color)

    assert result.size == source.size
    assert all(result.getpixel((x, y)) == source.getpixel((x, y)) for y in range(1, 4) for x in range(1, 4))
    assert result.getpixel((0, 0)) == color
    assert result.getpixel((4, 4)) == color
    assert result.getpixel((2, 0)) == color
    assert result.getpixel((0, 4)) == color


def test_fit_character_to_canvas_preserves_aspect_ratio_and_bottom_anchor() -> None:
    source = Image.new("RGBA", (1024, 768), (0, 0, 0, 0))
    for y in range(0, 600):
        for x in range(256, 768):
            source.putpixel((x, y), (80, 140, 220, 255))

    result = fit_character_to_canvas(source, canvas_size=(64, 64), frame_size=(54, 54), bottom_margin=7)
    bbox = result.getchannel("A").getbbox()

    assert bbox == (9, 3, 55, 57)
    assert result.size == (64, 64)


def test_fit_character_to_canvas_returns_empty_canvas_for_transparent_input() -> None:
    result = fit_character_to_canvas(Image.new("RGBA", (32, 48), (12, 34, 56, 0)))

    assert result.size == (64, 64)
    assert result.getchannel("A").getbbox() is None


def test_character_purpose_uses_stable_object_settings() -> None:
    config = compiler_config_for_purpose("character", output_root=Path("output"), palette_budget=16)

    assert config.pixelization_mode == "nearest"
    assert config.tile_mode == "object"
    assert config.repeat_opt_enabled is False
    assert config.dither == "off"
    assert config.background_mode == "auto"
    assert config.character_detail_level == "detailed"


def test_terrain_purpose_keeps_existing_defaults() -> None:
    config = compiler_config_for_purpose("terrain", output_root=Path("output"), palette_budget=16)

    assert config.pixelization_mode == "region"
    assert config.tile_mode == "repeatable"
    assert config.repeat_opt_enabled is True


def test_compiler_config_accepts_character_pixelization_and_outline() -> None:
    config = CompilerConfig(pixelization_mode="nearest", outline_color="black")

    assert config.pixelization_mode == "nearest"
    assert config.outline_color == "black"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pixelization_mode": "unknown"}, "pixelization_mode"),
        ({"outline_color": "red"}, "outline_color"),
        ({"character_detail_level": "noisy"}, "character_detail_level"),
    ],
)
def test_compiler_config_rejects_unknown_character_options(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CompilerConfig(**kwargs)


def test_character_mode_uses_nearest_path_and_preserves_transparent_background(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pixel_tile_compiler.pipeline.compiler as compiler_module

    source = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(16, 48):
        for x in range(16, 48):
            source.putpixel((x, y), (40, 140, 230, 255) if (x + y) % 2 else (230, 160, 40, 255))

    nearest_calls = 0

    original_nearest = compiler_module.nearest_pixelize

    def spy_nearest(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal nearest_calls
        nearest_calls += 1
        return original_nearest(*args, **kwargs)

    def fail_region(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("character mode must not use region-aware pixelization")

    monkeypatch.setattr(compiler_module, "nearest_pixelize", spy_nearest)
    monkeypatch.setattr(compiler_module, "region_aware_pixelize", fail_region)

    result = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(
            output_root=tmp_path / "character",
            palette_budget=32,
            tile_mode="object",
            pixelization_mode="nearest",
            outline_color="black",
            repeat_opt_enabled=False,
            work_size=64,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    final = Image.open(result.final_path).convert("RGBA")
    assert nearest_calls == 1
    assert final.size == (64, 64)
    assert set(pixel[3] for pixel in final.getdata()) <= {0, 255}
    assert (0, 0, 0, 255) in final.getdata()
    assert result.metadata["config"]["pixelization_mode"] == "nearest"
    assert result.metadata["config"]["outline_color"] == "black"


def test_character_mode_keeps_a_safe_transparent_margin_at_source_edge(tmp_path: Path) -> None:
    source = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(0, 32):
        for x in range(20, 44):
            source.putpixel((x, y), (40, 140, 230, 255))

    result = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(
            output_root=tmp_path / "character-edge",
            palette_budget=32,
            tile_mode="object",
            pixelization_mode="nearest",
            outline_color="black",
            repeat_opt_enabled=False,
            work_size=64,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    final = Image.open(result.final_path).convert("RGBA")
    assert final.getchannel("A").getbbox() is not None
    assert final.getchannel("A").getbbox()[1] >= 1


def test_character_detail_default_is_pixel_identical_to_explicit_detailed(tmp_path: Path) -> None:
    source = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    for y in range(16, 80):
        for x in range(24, 72):
            source.putpixel((x, y), (90, 110, 170, 255))
    source.putpixel((48, 40), (100, 120, 180, 255))

    default = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose("character", output_root=tmp_path / "default", palette_budget=24, work_size=64, debug_enabled=False),
    )
    explicit = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose(
            "character",
            output_root=tmp_path / "explicit",
            palette_budget=24,
            character_detail_level="detailed",
            work_size=64,
            debug_enabled=False,
        ),
    )

    assert default.final_path.read_bytes() == explicit.final_path.read_bytes()


def test_character_density_changes_rgb_only_and_terrain_ignores_it(tmp_path: Path) -> None:
    source = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    for y in range(16, 80):
        for x in range(24, 72):
            source.putpixel((x, y), (90, 110, 170, 255))
    source.putpixel((48, 40), (100, 120, 180, 255))

    sparse = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose("character", output_root=tmp_path / "sparse", palette_budget=24, character_detail_level="sparse", work_size=64, debug_enabled=False),
    )
    detailed = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose("character", output_root=tmp_path / "detailed", palette_budget=24, work_size=64, debug_enabled=False),
    )
    terrain_default = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(output_root=tmp_path / "terrain-default", palette_budget=8, work_size=64, debug_enabled=False),
    )
    terrain_sparse = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(output_root=tmp_path / "terrain-sparse", palette_budget=8, character_detail_level="sparse", work_size=64, debug_enabled=False),
    )

    sparse_image = Image.open(sparse.final_path).convert("RGBA")
    detailed_image = Image.open(detailed.final_path).convert("RGBA")
    assert sparse_image.getchannel("A").tobytes() == detailed_image.getchannel("A").tobytes()
    assert sparse_image.getchannel("A").getbbox() == detailed_image.getchannel("A").getbbox()
    assert terrain_default.final_path.read_bytes() == terrain_sparse.final_path.read_bytes()


def test_character_mode_fits_high_resolution_input_to_output_frame(tmp_path: Path) -> None:
    source = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    for y in range(0, 800):
        for x in range(300, 724):
            source.putpixel((x, y), (240, 180, 60, 255))

    result = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose(
            "character",
            output_root=tmp_path / "character-fit",
            palette_budget=16,
            work_size=64,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    final = Image.open(result.final_path).convert("RGBA")
    assert final.getchannel("A").getbbox() == (17, 3, 46, 57)


def test_character_outline_is_reserved_inside_palette_budget(tmp_path: Path) -> None:
    source = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    colors = ((220, 30, 30, 255), (30, 220, 30, 255), (30, 30, 220, 255), (220, 220, 30, 255))
    for y in range(16, 48):
        for x in range(16, 48):
            source.putpixel((x, y), colors[(x // 8 + y // 8) % len(colors)])

    result = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(
            output_root=tmp_path / "character-outline-budget",
            palette_budget=4,
            tile_mode="object",
            pixelization_mode="nearest",
            outline_color="black",
            repeat_opt_enabled=False,
            work_size=64,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    assert result.metrics.actual_palette_count <= 4
