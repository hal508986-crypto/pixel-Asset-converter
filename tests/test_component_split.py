from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    compile_character_animation_sheet,
    load_character_animation_report,
    load_component_assignments,
    map_source_point_to_frame,
    prepare_character_animation_sheet,
    save_component_assignments,
)
from pixel_tile_compiler.sheet.alpha_projection import split_sprite_sheet
from pixel_tile_compiler.sheet.component_split import analyze_component_split, decode_mask_rle


def _sheet(size: tuple[int, int] = (60, 36)) -> Image.Image:
    return Image.new("RGBA", size, (17, 23, 31, 0))


def _paint(image: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int, int]) -> None:
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            image.putpixel((x, y), color)


def test_overlapping_bboxes_split_by_components() -> None:
    image = _sheet()
    _paint(image, (4, 5, 25, 11), (220, 50, 50, 255))
    _paint(image, (16, 15, 37, 22), (50, 80, 220, 255))

    result = analyze_component_split(image, columns=2, rows=1, remove_small_components=False)

    assert result.status == "resolved"
    assert len(result.components) == 2
    assert result.components[0].bbox[0] < result.components[1].bbox[2]
    assert result.cells[0].source_box[2] > result.cells[1].source_box[0]
    assert result.row_methods[0].method == "components"


def test_component_masks_preserve_rgba_partition() -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 4, 17, 15), (220, 50, 50, 255))
    _paint(image, (25, 7, 40, 19), (50, 80, 220, 255))
    image.putpixel((30, 10), (50, 80, 220, 1))

    result = analyze_component_split(image, columns=2, rows=1, remove_small_components=False)

    assert result.status == "resolved"
    assert result.ownership_validation == {
        "visible_pixel_count": int(np.count_nonzero(np.asarray(image.getchannel("A")))),
        "assigned_pixel_count": int(np.count_nonzero(np.asarray(image.getchannel("A")))),
        "unassigned_pixel_count": 0,
        "overlap_pixel_count": 0,
        "rgba_mismatch_count": 0,
    }


def test_low_alpha_bridge_is_not_cut() -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 7, 17, 15), (220, 50, 50, 255))
    _paint(image, (31, 7, 45, 15), (50, 80, 220, 255))
    for x in range(17, 31):
        image.putpixel((x, 11), (120, 120, 120, 1))

    result = analyze_component_split(image, columns=2, rows=1, remove_small_components=True)

    assert len(result.components) == 1
    assert result.status == "needs_assignment"
    assert "主要候補" in result.reason


def test_full_image_labels_reject_row_crossing() -> None:
    image = _sheet((48, 48))
    _paint(image, (4, 5, 18, 16), (220, 50, 50, 255))
    _paint(image, (30, 31, 44, 42), (50, 80, 220, 255))
    for y in range(16, 32):
        image.putpixel((11, y), (120, 120, 120, 1))

    result = analyze_component_split(image, columns=2, rows=2, remove_small_components=True)

    assert result.status == "unsupported"
    assert "行の間" in result.reason


def test_mixed_gap_and_component_rows() -> None:
    image = _sheet((72, 60))
    for left, color in ((4, (220, 50, 50, 255)), (28, (50, 220, 80, 255)), (52, (50, 80, 220, 255))):
        _paint(image, (left, 4, left + 12, 14), color)
        _paint(image, (left, 50, left + 12, 60), color)
    _paint(image, (4, 24, 40, 31), (220, 180, 50, 255))
    _paint(image, (30, 34, 66, 41), (180, 50, 220, 255))
    _paint(image, (52, 24, 68, 31), (50, 180, 220, 255))

    result = analyze_component_split(image, columns=3, rows=3, remove_small_components=False)

    assert result.status == "resolved"
    assert [method.method for method in result.row_methods] == ["alpha_gap", "components", "alpha_gap"]
    assert result.ownership_validation["unassigned_pixel_count"] == 0


def test_unassigned_components_block_compile() -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (31, 5, 45, 17), (50, 80, 220, 255))
    image.putpixel((24, 20), (255, 255, 255, 1))

    result = analyze_component_split(image, columns=2, rows=1, remove_small_components=True)

    assert result.status == "needs_assignment"
    assert len(result.frames) == 2  # 仮所属プレビューは生成される
    assert "疑わしい" in result.reason or "確定" in result.reason


def test_component_registration_preserves_motion() -> None:
    image = _sheet((13, 12))
    _paint(image, (1, 2, 4, 7), (220, 50, 50, 255))
    _paint(image, (8, 3, 12, 8), (50, 80, 220, 255))

    result = analyze_component_split(image, columns=2, rows=1, remove_small_components=False)

    assert result.status == "resolved"
    assert result.cells[0].logical_origin == (0, 0)
    assert result.cells[1].logical_origin == (6, 0)
    assert result.cells[1].registration_translation == (2, 3)


