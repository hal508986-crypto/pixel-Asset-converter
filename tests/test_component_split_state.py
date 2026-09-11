import numpy as np
from PIL import Image

from pixel_tile_compiler.gui.component_split_state import ComponentSplitStateManager
from pixel_tile_compiler.sheet.component_split import (
    ComponentCell,
    ComponentInfo,
    ComponentSplitResult,
)


def _make_dummy_result(status="resolved", assignments=None, cells=None) -> ComponentSplitResult:
    components = (
        ComponentInfo(
            component_id=1,
            row=0,
            area=100,
            detection_pixel_count=100,
            bbox=(0, 0, 10, 10),
            centroid=(5.0, 5.0),
            frame_id=(assignments or {}).get(1, "F1"),
            candidate_frame_ids=("F1",),
        ),
        ComponentInfo(
            component_id=2,
            row=0,
            area=100,
            detection_pixel_count=100,
            bbox=(20, 0, 30, 10),
            centroid=(25.0, 5.0),
            frame_id=(assignments or {}).get(2, "F2"),
            candidate_frame_ids=("F2",),
        ),
    )
    if cells is None:
        cells = (
            ComponentCell(
                frame_id="F1",
                index=0,
                row=0,
                column=0,
                source_box=(0, 0, 10, 10),
                content_box=(0, 0, 10, 10),
                logical_origin=(0, 0),
                registration_translation=(0, 0),
                visible_pixel_count=100,
                mask_rle=(),
            ),
            ComponentCell(
                frame_id="F2",
                index=1,
                row=0,
                column=1,
                source_box=(20, 0, 30, 10),
                content_box=(20, 0, 30, 10),
                logical_origin=(16, 0),
                registration_translation=(4, 0),
                visible_pixel_count=100,
                mask_rle=(),
            ),
        )
    return ComponentSplitResult(
        status=status,
        reason="" if status == "resolved" else "要確認",
        rows=1,
        columns=2,
        source_size=(32, 16),
        visible_mask=np.zeros((16, 32), dtype=bool),
        labels=np.zeros((16, 32), dtype=np.int32),
        components=components,
        row_bands=((0, 16),),
        row_methods=(),
        cells=cells,
        frames=(Image.new("RGBA", (10, 10)), Image.new("RGBA", (10, 10))),
        owner_labels=None,
        ownership_validation={},
        assignment_status="user_confirmed" if status == "resolved" else "auto_tentative",
        assignment_source="gui",
        overlay=Image.new("RGBA", (32, 16)),
        suspicious_components=(),
    )


def test_state_maintains_confirmation_when_analysis_result_matches():
    state = ComponentSplitStateManager()
    result1 = _make_dummy_result("resolved", {1: "F1", 2: "F2"})
    sig1 = ("path", "row_alpha_components", 2, 1, ())

    # 1回目解析反映 → 未確定
    assert state.apply_analysis(result1, sig1, confirmed=False) is False
    assert state.confirmed is False
    assert state.status_message(result1) == "パーツをコマへ分けました。確定するとコンパイルできます。"

    # 確定
    state.confirm(result1, sig1)
    assert state.confirmed is True
    assert state.status_message(result1) == "全コマの割り当てを確定しました。コンパイル可能です。"

    # 2回目再解析反映（同一内容）
    result2 = _make_dummy_result("resolved", {1: "F1", 2: "F2"})
    assert state.apply_analysis(result2, sig1, confirmed=False) is True
    assert state.confirmed is True
    assert state.status_message(result2) == "全コマの割り当てを確定しました。コンパイル可能です。"


def test_state_drops_confirmation_when_assignments_differ():
    state = ComponentSplitStateManager()
    result1 = _make_dummy_result("resolved", {1: "F1", 2: "F2"})
    sig1 = ("path", "row_alpha_components", 2, 1, ())

    state.confirm(result1, sig1)
    assert state.confirmed is True

    # 手動変更
    state.record_manual_change(1, "F2")
    assert state.confirmed is False
    assert state.has_unconfirmed_changes is True

    # 異なる割り当てで再解析結果が返る
    result_diff = _make_dummy_result("resolved", {1: "F2", 2: "F2"})
    assert state.apply_analysis(result_diff, sig1, confirmed=False) is False
    assert state.confirmed is False
    assert state.status_message(result_diff) == "割り当てを変更しました。再度割り当てを確定してください。"


def test_state_invalidate_clears_everything():
    state = ComponentSplitStateManager()
    result1 = _make_dummy_result("resolved", {1: "F1", 2: "F2"})
    sig1 = ("path", "row_alpha_components", 2, 1, ())
    state.confirm(result1, sig1)

    state.invalidate()
    assert state.analysis is None
    assert state.signature is None
    assert state.confirmed is False
    assert state.confirmed_assignments is None
    assert state.confirmed_cells is None
    assert state.overrides == {}
    assert state.has_unconfirmed_changes is False


def test_can_confirm_rejects_empty_cells():
    state = ComponentSplitStateManager()
    empty_result = _make_dummy_result("needs_assignment", cells=())
    empty_result = empty_result.__class__(
        **{**empty_result.__dict__, "reason": "2行目の本体候補が不足しています"}
    )
    can, reason = state.can_confirm(empty_result)
    assert can is False
    assert "2行目の本体候補が不足しています" in reason


def test_status_message_reports_reason_when_cells_empty():
    state = ComponentSplitStateManager()
    empty_result = _make_dummy_result("needs_assignment", cells=())
    empty_result = empty_result.__class__(
        **{**empty_result.__dict__, "reason": "2行目の本体候補が不足しています"}
    )
    # cellsが空なら未確定時はもちろん、たとえ確定フラグが立っていても理由を返すべき
    assert state.status_message(empty_result) == "2行目の本体候補が不足しています"

