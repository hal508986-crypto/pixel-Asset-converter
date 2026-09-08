from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.config import CanvasSpec, CompilerConfig
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer import character_animation as character_animation_module
from pixel_tile_compiler.pixelizer.character_animation import (
    AlphaBoundingBox,
    CharacterAnimationConfig,
    align_character_frames,
    analyze_frame_alpha,
    compile_character_animation_sheet,
    prepare_character_animation_sheet,
)


def _frame_with_visible_box(
    box: tuple[int, int, int, int],
    *,
    size: tuple[int, int] = (20, 12),
) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            image.putpixel((x, y), (80, 140, 220, 255))
    return image


def test_analyze_frame_alpha_thresholds_and_removes_tiny_islands() -> None:
    image = _frame_with_visible_box((3, 4, 8, 9))
    image.putpixel((1, 1), (255, 0, 0, 8))
    image.putpixel((10, 2), (255, 0, 0, 255))
    image.putpixel((11, 2), (255, 0, 0, 255))

    cleaned, bbox = analyze_frame_alpha(image, alpha_threshold=16, min_component_area_px=3)

    assert bbox == AlphaBoundingBox(3, 4, 8, 9)
    assert cleaned.getchannel("A").getbbox() == (3, 4, 8, 9)
    assert set(cleaned.getchannel("A").getdata()) <= {0, 255}


def test_align_character_frames_uses_union_scale_and_fixed_foot_baseline() -> None:
    first = _frame_with_visible_box((4, 2, 9, 8))
    second = _frame_with_visible_box((5, 1, 11, 9))
    config = CharacterAnimationConfig(
        frame_count=2,
        canvas_size=(32, 32),
        fit_within=(20, 20),
        bottom_margin=3,
        padding_px=1,
    )

    result = align_character_frames((first, second), config)

    assert result.union_bbox == AlphaBoundingBox(3, 0, 12, 10)
    assert result.scale == 2.0
    assert [frame.size for frame in result.aligned_frames] == [(32, 32), (32, 32)]
    assert [frame.getchannel("A").getbbox()[3] for frame in result.aligned_frames] == [29, 29]
    assert result.frame_reports[0].bbox == AlphaBoundingBox(4, 2, 9, 8)
    assert result.frame_reports[1].bbox == AlphaBoundingBox(5, 1, 11, 9)


def test_prepare_character_animation_sheet_splits_non_divisible_width_deterministically() -> None:
    sheet = Image.new("RGBA", (19, 8), (0, 0, 0, 0))
    config = CharacterAnimationConfig(frame_count=4)

    result = prepare_character_animation_sheet(sheet, config)

    assert result.crop_box == (1, 0, 17, 8)
    assert len(result.aligned_frames) == 4
    assert {report.status for report in result.frame_reports} == {"empty"}


def test_prepare_character_animation_sheet_can_reject_non_divisible_width() -> None:
    sheet = Image.new("RGBA", (19, 8), (0, 0, 0, 0))

    try:
        prepare_character_animation_sheet(sheet, CharacterAnimationConfig(remainder_policy="error"))
    except ValueError as exc:
        assert "divisible" in str(exc)
    else:
        raise AssertionError("non-divisible sheet width must be rejected in error mode")


