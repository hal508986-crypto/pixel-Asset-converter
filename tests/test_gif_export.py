from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageSequence

from pixel_tile_compiler.io.gif_export import (
    gif_durations_ms,
    save_animated_gif,
    verify_gif_matches_frames,
)


def _frames(count: int = 8, size: int = 32, colors: int = 12, seed: int = 4) -> list[Image.Image]:
    rng = np.random.default_rng(seed)
    palette = rng.integers(0, 256, (colors, 3), dtype=np.uint8)
    frames = []
    for index in range(count):
        array = np.zeros((size, size, 4), np.uint8)
        yy, xx = np.mgrid[0:size, 0:size]
        mask = ((xx - 16 - index) ** 2 + (yy - 16) ** 2 < 90) & ((xx + yy + index) % 4 != 0)
        array[mask, :3] = palette[(xx[mask] + 2 * yy[mask] + index) % colors]
        array[mask, 3] = 255
        frames.append(Image.fromarray(array, "RGBA"))
    return frames


def _decode(path: Path) -> list[tuple[int, np.ndarray]]:
    with Image.open(path) as opened:
        return [
            (int(f.info["duration"]), np.asarray(f.convert("RGBA")).copy())
            for f in ImageSequence.Iterator(opened)
        ]


def test_durations_keep_total_time_for_common_fps() -> None:
    assert sum(gif_durations_ms(48, 24)) == 2000
    assert set(gif_durations_ms(48, 24)) == {40, 50}
    assert gif_durations_ms(3, 25) == (40, 40, 40)
    assert sum(gif_durations_ms(60, 30)) == 2000 and sum(gif_durations_ms(60, 12)) == 5000
    assert gif_durations_ms(1, 100) == (10,)
    for fps in (7, 15, 23.976, 29.97):
        total = sum(gif_durations_ms(120, fps))
        assert abs(total - 120 / fps * 1000) <= 10  # 誤差は10ms単位の1つ以内


@pytest.mark.parametrize("fps", [0, -1, 101])
def test_invalid_fps_rejected(fps: float) -> None:
    with pytest.raises(ValueError, match="fps"):
        gif_durations_ms(4, fps)


def test_roundtrip_is_pixel_exact_and_uses_expected_metadata(tmp_path: Path) -> None:
    frames = _frames()
    result = save_animated_gif(frames, tmp_path / "a.gif", fps=24)
    assert result.verified and result.frame_count == 8 and result.size == (32, 32)
    assert result.total_ms == sum(gif_durations_ms(8, 24))
    decoded = _decode(tmp_path / "a.gif")
    assert len(decoded) == 8
    with Image.open(tmp_path / "a.gif") as opened:
        assert opened.info.get("loop") == 0
    for (duration, shown), source, expected in zip(decoded, frames, gif_durations_ms(8, 24)):
        assert duration == expected
        want = np.asarray(source)
        visible = want[:, :, 3] > 0
        assert np.array_equal(shown[:, :, 3] > 0, visible)
        assert np.array_equal(shown[visible][:, :3], want[visible][:, :3])
    assert result.report_as_dict()["fps"] == 24.0


def test_visible_black_and_transparent_are_not_confused(tmp_path: Path) -> None:
    array = np.zeros((8, 8, 4), np.uint8)
    array[2:6, 2:6] = (0, 0, 0, 255)  # 不透明の純黒（パレット0番=透過のRGBと同じ値）
    array[3, 3] = (255, 255, 255, 255)
    save_animated_gif([Image.fromarray(array, "RGBA")], tmp_path / "b.gif")
    shown = _decode(tmp_path / "b.gif")[0][1]
    assert (shown[2:6, 2:6, 3] == 255).all() and (shown[0, 0, 3] == 0)
    assert tuple(shown[3, 3]) == (255, 255, 255, 255) and tuple(shown[2, 2]) == (0, 0, 0, 255)


def test_identical_consecutive_frames_are_merged_but_verified_by_time(tmp_path: Path) -> None:
    base = _frames(3)
    frames = [base[0], base[0], base[0], base[1], base[1], base[2]]  # 静止（同一フレームの連続）
    result = save_animated_gif(frames, tmp_path / "c.gif", fps=24)
    decoded = _decode(tmp_path / "c.gif")
    assert len(decoded) < len(frames)  # Pillowが結合する
    assert sum(d for d, _ in decoded) == result.total_ms  # 時間は保たれる
    verify_gif_matches_frames(tmp_path / "c.gif", frames, result.durations_ms)


def test_fully_transparent_frame_and_single_frame(tmp_path: Path) -> None:
    frames = _frames(3)
    frames[1] = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    save_animated_gif(frames, tmp_path / "d.gif")
    save_animated_gif(frames[:1], tmp_path / "single.gif")
    assert len(_decode(tmp_path / "single.gif")) == 1
    assert save_animated_gif([Image.new("RGBA", (4, 4))], tmp_path / "empty.gif").color_count == 0


def test_loop_options(tmp_path: Path) -> None:
    frames = _frames(2)
    save_animated_gif(frames, tmp_path / "once.gif", loop=None)
    save_animated_gif(frames, tmp_path / "three.gif", loop=3)
    with Image.open(tmp_path / "once.gif") as opened:
        assert "loop" not in opened.info
    with Image.open(tmp_path / "three.gif") as opened:
        assert opened.info["loop"] == 3
    with pytest.raises(ValueError, match="loop"):
        save_animated_gif(frames, tmp_path / "x.gif", loop=-1)


def test_more_than_255_colors_is_rejected_with_count(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    array = np.dstack([rng.integers(0, 256, (32, 32, 3), dtype=np.uint8), np.full((32, 32), 255, np.uint8)])
    with pytest.raises(ValueError, match="255色"):
        save_animated_gif([Image.fromarray(array, "RGBA")], tmp_path / "many.gif")
    assert not (tmp_path / "many.gif").exists()


def test_exactly_255_colors_is_accepted(tmp_path: Path) -> None:
    array = np.zeros((16, 16, 4), np.uint8)
    flat = np.arange(255)
    array.reshape(-1, 4)[:255] = np.stack([flat, 255 - flat, (flat * 7) % 256, np.full(255, 255)], axis=1)
    assert save_animated_gif([Image.fromarray(array, "RGBA")], tmp_path / "max.gif").color_count == 255


def test_output_is_deterministic_and_verification_catches_a_mismatch(tmp_path: Path) -> None:
    frames = _frames()
    first = save_animated_gif(frames, tmp_path / "1.gif")
    second = save_animated_gif(frames, tmp_path / "2.gif")
    assert first.sha256 == second.sha256
    altered = [f.copy() for f in frames]
    altered[3].putpixel((16, 16), (1, 2, 3, 255))
    with pytest.raises(RuntimeError, match="フレーム4"):
        verify_gif_matches_frames(tmp_path / "1.gif", altered, first.durations_ms)


def test_input_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        save_animated_gif([], tmp_path / "x.gif")
    with pytest.raises(ValueError, match="same size"):
        save_animated_gif([Image.new("RGBA", (4, 4)), Image.new("RGBA", (5, 4))], tmp_path / "x.gif")
