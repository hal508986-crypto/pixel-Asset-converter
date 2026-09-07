import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.character_study.config import load_character_study_config
from pixel_tile_compiler.character_study.native_resolution import (
    NativeResolutionStudyRunner,
    load_native_resolution_study_config,
)
from pixel_tile_compiler.character_study.runner import CharacterPaletteDensityStudyRunner


def _write_character_source(path: Path) -> None:
    image = Image.new("RGBA", (128, 128), (240, 240, 240, 0))
    for y in range(16, 112):
        for x in range(32, 96):
            image.putpixel((x, y), (70, 100, 160, 255))
    image.putpixel((62, 46), (240, 210, 170, 255))
    image.putpixel((66, 46), (240, 210, 170, 255))
    image.putpixel((64, 55), (30, 40, 80, 255))
    image.save(path)


def test_character_study_generates_a_3_by_3_matrix_and_review_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "character.png"
    _write_character_source(source)
    config_path = tmp_path / "study.yaml"
    config_path.write_text(
        """
study:
  output_root: output
  palette_budgets: [16, 24, 32]
  detail_levels: [sparse, balanced, detailed]
  cases:
    - id: synthetic_front
      source: character.png
      review_features: [eyes, ribbon, boots]
  character:
    frame_width: 54
    frame_height: 54
    bottom_margin: 7
    background_mode: auto
    outline: off
  seed: 42
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_character_study_config(config_path)
    result = CharacterPaletteDensityStudyRunner().run(config)
    case_root = result.output_root / "synthetic_front"
    manifest = json.loads((case_root / "manifest.json").read_text(encoding="utf-8"))
    records = manifest["cells"]

    assert len(records) == 9
    assert (case_root / "source_snapshot.png").read_bytes() == source.read_bytes()
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (case_root / "previews" / "comparison_board.png").exists()
    assert (case_root / "previews" / "comparison_board_8x.png").exists()
    assert (case_root / "metrics" / "matrix_metrics.json").exists()
    assert (case_root / "review" / "review_template.json").exists()
    assert {record["detail_level"] for record in records} == {"sparse", "balanced", "detailed"}
    assert {record["palette_budget"] for record in records} == {16, 24, 32}

    for record in records:
        image = Image.open(case_root / record["relative_final_path"])
        assert image.size == (64, 64)
        assert record["actual_palette_count"] <= record["palette_budget"]

    for budget in (16, 24, 32):
        same_budget = [record for record in records if record["palette_budget"] == budget]
        alpha = [Image.open(case_root / record["relative_final_path"]).getchannel("A").tobytes() for record in same_budget]
        assert alpha[0] == alpha[1] == alpha[2]


def test_character_study_config_rejects_non_study_palette_budgets(tmp_path: Path) -> None:
    source = tmp_path / "character.png"
    _write_character_source(source)
    config_path = tmp_path / "study.json"
    config_path.write_text(
        json.dumps(
            {
                "study": {
                    "output_root": "output",
                    "palette_budgets": [12],
                    "cases": [{"id": "case", "source": "character.png"}],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="palette_budgets"):
        load_character_study_config(config_path)


def test_native_resolution_study_produces_direct_128_control_and_review_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "character-native.png"
    image = Image.new("RGBA", (160, 120), (245, 245, 245, 0))
    for y in range(10, 108):
        for x in range(42, 118):
            image.putpixel((x, y), (66 + (x % 5) * 9, 80 + (y % 7) * 6, 150, 255))
    for x, y in ((61, 39), (93, 39), (77, 53), (53, 75), (101, 75), (77, 91)):
        image.putpixel((x, y), (240, 210, 170, 255))
    image.save(source)
    config_path = tmp_path / "native-study.json"
    config_path.write_text(
        json.dumps(
            {
                "study": {
                    "output_root": "output",
                    "source": "character-native.png",
                    "palette_budget": 24,
                    "detail_level": "balanced",
                    "review_features": ["eyes", "ribbon", "boots"],
                    "seed": 42,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_native_resolution_study_config(config_path)
    result = NativeResolutionStudyRunner().run(config)
    root = result.output_root
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "metrics" / "resolution_metrics.json").read_text(encoding="utf-8"))

    native_64 = Image.open(root / "native" / "64x64" / "final.png").convert("RGBA")
    native_128 = Image.open(root / "native" / "128x128" / "final.png").convert("RGBA")
    control = Image.open(root / "controls" / "64x64_upscaled_to_128.png").convert("RGBA")

    assert native_64.size == (64, 64)
    assert native_128.size == (128, 128)
    assert control.size == (128, 128)
    assert native_128.tobytes() != control.tobytes()
    assert metrics["native_128_equal_to_64_upscaled"] is False
    assert metrics["native_128_changed_pixels_vs_upscaled_64"] > 0
    assert manifest["preset"] == "b24"
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["native_outputs"]["128x128"]["character_layout"] == {
        "frame_width": 108,
        "frame_height": 108,
        "bottom_margin": 14,
    }
    assert json.loads((root / "native" / "128x128" / "metadata.json").read_text(encoding="utf-8"))["native_resolution"] is True
    assert (root / "previews" / "comparison_128_canvas.png").exists()
    assert (root / "previews" / "native_64_actual.png").exists()
    assert (root / "previews" / "native_128_actual.png").exists()
    assert (root / "review" / "review_template.json").exists()