def test_compiler_can_preserve_pre_aligned_character_frames(tmp_path: Path) -> None:
    source = _frame_with_visible_box((8, 4, 16, 12), size=(32, 32))
    result = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(
            output_root=tmp_path / "aligned",
            canvas=CanvasSpec(32, 32),
            palette_budget=16,
            tile_mode="object",
            pixelization_mode="nearest",
            character_input_mode="pre_aligned",
            repeat_opt_enabled=False,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    assert Image.open(result.final_path).getchannel("A").getbbox() == (8, 4, 16, 12)


def test_compile_character_animation_collects_renamed_final_frames(tmp_path: Path) -> None:
    source = tmp_path / "idle-sheet.png"
    sheet = Image.new("RGBA", (80, 20), (0, 0, 0, 0))
    for frame in range(4):
        left = frame * 20 + 5
        for y in range(3, 15):
            for x in range(left, left + 8):
                sheet.putpixel((x, y), (80, 140, 220, 255))
    sheet.save(source)

    result = compile_character_animation_sheet(
        source,
        tmp_path / "output",
        config=CharacterAnimationConfig(frame_count=4),
        debug_enabled=True,
    )

    assert [path.name for path in result.final_frame_paths] == [
        "F1_final.png",
        "F2_final.png",
        "F3_final.png",
        "F4_final.png",
    ]
    assert all(path.parent.name == "final_frames" for path in result.final_frame_paths)
    assert all(path.exists() for path in result.final_frame_paths)
    assert result.detection_overlay_path is not None
    assert result.detection_overlay_path.exists()


def test_compile_character_animation_removes_stale_renamed_frames_on_rerun(tmp_path: Path) -> None:
    source = tmp_path / "idle-sheet.png"
    sheet = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for frame in range(2):
        left = frame * 20 + 5
        for y in range(3, 15):
            for x in range(left, left + 8):
                sheet.putpixel((x, y), (80, 140, 220, 255))
    sheet.save(source)
    output = tmp_path / "output"

    first = compile_character_animation_sheet(
        source,
        output,
        config=CharacterAnimationConfig(frame_count=2),
    )
    assert len(first.final_frame_paths) == 2
    assert first.final_frame_paths[1].exists()
    user_notes = output / "user_notes.txt"
    user_notes.write_text("keep this file", encoding="utf-8")

    one_frame = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for y in range(3, 15):
        for x in range(5, 13):
            one_frame.putpixel((x, y), (80, 140, 220, 255))
    one_frame.save(source)
    second = compile_character_animation_sheet(
        source,
        output,
        config=CharacterAnimationConfig(frame_count=1),
    )

    assert len(second.final_frame_paths) == 1
    assert second.final_frame_paths[0].exists()
    assert not (output / "final_frames" / "F2_final.png").exists()
    assert not (output / "compiled" / "F2" / "final.png").exists()
    assert user_notes.read_text(encoding="utf-8") == "keep this file"


def test_compile_character_animation_failure_preserves_previous_success_output(tmp_path: Path) -> None:
    source = tmp_path / "idle-sheet.png"
    sheet = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for y in range(3, 15):
        for x in range(5, 13):
            sheet.putpixel((x, y), (80, 140, 220, 255))
    sheet.save(source)
    output = tmp_path / "output"

    compile_character_animation_sheet(
        source,
        output,
        config=CharacterAnimationConfig(frame_count=1),
    )
    previous_final = output / "final_frames" / "F1_final.png"
    previous_bytes = previous_final.read_bytes()

    with pytest.raises(ValueError, match="palette_budget"):
        compile_character_animation_sheet(
            source,
            output,
            config=CharacterAnimationConfig(frame_count=1),
            palette_budget=3,
        )

    assert previous_final.exists()
    assert previous_final.read_bytes() == previous_bytes
    assert (output / "compiled" / "F1" / "final.png").exists()


def test_output_replacement_preserves_backup_when_restore_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    (staging_root / "aligned_sheet.png").write_bytes(b"new")
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "aligned_sheet.png").write_bytes(b"old")

    original_rename = Path.rename

    def fail_install_and_restore(self: Path, target: Path) -> Path:
        if self == staging_root / "aligned_sheet.png":
            raise OSError("injected install rename failure")
        if self.name == "aligned_sheet.png" and self.parent.name.startswith(".output.previous-"):
            raise OSError("injected restore rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_install_and_restore)

    with pytest.raises(RuntimeError, match="復旧用バックアップを保持しています") as exc_info:
        character_animation_module._replace_output_root(staging_root, output_root)

    backup_roots = list(tmp_path.glob(".output.previous-*"))
    assert len(backup_roots) == 1
    assert (backup_roots[0] / "aligned_sheet.png").read_bytes() == b"old"
    assert str(backup_roots[0]) in str(exc_info.value)
    assert not (output_root / "aligned_sheet.png").exists()
