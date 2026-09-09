"""元絵の情報取得の契約テスト。

仕様: docs/spec/canvas_scale_and_palette_budget_spec.md 4.7節・S-14
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.analysis.source_info import describe_source


def _grid_source(path: Path, grid: int, pitch: int, colors: int = 4) -> Path:
    """1ドットがpitch画素で描かれた、格子のあるピクセルアート風画像を作る。"""
    size = grid * pitch
    image = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    for cell_y in range(grid):
        for cell_x in range(grid):
            value = ((cell_x + cell_y) % colors) * (200 // max(1, colors - 1)) + 20
            for y in range(pitch):
                for x in range(pitch):
                    image.putpixel((cell_x * pitch + x, cell_y * pitch + y), (value, value // 2, 60, 255))
    image.save(path)
    return path


def test_describe_source_reports_size_pixels_and_aspect(tmp_path: Path):
    path = tmp_path / "wide.png"
    Image.new("RGBA", (640, 360), (20, 30, 40, 255)).save(path)

    info = describe_source(path)

    assert info.size == (640, 360)
    assert info.pixel_count == 640 * 360
    assert info.aspect_ratio == pytest.approx(640 / 360)
    assert info.aspect_label == "16:9"


def test_describe_source_reports_common_aspect_labels(tmp_path: Path):
    for size, label in (((512, 512), "1:1"), ((640, 480), "4:3"), ((256, 128), "2:1")):
        path = tmp_path / f"{size[0]}x{size[1]}.png"
        Image.new("RGBA", size, (10, 10, 10, 255)).save(path)
        assert describe_source(path).aspect_label == label


def test_describe_source_counts_only_visible_colors(tmp_path: Path):
    path = tmp_path / "cutout.png"
    image = Image.new("RGBA", (16, 16), (99, 99, 99, 0))  # 透明部分は数えない
    for y in range(4, 12):
        for x in range(4, 12):
            image.putpixel((x, y), (10, 20, 30, 255))
    image.save(path)

    info = describe_source(path)

    assert info.visible_colors == 1
    assert info.semi_alpha_ratio == 0.0


def test_describe_source_detects_the_apparent_dot_grid(tmp_path: Path):
    """拡大されたドット絵の1ドットあたり画素数を見抜く。"""
    info = describe_source(_grid_source(tmp_path / "grid.png", grid=16, pitch=8))

    assert info.dot_pitch == 8
    assert info.apparent_grid == 16
    assert info.has_block_grid is True


def test_describe_source_reports_no_grid_for_a_smooth_image(tmp_path: Path):
    path = tmp_path / "smooth.png"
    image = Image.new("RGBA", (128, 128))
    for y in range(128):
        for x in range(128):
            image.putpixel((x, y), (x * 2 % 256, y * 2 % 256, (x + y) % 256, 255))
    image.save(path)

    info = describe_source(path)

    assert info.dot_pitch == 1
    assert info.has_block_grid is False


def test_describe_source_measures_semi_transparent_edges(tmp_path: Path):
    path = tmp_path / "soft.png"
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    for x in range(10):
        image.putpixel((x, 5), (200, 100, 50, 255))
        image.putpixel((x, 6), (200, 100, 50, 128))
    image.save(path)

    info = describe_source(path)

    assert info.semi_alpha_ratio == pytest.approx(0.5)


def test_describe_source_rejects_an_unreadable_path(tmp_path: Path):
    with pytest.raises(ValueError):
        describe_source(tmp_path / "missing.png")
