from __future__ import annotations

from PIL import Image

from pixel_tile_compiler.sheet.alpha_projection import split_sprite_sheet


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