def test_satellite_candidates_require_confirmation_and_assignments_cover_all_components() -> None:
    image = _sheet((64, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (38, 5, 52, 17), (50, 80, 220, 255))
    image.putpixel((27, 7), (255, 255, 255, 255))

    pending = analyze_component_split(image, columns=2, rows=1, remove_small_components=False)

    assert pending.status == "needs_assignment"
    assert set(pending.components[-1].candidate_frame_ids) == {"F1", "F2"}
    assignments = {component.component_id: ("F1" if component.bbox[0] < 30 else "F2") for component in pending.components}
    resolved = analyze_component_split(
        image,
        columns=2,
        rows=1,
        remove_small_components=False,
        assignments=assignments,
    )

    assert resolved.status == "resolved"
    assert resolved.assignment_status == "user_confirmed"
    assert len(resolved.frames) == 2


def test_invalid_assignment_is_rejected() -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (31, 5, 45, 17), (50, 80, 220, 255))

    with pytest.raises(ValueError, match="所属"):
        analyze_component_split(
            image,
            columns=2,
            rows=1,
            remove_small_components=False,
            assignments={999: "F1"},
        )

    with pytest.raises(ValueError, match="各フレーム"):
        analyze_component_split(
            image,
            columns=2,
            rows=1,
            remove_small_components=False,
            assignments={1: "F1", 2: "F1"},
        )

    with pytest.raises(ValueError, match="二重"):
        analyze_component_split(
            image,
            columns=2,
            rows=1,
            remove_small_components=False,
            assignments={"1": "F1", "01": "F2"},
        )

    # 部分オーバーライドは許容される（全成分個別指定の義務は撤回）
    partial = analyze_component_split(
        image,
        columns=2,
        rows=1,
        remove_small_components=False,
        assignments={1: "F1"},
    )
    assert partial.status == "resolved"


def test_row_alpha_components_is_exposed_as_schema_four_split_result() -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 4, 17, 15), (220, 50, 50, 255))
    _paint(image, (25, 7, 40, 19), (50, 80, 220, 255))

    result = split_sprite_sheet(image, mode="row_alpha_components", columns=2, rows=1, remove_small_components=False)

    assert result.detected_mode == "row_alpha_components"
    assert result.frame_count == 2
    assert result.report_as_dict()["schema_version"] == 4
    assert result.report_as_dict()["extraction_kind"] == "component_mask"
    assert result.report_as_dict()["x_bands"] == []
    assert result.report_as_dict()["ownership_validation"]["unassigned_pixel_count"] == 0


def test_hybrid_component_rescue_keeps_warning() -> None:
    image = _sheet()
    _paint(image, (4, 5, 25, 11), (220, 50, 50, 255))
    _paint(image, (16, 15, 37, 22), (50, 80, 220, 255))

    result = split_sprite_sheet(image, mode="hybrid", columns=2, rows=1, remove_small_components=False)

    assert result.detected_mode == "row_alpha_components"
    assert result.split_status == "resolved"
    assert result.report_as_dict()["quality_status"] == "warning"
    assert any("救済" in warning for warning in result.report_as_dict()["warnings"])


def test_character_animation_compiles_component_masks_without_mixing_overlapping_boxes(tmp_path) -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 4, 25, 11), (220, 50, 50, 255))
    _paint(image, (16, 15, 40, 22), (50, 80, 220, 255))
    source = tmp_path / "components.png"
    image.save(source)

    result = compile_character_animation_sheet(
        source,
        tmp_path / "output",
        config=CharacterAnimationConfig(
            frame_count=2,
            split_mode="row_alpha_components",
            grid_columns=2,
            grid_rows=1,
            canvas_size=(32, 32),
            fit_within=(28, 28),
            bottom_margin=2,
            remove_isolated_components=False,
        ),
    )

    assert len(result.final_frame_paths) == 2
    report = result.report_path.read_text(encoding="utf-8")
    assert '"schema_version": 4' in report


