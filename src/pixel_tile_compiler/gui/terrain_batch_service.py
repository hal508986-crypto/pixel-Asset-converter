"""Qt-independent batch execution and legacy-manifest loading for terrain work."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, UnidentifiedImageError

from pixel_tile_compiler.config import CanvasSpec, ColorConditioningConfig, CompilerConfig
from pixel_tile_compiler.gui.input import SUPPORTED_IMAGE_SUFFIXES
from pixel_tile_compiler.gui.terrain_batch_model import (
    FrozenTerrainBatchItem,
    TerrainBatch,
    TerrainBatchExecutionSnapshot,
    TerrainBatchItem,
    TerrainBatchProgress,
    TerrainBatchResult,
    TerrainBatchRun,
)
from pixel_tile_compiler.palette_contract import (
    extract_final_palette,
    fixed_palette_config,
    metadata_palette_matches_final,
    palette_id,
    validate_reference_palette,
)
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


ProgressCallback = Callable[[TerrainBatchProgress], None]
CancelCallback = Callable[[], bool]


class TerrainBatchError(RuntimeError):
    """Base error for batch workflow failures."""


class TerrainBatchManifestError(TerrainBatchError):
    """Raised when a manifest cannot be safely imported."""


class TerrainBatchReadOnlyError(TerrainBatchError):
    """Raised when a read-only imported batch is used as a write target."""


class TerrainBatchSourceChangedError(TerrainBatchError):
    """Raised when a source differs from the frozen run-start hash."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"画像を読み込めません: {path}") from exc


def _snapshot_config(config: CompilerConfig) -> dict[str, Any]:
    """Copy the JSON-safe effective configuration without runtime callbacks."""
    return json.loads(json.dumps(config.as_dict(), ensure_ascii=False))


