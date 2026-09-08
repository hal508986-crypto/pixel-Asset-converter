from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    CharacterAnimationTransform,
    _resolve_animation_preview_scale,
    align_character_frames,
    analyze_frame_alpha,
    compile_character_animation_sheet,
    load_character_animation_transform,
    resolve_action_scale,
    save_character_animation_transform,
)
from pixel_tile_compiler.pixelizer.character_detail import simplify_character_detail
from pixel_tile_compiler.cli import app
from pixel_tile_compiler.config import CanvasSpec


def _motion_frames() -> tuple[Image.Image, Image.Image]:
    frames = []
    for jump in (0, 20):
        image = Image.new("RGBA", (64, 64), (12, 34, 56, 0))
        for y in range(20 - jump, 50 - jump):
            for x in range(24, 40):
                image.putpixel((x, y), (48, 120, 196, 255))
        for y in range(50 - jump, 57 - jump):
            for x in range(31, 34):
                image.putpixel((x, y), (220, 180, 64, 255))
        frames.append(image)
    return tuple(frames)  # type: ignore[return-value]


def _preserve_config(canvas_size: tuple[int, int] = (256, 192)) -> CharacterAnimationConfig:
    return CharacterAnimationConfig(
        frame_count=2,
        canvas_size=canvas_size,
        placement_mode="preserve_motion",
        source_origin=(32, 50),
        output_origin=(128, 180),
        scale_override=0.5,
        remove_isolated_components=False,
        shared_palette_enabled=True,
    )


def test_preserve_jump_translation_uses_one_transform_and_does_not_reground() -> None:
    result = align_character_frames(_motion_frames(), _preserve_config())

    first_box = result.frame_reports[0].placed_bbox
    second_box = result.frame_reports[1].placed_bbox
    assert result.scale == 0.5
    assert first_box is not None and second_box is not None
    assert first_box.top - second_box.top == 10
    assert first_box.bottom - second_box.bottom == 10
    assert first_box.bottom > 180
    assert second_box.bottom < first_box.bottom
    assert result.report_as_dict()["transform"]["sampling_rounding"] == "floor(v + 0.5)"


