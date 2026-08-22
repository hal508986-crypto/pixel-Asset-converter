from pathlib import Path

from PIL import Image

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


def create_source(path: Path) -> None:
    image = Image.new("RGBA", (128, 128), (45, 110, 55, 255))
    for y in range(24, 104):
        for x in range(42, 86):
            image.putpixel((x, y), (130, 85, 45, 255))
    image.putpixel((0, 0), (45, 110, 55, 0))
    image.save(path)


def test_pipeline_exports_required_artifacts_and_is_deterministic(tmp_path: Path):
    source = tmp_path / "forest.png"
    create_source(source)
    compiler = PixelTileCompiler()
    config = CompilerConfig(output_root=tmp_path / "out", palette_budget=8, seed=42)

    first = compiler.compile(source, config)
    first_bytes = first.final_path.read_bytes()
    second = compiler.compile(source, config)

    final = Image.open(first.final_path)
    assert final.size == (64, 64)
    assert final.mode == "RGBA"
    assert first_bytes == second.final_path.read_bytes()
    assert first.ir_path.exists()
    assert (first.final_path.parent / "metadata.json").exists()
    assert {path.name for path in first.debug_paths.values()} >= {
        "01_normalized.png",
        "03_edges.png",
        "06_raw_pixelized.png",
        "08_tile_preview.png",
    }
    assert (first.final_path.parent / "baseline_nearest.png").exists()
    assert (first.final_path.parent / "baseline_bicubic_quantized.png").exists()


def test_pipeline_falls_back_when_semantic_provider_fails(tmp_path: Path):
    source = tmp_path / "forest.png"
    create_source(source)
    compiler = PixelTileCompiler()
    config = CompilerConfig(
        output_root=tmp_path / "out",
        semantic_provider="mcp",
        semantic_callable=lambda *_args: (_ for _ in ()).throw(RuntimeError("timeout")),
    )

    result = compiler.compile(source, config)

    assert result.final_path.exists()
    assert result.metadata["semantic_provider"] == "rule-fallback"


def test_pipeline_preserves_binary_alpha_and_supports_flat_background(tmp_path: Path):
    source = tmp_path / "flat-background.png"
    image = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
    for y in range(24, 104):
        for x in range(24, 104):
            image.putpixel((x, y), (40, 100, 50, 255))
    image.save(source)

    result = PixelTileCompiler().compile(
        source,
        CompilerConfig(
            output_root=tmp_path / "out",
            background_mode="color",
            background_color="#FFFFFF",
        ),
    )
    alpha_values = {pixel[3] for pixel in Image.open(result.final_path).getdata()}

    assert alpha_values <= {0, 255}
    assert 0 in alpha_values
