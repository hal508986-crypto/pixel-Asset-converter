from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.palette import extract_palette


OUTPUT_SIZE = (64, 64)
PALETTE_BUDGET = 24


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_repeat_preview(image: Image.Image, destination: Path, *, scale: int = 3) -> None:
    tile = image.convert("RGBA")
    repeated = Image.new("RGBA", (tile.width * 3, tile.height * 3), (0, 0, 0, 0))
    for row in range(3):
        for column in range(3):
            repeated.paste(tile, (column * tile.width, row * tile.height))
    repeated.resize((repeated.width * scale, repeated.height * scale), Image.Resampling.NEAREST).save(destination)


def save_variant_preview(image_path: Path, root: Path) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as opened:
        image = opened.convert("RGBA")
    nearest_path = root / "preview_4x_nearest.png"
    repeat_path = root / "repeat_preview_3x3_3x_nearest.png"
    image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST).save(nearest_path)
    save_repeat_preview(image, repeat_path)
    return {
        "preview_4x_nearest": str(nearest_path),
        "repeat_preview_3x3_3x_nearest": str(repeat_path),
    }


def compile_variant(
    source: Path,
    output_root: Path,
    *,
    pixelization_mode: str,
    repeat_opt_enabled: bool,
    palette_colors: tuple[tuple[int, int, int], ...] | None,
) -> dict[str, object]:
    result = PixelTileCompiler().compile(
        source,
        compiler_config_for_purpose(
            "terrain",
            output_root=output_root,
            canvas=CanvasSpec(*OUTPUT_SIZE),
            palette_budget=PALETTE_BUDGET,
            pixelization_mode=pixelization_mode,
            repeat_opt_enabled=repeat_opt_enabled,
            palette_colors=palette_colors,
            debug_enabled=True,
        ),
    )
    previews = save_variant_preview(result.final_path, output_root / "comparison")
    return {
        "output_root": str(output_root),
        "final": str(result.final_path),
        "metadata": str(output_root / "metadata.json"),
        "mode": pixelization_mode,
        "repeat_opt_enabled": repeat_opt_enabled,
        "palette_source": "fixed_shared_palette" if palette_colors is not None else "GUI_default_quantizer",
        "previews": previews,
        "actual_palette_count": result.metrics.actual_palette_count,
    }


def compare(source: Path, output_root: Path) -> Path:
    source = source.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise FileExistsError(f"comparison output directory must be empty: {output_root}")
    else:
        output_root.mkdir(parents=True)
    source_copy = output_root / "source" / "fixed_source.png"
    source_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, source_copy)

    with Image.open(source) as opened:
        source_image = opened.convert("RGBA")
        reference = source_image.resize(OUTPUT_SIZE, Image.Resampling.NEAREST)
    reference_path = output_root / "reference_nearest_unlimited.png"
    reference.save(reference_path)
    shared_palette = extract_palette(source_image, budget=PALETTE_BUDGET)

    variants = {
        "source_preserving_24_repeat_off": compile_variant(
            source,
            output_root / "source_preserving_24_repeat_off",
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            palette_colors=shared_palette,
        ),
        "region_24_repeat_off": compile_variant(
            source,
            output_root / "region_24_repeat_off",
            pixelization_mode="region",
            repeat_opt_enabled=False,
            palette_colors=shared_palette,
        ),
        "source_preserving_24_repeat_on": compile_variant(
            source,
            output_root / "source_preserving_24_repeat_on",
            pixelization_mode="nearest",
            repeat_opt_enabled=True,
            palette_colors=shared_palette,
        ),
        "gui_default_source_preserving_24_repeat_off": compile_variant(
            source,
            output_root / "gui_default_source_preserving_24_repeat_off",
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            palette_colors=None,
        ),
    }

    comparison = Image.new("RGBA", (OUTPUT_SIZE[0] * 4, OUTPUT_SIZE[1]), (0, 0, 0, 255))
    comparison_labels = (
        (reference_path, 0),
        (Path(variants["source_preserving_24_repeat_off"]["final"]), 1),
        (Path(variants["region_24_repeat_off"]["final"]), 2),
        (Path(variants["source_preserving_24_repeat_on"]["final"]), 3),
    )
    for path, column in comparison_labels:
        with Image.open(path) as opened:
            comparison.paste(opened.convert("RGBA"), (column * OUTPUT_SIZE[0], 0))
    comparison_path = output_root / "comparison_4way_4x.png"
    comparison.resize((comparison.width * 4, comparison.height * 4), Image.Resampling.NEAREST).save(comparison_path)

    manifest = {
        "source": {
            "path": str(source),
            "copy": str(source_copy),
            "sha256": sha256_file(source),
            "dimensions": list(source_image.size),
        },
        "output": {"dimensions": list(OUTPUT_SIZE)},
        "fixed_palette": {
            "budget": PALETTE_BUDGET,
            "colors": [list(color) for color in shared_palette],
            "extracted_from": "fixed source original RGBA pixels",
            "method": "extract_palette -> deterministic median-cut quantization",
        },
        "comparisons": {
            "reference_nearest_unlimited": str(reference_path),
            "four_way_preview": str(comparison_path),
            "variants": variants,
        },
        "visual_review_points": [
            "leaf-tip highlights",
            "dark root shadows",
            "grass cluster readability",
            "isolated point noise",
            "3x3 repeat seams",
        ],
        "scope_note": "One fixed source only; no general claim that nearest always beats region.",
    }
    manifest_path = output_root / "comparison_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare terrain source-preserving and region paths on one fixed source.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(compare(args.source, args.output))


if __name__ == "__main__":
    main()