@pytest.mark.parametrize("canvas_size", [(256, 192), (512, 320), (257, 193)])
def test_native_rectangular_animation_keeps_frame_and_sheet_dimensions(
    tmp_path: Path, canvas_size: tuple[int, int]
) -> None:
    source = tmp_path / "battle-source.png"
    frames = _motion_frames()
    sheet = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    sheet.alpha_composite(frames[0], (0, 0))
    sheet.alpha_composite(frames[1], (64, 0))
    sheet.save(source)

    config = _preserve_config(canvas_size)
    config = CharacterAnimationConfig(
        **{
            **config.__dict__,
            "source_origin": (32, 50),
            "output_origin": (canvas_size[0] // 2, canvas_size[1] - 12),
        }
    )
    result = compile_character_animation_sheet(
        source,
        tmp_path / "output",
        config=config,
        palette_budget=36,
    )

    assert all(Image.open(path).size == canvas_size for path in result.final_frame_paths)
    assert Image.open(result.compiled_sheet_path).size == (canvas_size[0] * 2, canvas_size[1])
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["output_frame_size"] == list(canvas_size)
    assert report["output_sheet_size"] == [canvas_size[0] * 2, canvas_size[1]]


def test_missing_preserve_motion_anchor_is_rejected() -> None:
    with pytest.raises(ValueError, match="原点"):
        align_character_frames(
            _motion_frames(),
            CharacterAnimationConfig(frame_count=2, placement_mode="preserve_motion"),
        )


@pytest.mark.parametrize("width,height", [(True, 64), (64, 1.5), ("256", 192)])
def test_canvas_dimensions_reject_bool_float_and_string_values(width, height) -> None:
    with pytest.raises(ValueError, match="canvas dimensions"):
        CanvasSpec(width, height)


def test_fixed_scale_rejects_anchor_extent_clipping_instead_of_shrinking() -> None:
    config = CharacterAnimationConfig(
        frame_count=2,
        canvas_size=(64, 64),
        placement_mode="preserve_motion",
        source_origin=(32, 50),
        output_origin=(2, 60),
        scale_override=1.0,
        remove_isolated_components=False,
    )

    with pytest.raises(ValueError, match="見切れ|clipped"):
        align_character_frames(_motion_frames(), config)


def test_shared_sampling_phase_rounds_negative_and_half_boundary_deterministically() -> None:
    transform = CharacterAnimationTransform(
        source_origin=(0, 0), output_origin=(0, 0), scale=1.0, frame_offsets=((-1, 0), (0, 0))
    )

    assert transform.map_source_point((-1, 0), frame_index=0) == (-2, 0)
    assert transform.map_source_point(-0.5, 0, frame_index=1) == (0, 0)
    assert transform.map_source_point(0.5, 0, frame_index=1) == (1, 0)


def test_transform_can_be_saved_and_reloaded_without_changing_the_mapping(tmp_path: Path) -> None:
    transform = CharacterAnimationTransform(
        source_origin=(32, 50),
        output_origin=(128, 180),
        scale=0.5,
        frame_offsets=((0, 0), (2, -1)),
    )
    path = tmp_path / "transform.json"
    save_character_animation_transform(transform, path)
    reloaded = load_character_animation_transform(path)
    assert reloaded == transform
    assert reloaded.map_source_point((40, 50), frame_index=1) == (134, 179)


def test_cross_action_scale_calibration_requires_explicit_reference_lengths() -> None:
    assert resolve_action_scale(target_reference_length=40, source_reference_length=80) == 0.5
    with pytest.raises(ValueError, match="基準長"):
        resolve_action_scale(target_reference_length=None, source_reference_length=80)


def test_cli_exposes_battle_canvas_origin_scale_and_shared_palette(tmp_path: Path) -> None:
    source = tmp_path / "cli-battle.png"
    sheet = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    for frame_index in range(2):
        for y in range(20, 50):
            for x in range(24 + frame_index * 64, 40 + frame_index * 64):
                sheet.putpixel((x, y), (48, 120, 196, 255))
    sheet.save(source)

    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(tmp_path / "cli-output"),
            "--frames",
            "2",
            "--width",
            "256",
            "--height",
            "192",
            "--placement-mode",
            "preserve_motion",
            "--source-origin",
            "32,50",
            "--output-origin",
            "128,180",
            "--scale",
            "0.5",
            "--shared-palette",
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads((tmp_path / "cli-output" / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["placement_mode"] == "preserve_motion"
    assert report["transform"]["output_origin"] == [128.0, 180.0]
    assert report["shared_palette"]["enabled"] is True


def test_cli_transform_file_selects_motion_mode(tmp_path: Path) -> None:
    source = tmp_path / "cli-transform-source.png"
    sheet = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    for frame_index in range(2):
        for y in range(8, 24):
            for x in range(8 + frame_index * 32, 24 + frame_index * 32):
                sheet.putpixel((x, y), (48, 120, 196, 255))
    sheet.save(source)
    transform_path = tmp_path / "transform.json"
    save_character_animation_transform(
        CharacterAnimationTransform(
            source_origin=(16, 24), output_origin=(128, 180), scale=1.0
        ),
        transform_path,
    )

    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(tmp_path / "transform-output"),
            "--frames",
            "2",
            "--width",
            "256",
            "--height",
            "192",
            "--transform",
            str(transform_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads(
        (tmp_path / "transform-output" / "bbox_report.json").read_text(encoding="utf-8")
    )
    assert report["placement_mode"] == "preserve_motion"


def test_gui_exposes_battle_motion_geometry_controls(monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app_qt = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app_qt.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        app_qt.processEvents()
        assert window.animation_width.isVisible()
        assert window.animation_height.isVisible()
        assert window.animation_source_origin_x.isVisible()
        assert window.animation_output_origin_y.isVisible()
    finally:
        window.close()


def test_animation_gui_profile_accepts_battle_rectangular_canvas() -> None:
    from pixel_tile_compiler.gui.policy import resolve_character_animation_gui_profile

    profile = resolve_character_animation_gui_profile((256, 192))
    assert profile.canvas_size == (256, 192)
    assert profile.fit_within == (216, 162)
    assert profile.bottom_margin == 18


def test_shared_visible_palette_is_used_by_every_final_frame(tmp_path: Path) -> None:
    frames = []
    for color in ((220, 60, 60), (60, 180, 90)):
        image = Image.new("RGBA", (32, 32), (7, 11, 19, 0))
        for y in range(8, 24):
            for x in range(8, 24):
                image.putpixel((x, y), (*color, 255))
        frames.append(image)

    result = align_character_frames(
        tuple(frames),
        CharacterAnimationConfig(
            frame_count=2,
            canvas_size=(64, 64),
            placement_mode="preserve_motion",
            source_origin=(16, 24),
            output_origin=(32, 56),
            scale_override=1.0,
            remove_isolated_components=False,
            shared_palette_enabled=True,
        ),
    )
    source = tmp_path / "shared-palette.png"
    sheet = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    sheet.alpha_composite(frames[0], (0, 0))
    sheet.alpha_composite(frames[1], (32, 0))
    sheet.save(source)
    compiled = compile_character_animation_sheet(
        source,
        tmp_path / "shared-output",
        config=result.config,
        palette_budget=24,
    )
    report = json.loads(compiled.report_path.read_text(encoding="utf-8"))
    assert report["shared_palette"]["enabled"] is True
    shared = {tuple(color) for color in report["shared_palette"]["colors"]}
    assert shared
    for path in compiled.final_frame_paths:
        image = Image.open(path).convert("RGBA")
        assert {pixel[:3] for pixel in image.getdata() if pixel[3] != 0} <= shared


def test_protected_pixel_survives_alpha_preprocess_and_detail_cleanup() -> None:
    source = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    source.putpixel((5, 5), (104, 104, 104, 64))
    source.putpixel((4, 5), (100, 100, 100, 255))
    source.putpixel((6, 5), (100, 100, 100, 255))
    source.putpixel((5, 4), (100, 100, 100, 255))
    source.putpixel((5, 6), (100, 100, 100, 255))
    protected = Image.new("L", source.size, 0)
    protected.putpixel((5, 5), 255)

    cleaned, bbox = analyze_frame_alpha(
        source,
        alpha_threshold=128,
        remove_isolated_components=True,
        min_component_area_px=3,
        protected_mask=protected,
    )
    assert bbox is not None
    assert cleaned.getpixel((5, 5))[3] == 255
    simplified = simplify_character_detail(
        cleaned,
        "sparse",
        canvas_size=(64, 64),
        protected_mask=protected,
    )
    assert simplified.getpixel((5, 5)) == cleaned.getpixel((5, 5))


def test_rgba_protected_mask_keeps_alpha_scope_through_motion_alignment() -> None:
    frame = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for y in range(4, 9):
        for x in range(4, 9):
            frame.putpixel((x, y), (100, 100, 100, 255))
    protected = Image.new("RGBA", frame.size, (255, 255, 255, 0))
    protected_alpha = Image.new("L", frame.size, 0)
    protected_alpha.putpixel((5, 5), 255)
    protected.putalpha(protected_alpha)

    result = align_character_frames(
        (frame,),
        CharacterAnimationConfig(
            frame_count=1,
            canvas_size=(64, 64),
            placement_mode="preserve_motion",
            source_origin=(10, 10),
            output_origin=(32, 32),
            scale_override=1.0,
            remove_isolated_components=False,
        ),
        protected_masks=(protected,),
    )

    aligned_mask = result.protected_masks[0]
    assert aligned_mask is not None
    assert aligned_mask.getpixel((27, 27))[3] > 0
    assert aligned_mask.getpixel((28, 27))[3] == 0


def test_detail_bridge_protection_and_padding_independence() -> None:
    source = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    for x in (2, 3, 4, 5, 6):
        source.putpixel((x, 4), (100, 100, 100, 255))
    source.putpixel((4, 4), (108, 108, 108, 255))
    source.putpixel((3, 3), (100, 100, 100, 255))
    source.putpixel((5, 5), (100, 100, 100, 255))

    result_64 = simplify_character_detail(
        source, "sparse", canvas_size=(64, 64), scale_with_canvas=False
    )
    result_128 = simplify_character_detail(
        source, "sparse", canvas_size=(128, 128), scale_with_canvas=False
    )
    assert result_64.tobytes() == result_128.tobytes()
    assert result_64.getpixel((4, 4)) == (108, 108, 108, 255)


def test_legacy_animation_palette_budget_is_checked_per_frame_when_not_shared(tmp_path: Path) -> None:
    palettes = (
        ((220, 40, 40), (180, 30, 30), (140, 20, 20), (100, 10, 10)),
        ((40, 220, 80), (30, 180, 70), (20, 140, 60), (10, 100, 50)),
    )
    sheet = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for frame_index, palette in enumerate(palettes):
        for index, color in enumerate(palette):
            left = frame_index * 20 + (index % 2) * 10
            top = (index // 2) * 10
            for y in range(top, top + 10):
                for x in range(left, left + 10):
                    sheet.putpixel((x, y), (*color, 255))
    source = tmp_path / "legacy-palette.png"
    sheet.save(source)

    result = compile_character_animation_sheet(
        source,
        tmp_path / "legacy-palette-output",
        config=CharacterAnimationConfig(
            frame_count=2,
            grid_columns=2,
            grid_rows=1,
            canvas_size=(32, 32),
            fit_within=(28, 28),
            bottom_margin=2,
            remove_isolated_components=False,
            shared_palette_enabled=False,
        ),
        palette_budget=4,
        character_detail_level="detailed",
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["final_palette"]["scope"] == "frame"
    assert report["final_palette"]["within_budget"] is True
    assert report["final_palette"]["frame_color_counts"] == [4, 4]


def test_auto_fit_adds_negative_offset_to_left_constraint() -> None:
    frame = Image.new("RGBA", (20, 20), (80, 140, 220, 255))

    result = align_character_frames(
        (frame,),
        CharacterAnimationConfig(
            frame_count=1,
            canvas_size=(64, 64),
            placement_mode="preserve_motion",
            source_origin=(10, 10),
            output_origin=(32, 32),
            scale_override=None,
            frame_offsets=((-10, 0),),
            remove_isolated_components=False,
        ),
    )

    assert result.scale == pytest.approx(2.2)


def test_saved_report_relocates_final_frame_paths_out_of_staging(tmp_path: Path) -> None:
    source = tmp_path / "report-paths.png"
    frame = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    for y in range(3, 15):
        for x in range(5, 13):
            frame.putpixel((x, y), (80, 140, 220, 255))
    frame.save(source)
    output = tmp_path / "report-paths-output"

    result = compile_character_animation_sheet(
        source,
        output,
        config=CharacterAnimationConfig(frame_count=1),
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    saved_path = Path(report["final_frames"][0]["path"])
    assert saved_path == result.final_frame_paths[0]
    assert saved_path.exists()
    assert ".staging-" not in str(saved_path)


def test_preview_scale_is_capped_for_large_animation_sheets() -> None:
    assert _resolve_animation_preview_scale((256, 192), max_preview_pixels=8_000_000) == 8
    assert _resolve_animation_preview_scale((512 * 16, 320), max_preview_pixels=8_000_000) == 1


def test_animation_sheet_pixel_cap_fails_before_frame_compilation(tmp_path: Path) -> None:
    source = tmp_path / "oversized-sheet.png"
    frame = Image.new("RGBA", (20, 20), (80, 140, 220, 255))
    frame.save(source)

    with pytest.raises(ValueError, match="総画素数が上限"):
        compile_character_animation_sheet(
            source,
            tmp_path / "oversized-output",
            config=CharacterAnimationConfig(
                frame_count=1,
                canvas_size=(4097, 4096),
                fit_within=(54, 54),
                bottom_margin=6,
            ),
        )


def test_animation_pixel_cap_fails_before_large_canvas_allocation(monkeypatch, tmp_path: Path) -> None:
    import pixel_tile_compiler.pixelizer.character_animation as animation_module

    source = tmp_path / "allocation-cap.png"
    Image.new("RGBA", (20, 20), (80, 140, 220, 255)).save(source)
    actual_new = animation_module.Image.new
    large_allocations: list[tuple[int, int]] = []

    def tracking_new(mode, size, *args, **kwargs):
        if size[0] * size[1] > animation_module.MAX_ANIMATION_OUTPUT_SHEET_PIXELS:
            large_allocations.append(size)
            raise AssertionError(f"large output allocation occurred: {size}")
        return actual_new(mode, size, *args, **kwargs)

    monkeypatch.setattr(animation_module.Image, "new", tracking_new)
    with pytest.raises(ValueError, match="総画素数が上限"):
        compile_character_animation_sheet(
            source,
            tmp_path / "allocation-cap-output",
            config=CharacterAnimationConfig(
                frame_count=1,
                canvas_size=(4097, 4096),
                fit_within=(4090, 4090),
                bottom_margin=0,
                remove_isolated_components=False,
            ),
        )

    assert large_allocations == []


@pytest.mark.parametrize(
    "fixture_name",
    (
        "battle_animation_generated_fixture.png",
        "battle_animation_review_fixture.png",
        "origin_mapping_generated_fixture.png",
        "palette_unification_generated_fixture.png",
    ),
)
def test_imagegen_fixture_runs_through_battle_animation_smoke(tmp_path: Path, fixture_name: str) -> None:
    source = Path("assets/test") / fixture_name
    if not source.exists():
        pytest.skip("local imagegen fixture is intentionally Git-ignored")
    result = compile_character_animation_sheet(
        source,
        tmp_path / "imagegen-output",
        config=CharacterAnimationConfig(
            frame_count=4,
            canvas_size=(256, 192),
            placement_mode="preserve_motion",
            source_origin=(260, 700),
            output_origin=(128, 180),
            scale_override=0.2,
            split_mode="fixed_grid",
            grid_columns=4,
            grid_rows=1,
            remove_isolated_components=False,
            shared_palette_enabled=True,
        ),
        palette_budget=36,
    )
    assert all(Image.open(path).size == (256, 192) for path in result.final_frame_paths)
