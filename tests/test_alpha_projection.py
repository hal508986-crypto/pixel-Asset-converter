from __future__ import annotations

import pytest
from PIL import Image

from pixel_tile_compiler.sheet.alpha_projection import split_sprite_sheet
from pixel_tile_compiler.pixelizer.character_animation import CharacterAnimationConfig, prepare_character_animation_sheet


def _grid_sheet(columns: int, rows: int, *, cell_size: tuple[int, int] = (20, 18), gap: int = 4) -> Image.Image:
    cell_width, cell_height = cell_size
    image = Image.new(
        "RGBA",
        (columns * cell_width + (columns - 1) * gap, rows * cell_height + (rows - 1) * gap),
        (0, 0, 0, 0),
    )
    for row in range(rows):
        for column in range(columns):
            left = column * (cell_width + gap) + 4
            top = row * (cell_height + gap) + 3
            for y in range(top, top + 8):
                for x in range(left, left + 7):
                    image.putpixel((x, y), (80 + column * 10, 140 + row * 10, 220, 255))
    return image


def test_alpha_gap_auto_detects_horizontal_and_matrix_grids() -> None:
    horizontal = split_sprite_sheet(_grid_sheet(4, 1), mode="alpha_gap_auto")
    matrix = split_sprite_sheet(_grid_sheet(4, 4), mode="alpha_gap_auto")

    assert (horizontal.columns, horizontal.rows, horizontal.frame_count) == (4, 1, 4)
    assert (matrix.columns, matrix.rows, matrix.frame_count) == (4, 4, 16)
    assert horizontal.fallback_used is False
    assert matrix.fallback_used is False
    assert len(horizontal.cells) == 4
    assert len(matrix.cells) == 16
    assert all(cell.valid for cell in matrix.cells)


def test_alpha_gap_auto_rejects_missing_gutters_and_hybrid_falls_back() -> None:
    connected = Image.new("RGBA", (32, 12), (80, 140, 220, 255))

    try:
        split_sprite_sheet(connected, mode="alpha_gap_auto")
    except ValueError as exc:
        assert "グリッド" in str(exc) or "gutter" in str(exc)
    else:
        raise AssertionError("alpha_gap_auto must reject a sheet without a detectable grid gap")

    result = split_sprite_sheet(connected, mode="hybrid", columns=4, rows=1)

    assert result.fallback_used is True
    assert result.detected_mode == "fixed_grid"
    assert result.frame_count == 4
    assert result.fallback_reason
    report = result.report_as_dict()
    assert report["attempted_modes"] == ["alpha_gap_auto", "row_alpha_gap", "row_alpha_components", "fixed_grid"]
    assert report["quality_status"] == "warning"
    assert report["confidence"] == 0.0
    assert report["warnings"]


def test_alpha_gap_auto_rejects_equal_boundary_that_crosses_visible_band() -> None:
    image = Image.new("RGBA", (100, 20), (0, 0, 0, 0))
    for y in range(5, 15):
        for x in range(10, 20):
            image.putpixel((x, y), (80, 140, 220, 255))
        for x in range(45, 55):
            image.putpixel((x, y), (80, 140, 220, 255))

    with pytest.raises(ValueError, match="境界"):
        split_sprite_sheet(image, mode="alpha_gap_auto")


def test_alpha_gap_auto_rejects_equal_boundary_that_crosses_thin_connected_decoration() -> None:
    image = Image.new("RGBA", (64, 20), (0, 0, 0, 0))
    color = (80, 140, 220, 255)
    for y in range(5, 15):
        for x in range(10, 20):
            image.putpixel((x, y), color)
        for x in range(40, 50):
            image.putpixel((x, y), color)
    for x in range(19, 37):
        image.putpixel((x, 10), color)

    with pytest.raises(ValueError, match="境界"):
        split_sprite_sheet(image, mode="alpha_gap_auto")


def test_fixed_grid_report_contains_cells_and_overlay() -> None:
    result = split_sprite_sheet(_grid_sheet(2, 2), mode="fixed_grid", columns=2, rows=2)

    report = result.report_as_dict()
    assert report["requested_mode"] == "fixed_grid"
    assert report["detected_mode"] == "fixed_grid"
    assert report["rows"] == 2
    assert report["columns"] == 2
    assert report["frame_count"] == 4
    assert len(report["cells"]) == 4
    assert result.detection_overlay.size == result.source_size


def test_alpha_projection_keeps_common_source_coordinates_before_shared_alignment() -> None:
    image = Image.new("RGBA", (64, 20), (0, 0, 0, 0))
    body = (40, 180, 80, 255)
    hair = (80, 80, 220, 255)
    for offset in (0, 32):
        for y in range(5, 15):
            for x in range(offset + 12, offset + 20):
                image.putpixel((x, y), body)
    for y in range(1, 8):
        for x in range(4, 12):
            image.putpixel((x, y), hair)
        for x in range(32, 44):
            image.putpixel((x, y), hair)

    split = split_sprite_sheet(image, mode="alpha_gap_auto", columns=2, rows=1)
    body_x = []
    for frame in split.frames:
        pixels = frame.load()
        xs = [x for y in range(frame.height) for x in range(frame.width) if pixels[x, y] == body]
        body_x.append((min(xs), max(xs)))

    assert body_x[0] == body_x[1]

    aligned = prepare_character_animation_sheet(
        image,
        CharacterAnimationConfig(
            frame_count=2,
            split_mode="alpha_gap_auto",
            grid_columns=2,
            grid_rows=1,
        ),
    )
    aligned_body_x = []
    for frame in aligned.aligned_frames:
        pixels = frame.load()
        xs = [x for y in range(frame.height) for x in range(frame.width) if pixels[x, y] == body]
        aligned_body_x.append((min(xs), max(xs)))
    assert aligned_body_x[0] == aligned_body_x[1]


