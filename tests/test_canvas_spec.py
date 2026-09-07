import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.config import CanvasSpec, CompilerConfig, compiler_config_for_purpose
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.character import resolve_character_layout


def _native_fixture() -> Image.Image:
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    for y in range(96, 416):
        for x in range(128, 384):
            image.putpixel((x, y), (210, 150, 70, 255))
    for y in range(140, 370):
        for x in range(180, 332):
            image.putpixel((x, y), (80, 100, 180, 255))
    for y in range(180, 184):
        for x in range(210, 302):
            image.putpixel((x, y), (20, 30, 90, 255))
    for y in range(220, 224):
        for x in range(246, 250):
            image.putpixel((x, y), (250, 240, 220, 255))
    return image


def test_canvas_spec_accepts_square_and_rectangular_output() -> None:
    assert CanvasSpec().size == (64, 64)
    assert CanvasSpec(128, 128).size == (128, 128)
    assert CanvasSpec(128, 96).size == (128, 96)


@pytest.mark.parametrize("width,height", [(0, 64), (64, 0), (-1, 64)])
def test_canvas_spec_rejects_non_positive_dimensions(width: int, height: int) -> None:
    with pytest.raises(ValueError, match="canvas dimensions"):
        CanvasSpec(width, height)


def test_character_layout_scales_from_the_64px_reference() -> None:
    assert resolve_character_layout(CanvasSpec(64, 64)) == (54, 54, 7)
    assert resolve_character_layout(CanvasSpec(128, 128)) == (108, 108, 14)
    assert resolve_character_layout(CanvasSpec(128, 96)) == (108, 81, 11)
    assert resolve_character_layout(CanvasSpec(96, 128)) == (81, 108, 14)


def test_non_64_canvas_is_character_only() -> None:
    character = CompilerConfig(canvas=CanvasSpec(128, 128), tile_mode="object", pixelization_mode="nearest")
    assert character.canvas.size == (128, 128)

    with pytest.raises(ValueError, match="object/nearest"):
        CompilerConfig(canvas=CanvasSpec(128, 128))


def test_native_character_128_is_not_a_nearest_upscale_of_native_64(tmp_path: Path) -> None:
    source = _native_fixture()
    compiler = PixelTileCompiler()
    native_64 = compiler.compile_image(
        source,
        compiler_config_for_purpose(
            "character",
            output_root=tmp_path / "native-64",
            canvas=CanvasSpec(64, 64),
            palette_budget=24,
            character_detail_level="balanced",
            debug_enabled=False,
        ),
    )
    native_128 = compiler.compile_image(
        source,
        compiler_config_for_purpose(
            "character",
            output_root=tmp_path / "native-128",
            canvas=CanvasSpec(128, 128),
            palette_budget=24,
            character_detail_level="balanced",
            debug_enabled=False,
        ),
    )

    with Image.open(native_64.final_path) as image:
        upscaled = image.convert("RGBA").resize((128, 128), Image.Resampling.NEAREST)
    with Image.open(native_128.final_path) as image:
        actual = image.convert("RGBA")
        assert actual.size == (128, 128)
        assert actual.tobytes() != upscaled.tobytes()

    metadata = json.loads((native_128.final_path.parent / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["output_canvas"] == {"width": 128, "height": 128}
    assert metadata["analysis_canvas"] == {"width": 256, "height": 256}
    assert metadata["character_layout"] == {
        "frame_width": 108,
        "frame_height": 108,
        "bottom_margin": 14,
        "reference_canvas": [64, 64],
    }
    assert metadata["native_resolution"] is True


def test_native_character_rectangular_canvas_keeps_all_output_dimensions_in_sync(tmp_path: Path) -> None:
    result = PixelTileCompiler().compile_image(
        _native_fixture(),
        compiler_config_for_purpose(
            "character",
            output_root=tmp_path / "native-rectangular",
            canvas=CanvasSpec(128, 96),
            palette_budget=24,
            character_detail_level="balanced",
            debug_enabled=False,
        ),
    )

    assert Image.open(result.final_path).size == (128, 96)
    assert json.loads((result.final_path.parent / "ir.json").read_text(encoding="utf-8"))["width"] == 128
    assert json.loads((result.final_path.parent / "ir.json").read_text(encoding="utf-8"))["height"] == 96
    assert Image.open(result.final_path.parent / "baseline_nearest.png").size == (128, 96)
    assert Image.open(result.final_path.parent / "baseline_bicubic_quantized.png").size == (128, 96)
    metadata = json.loads((result.final_path.parent / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["output_canvas"] == {"width": 128, "height": 96}
    assert metadata["character_layout"]["frame_width"] == 108
    assert metadata["character_layout"]["frame_height"] == 81
    assert metadata["character_layout"]["bottom_margin"] == 11
    assert metadata["character_layout"]["frame_height"] + metadata["character_layout"]["bottom_margin"] <= 96


def test_default_character_64_output_is_frozen(tmp_path: Path) -> None:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    for y in range(16, 80):
        for x in range(24, 72):
            image.putpixel((x, y), (90, 110, 170, 255))
    image.putpixel((48, 40), (100, 120, 180, 255))

    result = PixelTileCompiler().compile_image(
        image,
        compiler_config_for_purpose("character", output_root=tmp_path / "freeze", palette_budget=24, work_size=64, debug_enabled=False),
    )

    assert hashlib.sha256(result.final_path.read_bytes()).hexdigest() == "3a1c3a0d7b487763b75810fa8ba156c13c1e8fe2a1feb91f4004416df0d46f58"
