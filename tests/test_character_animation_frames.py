from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.io.frame_sequence import frame_label
from pixel_tile_compiler.pixelizer import character_animation
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
def test_frame_sequence_matches_sheet_path_pixel_for_pixel(tmp_path: Path, placement: str) -> None:
    """名前（F1 / F001）以外、Sheet経路と連番経路の画像出力は同一バイト。"""
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

    a, b = tmp_path / "from_sheet", tmp_path / "from_frames"
    for name in ("aligned_sheet.png", "compiled_sheet.png", "compiled_sheet_8x.png"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    for index in range(4):
        assert (a / "final_frames" / f"F{index + 1}.png").read_bytes() == (
            b / "final_frames" / f"F{index + 1:03d}.png"
        ).read_bytes()


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


def test_frame_sequence_writes_every_stage_as_separate_zero_padded_files(tmp_path: Path) -> None:
    frames = _frames(3)
    result = compile_character_animation_frames(frames, tmp_path / "out")
    root = tmp_path / "out"
    assert [p.name for p in result.final_frame_paths] == ["F001.png", "F002.png", "F003.png"]
    assert [p.name for p in result.aligned_frame_paths] == ["F001.png", "F002.png", "F003.png"]
    assert sorted(p.name for p in (root / "compiled").iterdir()) == ["F001", "F002", "F003"]
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert [f["frame_id"] for f in report["frames"]] == ["F001", "F002", "F003"]
    assert [f["frame_id"] for f in report["final_frames"]] == ["F001", "F002", "F003"]
    aligned_records = report["stages"]["aligned_frames"]
    assert [r["frame_id"] for r in aligned_records] == ["F001", "F002", "F003"]
    import hashlib
    assert aligned_records[0]["sha256"] == hashlib.sha256(
        (root / "aligned_frames" / "F001.png").read_bytes()
    ).hexdigest()


def test_frame_ids_stay_sortable_beyond_999_frames() -> None:
    assert frame_label(0, 12) == "F001"
    assert frame_label(998, 999) == "F999"
    assert frame_label(0, 1000) == "F0001"
    assert sorted(frame_label(i, 1200) for i in range(1200)) == [frame_label(i, 1200) for i in range(1200)]


def test_archive_frames_are_copied_byte_for_byte_and_recorded(tmp_path: Path) -> None:
    frames = _frames(2)
    originals = []
    for index in range(2):
        path = tmp_path / f"in_{index}.png"
        frames[index].save(path, optimize=True)
        originals.append(path)
    result = compile_character_animation_frames(
        frames, tmp_path / "out",
        archive_frames={"source_frames": originals, "keyed_frames": list(frames)},
    )
    root = tmp_path / "out"
    for index, original in enumerate(originals):
        assert (root / "source_frames" / f"F{index + 1:03d}.png").read_bytes() == original.read_bytes()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["stages"]["source_frames"][0]["original_path"] == str(originals[0])
    assert len(report["stages"]["keyed_frames"]) == 2
    with pytest.raises(ValueError, match="保存できない段階フォルダ"):
        compile_character_animation_frames(frames, tmp_path / "bad", archive_frames={"final_frames": list(frames)})
    with pytest.raises(ValueError, match="枚数"):
        compile_character_animation_frames(frames, tmp_path / "bad2", archive_frames={"source_frames": originals[:1]})


def test_sheets_are_skipped_not_fatal_when_over_the_pixel_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(character_animation, "MAX_ANIMATION_OUTPUT_SHEET_PIXELS", 64 * 64 * 3)
    frames = _frames(4)
    result = compile_character_animation_frames(frames, tmp_path / "out")
    root = tmp_path / "out"
    assert result.aligned_sheet_path is None and result.compiled_sheet_path is None
    assert result.preview_8x_path is None
    assert not (root / "aligned_sheet.png").exists() and not (root / "compiled_sheet.png").exists()
    assert len(list((root / "final_frames").glob("*.png"))) == 4
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["sheets"]["written"] is False
    assert any("Sheet" in warning for warning in result.warnings)
    # 上限内ならSheetも作る
    monkeypatch.setattr(character_animation, "MAX_ANIMATION_OUTPUT_SHEET_PIXELS", 64 * 64 * 4)
    within = compile_character_animation_frames(frames, tmp_path / "out2")
    assert within.compiled_sheet_path is not None and "sheets" not in json.loads(
        within.report_path.read_text(encoding="utf-8")
    )


def test_512_canvas_beyond_the_sheet_limit_is_not_rejected_by_size_check() -> None:
    # 512x512 を65枚 = 上限(16,777,216)超え。以前は弾かれていた組み合わせ
    assert character_animation.animation_sheet_pixels((512, 512), 64) == character_animation.MAX_ANIMATION_OUTPUT_SHEET_PIXELS
    assert character_animation.animation_sheet_pixels((512, 512), 65) > character_animation.MAX_ANIMATION_OUTPUT_SHEET_PIXELS


def test_auto_fit_touching_the_source_edge_is_not_rejected_by_float_noise(monkeypatch) -> None:
    """実素材（槍が元画像の上端で切れるコマ）で発生: 自動フィットの倍率が境界ちょうどに載り、
    -1e-13 のような誤差が「1px見切れ」と誤判定されていた。"""
    import numpy as np
    from pixel_tile_compiler.pixelizer.character_animation import align_character_frames

    frames = []
    for index in range(3):
        array = np.zeros((200, 200, 4), np.uint8)
        array[0:100 + index * 7, 66:83 + index * 3] = (200, 60, 60, 255)  # 上端に接する
        frames.append(Image.fromarray(array, "RGBA"))
    config = CharacterAnimationConfig(
        frame_count=3, canvas_size=(64, 64), fit_within=(62, 62), bottom_margin=1,
        placement_mode="preserve_motion", source_origin=(100.0, 114.0), output_origin=(32.0, 63.0),
        remove_isolated_components=False,
    )
    result = align_character_frames(frames, config)  # 許容ありなら通る
    assert all(report.placed_bbox is not None and report.placed_bbox.top == 0 for report in result.frame_reports)
    # 許容を外すと落ちる = このケースが誤差起因であることの確認
    monkeypatch.setattr(character_animation, "_FIT_EPSILON", 0.0)
    with pytest.raises(ValueError, match="見切れ"):
        align_character_frames(frames, config)
    # 本当に見切れる指定（明示倍率で1pxはみ出す）は許容があっても弾く
    monkeypatch.setattr(character_animation, "_FIT_EPSILON", 1e-9)
    too_big = CharacterAnimationConfig(
        **{**config.__dict__, "scale_override": 5.0, "frame_offsets": None}
    )
    with pytest.raises(ValueError, match="見切れ"):
        align_character_frames(frames, too_big)