def _atomic_write_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _new_directory(parent: Path, prefix: str) -> tuple[str, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        identifier = f"{prefix}_{uuid.uuid4().hex}"
        target = parent / identifier
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return identifier, target


def _resolve_manifest_path(value: object, manifest_path: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else manifest_path.parent / path


def _copy_result(result: TerrainBatchResult) -> TerrainBatchResult:
    return TerrainBatchResult(
        result_id=result.result_id,
        phase=result.phase,
        status=result.status,
        source_sha256=result.source_sha256,
        previous_result_id=result.previous_result_id,
        output_root=result.output_root,
        final_path=result.final_path,
        final_sha256=result.final_sha256,
        actual_palette=tuple(result.actual_palette),
        config_snapshot=dict(result.config_snapshot) if result.config_snapshot is not None else None,
        error_code=result.error_code,
        error_message=result.error_message,
        warnings=list(result.warnings),
        runtime_config=result.runtime_config,
    )


def _config_from_snapshot(snapshot: dict[str, Any], output_root: Path) -> CompilerConfig:
    """Restore a complete rule-based CompilerConfig from metadata JSON."""
    if not isinstance(snapshot, dict):
        raise TerrainBatchError("前回の変換条件を確認できません")
    if snapshot.get("semantic_provider") == "mcp":
        raise TerrainBatchError("MCPの実行条件を復元できないため再変換できません")
    canvas_value = snapshot.get("canvas")
    if not isinstance(canvas_value, dict):
        canvas_value = {
            "width": snapshot.get("width", 64),
            "height": snapshot.get("height", 64),
        }
    try:
        canvas = CanvasSpec(int(canvas_value["width"]), int(canvas_value["height"]))
        conditioning_value = snapshot.get("color_conditioning", {})
        conditioning = ColorConditioningConfig(**conditioning_value) if isinstance(conditioning_value, dict) else ColorConditioningConfig()
        required_fields = {
            field.name
            for field in fields(CompilerConfig)
            if field.name not in {"output_root", "semantic_callable"}
        }
        missing = sorted(name for name in required_fields if name not in snapshot)
        if missing:
            raise TerrainBatchError("前回の変換条件を確認できません")
        known_fields = {field.name for field in fields(CompilerConfig)}
        excluded = {"output_root", "canvas", "semantic_callable", "palette_colors", "color_conditioning"}
        values = {
            name: snapshot[name]
            for name in known_fields - excluded
            if name in snapshot
        }
        raw_palette = snapshot.get("palette_colors")
        if raw_palette is not None:
            values["palette_colors"] = tuple(tuple(int(channel) for channel in color) for color in raw_palette)
        values["output_root"] = output_root
        values["canvas"] = canvas
        values["color_conditioning"] = conditioning
        return CompilerConfig(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise TerrainBatchError("前回の変換条件を確認できません") from exc


def load_legacy_manifest(manifest_path: Path | str) -> TerrainBatch:
    """Load the existing trial batch manifest without writing to its directory."""
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TerrainBatchManifestError("試作manifestを読み込めません") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("inputs"), list):
        raise TerrainBatchManifestError("試作manifestの形式を確認できません")

    items: list[TerrainBatchItem] = []
    for index, entry in enumerate(raw["inputs"], start=1):
        if not isinstance(entry, dict):
            raise TerrainBatchManifestError(f"試作manifestの項目{index}が不正です")
        source_path = _resolve_manifest_path(entry.get("source"), path)
        final_path = _resolve_manifest_path(entry.get("final"), path)
        source_hash = entry.get("source_sha256") if isinstance(entry.get("source_sha256"), str) else None
        source_dimensions: tuple[int, int] | None = None
        warnings: list[str] = []
        source_hash_matches = source_hash is not None
        if source_path is not None and source_path.is_file():
            try:
                observed_hash = _sha256(source_path)
                source_dimensions = _image_dimensions(source_path)
                if source_hash is not None and observed_hash != source_hash:
                    source_hash_matches = False
                    warnings.append("原画が前回から変更されています")
            except (OSError, ValueError):
                source_hash_matches = False
                warnings.append("原画情報を読み込めません")
        elif source_path is not None:
            warnings.append("原画が見つからないため再変換できません")

        result_id = entry.get("result_id") if isinstance(entry.get("result_id"), str) else f"legacy-result-{index}"
        metadata: dict[str, Any] | None = None
        result_warnings: list[str] = []
        actual_palette: tuple[tuple[int, int, int], ...] = ()
        final_sha256: str | None = None
        final_dimensions = entry.get("dimensions")
        if final_path is not None and final_path.is_file():
            try:
                metadata_path = final_path.parent / "metadata.json"
                if metadata_path.is_file():
                    try:
                        loaded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                        metadata = loaded_metadata if isinstance(loaded_metadata, dict) else None
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        result_warnings.append("metadata.jsonを読み込めないためfinal.pngから色を測定しました")
                actual_palette, palette_warning = metadata_palette_matches_final(final_path, metadata)
                if palette_warning:
                    result_warnings.append(palette_warning)
                final_sha256 = _sha256(final_path)
                final_dimensions = list(_image_dimensions(final_path))
            except (OSError, ValueError):
                result_warnings.append("final.pngの情報を読み込めません")
        else:
            result_warnings.append("final.pngが見つからないため使用色を確認できません")

        config_snapshot = metadata.get("config") if metadata and isinstance(metadata.get("config"), dict) else None
        runtime_config: CompilerConfig | None = None
        if config_snapshot is not None:
            try:
                runtime_config = _config_from_snapshot(config_snapshot, final_path.parent if final_path else path.parent)
            except TerrainBatchError:
                result_warnings.append("前回の変換条件を復元できません")
                runtime_config = None
        result_status = "success" if final_path is not None and final_path.is_file() else "failed"
        result = TerrainBatchResult(
            result_id=result_id,
            phase="legacy",
            status=result_status,
            source_sha256=source_hash,
            output_root=final_path.parent if final_path else None,
            final_path=final_path if final_path and final_path.is_file() else None,
            final_sha256=final_sha256,
            actual_palette=actual_palette,
            config_snapshot=config_snapshot,
            error_code=None if result_status == "success" else "final_missing",
            error_message=None if result_status == "success" else "final.pngが見つかりません",
            warnings=result_warnings,
            runtime_config=runtime_config,
        )
        display_name = str(entry.get("display_name") or (source_path.name if source_path else f"項目{index}"))
        item = TerrainBatchItem(
            item_id=f"legacy-item-{index}",
            display_name=display_name,
            source_path=source_path,
            source_sha256=source_hash,
            source_dimensions=source_dimensions,
            config_snapshot=config_snapshot,
            status=result.status,
            error_code=result.error_code,
            error_message=result.error_message,
            warnings=[*warnings, *result_warnings],
            results=[result],
            runtime_config=runtime_config,
            checked=source_path is not None and source_path.is_file() and source_hash_matches and runtime_config is not None,
        )
        items.append(item)

    manifest_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return TerrainBatch(
        batch_id=f"legacy_{manifest_hash}",
        items=items,
        storage_root=None,
        manifest_path=path,
        read_only=True,
        created_at=None,
    )


class TerrainBatchService:
    """Create, freeze, and execute terrain batch runs sequentially."""

    def __init__(self, compiler: PixelTileCompiler | None = None) -> None:
        self.compiler = compiler or PixelTileCompiler()

    def create_batch(
        self,
        source_paths: Iterable[Path | str],
        config: CompilerConfig,
        *,
        output_root: Path | str = Path("Output") / "batches",
    ) -> TerrainBatch:
        paths = [Path(path) for path in source_paths]
        if not paths:
            raise ValueError("地形画像を1枚以上追加してください")
        normalized_paths: list[Path] = []
        for path in paths:
            normalized = path.expanduser().resolve()
            if not normalized.is_file() or normalized.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
                raise ValueError(f"対応している画像ファイルを指定してください: {path}")
            normalized_paths.append(normalized)
        batch_id, storage_root = _new_directory(Path(output_root), "batch")
        items: list[TerrainBatchItem] = []
        config_snapshot = _snapshot_config(config)
        for normalized in normalized_paths:
            items.append(
                TerrainBatchItem(
                    item_id=f"item_{uuid.uuid4().hex}",
                    display_name=normalized.name,
                    source_path=normalized,
                    source_sha256=_sha256(normalized),
                    source_dimensions=_image_dimensions(normalized),
                    config_snapshot=deepcopy(config_snapshot),
                    runtime_config=config,
                )
            )
        batch = TerrainBatch(
            batch_id=batch_id,
            items=items,
            storage_root=storage_root,
            manifest_path=storage_root / "batch_manifest.json",
            read_only=False,
            created_at=_now(),
        )
        self._persist_batch(batch)
        return batch

    def add_sources(
        self,
        batch: TerrainBatch,
        source_paths: Iterable[Path | str],
        config: CompilerConfig,
    ) -> list[TerrainBatchItem]:
        self._ensure_writable(batch)
        added: list[TerrainBatchItem] = []
        existing = {item.source_path.resolve() for item in batch.items if item.source_path is not None}
        for path in source_paths:
            normalized = Path(path).expanduser().resolve()
            if normalized in existing:
                continue
            if not normalized.is_file() or normalized.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
                continue
            item = TerrainBatchItem(
                item_id=f"item_{uuid.uuid4().hex}",
                display_name=normalized.name,
                source_path=normalized,
                source_sha256=_sha256(normalized),
                source_dimensions=_image_dimensions(normalized),
                config_snapshot=_snapshot_config(config),
                runtime_config=config,
            )
            batch.items.append(item)
            added.append(item)
            existing.add(normalized)
        self._persist_batch(batch)
        return added

    def remove_items(self, batch: TerrainBatch, item_ids: Iterable[str]) -> None:
        """Remove only unprocessed list entries from a writable batch manifest."""
        self._ensure_writable(batch)
        targets = set(item_ids)
        batch.items[:] = [item for item in batch.items if item.item_id not in targets]
        self._persist_batch(batch)

    def make_writable(self, batch: TerrainBatch, *, output_root: Path | str = Path("Output") / "batches") -> TerrainBatch:
        """Copy imported read-only state into a new batch storage root."""
        if not batch.read_only:
            return batch
        batch_id, storage_root = _new_directory(Path(output_root), "batch")
        items: list[TerrainBatchItem] = []
        for original in batch.items:
            copied_results = [_copy_result(result) for result in original.results]
            item = TerrainBatchItem(
                item_id=f"item_{uuid.uuid4().hex}",
                display_name=original.display_name,
                source_path=original.source_path,
                source_sha256=original.source_sha256,
                source_dimensions=original.source_dimensions,
                config_snapshot=deepcopy(original.config_snapshot) if original.config_snapshot is not None else None,
                checked=original.checked,
                status=original.status,
                error_code=original.error_code,
                error_message=original.error_message,
                warnings=list(original.warnings),
                results=copied_results,
                runtime_config=original.runtime_config,
            )
            items.append(item)
        writable = TerrainBatch(
            batch_id=batch_id,
            items=items,
            storage_root=storage_root,
            manifest_path=storage_root / "batch_manifest.json",
            read_only=False,
            created_at=_now(),
            runs=list(batch.runs),
        )
        self._persist_batch(writable)
        return writable

    def prepare_initial_run(
        self,
        batch: TerrainBatch,
        target_item_ids: Iterable[str] | None = None,
        *,
        config: CompilerConfig | None = None,
    ) -> TerrainBatchExecutionSnapshot:
        self._ensure_writable(batch)
        target_ids = tuple(target_item_ids) if target_item_ids is not None else tuple(item.item_id for item in batch.items if item.checked)
        return self._prepare_snapshot(batch, "initial", target_ids, config_override=config)

    def prepare_fixed_run(
        self,
        batch: TerrainBatch,
        *,
        reference_result_id: str,
        target_item_ids: Iterable[str],
    ) -> TerrainBatchExecutionSnapshot:
        self._ensure_writable(batch)
        reference = batch.result_by_id(reference_result_id)
        if reference is None or reference.status != "success":
            raise ValueError("基準paletteを選択してください")
        colors = validate_reference_palette(reference.actual_palette)
        target_ids = tuple(target_item_ids)
        if not target_ids:
            raise ValueError("再変換する対象を1件以上チェックしてください")
        return self._prepare_snapshot(
            batch,
            "fixed",
            target_ids,
            reference_result_id=reference_result_id,
            reference_final_sha256=reference.final_sha256,
            palette_id_value=palette_id(colors),
            palette_colors=colors,
        )

    def run_initial(
        self,
        batch: TerrainBatch,
        target_item_ids: Iterable[str] | None = None,
        *,
        config: CompilerConfig | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> TerrainBatchRun:
        snapshot = self.prepare_initial_run(batch, target_item_ids, config=config)
        return self.execute(batch, snapshot, on_progress=on_progress, cancel_requested=cancel_requested)

    def run_fixed(
        self,
        batch: TerrainBatch,
        *,
        reference_result_id: str,
        target_item_ids: Iterable[str],
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> TerrainBatchRun:
        snapshot = self.prepare_fixed_run(
            batch,
            reference_result_id=reference_result_id,
            target_item_ids=target_item_ids,
        )
        return self.execute(batch, snapshot, on_progress=on_progress, cancel_requested=cancel_requested)

    def execute(
        self,
        batch: TerrainBatch,
        snapshot: TerrainBatchExecutionSnapshot,
        *,
        on_progress: ProgressCallback | None = None,
        cancel_requested: CancelCallback | None = None,
    ) -> TerrainBatchRun:
        self._ensure_writable(batch)
        if snapshot.batch_id != batch.batch_id:
            raise ValueError("実行要求とバッチが一致しません")
        run = TerrainBatchRun(
            run_id=snapshot.run_id,
            batch_id=snapshot.batch_id,
            phase=snapshot.phase,
            run_root=snapshot.run_root,
            snapshot=snapshot,
        )
        if snapshot.phase == "fixed":
            self._persist_palette(snapshot)
        self._persist_run(run)
        total = len(snapshot.items)
        cancelled = False
        for index, frozen in enumerate(snapshot.items, start=1):
            if cancelled or (cancel_requested is not None and cancel_requested()):
                cancelled = True
                result = TerrainBatchResult(
                    result_id=f"result_{uuid.uuid4().hex}",
                    phase=snapshot.phase,
                    status="cancelled",
                    source_sha256=frozen.source_sha256,
                    previous_result_id=frozen.previous_result_id,
                    error_code="cancelled",
                    error_message="中止されました（未着手）",
                )
            else:
                self._set_item_status(batch, frozen.item_id, "running")
                self._notify(on_progress, TerrainBatchProgress(index, total, frozen.item_id, snapshot.phase, "running", "処理中"))
                result = self._execute_one(snapshot, frozen)
            self._record_result(batch, frozen.item_id, result)
            run.results.append(result)
            self._persist_run(run)
            self._notify(on_progress, TerrainBatchProgress(index, total, frozen.item_id, snapshot.phase, result.status, result.error_message or result.status))

        if cancelled:
            run.status = "cancelled"
        elif any(result.status == "failed" for result in run.results):
            run.status = "partial"
        else:
            run.status = "success"
        self._persist_run(run)
        batch.runs.append(
            {
                "run_id": run.run_id,
                "phase": run.phase,
                "status": run.status,
                "run_root": str(run.run_root),
                "reference_result_id": snapshot.reference_result_id,
                "palette_id": snapshot.palette_id,
            }
        )
        self._persist_batch(batch)
        return run

    def _prepare_snapshot(
        self,
        batch: TerrainBatch,
        phase: str,
        target_ids: tuple[str, ...],
        *,
        reference_result_id: str | None = None,
        reference_final_sha256: str | None = None,
        palette_id_value: str | None = None,
        palette_colors: tuple[tuple[int, int, int], ...] = (),
        config_override: CompilerConfig | None = None,
    ) -> TerrainBatchExecutionSnapshot:
        if not target_ids:
            raise ValueError("処理対象を1件以上選択してください")
        frozen_items: list[FrozenTerrainBatchItem] = []
        for item_id in target_ids:
            item = batch.item_by_id(item_id)
            if item is None:
                raise ValueError(f"処理対象が見つかりません: {item_id}")
            previous = item.latest_success
            if config_override is not None and phase == "initial":
                config_snapshot = _snapshot_config(config_override)
                runtime_config = config_override
            else:
                config_snapshot = deepcopy(previous.config_snapshot) if previous and previous.config_snapshot is not None else deepcopy(item.config_snapshot)
                runtime_config = previous.runtime_config if previous and previous.runtime_config is not None else item.runtime_config
            frozen_items.append(
                FrozenTerrainBatchItem(
                    item_id=item.item_id,
                    display_name=item.display_name,
                    source_path=item.source_path,
                    source_sha256=item.source_sha256,
                    source_dimensions=item.source_dimensions,
                    previous_result_id=previous.result_id if previous else None,
                    config_snapshot=config_snapshot,
                    runtime_config=deepcopy(runtime_config) if runtime_config is not None else None,
                )
            )
        run_id, run_root = _new_directory(batch.storage_root / "runs", "run")  # type: ignore[operator]
        return TerrainBatchExecutionSnapshot(
            batch_id=batch.batch_id,
            run_id=run_id,
            phase=phase,  # type: ignore[arg-type]
            run_root=run_root,
            target_item_ids=target_ids,
            items=tuple(frozen_items),
            reference_result_id=reference_result_id,
            reference_final_sha256=reference_final_sha256,
            palette_id=palette_id_value,
            palette_colors=palette_colors,
            created_at=_now(),
        )

    def _execute_one(self, snapshot: TerrainBatchExecutionSnapshot, frozen: FrozenTerrainBatchItem) -> TerrainBatchResult:
        result_id = f"result_{uuid.uuid4().hex}"
        output_root = snapshot.run_root / "items" / frozen.item_id
        try:
            source = frozen.source_path
            if source is None or not source.is_file():
                raise FileNotFoundError("原画が見つからないため再変換できません")
            observed_hash = _sha256(source)
            if not frozen.source_sha256:
                raise TerrainBatchError("原画hashを確認できないため再変換できません")
            if observed_hash != frozen.source_sha256:
                raise TerrainBatchSourceChangedError("原画が前回から変更されています")
            if frozen.runtime_config is not None:
                config = replace(frozen.runtime_config, output_root=output_root)
            elif frozen.config_snapshot is not None:
                config = _config_from_snapshot(frozen.config_snapshot, output_root)
            else:
                raise TerrainBatchError("前回の変換条件を確認できません")
            if snapshot.phase == "fixed":
                config = fixed_palette_config(config, snapshot.palette_colors, output_root)
            self._ensure_output_target(output_root)
            config_snapshot = _snapshot_config(config)
            compiled = self.compiler.compile(source, config)
            actual_palette = extract_final_palette(compiled.final_path)
            return TerrainBatchResult(
                result_id=result_id,
                phase=snapshot.phase,
                status="success",
                source_sha256=observed_hash,
                previous_result_id=frozen.previous_result_id,
                output_root=output_root,
                final_path=compiled.final_path,
                final_sha256=_sha256(compiled.final_path),
                actual_palette=actual_palette,
                config_snapshot=config_snapshot,
                runtime_config=config,
            )
        except FileNotFoundError as exc:
            return self._failure(result_id, snapshot, frozen, "source_missing", str(exc))
        except TerrainBatchSourceChangedError as exc:
            return self._failure(result_id, snapshot, frozen, "source_hash_mismatch", str(exc))
        except TerrainBatchError as exc:
            code = "conditions_unavailable"
            return self._failure(result_id, snapshot, frozen, code, str(exc))
        except OSError as exc:
            return self._failure(result_id, snapshot, frozen, "write_failed", f"出力を書き込めません: {exc}")
        except Exception as exc:  # pragma: no cover - defensive boundary for one-row continuation
            return self._failure(result_id, snapshot, frozen, "compile_failed", f"変換に失敗しました: {exc}")

    @staticmethod
    def _failure(
        result_id: str,
        snapshot: TerrainBatchExecutionSnapshot,
        frozen: FrozenTerrainBatchItem,
        error_code: str,
        error_message: str,
    ) -> TerrainBatchResult:
        return TerrainBatchResult(
            result_id=result_id,
            phase=snapshot.phase,
            status="failed",
            source_sha256=frozen.source_sha256,
            previous_result_id=frozen.previous_result_id,
            error_code=error_code,
            error_message=error_message,
        )

    @staticmethod
    def _ensure_output_target(output_root: Path) -> None:
        if output_root.exists():
            if not output_root.is_dir() or any(output_root.iterdir()):
                raise FileExistsError(f"出力先が既に存在します: {output_root}")

    @staticmethod
    def _ensure_writable(batch: TerrainBatch) -> None:
        if batch.read_only or batch.storage_root is None:
            raise TerrainBatchReadOnlyError("読み取り専用の試作manifestです。新しいバッチへ移して実行してください")

    @staticmethod
    def _set_item_status(batch: TerrainBatch, item_id: str, status: str) -> None:
        item = batch.item_by_id(item_id)
        if item is not None:
            item.status = status  # type: ignore[assignment]
            item.error_code = None
            item.error_message = None

    @staticmethod
    def _record_result(batch: TerrainBatch, item_id: str, result: TerrainBatchResult) -> None:
        item = batch.item_by_id(item_id)
        if item is None:
            return
        item.results.append(result)
        item.status = result.status
        item.error_code = result.error_code
        item.error_message = result.error_message
        if result.warnings:
            item.warnings.extend(result.warnings)

    @staticmethod
    def _notify(callback: ProgressCallback | None, progress: TerrainBatchProgress) -> None:
        if callback is not None:
            callback(progress)

    def _persist_batch(self, batch: TerrainBatch) -> None:
        if batch.manifest_path is not None:
            _atomic_write_json(batch.as_dict(), batch.manifest_path)

    def _persist_run(self, run: TerrainBatchRun) -> None:
        _atomic_write_json(run.as_dict(), run.run_root / "run_manifest.json")

    @staticmethod
    def _persist_palette(snapshot: TerrainBatchExecutionSnapshot) -> None:
        _atomic_write_json(
            {
                "schema_version": 1,
                "palette_id": snapshot.palette_id,
                "reference_final_sha256": snapshot.reference_final_sha256,
                "colors": [list(color) for color in snapshot.palette_colors],
                "reference_result_id": snapshot.reference_result_id,
                "created_at": snapshot.created_at,
            },
            snapshot.run_root / "palette.json",
        )


__all__ = [
    "TerrainBatchError",
    "TerrainBatchManifestError",
    "TerrainBatchReadOnlyError",
    "TerrainBatchService",
    "load_legacy_manifest",
]
