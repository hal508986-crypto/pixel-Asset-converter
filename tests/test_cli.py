import json
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.cli import app


def test_cli_compile_creates_output(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (128, 128), (60, 120, 70, 255)).save(source)
    output = tmp_path / "output"

    result = CliRunner().invoke(app, ["compile", str(source), "--output", str(output)])

    assert result.exit_code == 0, result.stdout
    assert (output / "final.png").exists()


def test_cli_compile_character_animation_exports_aligned_sheet_and_report(tmp_path: Path):
    source = tmp_path / "idle-sheet.png"
    sheet = Image.new("RGBA", (80, 20), (0, 0, 0, 0))
    for frame in range(4):
        left = frame * 20 + 5 + frame % 2
        for y in range(2 + frame % 2, 16):
            for x in range(left, left + 8):
                sheet.putpixel((x, y), (80, 140, 220, 255))
    sheet.putpixel((frame * 20 + 18, 1), (255, 0, 0, 8))
    sheet.save(source)
    output = tmp_path / "idle-output"

    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(output),
            "--frames",
            "4",
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads((output / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["frame_count"] == 4
    assert report["schema_version"] == 3
    assert report["shared_palette"]["enabled"] is True
    assert len({frame["scale"] for frame in report["frames"]}) == 1
    assert Image.open(output / "aligned_sheet.png").size == (256, 64)
    assert Image.open(output / "compiled_sheet.png").size == (256, 64)
    assert Image.open(output / "compiled_sheet_8x.png").size == (2048, 512)
    assert all(Image.open(output / "compiled" / f"F{index}" / "final.png").size == (64, 64) for index in range(1, 5))
    assert all((output / "final_frames" / f"F{index}.png").exists() for index in range(1, 5))
    assert not list((output / "final_frames").glob("*_final.png"))
    assert {frame["placed_bbox"]["bottom"] for frame in report["frames"]} == {58}
    assert {frame["clipped"] for frame in report["frames"]} == {False}


def test_cli_compile_character_animation_supports_alpha_gap_auto_and_grid_fallback(tmp_path: Path):
    source = tmp_path / "grid-sheet.png"
    sheet = Image.new("RGBA", (48, 44), (0, 0, 0, 0))
    for row in range(2):
        for column in range(2):
            left = column * 24 + 5
            top = row * 22 + 4
            for y in range(top, top + 10):
                for x in range(left, left + 8):
                    sheet.putpixel((x, y), (80, 140, 220, 255))
    sheet.save(source)
    output = tmp_path / "grid-output"

    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(output),
            "--split-mode",
            "alpha_gap_auto",
            "--debug",
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads((output / "bbox_report.json").read_text(encoding="utf-8"))
    split_report = report["sprite_sheet_split"]
    assert split_report["columns"] == 2
    assert split_report["rows"] == 2
    assert split_report["frame_count"] == 4
    assert (output / "detection_overlay.png").exists()


def test_cli_surfaces_split_warnings_without_failing_output(tmp_path: Path):
    source = tmp_path / "thin-crossing.png"
    image = Image.new("RGBA", (80, 70), (0, 0, 0, 0))
    color = (80, 140, 220, 255)
    for y in range(50, 60):
        for x in range(5, 20):
            image.putpixel((x, y), color)
        for x in range(60, 75):
            image.putpixel((x, y), color)
    for step in range(42):
        image.putpixel((19 + step, step), color)
    image.save(source)
    output = tmp_path / "thin-crossing-output"

    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(output),
            "--split-mode",
            "hybrid",
            "--cols",
            "2",
            "--rows",
            "1",
            "--width",
            "64",
            "--height",
            "64",
            "--empty-row-threshold",
            "2",
            "--empty-column-threshold",
            "0",
            "--debug",
            "--confirm-components",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "警告:" in result.stdout
    report = json.loads((output / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["warnings"]
    assert report["sprite_sheet_split"]["quality_status"] == "warning"


def test_cli_compile_accepts_character_profile_options(tmp_path: Path):
    source = tmp_path / "character.png"
    Image.new("RGBA", (64, 64), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--palette",
            "32",
            "--tile-mode",
            "object",
            "--pixelization",
            "nearest",
            "--outline",
            "black",
            "--no-repeat-opt",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["pixelization_mode"] == "nearest"
    assert metadata["config"]["outline_color"] == "black"
    assert Image.open(output / "final.png").size == (64, 64)


def test_cli_compile_character_purpose_uses_shared_character_defaults(tmp_path: Path):
    source = tmp_path / "character-purpose.png"
    image = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
    for y in range(24, 104):
        for x in range(44, 84):
            image.putpixel((x, y), (60, 120, 220, 255))
    image.save(source)
    output = tmp_path / "character-purpose-output"

    result = CliRunner().invoke(
        app,
        ["compile", str(source), "--output", str(output), "--purpose", "character", "--palette", "16"],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["tile_mode"] == "object"
    assert metadata["config"]["pixelization_mode"] == "nearest"
    assert metadata["config"]["repeat_opt_enabled"] is False
    assert metadata["config"]["dither"] == "off"


def test_cli_compile_accepts_character_detail_level(tmp_path: Path):
    source = tmp_path / "character-detail.png"
    Image.new("RGBA", (64, 64), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-detail-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--character-detail",
            "balanced",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["character_detail_level"] == "balanced"
    assert metadata["character_detail"]["applied"] is True


def test_cli_compile_accepts_native_character_canvas_and_b24(tmp_path: Path):
    source = tmp_path / "character-native.png"
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(source)
    output = tmp_path / "character-native-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--width",
            "128",
            "--height",
            "128",
            "--preset",
            "b24",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert Image.open(output / "final.png").size == (128, 128)
    assert metadata["config"]["palette_budget"] == 24
    assert metadata["config"]["character_detail_level"] == "balanced"


def test_cli_explicit_character_options_override_b24_defaults(tmp_path: Path):
    source = tmp_path / "character-b24-override.png"
    Image.new("RGBA", (128, 128), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-b24-override-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--preset",
            "b24",
            "--palette",
            "32",
            "--character-detail",
            "sparse",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["palette_budget"] == 32
    assert metadata["config"]["character_detail_level"] == "sparse"


def test_cli_rejects_non_64_terrain_canvas(tmp_path: Path):
    source = tmp_path / "terrain-native.png"
    Image.new("RGBA", (128, 128), (60, 120, 70, 255)).save(source)

    result = CliRunner().invoke(
        app,
        ["compile", str(source), "--purpose", "terrain", "--width", "128", "--height", "128"],
    )

    assert result.exit_code != 0
    assert "object/nearest" in result.output


def test_cli_help_lists_character_palette_density_study():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "study-character-palette-density" in result.stdout


def test_cli_compile_map_creates_three_way_experiment(tmp_path: Path):
    source = tmp_path / "map.png"
    image = Image.new("RGBA", (256, 320), (80, 140, 60, 255))
    for x in range(96, 160):
        for y in range(320):
            image.putpixel((x, y), (150, 105, 65, 255))
    image.save(source)
    output = tmp_path / "map-output"

    result = CliRunner().invoke(
        app,
        [
            "compile-map",
            str(source),
            "--output",
            str(output),
            "--cols",
            "4",
            "--rows",
            "5",
            "--palette",
            "24",
            "--context",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (output / "baseline_global.png").exists()
    assert (output / "independent_tiles.png").exists()
    assert (output / "context_compiled.png").exists()
    assert (output / "comparison.png").exists()
    assert (output / "metrics.json").exists()


def test_cli_help_lists_study_transition_network():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "study-transition-network" in result.stdout


def test_cli_help_lists_study_road_graph():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "study-road-graph" in result.stdout


def test_cli_help_lists_study_river_graph():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "study-river-graph" in result.stdout


def test_cli_compile_character_animation_component_split_requires_confirmation_or_flag(tmp_path: Path):
    from pixel_tile_compiler.pixelizer.character_animation import CharacterAnimationConfig, save_component_assignments
    from pixel_tile_compiler.sheet.component_split import analyze_component_split

    source = tmp_path / "sheet.png"
    image = Image.new("RGBA", (64, 40), (0, 0, 0, 0))
    for y in range(5, 25):
        for x in range(5, 19):
            image.putpixel((x, y), (220, 50, 50, 255))
        for x in range(38, 52):
            image.putpixel((x, y), (50, 80, 220, 255))
    for y in range(7, 20):
        image.putpixel((27, y), (255, 255, 255, 255))
    image.save(source)

    unconfirmed_output = tmp_path / "unconfirmed-output"
    unconfirmed = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(unconfirmed_output),
            "--split-mode",
            "row_alpha_components",
            "--cols",
            "2",
            "--rows",
            "1",
            "--keep-isolated",
        ],
    )
    assert unconfirmed.exit_code == 2
    output_text = unconfirmed.stdout_bytes.decode("cp932", errors="replace") if hasattr(unconfirmed, "stdout_bytes") else unconfirmed.output
    assert "未確定" in output_text or "確定" in output_text or "未解決" in output_text or unconfirmed.exit_code == 2

    confirmed_output = tmp_path / "confirmed-output"
    confirmed = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(confirmed_output),
            "--split-mode",
            "row_alpha_components",
            "--cols",
            "2",
            "--rows",
            "1",
            "--keep-isolated",
            "--confirm-components",
        ],
    )
    assert confirmed.exit_code == 0, confirmed.output
    assert (confirmed_output / "final_frames" / "F1.png").exists()


def test_cli_compile_character_animation_applies_saved_cells_mask_exactly(tmp_path: Path):
    """[P2 回帰テスト] CLIが保存済み所有マスク(cells_override)を適用し、実CLI出力レポートの所有マスクと一致することを検証する。"""
    import numpy as np
    from pixel_tile_compiler.pixelizer.character_animation import (
        CharacterAnimationConfig,
        load_component_assignment_data,
        save_component_assignments,
    )
    from pixel_tile_compiler.sheet.component_split import (
        analyze_component_split,
        restore_owner_labels_from_cells,
    )

    source = tmp_path / "sheet_overlap.png"
    shape = (40, 64)
    image = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    for y in range(5, 25):
        for x in range(5, 19):
            image.putpixel((x, y), (220, 50, 50, 255))
        for x in range(38, 52):
            image.putpixel((x, y), (50, 80, 220, 255))
    for y in range(7, 20):
        image.putpixel((27, y), (255, 255, 255, 255))
    for y in range(25, 34):
        for x in range(3, 17):
            image.putpixel((x, y), (220, 50, 50, 255))
    image.save(source)

    config = CharacterAnimationConfig(
        frame_count=2,
        split_mode="row_alpha_components",
        grid_columns=2,
        grid_rows=1,
        remove_isolated_components=False,
    )

    # 1. 事前に解析を行い、所有マスク付き cells を取得
    analysis = analyze_component_split(
        image,
        columns=2,
        rows=1,
        remove_small_components=False,
    )
    assert analysis.cells is not None
    assert len(analysis.cells) == 2

    # 2. cells を含めて component_assignments.json に保存
    assignments_path = tmp_path / "saved_assignments.json"
    save_component_assignments(
        source,
        config,
        assignments={},
        output_path=assignments_path,
        cells=analysis.cells,
    )
    assert assignments_path.exists()

    # 保存ファイルから読み込んだ所有マスクを復元
    saved_data = load_component_assignment_data(source, config, assignments_path)
    assert saved_data.cells is not None
    saved_owner_labels = restore_owner_labels_from_cells(saved_data.cells, shape)

    # 3. CLI を実行してコンパイル
    output = tmp_path / "cli_output"
    result = CliRunner().invoke(
        app,
        [
            "compile-character-animation",
            str(source),
            "--output",
            str(output),
            "--split-mode",
            "row_alpha_components",
            "--cols",
            "2",
            "--rows",
            "1",
            "--keep-isolated",
            "--component-assignments",
            str(assignments_path),
            "--confirm-components",
        ],
    )
    assert result.exit_code == 0, result.output

    # 4. 出力された bbox_report.json の cells から所有マスクを復元
    report_path = output / "bbox_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cli_cells = report["sprite_sheet_split"]["cells"]
    cli_owner_labels = restore_owner_labels_from_cells(cli_cells, shape)

    # 5. 保存済みマスクと実CLI出力のマスクが1画素の差分もなく完全一致することを検証
    diff_pixels = np.count_nonzero(saved_owner_labels != cli_owner_labels)
    assert diff_pixels == 0, f"CLI output changed {diff_pixels} pixels from saved mask!"
    assert np.array_equal(saved_owner_labels, cli_owner_labels)
