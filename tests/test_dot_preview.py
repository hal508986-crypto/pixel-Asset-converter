"""ドット確認画像の契約テスト。

仕様: docs/spec/dot_preview_spec.md
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.io.dot_preview import (
    DOT_PREVIEW_MAJOR_INTERVAL,
    render_dot_inspect_preview,
    render_dot_showcase_preview,
    resolve_dot_preview_scale,
    write_dot_previews,
)


def _dot_art(size: tuple[int, int] = (8, 8)) -> Image.Image:
    """市松に色を置き、右下だけ透明にした小さなドット絵。"""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(size[1]):
        for x in range(size[0]):
            if x >= size[0] - 2 and y >= size[1] - 2:
                continue
            value = 200 if (x + y) % 2 == 0 else 90
            image.putpixel((x, y), (value, value // 2, 60, 255))
    return image


def test_dot_scale_keeps_the_long_side_near_the_target():
    assert resolve_dot_preview_scale((64, 64)) == 12
    assert resolve_dot_preview_scale((128, 128)) == 6
    assert resolve_dot_preview_scale((256, 256)) == 4
    # 長辺で決まる
    assert resolve_dot_preview_scale((256, 128)) == 4


def test_dot_scale_stays_within_bounds():
    assert resolve_dot_preview_scale((16, 16)) == 16       # 上限で頭打ち
    assert resolve_dot_preview_scale((1024, 1024)) == 4    # 下限で頭打ち


def test_inspect_preview_scales_by_an_integer_factor():
    art = _dot_art((8, 8))
    preview = render_dot_inspect_preview(art, scale=10)
    assert preview.size == (80, 80)


def test_inspect_preview_draws_a_line_on_every_dot_boundary():
    art = _dot_art((8, 8))
    scale = 10
    preview = np.array(render_dot_inspect_preview(art, scale=scale).convert("RGB")).astype(int)

    # ドット境界(x=scale)の列と、ドット内部(x=scale+scale//2)の列を比べる
    boundary = preview[:, scale, :]
    inside = preview[:, scale + scale // 2, :]
    assert not np.array_equal(boundary, inside)
    # 境界は元のドット色から変化している
    art_scaled = np.array(art.resize((80, 80), Image.NEAREST).convert("RGB")).astype(int)
    assert not np.array_equal(boundary, art_scaled[:, scale, :])


def test_inspect_preview_marks_every_eighth_dot_more_strongly():
    art = _dot_art((24, 24))
    scale = 8
    preview = np.array(render_dot_inspect_preview(art, scale=scale).convert("RGB")).astype(float)

    minor = preview[:, scale, :].mean()
    major = preview[:, DOT_PREVIEW_MAJOR_INTERVAL * scale, :].mean()
    # 濃い線ほど暗くなる
    assert major < minor


def test_showcase_preview_keeps_transparent_dots_transparent():
    art = _dot_art((8, 8))
    scale = 10
    preview = np.array(render_dot_showcase_preview(art, scale=scale))

    # 右下2x2ドットは透明。拡大後も全て透明であること
    assert preview[-2 * scale:, -2 * scale:, 3].max() == 0


def test_showcase_preview_darkens_the_dot_edges():
    art = Image.new("RGBA", (2, 2), (200, 200, 200, 255))
    scale = 8
    preview = np.array(render_dot_showcase_preview(art, scale=scale).convert("RGB")).astype(int)

    body = preview[0, 0, :]
    right_edge = preview[0, scale - 1, :]
    bottom_edge = preview[scale - 1, 0, :]
    assert right_edge.sum() < body.sum()
    assert bottom_edge.sum() < body.sum()


def test_write_dot_previews_creates_both_files(tmp_path: Path):
    final = tmp_path / "final.png"
    _dot_art((16, 16)).save(final)

    written = write_dot_previews(final)

    assert len(written) == 2
    for path in written:
        assert path.exists()
        assert path.parent == tmp_path
    names = sorted(path.name for path in written)
    assert names == ["final_dot_16x.png", "final_grid_16x.png"]


def test_write_dot_previews_rejects_a_missing_source(tmp_path: Path):
    with pytest.raises(ValueError):
        write_dot_previews(tmp_path / "missing.png")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_main_window_dot_preview_button_needs_a_result(monkeypatch):
    """コンパイル結果が無い間は書き出しボタンを無効にする。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert not window.dot_preview_button.isEnabled()
        assert "書き出" in window.dot_preview_button.text()
    finally:
        window.close()


def test_main_window_writes_dot_previews_next_to_the_result(monkeypatch, tmp_path: Path):
    """結果と同じ場所へ2枚書き出し、状態表示に名前を出す。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    source = tmp_path / "subject.png"
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    for y in range(12, 84):
        for x in range(30, 66):
            image.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, 160, 255))
    image.save(source)
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character"))
        assert window.set_source_path(source)
        window.output_root_field.setText(str(tmp_path / "out"))
        def run_now(operation, context):
            # 実際の経路と同じく、結果ハンドラまで通す
            window._compile_context = {
                **context,
                "configuration_revision": window._configuration_revision,
            }
            window._on_compile_succeeded(operation())

        monkeypatch.setattr(window, "_start_compile", run_now)
        window.compile_image()
        app.processEvents()

        assert window.dot_preview_button.isEnabled()
        window.write_dot_previews()
        app.processEvents()

        written = sorted((tmp_path / "out").rglob("final_*x.png"))
        assert len(written) == 2
        assert "ドット確認画像を書き出しました" in window.status.text()
        for path in written:
            assert path.name in window.status.text()
    finally:
        window.close()
