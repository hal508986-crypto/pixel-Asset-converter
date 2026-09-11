"""Component split state management for the character animation workbench."""

from __future__ import annotations

from dataclasses import dataclass, field

from pixel_tile_compiler.sheet.component_split import ComponentCell, ComponentSplitResult


@dataclass
class ComponentSplitStateManager:
    """Manages component split analysis results, overrides, confirmation snapshots, and status messages."""

    analysis: ComponentSplitResult | None = None
    signature: tuple[object, ...] | None = None
    confirmed: bool = False
    confirmed_assignments: dict[int, str] | None = None
    confirmed_cells: tuple[ComponentCell, ...] | None = None
    overrides: dict[int, str] = field(default_factory=dict)
    has_unconfirmed_changes: bool = False

    def can_confirm(self, result: ComponentSplitResult | None) -> tuple[bool, str]:
        """Check if the analysis result can be confirmed."""
        if result is None:
            return False, "先にパーツを解析してください"
        if len(result.cells) == 0:
            reason = result.reason or "コマが抽出されていません（本体候補不足など）"
            return False, f"コマが抽出できていないため確定できません: {reason}"
        if result.status == "unsupported":
            return False, f"対象外の入力のため確定できません: {result.reason}"
        return True, ""

    def is_same_as_confirmed(self, result: ComponentSplitResult) -> bool:
        """Check if the given analysis result matches the last confirmed assignments and cells."""
        if self.confirmed_assignments is None or self.confirmed_cells is None:
            return False
        if len(result.cells) == 0:
            return False
        if result.status != "resolved":
            return False
        current_assignments = {
            comp.component_id: comp.frame_id
            for comp in result.components
            if comp.frame_id is not None
        }
        if current_assignments != self.confirmed_assignments:
            return False
        if tuple(result.cells) != self.confirmed_cells:
            return False
        return True

    def apply_analysis(
        self,
        result: ComponentSplitResult,
        signature: tuple[object, ...],
        *,
        confirmed: bool = False,
    ) -> bool:
        """Apply a new analysis result, maintaining confirmation if identical."""
        self.analysis = result
        self.signature = signature

        if confirmed:
            can, _reason = self.can_confirm(result)
            if not can:
                self.confirmed = False
                return False
            self.confirmed = True
            self.confirmed_assignments = {
                comp.component_id: comp.frame_id
                for comp in result.components
                if comp.frame_id is not None
            }
            self.confirmed_cells = tuple(result.cells)
            self.has_unconfirmed_changes = False
            return True

        if self.is_same_as_confirmed(result):
            # 前回と同じ確定済み内容なら確定を維持する
            self.confirmed = True
            self.has_unconfirmed_changes = False
            return True

        # 内容が異なる、または初回、または未解決
        self.confirmed = False
        return False

    def confirm(self, result: ComponentSplitResult, signature: tuple[object, ...]) -> None:
        """Record explicit confirmation."""
        can, _reason = self.can_confirm(result)
        if not can:
            self.confirmed = False
            return
        self.analysis = result
        self.signature = signature
        self.confirmed = True
        self.confirmed_assignments = {
            comp.component_id: comp.frame_id
            for comp in result.components
            if comp.frame_id is not None
        }
        self.confirmed_cells = tuple(result.cells)
        self.has_unconfirmed_changes = False

    def record_manual_change(self, component_id: int, frame_id: str | None) -> None:
        """Record user modification to an assignment."""
        if frame_id is not None:
            self.overrides[component_id] = str(frame_id)
        else:
            self.overrides.pop(component_id, None)
        self.confirmed = False
        self.has_unconfirmed_changes = True

    def reset_overrides(self) -> None:
        """Reset manual overrides."""
        self.overrides.clear()
        self.confirmed = False
        self.has_unconfirmed_changes = False
        self.confirmed_assignments = None
        self.confirmed_cells = None

    def invalidate(self) -> None:
        """Invalidate all analysis state and confirmation due to setting changes."""
        self.analysis = None
        self.signature = None
        self.confirmed = False
        self.confirmed_assignments = None
        self.confirmed_cells = None
        self.overrides.clear()
        self.has_unconfirmed_changes = False

    def status_message(self, result: ComponentSplitResult) -> str:
        """Determine guidance text based on actual state."""
        if len(result.cells) == 0:
            return result.reason or "コマが抽出されていません。元絵や設定を確認してください。"
        if self.confirmed:
            return "全コマの割り当てを確定しました。コンパイル可能です。"
        if self.has_unconfirmed_changes:
            return "割り当てを変更しました。再度割り当てを確定してください。"
        if result.status == "resolved":
            return "パーツをコマへ分けました。確定するとコンパイルできます。"
        return result.reason