def test_alpha_projection_preserves_thin_connected_decoration_outside_detection_band() -> None:
    image = Image.new("RGBA", (64, 20), (0, 0, 0, 0))
    body = (40, 180, 80, 255)
    decoration = (220, 80, 80, 255)
    for offset in (0, 32):
        for y in range(5, 15):
            for x in range(offset + 8, offset + 16):
                image.putpixel((x, y), body)
    for x in range(1, 8):
        image.putpixel((x, 5), decoration)
    for x in range(1, 3):
        image.putpixel((x, 6), decoration)

    split = split_sprite_sheet(image, mode="alpha_gap_auto", columns=2, rows=1)

    assert split.frames[0].getpixel((1, 5)) == decoration
    assert split.frames[0].getchannel("A").getbbox() is not None
    assert split.cells[0].visible_pixel_count == 89


def _row_sheet() -> Image.Image:
    image = Image.new("RGBA", (100, 70), (0, 0, 0, 0))
    colors = (
        (220, 60, 60, 255),
        (60, 220, 60, 255),
        (60, 60, 220, 255),
        (220, 180, 60, 255),
        (180, 60, 220, 255),
        (60, 220, 220, 255),
    )
    boxes = (
        (4, 5, 26, 18),
        (36, 5, 48, 18),
        (66, 5, 92, 18),
        (2, 45, 16, 62),
        (29, 45, 58, 62),
        (72, 45, 96, 62),
    )
    for color, (left, top, right, bottom) in zip(colors, boxes):
        for y in range(top, bottom):
            for x in range(left, right):
                image.putpixel((x, y), color)
    return image


def test_row_alpha_gap_splits_each_row_with_variable_boundaries_and_logical_origins() -> None:
    result = split_sprite_sheet(
        _row_sheet(),
        mode="row_alpha_gap",
        columns=3,
        rows=2,
        empty_column_threshold=0,
        empty_row_threshold=0,
        min_gutter_width_px=2,
    )

    assert result.detected_mode == "row_alpha_gap"
    assert result.frame_count == 6
    assert result.x_bands == ()
    assert [row["row"] for row in result.report_as_dict()["row_bands"]] == [0, 1]
    assert result.cells[0].logical_origin == (0, 0)
    assert result.cells[1].logical_origin == (33, 0)
    assert result.cells[3].logical_origin == (0, 35)
    assert result.cells[4].source_box != result.cells[1].source_box
    report = result.report_as_dict()
    assert report["schema_version"] == 3
    assert report["expected_frame_count"] == report["detected_frame_count"] == 6
    assert report["quality_status"] == "passed"

    boxes = [cell.source_box for cell in result.cells]
    reconstructed = Image.new("RGBA", result.source_size, (0, 0, 0, 0))
    for index, box in enumerate(boxes):
        reconstructed.alpha_composite(result.frames[index], (box[0], box[1]))
    assert list(reconstructed.getdata()) == list(_row_sheet().getdata())


def test_row_alpha_gap_uses_gap_boundaries_instead_of_nominal_equal_grid() -> None:
    image = Image.new("RGBA", (100, 24), (0, 0, 0, 0))
    color = (80, 140, 220, 255)
    for y in range(6, 18):
        for x in range(4, 42):
            image.putpixel((x, y), color)
        for x in range(66, 82):
            image.putpixel((x, y), color)

    result = split_sprite_sheet(
        image,
        mode="row_alpha_gap",
        columns=2,
        rows=1,
        empty_column_threshold=0,
        empty_row_threshold=0,
    )

    assert result.cells[0].source_box[2] == 54
    assert result.cells[0].source_box[2] != 50
    assert result.frames[0].getpixel((37, 6)) == color
    assert result.frames[1].getpixel((12, 6)) == color


def test_row_alpha_gap_rejects_an_ambiguous_extra_band() -> None:
    image = _row_sheet()
    color = (255, 255, 255, 255)
    for y in range(28, 34):
        for x in range(48, 54):
            image.putpixel((x, y), color)

    with pytest.raises(ValueError, match="期待|検出"):
        split_sprite_sheet(
            image,
            mode="row_alpha_gap",
            columns=3,
            rows=2,
            empty_column_threshold=0,
            empty_row_threshold=0,
            min_gutter_width_px=2,
        )


def test_row_alpha_gap_checks_x_boundaries_over_the_full_extracted_row() -> None:
    image = Image.new("RGBA", (80, 70), (0, 0, 0, 0))
    color = (80, 140, 220, 255)
    for y in range(50, 60):
        for x in range(5, 20):
            image.putpixel((x, y), color)
        for x in range(60, 75):
            image.putpixel((x, y), color)
    # Y投影のempty_row_thresholdには掛からない細線だが、全高ではX境界を連続して横切る。
    for step in range(42):
        image.putpixel((19 + step, step), color)

    with pytest.raises(ValueError, match="安全"):
        split_sprite_sheet(
            image,
            mode="row_alpha_gap",
            columns=2,
            rows=1,
            empty_column_threshold=0,
            empty_row_threshold=2,
            min_gutter_width_px=2,
        )

    fallback = split_sprite_sheet(
        image,
        mode="hybrid",
        columns=2,
        rows=1,
        empty_column_threshold=0,
        empty_row_threshold=2,
        min_gutter_width_px=2,
    )
    assert fallback.detected_mode == "row_alpha_components"
    assert fallback.split_status == "needs_assignment"
    assert fallback.report_as_dict()["quality_status"] == "warning"
    assert fallback.report_as_dict()["warnings"]
