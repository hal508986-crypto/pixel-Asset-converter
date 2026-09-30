from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.pixelizer.character_frame_sequence import (
    compile_character_frame_directory,
    default_sequence_config,
)

SIZE = 96
GREEN = (0, 177, 64)


def _write_sequence(directory: Path, count: int = 6, background=GREEN, opaque: bool = True) -> list[Path]:
    """緑背景・ノイズ入りで、跳ねながら歩く簡単なキャラの連番PNGを書く。"""
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    paths = []
    for index in range(count):
        array = np.zeros((SIZE, SIZE, 4), np.uint8)
        if opaque:
            array[:, :, :3] = np.clip(np.array(background) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255)
            array[:, :, 3] = 255
        jump = (index % 3) * 6
        top = 30 - jump
        array[top:top + 30, 40:56, :3] = (200, 60, 60)
        array[top - 10:top, 43:53, :3] = (240, 200, 70)
        array[top + 30:SIZE - 14 - jump, 42 + index % 3:48 + index % 3, :3] = (60, 80, 200)
        body = np.zeros((SIZE, SIZE), bool)
        body[top:top + 30, 40:56] = True
        body[top - 10:top, 43:53] = True
        body[top + 30:SIZE - 14 - jump, 42 + index % 3:48 + index % 3] = True
        array[body, 3] = 255
        path = directory / f"frame_{index:04d}.png"
        Image.fromarray(array, "RGBA").save(path)
        paths.append(path)
    return paths


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_default_config_is_512_preserve_motion() -> None:
    config = default_sequence_config()
    assert config.canvas_size == (512, 512) and config.placement_mode == "preserve_motion"
    small = default_sequence_config((64, 64))
    assert small.canvas_size == (64, 64) and small.bottom_margin == 2


@pytest.mark.parametrize("canvas", [(64, 64), (128, 128)])
def test_directory_to_all_stages_at_flexible_canvas_sizes(tmp_path: Path, canvas) -> None:
    sources = _write_sequence(tmp_path / "in")
    result = compile_character_frame_directory(tmp_path / "in", tmp_path / "out", canvas_size=canvas)
    root = tmp_path / "out"
    names = [f"F{i:03d}.png" for i in range(1, 7)]
    for directory in ("source_frames", "keyed_frames", "aligned_frames", "final_frames"):
        assert sorted(p.name for p in (root / directory).glob("*.png")) == names, directory
    assert sorted(p.name for p in (root / "compiled").iterdir()) == [n[:-4] for n in names]
    for path in (root / "final_frames").glob("*.png"):
        assert Image.open(path).size == canvas
    # 入力は不変コピー
    for index, original in enumerate(sources):
        assert _sha(root / "source_frames" / names[index]) == _sha(original)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["background_key"]["color_source"] == "estimated"
    assert report["source_directory"]["files"][0] == "frame_0000.png"
    assert set(report["stages"]) == {"source_frames", "keyed_frames", "aligned_frames"}
    assert report["final_palette"]["within_budget"] is True
    for stage, records in report["stages"].items():
        for record in records:
            assert record["sha256"] == _sha(Path(record["path"])), stage


def test_jump_motion_is_preserved_by_derived_origins(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    result = compile_character_frame_directory(tmp_path / "in", tmp_path / "out", canvas_size=(128, 128))
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    tops = [frame["placed_bbox"]["top"] for frame in report["frames"]]
    bottoms = [frame["placed_bbox"]["bottom"] for frame in report["frames"]]
    assert len(set(bottoms)) > 1 and len(set(tops)) > 1  # 足元固定で潰していない
    assert report["transform"]["scale"] > 0
    assert report["placement_mode"] == "preserve_motion"


def test_already_transparent_sequence_can_skip_keying(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in", opaque=False)
    result = compile_character_frame_directory(
        tmp_path / "in", tmp_path / "out", canvas_size=(64, 64), key_background=False
    )
    root = tmp_path / "out"
    assert not (root / "keyed_frames").exists()
    assert json.loads(result.report_path.read_text(encoding="utf-8"))["background_key"] is None
    # 背景除去なしで不透明のまま通すのではなく、透過済みを前提に正しく収まる
    assert Image.open(root / "final_frames" / "F001.png").getchannel("A").getbbox() is not None


def test_fully_transparent_input_gives_actionable_error(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    for index in range(2):
        Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(tmp_path / "in" / f"f{index}.png")
    with pytest.raises(ValueError, match="可視画素のあるフレームがありません"):
        compile_character_frame_directory(
            tmp_path / "in", tmp_path / "out", canvas_size=(64, 64), key_background=False
        )
    assert not (tmp_path / "out").exists()


def test_rerun_replaces_managed_stage_directories_and_keeps_other_files(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    out = tmp_path / "out"
    compile_character_frame_directory(tmp_path / "in", out, canvas_size=(64, 64))
    (out / "memo.txt").write_text("keep", encoding="utf-8")
    compile_character_frame_directory(tmp_path / "in", out, canvas_size=(64, 64), key_background=False)
    assert not (out / "keyed_frames").exists()  # 今回の実行に無い段階の古い成果物は残さない
    assert (out / "source_frames" / "F001.png").exists()
    assert (out / "memo.txt").read_text(encoding="utf-8") == "keep"


def test_directory_pipeline_is_deterministic(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    compile_character_frame_directory(tmp_path / "in", tmp_path / "a", canvas_size=(64, 64))
    compile_character_frame_directory(tmp_path / "in", tmp_path / "b", canvas_size=(64, 64))
    for directory in ("keyed_frames", "aligned_frames", "final_frames"):
        assert [_sha(p) for p in sorted((tmp_path / "a" / directory).glob("*.png"))] == [
            _sha(p) for p in sorted((tmp_path / "b" / directory).glob("*.png"))
        ]
