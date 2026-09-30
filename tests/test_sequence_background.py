from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.io.frame_sequence import list_frame_files, load_frame_directory
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    compile_character_animation_frames,
)
from pixel_tile_compiler.preprocess.sequence_background import (
    estimate_sequence_background,
    remove_sequence_background,
)

GREEN = (0, 177, 64)
SIZE = 96


def _character_mask(index: int, size: int = SIZE) -> np.ndarray:
    mask = np.zeros((size, size), bool)
    top = 20 + (index % 3) * 2
    mask[top:top + 40, 36:60] = True   # 胴
    mask[top - 12:top, 40:56] = True   # 頭
    mask[top + 40:size - 12, 38 + index % 4:44 + index % 4] = True  # 脚
    return mask


def _frames(background: tuple[int, int, int] = GREEN, count: int = 4, noise: int = 3, seed: int = 5):
    rng = np.random.default_rng(seed)
    palette = np.array([(200, 50, 50), (240, 210, 60), (60, 80, 200)], np.uint8)
    frames, masks = [], []
    for index in range(count):
        mask = _character_mask(index)
        array = np.empty((SIZE, SIZE, 4), np.uint8)
        array[:, :, :3] = np.clip(
            np.array(background) + rng.integers(-noise, noise + 1, (SIZE, SIZE, 3)), 0, 255
        )
        array[:, :, 3] = 255
        yy, xx = np.nonzero(mask)
        array[yy, xx, :3] = palette[(yy // 8 + xx // 8) % 3]
        frames.append(Image.fromarray(array, "RGBA"))
        masks.append(mask)
    return tuple(frames), masks


@pytest.mark.parametrize("background", [GREEN, (255, 255, 255), (250, 252, 249)])
def test_estimates_shared_color_and_keys_exactly_the_character(background) -> None:
    frames, masks = _frames(background)
    result = remove_sequence_background(frames)
    assert result.color_source == "estimated"
    assert max(abs(a - b) for a, b in zip(result.color, background)) <= 3
    assert result.border_coverage is not None and result.border_coverage > 0.99
    for keyed, mask in zip(result.frames, masks):
        alpha = np.asarray(keyed)[:, :, 3]
        assert set(np.unique(alpha)) <= {0, 255}
        assert np.array_equal(alpha == 255, mask)
        assert not np.asarray(keyed)[alpha == 0][:, :3].any()  # 透過画素のRGBは0
    assert result.warnings == ()


def test_specified_color_wins_and_is_reported() -> None:
    frames, masks = _frames()
    result = remove_sequence_background(frames, color="#00B140", tolerance=20)
    assert result.color == (0, 177, 64) and result.color_source == "specified"
    assert np.array_equal(np.asarray(result.frames[0])[:, :, 3] == 255, masks[0])
    report = result.report_as_dict()
    assert report["color"] == "#00B140" and report["tolerance"] == 20
    json.dumps(report)  # JSON化できる


def test_color_is_shared_even_when_character_touches_border_in_one_frame() -> None:
    frames, masks = _frames()
    array = np.asarray(frames[2]).copy()
    array[:, :10, :3] = (200, 50, 50)  # 左端が全面キャラ色（外周の約1/4）
    touched = Image.fromarray(array, "RGBA")
    result = remove_sequence_background((*frames[:2], touched, frames[3]))
    assert max(abs(a - b) for a, b in zip(result.color, GREEN)) <= 3
    # 他フレームは影響を受けない
    assert np.array_equal(np.asarray(result.frames[0])[:, :, 3] == 255, masks[0])


def test_connected_keeps_enclosed_white_but_global_removes_it() -> None:
    frames, masks = _frames((255, 255, 255), noise=0)
    array = np.asarray(frames[0]).copy()
    array[30:38, 40:48, :3] = 255  # 胴の内側の白いハイライト（8x8）
    frames = (Image.fromarray(array, "RGBA"),)
    connected = remove_sequence_background(frames, mode="connected")
    kept = np.asarray(connected.frames[0])[:, :, 3] == 255
    assert kept[30:38, 40:48].all()
    assert connected.frame_reports[0]["enclosed_bg_like_px"] == 64
    assert any("内側" in warning for warning in connected.warnings)
    globally = remove_sequence_background(frames, mode="global")
    assert not (np.asarray(globally.frames[0])[:, :, 3] == 255)[30:38, 40:48].any()


def test_already_transparent_pixels_stay_transparent() -> None:
    frames, masks = _frames()
    array = np.asarray(frames[0]).copy()
    array[:6, :6, 3] = 0
    result = remove_sequence_background((Image.fromarray(array, "RGBA"),), color="#00B140")
    assert (np.asarray(result.frames[0])[:6, :6, 3] == 0).all()


def test_non_flat_border_is_rejected_with_actionable_message() -> None:
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(0, 256, (32, 32, 4), dtype=np.uint8), "RGBA")
    with pytest.raises(ValueError, match="背景色を明示指定"):
        estimate_sequence_background((noise,))
    with pytest.raises(ValueError, match="外周に可視画素がない"):
        estimate_sequence_background((Image.new("RGBA", (8, 8), (0, 0, 0, 0)),))


def test_input_validation() -> None:
    frames, _ = _frames()
    with pytest.raises(ValueError, match="at least one"):
        remove_sequence_background([])
    with pytest.raises(ValueError, match="same size"):
        remove_sequence_background((frames[0], Image.new("RGBA", (8, 8))))
    with pytest.raises(ValueError, match="mode"):
        remove_sequence_background(frames, mode="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="#RRGGBB"):
        remove_sequence_background(frames, color="green")
    with pytest.raises(ValueError, match="0〜255"):
        remove_sequence_background(frames, color=(0, 300, 0))


def test_result_is_deterministic() -> None:
    frames, _ = _frames()
    first = remove_sequence_background(frames)
    second = remove_sequence_background(frames)
    assert first.color == second.color
    assert [f.tobytes() for f in first.frames] == [f.tobytes() for f in second.frames]


def test_load_frame_directory_uses_natural_order_and_checks_sizes(tmp_path: Path) -> None:
    for name in ("f10", "f2", "f1", "f11"):
        Image.new("RGB", (8, 8), (10, 20, 30)).save(tmp_path / f"{name}.png")
    (tmp_path / "readme.txt").write_text("x")
    assert [p.stem for p in list_frame_files(tmp_path)] == ["f1", "f2", "f10", "f11"]
    paths, frames = load_frame_directory(tmp_path)
    assert len(paths) == len(frames) == 4 and frames[0].mode == "RGBA"
    # 桁の違い・接頭辞の有無が混在しても落ちない
    Image.new("RGB", (8, 8)).save(tmp_path / "a.png")
    Image.new("RGB", (8, 8)).save(tmp_path / "a1.png")
    assert [p.stem for p in list_frame_files(tmp_path)][:2] == ["a", "a1"]
    Image.new("RGB", (9, 8)).save(tmp_path / "z.png")
    with pytest.raises(ValueError, match="サイズが揃っていません"):
        load_frame_directory(tmp_path)
    with pytest.raises(ValueError, match="PNG連番が見つかりません"):
        list_frame_files(_empty(tmp_path))
    with pytest.raises(ValueError, match="フォルダが見つかりません"):
        list_frame_files(tmp_path / "missing")


def _empty(base: Path) -> Path:
    path = base / "empty"
    path.mkdir()
    return path


@pytest.mark.parametrize("background", [GREEN, (255, 255, 255)])
def test_keyed_sequence_compiles_with_tight_shared_bbox(tmp_path: Path, background) -> None:
    frames, masks = _frames(background)
    keyed = remove_sequence_background(frames)
    config = CharacterAnimationConfig(
        frame_count=len(frames), placement_mode="preserve_motion",
        source_origin=(48, 84), output_origin=(32, 58), scale_override=0.5,
        remove_isolated_components=False,
    )
    result = compile_character_animation_frames(
        keyed.frames, tmp_path / "out", config=config,
        report_extras={"background_key": keyed.report_as_dict()},
    )
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["background_key"]["color_source"] == "estimated"
    union = report["union_bbox"]
    assert (union["right"] - union["left"]) < SIZE // 2 and (union["bottom"] - union["top"]) < SIZE
    assert report["final_palette"]["within_budget"] is True
    # 背景色は最終パレットに残らない
    final = {tuple(c) for c in report["final_palette"]["colors"]}
    assert all(max(abs(a - b) for a, b in zip(color, background)) > 30 for color in final)
