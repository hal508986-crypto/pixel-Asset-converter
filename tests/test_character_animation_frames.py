from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    compile_character_animation_frames,
    compile_character_animation_sheet,
    split_horizontal_sheet,
)


def _frames(count: int = 4, size: int = 64) -> tuple[Image.Image, ...]:
    frames = []
    palette = [(200, 60, 60), (60, 160, 90), (70, 90, 210), (230, 200, 70)]
    for index in range(count):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        bob = (index % 2) * 2
        for y in range(14 - bob, 42 - bob):
            for x in range(24, 40):
                image.putpixel((x, y), (*palette[(x // 4 + y // 6) % 4], 255))
        for y in range(42 - bob, 56):
            for x in range(27 + index % 3, 31 + index % 3):
                image.putpixel((x, y), (40, 40, 60, 255))
        frames.append(image)
    return tuple(frames)


def _sheet(frames: tuple[Image.Image, ...], path: Path) -> Path:
    sheet = Image.new("RGBA", (frames[0].width * len(frames), frames[0].height), (0, 0, 0, 0))
    for index, frame in enumerate(frames):
        sheet.alpha_composite(frame, (index * frame.width, 0))
    sheet.save(path)
    return path


def _png_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*.png"))
    }


@pytest.mark.parametrize("placement", ["legacy_foot", "preserve_motion"])
def test_frame_sequence_matches_sheet_path_byte_for_byte(tmp_path: Path, placement: str) -> None:
    frames = _frames()
    extra = (
        dict(source_origin=(32, 56), output_origin=(32, 58), scale_override=1.0)
        if placement == "preserve_motion"
        else {}
    )
    config = CharacterAnimationConfig(frame_count=4, placement_mode=placement, **extra)
    sheet_path = _sheet(frames, tmp_path / "sheet.png")
    split_frames, _, _ = split_horizontal_sheet(Image.open(sheet_path), 4)

    compile_character_animation_sheet(sheet_path, tmp_path / "from_sheet", config=config)
    compile_character_animation_frames(split_frames, tmp_path / "from_frames", config=config)

    assert _png_bytes(tmp_path / "from_sheet") == _png_bytes(tmp_path / "from_frames")


def test_frame_sequence_report_records_source_frames_and_uses_len_as_frame_count(tmp_path: Path) -> None:
    frames = _frames(3)
    # config.frame_count(既定4)と実枚数が違っても、実枚数が正になる
    result = compile_character_animation_frames(
        frames, tmp_path / "out", config=CharacterAnimationConfig(), source_label="h3_run"
    )
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert len(result.final_frame_paths) == 3
    assert report["frame_count"] == 3
    assert report["source_frames"]["label"] == "h3_run"
    assert report["source_frames"]["frame_count"] == 3
    assert len(report["source_frames"]["sha256"]) == 3
    assert "source_image" not in report
    assert report["final_palette"]["within_budget"] is True


def test_frame_sequence_is_deterministic(tmp_path: Path) -> None:
    frames = _frames()
    compile_character_animation_frames(frames, tmp_path / "a")
    compile_character_animation_frames(frames, tmp_path / "b")
    assert _png_bytes(tmp_path / "a") == _png_bytes(tmp_path / "b")


def test_frame_sequence_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        compile_character_animation_frames([], tmp_path / "out")
    mixed = (Image.new("RGBA", (64, 64)), Image.new("RGBA", (32, 32)))
    with pytest.raises(ValueError, match="same size"):
        compile_character_animation_frames(mixed, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_frame_sequence_keeps_unrelated_files_and_replaces_managed_outputs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    compile_character_animation_frames(_frames(), out)
    compile_character_animation_frames(_frames(2), out)
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert len(list((out / "final_frames").glob("*.png"))) == 2


def test_frame_sequence_applies_output_sheet_pixel_limit(tmp_path: Path) -> None:
    config = CharacterAnimationConfig(canvas_size=(512, 512), fit_within=(500, 500))
    frames = tuple(Image.new("RGBA", (8, 8), (0, 0, 0, 0)) for _ in range(65))
    with pytest.raises(ValueError, match="総画素数"):
        compile_character_animation_frames(frames, tmp_path / "out", config=config)
