from __future__ import annotations

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
