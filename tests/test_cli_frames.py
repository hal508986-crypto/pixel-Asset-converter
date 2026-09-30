from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.cli import app

SIZE = 96
TEAL = (92, 156, 155)


def _write_sequence(directory: Path, count: int = 5, background=TEAL, transparent: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2)
    for index in range(count):
        array = np.zeros((SIZE, SIZE, 4), np.uint8)
        if not transparent:
            array[:, :, :3] = np.clip(np.array(background) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255)
            array[:, :, 3] = 255
        top = 30 - (index % 3) * 5
        body = np.zeros((SIZE, SIZE), bool)
        body[top:top + 30, 40:56] = True
        body[top - 10:top, 43:53] = True
        body[top + 30:SIZE - 14, 42 + index % 3:48 + index % 3] = True
        array[body, :3] = (190, 50, 60)
        array[body, 3] = 255
        Image.fromarray(array, "RGBA").save(directory / f"frame_{index + 1:05d}_.png")


def _run(*args: str):
    return CliRunner().invoke(app, ["compile-character-frames", *map(str, args)])


def test_cli_frames_end_to_end_writes_every_stage_and_a_gif(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    output = tmp_path / "out"
    result = _run(tmp_path / "in", "--output", output, "--width", 64, "--height", 64)
    assert result.exit_code == 0, result.stdout
    for directory in ("source_frames", "keyed_frames", "aligned_frames", "final_frames"):
        assert len(list((output / directory).glob("F00?.png"))) == 5, directory
    assert (output / "animation.gif").exists()
    assert "GIF:" in result.stdout and "5フレーム" in result.stdout
    report = json.loads((output / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["background_key"]["mode"] == "global"  # ティールは鮮やかなのでauto→global
    assert report["animation"]["gif"]["fps"] == 24.0 and report["animation"]["gif"]["verified"] is True
    assert Image.open(output / "final_frames" / "F001.png").size == (64, 64)


def test_cli_frames_options_are_passed_through(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    output = tmp_path / "out"
    result = _run(
        tmp_path / "in", "-o", output, "--width", 64, "--height", 64, "--palette", 8, "--fps", 12,
        "--play-once", "--background-color", "#5C9C9B", "--background-tolerance", 40,
        "--background-mode", "connected", "--choke", 1,
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((output / "bbox_report.json").read_text(encoding="utf-8"))
    key = report["background_key"]
    assert key["color"] == "#5C9C9B" and key["color_source"] == "specified"
    assert key["tolerance"] == 40 and key["mode"] == "connected" and key["choke_px"] == 1
    gif = report["animation"]["gif"]
    assert gif["fps"] == 12.0 and gif["loop"] is None
    assert report["final_palette"]["color_count"] <= 8


def test_cli_frames_no_gif_and_pre_keyed_input(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in", transparent=True)
    output = tmp_path / "out"
    result = _run(tmp_path / "in", "-o", output, "--width", 64, "--height", 64, "--no-key-background", "--no-gif")
    assert result.exit_code == 0, result.stdout
    assert not (output / "animation.gif").exists() and not (output / "keyed_frames").exists()
    assert "GIF:" not in result.stdout


def test_cli_frames_default_output_dir_is_named_after_the_source(tmp_path: Path, monkeypatch) -> None:
    _write_sequence(tmp_path / "walk")
    monkeypatch.chdir(tmp_path)
    result = _run(tmp_path / "walk", "--width", 64, "--height", 64, "--no-gif")
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "output" / "walk_frames" / "final_frames" / "F001.png").exists()


def test_cli_frames_reports_actionable_errors(tmp_path: Path) -> None:
    missing = _run(tmp_path / "nope")
    assert missing.exit_code != 0
    (tmp_path / "empty").mkdir()
    empty = _run(tmp_path / "empty", "-o", tmp_path / "o1")
    assert empty.exit_code != 0 and "PNG連番が見つかりません" in empty.output
    _write_sequence(tmp_path / "in")
    for bad in (["--background-mode", "nope"], ["--fps", "500"], ["--outline", "red"], ["--palette", "2"]):
        result = _run(tmp_path / "in", "-o", tmp_path / "o2", *bad)
        assert result.exit_code != 0, bad
    rng = np.random.default_rng(0)
    (tmp_path / "noisy").mkdir()
    Image.fromarray(rng.integers(0, 256, (SIZE, SIZE, 4), dtype=np.uint8), "RGBA").save(tmp_path / "noisy" / "a.png")
    noisy = _run(tmp_path / "noisy", "-o", tmp_path / "o3")
    assert noisy.exit_code != 0 and "背景色を明示指定" in noisy.output
    assert not (tmp_path / "o3").exists() and not (tmp_path / "o1").exists()
