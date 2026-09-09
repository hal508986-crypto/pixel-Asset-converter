"""アルファ可視画素の連結成分を使ったアニメーションSheet分割。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


SourceBox = tuple[int, int, int, int]
ComponentSplitStatus = Literal["resolved", "needs_assignment", "unsupported"]


@dataclass(frozen=True)
class ComponentInfo:
    """V上の一つの8近傍連結成分。component_idは走査順の1始まり。"""

    component_id: int
    row: int | None
    area: int
    detection_pixel_count: int
    bbox: SourceBox
    centroid: tuple[float, float]
    frame_id: str | None = None
    decision_basis: str | None = None
    candidate_frame_ids: tuple[str, ...] = ()
    candidate_distance_squared: tuple[float, ...] = ()
    is_suspicious: bool = False
    suspicious_reason: str | None = None
    is_primary: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "row": self.row,
            "area": self.area,
            "detection_pixel_count": self.detection_pixel_count,
            "bbox": list(self.bbox),
            "centroid": [self.centroid[0], self.centroid[1]],
            "frame_id": self.frame_id,
            "decision_basis": self.decision_basis,
            "candidate_frame_ids": list(self.candidate_frame_ids),
            "candidate_distance_squared": list(self.candidate_distance_squared),
            "is_suspicious": self.is_suspicious,
            "suspicious_reason": self.suspicious_reason,
            "is_primary": self.is_primary,
        }


@dataclass(frozen=True)
class ComponentRowMethod:
    row: int
    y_range: tuple[int, int]
    method: Literal["alpha_gap", "components"]
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "row": self.row,
            "y_range": list(self.y_range),
            "extraction_method": self.method,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComponentCell:
    """所有マスクで抽出した一つの論理フレーム。"""

    frame_id: str
    index: int
    row: int
    column: int
    source_box: SourceBox
    content_box: SourceBox
    logical_origin: tuple[int, int]
    registration_translation: tuple[int, int]
    visible_pixel_count: int
    mask_rle: tuple[tuple[int, int, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "index": self.index,
            "row": self.row,
            "column": self.column,
            "source_box": list(self.source_box),
            "content_box": list(self.content_box),
            "logical_origin": list(self.logical_origin),
            "registration_translation": list(self.registration_translation),
            "visible_pixel_count": self.visible_pixel_count,
            "extraction_kind": "component_mask",
            "mask_encoding": {"version": 1, "rle": [list(run) for run in self.mask_rle]},
        }


@dataclass(frozen=True)
class ComponentSplitResult:
    """成分解析の結果。仮所属状態でも抽出プレビュー(frames)を保持する。"""

    status: ComponentSplitStatus
    reason: str
    rows: int
    columns: int
    source_size: tuple[int, int]
    visible_mask: np.ndarray
    labels: np.ndarray
    components: tuple[ComponentInfo, ...]
    row_bands: tuple[tuple[int, int], ...]
    row_methods: tuple[ComponentRowMethod, ...]
    cells: tuple[ComponentCell, ...]
    frames: tuple[Image.Image, ...]
    owner_labels: np.ndarray | None
    ownership_validation: dict[str, int]
    assignment_status: Literal["geometric", "user_confirmed", "auto_tentative"] | None
    assignment_source: str | None
    overlay: Image.Image
    suspicious_components: tuple[ComponentInfo, ...] = ()
    primary_components: tuple[ComponentInfo, ...] = ()
    is_confirmed: bool = False

    @property
    def frame_count(self) -> int:
        return len(self.frames)


def _projection_bands(mask: np.ndarray, empty_threshold: int, min_gutter_width_px: int) -> tuple[tuple[int, int], ...]:
    projection = mask.sum(axis=0)
    occupied = projection > empty_threshold
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate((*occupied.tolist(), False)):
        if value and start is None:
            start = index
        elif not value and start is not None:
            bands.append((start, index))
            start = None
    if len(bands) < 2:
        return tuple(bands)
    merged = [bands[0]]
    for start, end in bands[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end < min_gutter_width_px:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return tuple(merged)


def _row_projection_bands(mask: np.ndarray, empty_threshold: int, min_gutter_width_px: int) -> tuple[tuple[int, int], ...]:
    projection = mask.sum(axis=1)
    occupied = projection > empty_threshold
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate((*occupied.tolist(), False)):
        if value and start is None:
            start = index
        elif not value and start is not None:
            bands.append((start, index))
            start = None
    if len(bands) < 2:
        return tuple(bands)
    merged = [bands[0]]
    for start, end in bands[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end < min_gutter_width_px:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return tuple(merged)


def _coalesce_bands(bands: tuple[tuple[int, int], ...], expected: int, min_gutter_width_px: int) -> tuple[tuple[int, int], ...]:
    if expected == 1 and bands:
        return ((bands[0][0], bands[-1][1]),)
    if len(bands) <= expected:
        return bands
    work = list(bands)
    while len(work) > expected:
        gaps = [work[index + 1][0] - work[index][1] for index in range(len(work) - 1)]
        smallest = min(gaps)
        if smallest > min_gutter_width_px + 1 or gaps.count(smallest) != 1:
            return bands
        index = gaps.index(smallest)
        work[index : index + 2] = [(work[index][0], work[index + 1][1])]
    return tuple(work)


def _mask_crosses_boundary(mask: np.ndarray, boundary: int, *, axis: int) -> bool:
    if axis == 0:
        before, after = mask[:, boundary - 1], mask[:, boundary]
    else:
        before, after = mask[boundary - 1, :], mask[boundary, :]
    return bool(
        np.any(before & after)
        or np.any(before[:-1] & after[1:])
        or np.any(before[1:] & after[:-1])
    )


def _choose_safe_boundary(mask: np.ndarray, left_end: int, right_start: int, *, axis: int) -> int:
    extent = mask.shape[1] if axis == 0 else mask.shape[0]
    if right_start <= left_end:
        raise ValueError("可視帯の順序が不正です")
    center = (left_end + right_start) // 2
    candidates = sorted(
        range(max(1, left_end), min(extent - 1, right_start) + 1),
        key=lambda value: (abs(value - center), value),
    )
    for candidate in candidates:
        if not _mask_crosses_boundary(mask, candidate, axis=axis):
            return candidate
    raise ValueError("安全な境界がありません")


def _label_visible_components(visible: np.ndarray) -> tuple[np.ndarray, tuple[ComponentInfo, ...]]:
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(visible.astype(np.uint8), connectivity=8)
    components: list[ComponentInfo] = []
    for component_id in range(1, num_labels):
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        min_x = int(stats[component_id, cv2.CC_STAT_LEFT])
        min_y = int(stats[component_id, cv2.CC_STAT_TOP])
        w = int(stats[component_id, cv2.CC_STAT_WIDTH])
        h = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        cent = (float(centroids[component_id][0]), float(centroids[component_id][1]))
        components.append(
            ComponentInfo(
                component_id=component_id,
                row=None,
                area=area,
                detection_pixel_count=0,
                bbox=(min_x, min_y, min_x + w, min_y + h),
                centroid=cent,
            )
        )
    return labels, tuple(components)


def _detection_mask(image: Image.Image, alpha_threshold: int, remove_small_components: bool, min_component_area_px: int) -> np.ndarray:
    alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
    visible = alpha > 0
    threshold = max(1, alpha_threshold)
    mask = (alpha >= threshold) & visible
    if not remove_small_components or min_component_area_px <= 1:
        return mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    kept = np.zeros_like(mask)
    for component_id in range(1, count):
        if int(stats[component_id, cv2.CC_STAT_AREA]) >= min_component_area_px:
            kept[labels == component_id] = True
    return kept


def _component_ids_in_box(labels: np.ndarray, box: SourceBox) -> tuple[int, ...]:
    left, top, right, bottom = box
    values = np.unique(labels[top:bottom, left:right])
    return tuple(int(value) for value in values if int(value) != 0)


def _component_crosses_boundary(labels: np.ndarray, component_id: int, boundary: int, *, axis: int) -> bool:
    component = labels == component_id
    if axis == 0:
        return bool(np.any(component[:, :boundary]) and np.any(component[:, boundary:]))
    return bool(np.any(component[:boundary, :]) and np.any(component[boundary:, :]))


def encode_mask_rle(mask: np.ndarray, box: SourceBox) -> tuple[tuple[int, int, int], ...]:
    """source_box左上基準の半開区間RLEを安定順序で作る。"""
    left, top, right, bottom = box
    local = np.asarray(mask[top:bottom, left:right], dtype=bool)
    runs: list[tuple[int, int, int]] = []
    for y, row in enumerate(local):
        occupied = np.flatnonzero(row)
        if occupied.size == 0:
            continue
        start = int(occupied[0])
        previous = start
        for value in occupied[1:]:
            current = int(value)
            if current != previous + 1:
                runs.append((y, start, previous + 1))
                start = current
            previous = current
        runs.append((y, start, previous + 1))
    return tuple(runs)


def decode_mask_rle(rle: Sequence[Sequence[int]], box: SourceBox) -> np.ndarray:
    """RLEをsource_boxサイズのboolマスクへ復元し、範囲と重複を検査する。"""
    width, height = box[2] - box[0], box[3] - box[1]
    result = np.zeros((height, width), dtype=bool)
    previous_key: tuple[int, int] | None = None
    for raw in rle:
        if len(raw) != 3:
            raise ValueError("mask RLEは[y, x_start, x_end]の配列です")
        y, start, end = (int(value) for value in raw)
        if not (0 <= y < height and 0 <= start < end <= width):
            raise ValueError("mask RLEがsource_boxの範囲外です")
        key = (y, start)
        if previous_key is not None and key < previous_key:
            raise ValueError("mask RLEはy、x_start順で指定してください")
        if np.any(result[y, start:end]):
            raise ValueError("mask RLEに重複区間があります")
        result[y, start:end] = True
        previous_key = key
    return result


def restore_owner_labels_from_cells(
    cells_data: Sequence[Mapping[str, object] | ComponentCell],
    shape: tuple[int, int],
) -> np.ndarray:
    """ComponentCellまたはセルのMapping情報から全体のowner_labelsを完全復元する。"""
    height, width = shape
    owner_labels = np.zeros((height, width), dtype=np.int16)
    for cell in cells_data:
        if isinstance(cell, ComponentCell):
            frame_index = cell.index
            box = cell.source_box
            mask_rle = cell.mask_rle
        elif isinstance(cell, Mapping):
            frame_index = int(cell.get("index", 0))
            raw_box = cell.get("source_box")
            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
                raise ValueError("セルのsource_boxが不正です")
            box = (int(raw_box[0]), int(raw_box[1]), int(raw_box[2]), int(raw_box[3]))
            raw_rle = cell.get("mask_rle")
            if raw_rle is None and isinstance(cell.get("mask_encoding"), Mapping):
                raw_rle = cell["mask_encoding"].get("rle")
            if not isinstance(raw_rle, (list, tuple)):
                raise ValueError("セルのmask_rleが不正です")
            mask_rle = tuple(tuple(int(v) for v in item) for item in raw_rle)
        else:
            raise TypeError(f"不正なセル形式です: {type(cell)}")

        local_mask = decode_mask_rle(mask_rle, box)
        target_region = owner_labels[box[1] : box[3], box[0] : box[2]]
        if np.any(target_region[local_mask] > 0):
            raise ValueError(f"フレームF{frame_index + 1}のマスクが既存フレームと重複しています")
        target_region[local_mask] = frame_index + 1
    return owner_labels


def _frame_for_value(value: object, columns: int, rows: int) -> int:
    if isinstance(value, str) and value.startswith("F"):
        try:
            index = int(value[1:]) - 1
        except ValueError as exc:
            raise ValueError("所属先はF1形式で指定してください") from exc
    else:
        raise ValueError("所属先はF1形式で指定してください")
    if not 0 <= index < columns * rows:
        raise ValueError("所属先がフレーム数の範囲外です")
    return index


def _normalize_assignments(
    assignments: Mapping[object, object],
    component_count: int,
    columns: int,
    rows: int,
    *,
    require_all: bool = False,
) -> dict[int, int]:
    normalized: dict[int, int] = {}
    for raw_id, raw_frame in assignments.items():
        try:
            component_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("所属指定のcomponent_idが不正です") from exc
        if component_id not in range(1, component_count + 1):
            raise ValueError("所属指定に未知の成分IDがあります")
        if component_id in normalized:
            raise ValueError("成分を二重に所属指定できません")
        normalized[component_id] = _frame_for_value(raw_frame, columns, rows)
    if require_all and len(normalized) != component_count:
        raise ValueError("全成分の所属を指定してください")
    return normalized


def _empty_result(
    *,
    status: ComponentSplitStatus,
    reason: str,
    rows: int,
    columns: int,
    source_size: tuple[int, int],
    visible: np.ndarray,
    labels: np.ndarray,
    components: tuple[ComponentInfo, ...],
    row_bands: tuple[tuple[int, int], ...],
    row_methods: tuple[ComponentRowMethod, ...],
    overlay: Image.Image,
) -> ComponentSplitResult:
    visible_count = int(np.count_nonzero(visible))
    return ComponentSplitResult(
        status=status,
        reason=reason,
        rows=rows,
        columns=columns,
        source_size=source_size,
        visible_mask=visible,
        labels=labels,
        components=components,
        row_bands=row_bands,
        row_methods=row_methods,
        cells=(),
        frames=(),
        owner_labels=None,
        ownership_validation={
            "visible_pixel_count": visible_count,
            "assigned_pixel_count": 0,
            "unassigned_pixel_count": visible_count,
            "overlap_pixel_count": 0,
            "rgba_mismatch_count": 0,
        },
        assignment_status=None,
        assignment_source=None,
        overlay=overlay,
        suspicious_components=(),
        primary_components=(),
        is_confirmed=False,
    )


def _render_overlay(
    image: Image.Image,
    labels: np.ndarray,
    components: tuple[ComponentInfo, ...],
    frame_by_component: Mapping[int, int] | None = None,
    owner_labels: np.ndarray | None = None,
) -> Image.Image:
    source = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    colors = (
        (255, 92, 92),
        (92, 170, 255),
        (255, 205, 84),
        (166, 111, 255),
        (78, 214, 161),
        (255, 139, 73),
        (255, 115, 190),
        (80, 200, 230),
        (210, 220, 70),
        (190, 140, 255),
        (120, 230, 140),
        (255, 170, 90),
    )
    overlay = source.copy()
    if owner_labels is not None:
        visible = owner_labels > 0
        color_table = np.zeros((max(len(colors) + 1, int(owner_labels.max()) + 1), 3), dtype=np.uint8)
        for frame_index in range(len(colors)):
            color_table[frame_index + 1] = colors[frame_index % len(colors)]
        for frame_idx in range(len(colors), int(owner_labels.max()) + 1):
            color_table[frame_idx] = colors[(frame_idx - 1) % len(colors)]
        overlay[visible, :3] = color_table[owner_labels[visible]]
        overlay[visible, 3] = np.maximum(overlay[visible, 3], 210)
    else:
        color_table = np.zeros((len(components) + 1, 3), dtype=np.uint8)
        for component in components:
            color_index = frame_by_component.get(component.component_id, component.component_id - 1) if frame_by_component else component.component_id - 1
            color_table[component.component_id] = colors[color_index % len(colors)]
        visible_labels = labels != 0
        overlay[visible_labels, :3] = color_table[labels[visible_labels]]
        overlay[visible_labels, 3] = np.maximum(overlay[visible_labels, 3], 210)

    result = Image.fromarray(overlay, mode="RGBA")
    draw = ImageDraw.Draw(result, "RGBA")
    font = ImageFont.load_default()
    for component in components:
        left, top, right, bottom = component.bbox
        if component.is_suspicious:
            outline_color = (255, 230, 30, 255)
            width = max(2, min(4, result.width // 256 + 1))
        elif component.is_primary:
            outline_color = (255, 255, 255, 240)
            width = max(1, min(3, result.width // 256 + 1))
        else:
            outline_color = (200, 200, 200, 140)
            width = 1

        if component.is_primary or component.is_suspicious or component.area > 20:
            draw.rectangle((left, top, right - 1, bottom - 1), outline=outline_color, width=width)
            if component.is_primary or component.is_suspicious or component.area > 100:
                label = f"C{component.component_id}"
                if component.frame_id:
                    label += f":{component.frame_id}"
                tag_w = len(label) * 7 + 4
                draw.rectangle((left, top, left + tag_w, top + 10), fill=(0, 0, 0, 180))
                draw.text((left + 2, top), label, fill=(255, 255, 255, 255), font=font)
    return result


def render_component_split_overlay(
    image: Image.Image,
    labels: np.ndarray,
    components: tuple[ComponentInfo, ...],
    frame_by_component: Mapping[int, int] | None = None,
    owner_labels: np.ndarray | None = None,
) -> Image.Image:
    """所属確認・色分けオーバーレイを再描画する。"""
    return _render_overlay(image, labels, components, frame_by_component, owner_labels)


def analyze_component_split(
    image: Image.Image,
    *,
    columns: int,
    rows: int,
    alpha_threshold: int = 16,
    remove_small_components: bool = True,
    min_component_area_px: int = 3,
    empty_column_threshold: int = 2,
    empty_row_threshold: int = 2,
    min_gutter_width_px: int = 2,
    assignments: Mapping[object, object] | None = None,
    cells_override: Sequence[Mapping[str, object] | ComponentCell] | None = None,
    confirmed: bool = False,
) -> ComponentSplitResult:
    """Vを正本に全成分を解析し、コマ単位の本体候補特定と実画素距離による周辺仮所属を行う。"""
    if columns < 1 or rows < 1:
        raise ValueError("component split columns and rows must be positive")
    if not 0 <= alpha_threshold <= 255:
        raise ValueError("alpha_threshold must be between 0 and 255")
    if min_component_area_px < 1:
        raise ValueError("min_component_area_px must be positive")
    if empty_column_threshold < 0 or empty_row_threshold < 0:
        raise ValueError("empty band thresholds must be non-negative")
    if min_gutter_width_px < 1:
        raise ValueError("min_gutter_width_px must be positive")

    source = image.convert("RGBA")
    W, H = source.size
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    visible = alpha > 0
    labels, raw_components = _label_visible_components(visible)
    detection = _detection_mask(source, alpha_threshold, remove_small_components, min_component_area_px)

    components = tuple(
        replace(component, detection_pixel_count=int(np.count_nonzero(detection[labels == component.component_id])))
        for component in raw_components
    )
    overlay = _render_overlay(source, labels, components)
    y_bands = _row_projection_bands(detection, empty_row_threshold, min_gutter_width_px)
    y_bands = _coalesce_bands(y_bands, rows, min_gutter_width_px)

    if cells_override is not None:
        owner_labels = restore_owner_labels_from_cells(cells_override, (H, W))
        if len(y_bands) == rows:
            row_bands = y_bands
        else:
            row_boxes: dict[int, list[int]] = {r: [] for r in range(rows)}
            for cell in cells_override:
                r = cell.row if isinstance(cell, ComponentCell) else int(cell.get("row", 0))
                box = cell.source_box if isinstance(cell, ComponentCell) else cell["source_box"]  # type: ignore[index]
                row_boxes.setdefault(r, []).extend([int(box[1]), int(box[3])])
            row_bands = tuple((min(row_boxes[r]), max(row_boxes[r])) if row_boxes.get(r) else (0, H) for r in range(rows))

        updated_comps: list[ComponentInfo] = []
        for comp in components:
            comp_mask = labels == comp.component_id
            assigned_f = owner_labels[comp_mask]
            assigned_f = assigned_f[assigned_f > 0]
            if assigned_f.size > 0:
                most_f = int(np.bincount(assigned_f).argmax()) - 1
                comp_row = most_f // columns
            else:
                comp_row = 0
            updated_comps.append(replace(comp, row=comp_row))
        components = tuple(updated_comps)
        component_by_id = {c.component_id: c for c in components}
        row_methods = tuple(
            ComponentRowMethod(r, row_bands[r] if r < len(row_bands) else (0, H), "components", "cells_override")
            for r in range(rows)
        )
        primary_component_ids = []
        suspicious_ids = set()
        suspicious_reasons = {}
    else:
        if len(y_bands) != rows:
            return _empty_result(
                status="unsupported",
                reason=f"Y方向の可視帯が期待数に一致しません（期待{rows}、検出{len(y_bands)}）",
                rows=rows,
                columns=columns,
                source_size=source.size,
                visible=visible,
                labels=labels,
                components=components,
                row_bands=y_bands,
                row_methods=(),
                overlay=overlay,
            )

        y_boundaries = [0]
        try:
            for previous, current in zip(y_bands, y_bands[1:]):
                y_boundaries.append(_choose_safe_boundary(visible, previous[1], current[0], axis=1))
        except ValueError:
            return _empty_result(
                status="unsupported",
                reason="行の間で半透明の画素がつながっています。元絵の行間を確認してください。",
                rows=rows,
                columns=columns,
                source_size=source.size,
                visible=visible,
                labels=labels,
                components=components,
                row_bands=y_bands,
                row_methods=(),
                overlay=overlay,
            )
        y_boundaries.append(source.height)

        component_rows: dict[int, set[int]] = {component.component_id: set() for component in components}
        for row in range(rows):
            for component_id in _component_ids_in_box(labels, (0, y_boundaries[row], source.width, y_boundaries[row + 1])):
                component_rows[component_id].add(row)
        invalid_row_components = [component_id for component_id, memberships in component_rows.items() if len(memberships) != 1]
        if invalid_row_components:
            return _empty_result(
                status="unsupported",
                reason=f"行をまたぐ可視成分を分離できません（成分: {', '.join(f'C{id}' for id in invalid_row_components)}）",
                rows=rows,
                columns=columns,
                source_size=source.size,
                visible=visible,
                labels=labels,
                components=components,
                row_bands=y_bands,
                row_methods=(),
                overlay=overlay,
            )
        components = tuple(replace(component, row=next(iter(component_rows[component.component_id]))) for component in components)
        component_by_id = {component.component_id: component for component in components}

        owner_labels = np.zeros((H, W), dtype=np.int16)
        row_methods_list: list[ComponentRowMethod] = []
        primary_component_ids = []
        suspicious_ids = set()
        suspicious_reasons = {}
        pending_reasons: list[str] = []

        # 各行の本体候補抽出と画素割り当て
        for row in range(rows):
            row_top, row_bottom = y_boundaries[row], y_boundaries[row + 1]
            row_component_ids = tuple(component.component_id for component in components if component.row == row)
            detection_row = detection[row_top:row_bottom, :]
            visible_row = visible[row_top:row_bottom, :]
            labels_row = labels[row_top:row_bottom, :]

            # 1. まず透明帯分割 (alpha_gap) を試みる
            x_bands = _projection_bands(detection_row, empty_column_threshold, min_gutter_width_px)
            x_bands = _coalesce_bands(x_bands, columns, min_gutter_width_px)
            alpha_gap_success = False
            if len(x_bands) == columns:
                x_boundaries = [0]
                try:
                    for previous, current in zip(x_bands, x_bands[1:]):
                        x_boundaries.append(_choose_safe_boundary(visible_row, previous[1], current[0], axis=0))
                    x_boundaries.append(source.width)
                except ValueError:
                    x_boundaries = []
                if x_boundaries:
                    groups: list[tuple[int, ...]] = []
                    valid_grouping = True
                    for column in range(columns):
                        group = _component_ids_in_box(labels, (x_boundaries[column], row_top, x_boundaries[column + 1], row_bottom))
                        groups.append(group)
                        if not group:
                            valid_grouping = False
                        if any(_component_crosses_boundary(labels, component_id, boundary, axis=0) for component_id in group for boundary in x_boundaries[1:-1]):
                            valid_grouping = False
                    flat_ids = [component_id for group in groups for component_id in group]
                    if (
                        valid_grouping
                        and set(flat_ids) == set(row_component_ids)
                        and len(flat_ids) == len(set(flat_ids))
                        and all(len(group) == 1 for group in groups)
                        and all(component_by_id[component_id].detection_pixel_count > 0 for component_id in flat_ids)
                    ):
                        for column, group in enumerate(groups):
                            frame_index = row * columns + column
                            for component_id in group:
                                owner_labels[row_top:row_bottom, :][labels_row == component_id] = frame_index + 1
                                primary_component_ids.append(component_id)
                        row_methods_list.append(ComponentRowMethod(row, (row_top, row_bottom), "alpha_gap"))
                        alpha_gap_success = True

            if alpha_gap_success:
                continue

            # 2. 成分救済 (components) モード
            num_d, labels_d, stats_d, centroids_d = cv2.connectedComponentsWithStats(detection_row.astype(np.uint8), connectivity=8)
            d_candidates = []
            for d_id in range(1, num_d):
                area = int(stats_d[d_id, cv2.CC_STAT_AREA])
                left = int(stats_d[d_id, cv2.CC_STAT_LEFT])
                top = int(stats_d[d_id, cv2.CC_STAT_TOP])
                w = int(stats_d[d_id, cv2.CC_STAT_WIDTH])
                h = int(stats_d[d_id, cv2.CC_STAT_HEIGHT])
                cent = centroids_d[d_id]
                if area >= min_component_area_px:
                    d_candidates.append((d_id, area, left, top, w, h, cent))

            d_candidates.sort(key=lambda item: -item[1])
            top_seeds = d_candidates[:columns]

            if len(top_seeds) < columns:
                row_methods_list.append(ComponentRowMethod(row, (row_top, row_bottom), "components", "insufficient_primary_candidates"))
                pending_reasons.append(
                    f"{row + 1}行目の本体候補が不足しています（検出{len(top_seeds)}、期待{columns}）。元絵の接触部分を確認してください。"
                )
                continue

            top_seeds.sort(key=lambda item: item[6][0])

            dist_maps = []
            for col_idx, (d_id, s_area, s_left, s_top, s_w, s_h, s_cent) in enumerate(top_seeds):
                seed_mask = (labels_d == d_id).astype(np.uint8)
                dist = cv2.distanceTransform(1 - seed_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
                dist_maps.append(dist)

                intersected_v = np.unique(labels_row[labels_d == d_id])
                intersected_v = [int(v) for v in intersected_v if int(v) != 0]
                if intersected_v:
                    best_v = max(intersected_v, key=lambda v_id: int(np.count_nonzero((labels_row == v_id) & (labels_d == d_id))))
                    primary_component_ids.append(best_v)

            stack_dist = np.stack(dist_maps, axis=0)

            closest_col = np.argmin(stack_dist, axis=0)
            min_dists = np.min(stack_dist, axis=0)
            sorted_dists = np.sort(stack_dist, axis=0)
            second_min_dists = sorted_dists[1] if columns > 1 else np.full_like(min_dists, float("inf"))

            for col_idx in range(columns):
                frame_index = row * columns + col_idx
                frame_pixel_mask = (closest_col == col_idx) & visible_row
                owner_labels[row_top:row_bottom, :][frame_pixel_mask] = frame_index + 1

            row_methods_list.append(ComponentRowMethod(row, (row_top, row_bottom), "components", "tentative_distance_partition"))

            border_mask = (min_dists > 3.0) & ((second_min_dists - min_dists) <= 2.0) & visible_row
            suspicious_v_border = np.unique(labels_row[border_mask])
            for v_id in suspicious_v_border:
                if int(v_id) != 0 and int(v_id) not in primary_component_ids:
                    suspicious_ids.add(int(v_id))
                    suspicious_reasons[int(v_id)] = "境界近傍（複数コマとの距離が近接）"

            for v_id in row_component_ids:
                if v_id in primary_component_ids:
                    continue
                v_info = component_by_id[v_id]
                v_mask_row = labels_row == v_id
                if np.any(v_mask_row):
                    v_min_dist = float(np.min(min_dists[v_mask_row]))
                    if v_min_dist > 40.0 and v_info.area >= 30:
                        suspicious_ids.add(v_id)
                        suspicious_reasons[v_id] = "遠隔大成分（離れたエフェクト・残像候補）"

            for v_id in row_component_ids:
                v_mask_row = labels_row == v_id
                intersected_seeds = [col_idx for col_idx, (d_id, _, _, _, _, _, _) in enumerate(top_seeds) if np.any(v_mask_row & (labels_d == d_id))]
                if len(intersected_seeds) > 1:
                    suspicious_ids.add(v_id)
                    suspicious_reasons[v_id] = "低アルファ接続領域（主要候補間の跨がり）"

        row_methods = tuple(row_methods_list)
        if pending_reasons:
            return _empty_result(
                status="needs_assignment" if len(primary_component_ids) >= 1 else "unsupported",
                reason="；".join(pending_reasons),
                rows=rows,
                columns=columns,
                source_size=source.size,
                visible=visible,
                labels=labels,
                components=components,
                row_bands=y_bands,
                row_methods=row_methods,
                overlay=overlay,
            )

    # ユーザーからの assignments（差分または全体オーバーライド）の適用
    if assignments:
        normalized_assignments = _normalize_assignments(assignments, len(components), columns, rows, require_all=False)
        for comp_id, target_frame_idx in normalized_assignments.items():
            owner_labels[labels == comp_id] = target_frame_idx + 1

    # 各コンポーネントの情報（frame_id, candidate_frame_ids, is_suspicious, is_primary 等）を確定
    updated_components: list[ComponentInfo] = []
    for comp in components:
        comp_mask = labels == comp.component_id
        assigned_frames = owner_labels[comp_mask]
        assigned_frames = assigned_frames[assigned_frames > 0]
        if assigned_frames.size > 0:
            most_common_frame = int(np.bincount(assigned_frames).argmax()) - 1
            frame_id_str = f"F{most_common_frame + 1}"
        else:
            frame_id_str = None

        is_primary = comp.component_id in primary_component_ids
        is_suspicious = comp.component_id in suspicious_ids
        suspicious_reason = suspicious_reasons.get(comp.component_id)

        candidate_fids = [frame_id_str] if frame_id_str else []
        if is_suspicious and comp.row is not None:
            row_start_f = comp.row * columns + 1
            candidate_fids = [f"F{f}" for f in range(row_start_f, row_start_f + columns)]

        updated_components.append(
            replace(
                comp,
                frame_id=frame_id_str,
                is_primary=is_primary,
                is_suspicious=is_suspicious,
                suspicious_reason=suspicious_reason,
                candidate_frame_ids=tuple(candidate_fids),
                decision_basis="user_override" if (assignments and comp.component_id in assignments) else ("primary_body" if is_primary else "distance_partition"),
            )
        )

    components = tuple(updated_components)
    suspicious_components = tuple(comp for comp in components if comp.is_suspicious)
    primary_components = tuple(comp for comp in components if comp.is_primary)

    # 全可視画素の所有権検査とフレーム画像・セルの抽出
    assigned = owner_labels > 0
    visible_count = int(np.count_nonzero(visible))
    assigned_count = int(np.count_nonzero(assigned))
    unassigned_count = int(np.count_nonzero(visible & ~assigned))
    overlap_count = int(np.count_nonzero(~visible & assigned))

    frame_count = rows * columns
    cells: list[ComponentCell] = []
    frames: list[Image.Image] = []
    rgba_source = np.asarray(source, dtype=np.uint8)
    rgba_mismatch_count = 0

    for frame in range(frame_count):
        frame_mask = owner_labels == frame + 1
        ys, xs = np.where(frame_mask)
        if xs.size == 0:
            raise ValueError("各フレームに少なくとも一つの成分を所属させてください")
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        crop = rgba_source[box[1] : box[3], box[0] : box[2]].copy()
        crop_mask = frame_mask[box[1] : box[3], box[0] : box[2]]
        rgba_mismatch_count += int(np.count_nonzero(crop[crop_mask] != rgba_source[box[1] : box[3], box[0] : box[2]][crop_mask]))
        crop[:, :, 3] = np.where(crop_mask, crop[:, :, 3], 0).astype(np.uint8)
        frames.append(Image.fromarray(crop, mode="RGBA"))
        row, column = divmod(frame, columns)
        logical_origin = (round(column * source.width / columns), round(row * source.height / rows))
        cells.append(
            ComponentCell(
                frame_id=f"F{frame + 1}",
                index=frame,
                row=row,
                column=column,
                source_box=box,
                content_box=box,
                logical_origin=logical_origin,
                registration_translation=(box[0] - logical_origin[0], box[1] - logical_origin[1]),
                visible_pixel_count=int(np.count_nonzero(frame_mask)),
                mask_rle=encode_mask_rle(frame_mask, box),
            )
        )

    validation = {
        "visible_pixel_count": visible_count,
        "assigned_pixel_count": assigned_count,
        "unassigned_pixel_count": unassigned_count,
        "overlap_pixel_count": overlap_count,
        "rgba_mismatch_count": rgba_mismatch_count,
    }
    if validation["unassigned_pixel_count"] or validation["overlap_pixel_count"] or validation["rgba_mismatch_count"]:
        raise ValueError("分割時に画素の欠落または重複を検出したため、出力を中止しました。")

    if cells_override is not None:
        status: ComponentSplitStatus = "resolved"
        assignment_status: Literal["geometric", "user_confirmed", "auto_tentative"] = "user_confirmed"
        assignment_source = "cells_override"
        reason = ""
        is_confirmed = True
    elif confirmed or assignments is not None:
        status: ComponentSplitStatus = "resolved"
        assignment_status: Literal["geometric", "user_confirmed", "auto_tentative"] = "user_confirmed" if (confirmed or assignments) else "geometric"
        assignment_source = "user_confirmed" if confirmed else "component_assignments"
        reason = ""
        is_confirmed = True
    elif len(suspicious_components) > 0:
        status = "needs_assignment"
        assignment_status = "auto_tentative"
        assignment_source = "automatic_distance_partition"
        has_bridge = any(c.suspicious_reason and "跨がり" in c.suspicious_reason for c in suspicious_components)
        bridge_hint = "（低アルファ接続・主要候補の確認が必要です）" if has_bridge else ""
        reason = f"{len(suspicious_components)}件の疑わしい成分があります。プレビューを確認して確定してください。{bridge_hint}"
        is_confirmed = False
    else:
        # 疑わしい成分がなく、全コマ綺麗に成立している幾何的完全成立
        status = "resolved"
        assignment_status = "geometric"
        assignment_source = "automatic_distance_partition"
        reason = ""
        is_confirmed = True

    final_overlay = _render_overlay(source, labels, components, owner_labels=owner_labels)

    return ComponentSplitResult(
        status=status,
        reason=reason,
        rows=rows,
        columns=columns,
        source_size=source.size,
        visible_mask=visible,
        labels=labels,
        components=components,
        row_bands=y_bands,
        row_methods=tuple(row_methods),
        cells=tuple(cells),
        frames=tuple(frames),
        owner_labels=owner_labels,
        ownership_validation=validation,
        assignment_status=assignment_status,
        assignment_source=assignment_source,
        overlay=final_overlay,
        suspicious_components=suspicious_components,
        primary_components=primary_components,
        is_confirmed=is_confirmed,
    )


__all__ = [
    "ComponentCell",
    "ComponentInfo",
    "ComponentRowMethod",
    "ComponentSplitResult",
    "analyze_component_split",
    "decode_mask_rle",
    "encode_mask_rle",
    "render_component_split_overlay",
    "restore_owner_labels_from_cells",
]