def test_component_compile_failure_keeps_existing_outputs(tmp_path) -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (31, 5, 45, 17), (50, 80, 220, 255))
    image.putpixel((24, 20), (255, 255, 255, 1))
    source = tmp_path / "pending.png"
    image.save(source)
    output = tmp_path / "existing-output"
    output.mkdir()
    sentinel = output / "user-note.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="疑わしい|確定|未割当"):
        compile_character_animation_sheet(
            source,
            output,
            config=CharacterAnimationConfig(
                frame_count=2,
                split_mode="row_alpha_components",
                grid_columns=2,
                grid_rows=1,
                remove_isolated_components=True,
            ),
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (output / "aligned_sheet.png").exists()


def test_assignment_replay_and_rle_roundtrip(tmp_path) -> None:
    image = _sheet((64, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (37, 5, 51, 17), (50, 80, 220, 255))
    image.putpixel((20, 7), (255, 255, 255, 255))
    source = tmp_path / "replay.png"
    image.save(source)
    config = CharacterAnimationConfig(
        frame_count=2,
        split_mode="row_alpha_components",
        grid_columns=2,
        grid_rows=1,
        remove_isolated_components=False,
    )
    pending = analyze_component_split(image, columns=2, rows=1, remove_small_components=False)
    assignments = {component.component_id: ("F1" if component.bbox[0] < 30 else "F2") for component in pending.components}
    path = tmp_path / "component_assignments.json"
    save_component_assignments(source, config, assignments, path)
    loaded = load_component_assignments(source, config, path)
    first = analyze_component_split(image, columns=2, rows=1, remove_small_components=False, assignments=loaded)
    second = analyze_component_split(image, columns=2, rows=1, remove_small_components=False, assignments=loaded)

    assert first.cells == second.cells
    assert [frame.tobytes() for frame in first.frames] == [frame.tobytes() for frame in second.frames]
    for cell in first.cells:
        local = decode_mask_rle(cell.mask_rle, cell.source_box)
        assert int(local.sum()) == cell.visible_pixel_count


def test_stale_assignment_rejected(tmp_path) -> None:
    image = _sheet((48, 24))
    _paint(image, (3, 5, 17, 17), (220, 50, 50, 255))
    _paint(image, (31, 5, 45, 17), (50, 80, 220, 255))
    source = tmp_path / "stale.png"
    image.save(source)
    config = CharacterAnimationConfig(
        frame_count=2,
        split_mode="row_alpha_components",
        grid_columns=2,
        grid_rows=1,
        remove_isolated_components=False,
    )
    path = tmp_path / "component_assignments.json"
    save_component_assignments(source, config, {1: "F1", 2: "F2"}, path)
    image.putpixel((20, 10), (1, 2, 3, 255))
    image.save(source)

    with pytest.raises(ValueError, match="元絵"):
        load_component_assignments(source, config, path)


def test_protected_mask_uses_component_coordinates() -> None:
    image = _sheet((24, 12))
    _paint(image, (2, 3, 10, 8), (220, 50, 50, 255))
    _paint(image, (15, 3, 22, 8), (50, 80, 220, 255))
    protected = Image.new("L", image.size, 0)
    protected.putpixel((5, 5), 255)

    result = prepare_character_animation_sheet(
        image,
        CharacterAnimationConfig(
            frame_count=2,
            split_mode="row_alpha_components",
            grid_columns=2,
            grid_rows=1,
            canvas_size=(24, 12),
            fit_within=(24, 12),
            bottom_margin=0,
            placement_mode="preserve_motion",
            source_origin=(0, 0),
            output_origin=(0, 0),
            scale_override=1.0,
            remove_isolated_components=False,
        ),
        protected_masks=(protected, None),
    )

    aligned_mask = result.protected_masks[0]
    assert aligned_mask is not None
    assert aligned_mask.getpixel((5, 5))[3] > 0
    assert aligned_mask.getpixel((17, 5))[3] == 0


def test_component_schema_reader_accepts_three_and_four_and_rejects_unknown(tmp_path) -> None:
    report = {
        "schema_version": 3,
        "sprite_sheet_split": {"schema_version": 3},
    }
    path = tmp_path / "report-v3.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert load_character_animation_report(path)["schema_version"] == 3

    report["schema_version"] = 4
    report["sprite_sheet_split"]["schema_version"] = 4
    path.write_text(json.dumps(report), encoding="utf-8")
    assert load_character_animation_report(path)["schema_version"] == 4

    report["schema_version"] = 5
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="未対応"):
        load_character_animation_report(path)


def test_owner_based_click_wins_when_component_bboxes_overlap() -> None:
    frame_boxes = ((2, 2, 12, 12), (8, 2, 18, 12))
    logical_origins = ((0, 0), (10, 0))
    owners = np.zeros((20, 20), dtype=np.int16)
    owners[5, 9] = 2

    assert map_source_point_to_frame(
        (9, 5),
        frame_boxes,
        logical_origins=logical_origins,
        owner_labels=owners,
    ) == (1, (-1, 5))
    with pytest.raises(ValueError, match="透明"):
        map_source_point_to_frame(
            (5, 5),
            frame_boxes,
            logical_origins=logical_origins,
            owner_labels=owners,
        )
    assert map_source_point_to_frame(
        (5, 5),
        frame_boxes,
        logical_origins=logical_origins,
        owner_labels=owners,
        selected_frame_index=0,
    ) == (0, (5, 5))
