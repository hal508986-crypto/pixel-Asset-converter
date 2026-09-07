"""Qt-independent state contracts for terrain batch conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pixel_tile_compiler.config import CompilerConfig


BatchItemStatus = Literal["waiting", "running", "success", "failed", "cancelled"]
BatchRunPhase = Literal["initial", "fixed"]


@dataclass
class TerrainBatchResult:
    """One immutable-in-history compilation result for a batch item."""

    result_id: str
    phase: BatchRunPhase | Literal["legacy"]
    status: BatchItemStatus
    source_sha256: str | None = None
    previous_result_id: str | None = None
    output_root: Path | None = None
    final_path: Path | None = None
    final_sha256: str | None = None
    actual_palette: tuple[tuple[int, int, int], ...] = ()
    config_snapshot: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    runtime_config: CompilerConfig | None = field(default=None, repr=False, compare=False)

    @property
    def actual_palette_count(self) -> int:
        return len(self.actual_palette)

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "phase": self.phase,
            "status": self.status,
            "source_sha256": self.source_sha256,
            "previous_result_id": self.previous_result_id,
            "output_root": str(self.output_root) if self.output_root is not None else None,
            "final_path": str(self.final_path) if self.final_path is not None else None,
            "final_sha256": self.final_sha256,
            "actual_palette": [list(color) for color in self.actual_palette],
            "actual_palette_count": self.actual_palette_count,
            "config_snapshot": self.config_snapshot,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "warnings": list(self.warnings),
        }


@dataclass
class TerrainBatchItem:
    """A source image and its append-only result history."""

    item_id: str
    display_name: str
    source_path: Path | None
    source_sha256: str | None
    source_dimensions: tuple[int, int] | None
    config_snapshot: dict[str, Any] | None
    checked: bool = True
    status: BatchItemStatus = "waiting"
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    results: list[TerrainBatchResult] = field(default_factory=list)
    runtime_config: CompilerConfig | None = field(default=None, repr=False, compare=False)

    @property
    def latest_success(self) -> TerrainBatchResult | None:
        for result in reversed(self.results):
            if result.status == "success":
                return result
        return None

    def result_by_id(self, result_id: str) -> TerrainBatchResult | None:
        return next((result for result in self.results if result.result_id == result_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "display_name": self.display_name,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "source_sha256": self.source_sha256,
            "source_dimensions": list(self.source_dimensions) if self.source_dimensions else None,
            "config_snapshot": self.config_snapshot,
            "checked": self.checked,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "warnings": list(self.warnings),
            "results": [result.as_dict() for result in self.results],
        }


@dataclass
class TerrainBatch:
    """Batch state; storage may be read-only when imported from a legacy manifest."""

    batch_id: str
    items: list[TerrainBatchItem] = field(default_factory=list)
    storage_root: Path | None = None
    manifest_path: Path | None = None
    read_only: bool = False
    created_at: str | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)

    def item_by_id(self, item_id: str) -> TerrainBatchItem | None:
        return next((item for item in self.items if item.item_id == item_id), None)

    def result_by_id(self, result_id: str) -> TerrainBatchResult | None:
        for item in self.items:
            result = item.result_by_id(result_id)
            if result is not None:
                return result
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "batch_id": self.batch_id,
            "created_at": self.created_at,
            "storage_root": str(self.storage_root) if self.storage_root is not None else None,
            "read_only": self.read_only,
            "items": [item.as_dict() for item in self.items],
            "runs": list(self.runs),
        }


@dataclass(frozen=True)
class FrozenTerrainBatchItem:
    """Source and effective conditions captured at run start."""

    item_id: str
    display_name: str
    source_path: Path | None
    source_sha256: str | None
    source_dimensions: tuple[int, int] | None
    previous_result_id: str | None
    config_snapshot: dict[str, Any] | None
    runtime_config: CompilerConfig | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "display_name": self.display_name,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "source_sha256": self.source_sha256,
            "source_dimensions": list(self.source_dimensions) if self.source_dimensions else None,
            "previous_result_id": self.previous_result_id,
            "config_snapshot": self.config_snapshot,
        }


@dataclass(frozen=True)
class TerrainBatchExecutionSnapshot:
    """Frozen request passed to the single sequential worker."""

    batch_id: str
    run_id: str
    phase: BatchRunPhase
    run_root: Path
    target_item_ids: tuple[str, ...]
    items: tuple[FrozenTerrainBatchItem, ...]
    reference_result_id: str | None = None
    reference_final_sha256: str | None = None
    palette_id: str | None = None
    palette_colors: tuple[tuple[int, int, int], ...] = ()
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "batch_id": self.batch_id,
            "run_id": self.run_id,
            "phase": self.phase,
            "run_root": str(self.run_root),
            "target_item_ids": list(self.target_item_ids),
            "items": [item.as_dict() for item in self.items],
            "reference_result_id": self.reference_result_id,
            "reference_final_sha256": self.reference_final_sha256,
            "palette_id": self.palette_id,
            "palette_colors": [list(color) for color in self.palette_colors],
            "created_at": self.created_at,
        }


@dataclass
class TerrainBatchRun:
    """Execution result returned after a snapshot has been processed."""

    run_id: str
    batch_id: str
    phase: BatchRunPhase
    run_root: Path
    snapshot: TerrainBatchExecutionSnapshot
    results: list[TerrainBatchResult] = field(default_factory=list)
    status: Literal["running", "success", "partial", "cancelled"] = "running"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "phase": self.phase,
            "status": self.status,
            "snapshot": self.snapshot.as_dict(),
            "results": [result.as_dict() for result in self.results],
        }


@dataclass(frozen=True)
class TerrainBatchProgress:
    """Worker-to-UI progress payload without a Qt dependency."""

    index: int
    total: int
    item_id: str
    phase: BatchRunPhase
    status: BatchItemStatus
    message: str


__all__ = [
    "BatchItemStatus",
    "BatchRunPhase",
    "FrozenTerrainBatchItem",
    "TerrainBatch",
    "TerrainBatchExecutionSnapshot",
    "TerrainBatchItem",
    "TerrainBatchResult",
    "TerrainBatchRun",
    "TerrainBatchProgress",
]
