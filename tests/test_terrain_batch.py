from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.gui.terrain_batch_palette import (
    extract_final_palette,
    fixed_palette_config,
    metadata_palette_matches_final,
    palette_id,
)
from pixel_tile_compiler.gui.terrain_batch_service import TerrainBatchService, load_legacy_manifest
from pixel_tile_compiler.gui.terrain_batch_service import TerrainBatchManifestError
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


def _source(path: Path) -> None:
    image = Image.new("RGBA", (128, 128), (40, 110, 50, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 31, 31), fill=(210, 230, 90, 255))
    draw.rectangle((96, 96, 127, 127), fill=(25, 65, 40, 255))
    image.putpixel((0, 127), (255, 0, 255, 0))
    image.save(path)


def test_extract_final_palette_uses_visible_rgb_only_and_is_sorted() -> None:
    image = Image.new("RGBA", (4, 1), (255, 0, 255, 0))
    image.putpixel((0, 0), (30, 40, 50, 255))
    image.putpixel((1, 0), (10, 20, 30, 255))
    image.putpixel((2, 0), (30, 40, 50, 255))

    assert extract_final_palette(image) == ((10, 20, 30), (30, 40, 50))


def test_palette_id_is_stable_for_normalized_rgb_sequence() -> None:
    colors = ((30, 40, 50), (10, 20, 30))
    payload = b"palette-rgb-v1\n" + bytes((10, 20, 30, 30, 40, 50))

    assert palette_id(colors) == hashlib.sha256(payload).hexdigest()
    assert palette_id(colors) == palette_id(tuple(reversed(colors)))


def test_fixed_palette_config_uses_actual_colors_without_budget_padding(tmp_path: Path) -> None:
    config = CompilerConfig(output_root=tmp_path / "initial", palette_budget=24)

    fixed = fixed_palette_config(config, ((10, 20, 30), (30, 40, 50)), tmp_path / "fixed")

    assert fixed.palette_colors == ((10, 20, 30), (30, 40, 50))
    assert fixed.palette_budget == 4
    assert fixed.output_root == tmp_path / "fixed"


def test_fixed_recompile_uses_original_source_and_writes_a_new_run(tmp_path: Path) -> None:
    source_path = tmp_path / "terrain.png"
    _source(source_path)
    config = CompilerConfig(
        output_root=tmp_path / "unused",
        palette_budget=8,
        tile_mode="directional",
        repeat_opt_enabled=False,
        pixelization_mode="nearest",
        debug_enabled=False,
    )
    service = TerrainBatchService()
    batch = service.create_batch([source_path], config, output_root=tmp_path / "batches")

    initial_run = service.run_initial(batch)
    initial_result = initial_run.results[0]
    snapshot = service.prepare_fixed_run(
        batch,
        reference_result_id=initial_result.result_id,
        target_item_ids=[batch.items[0].item_id],
    )
    Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(initial_result.final_path)

    fixed_run = service.execute(batch, snapshot)
    fixed_result = fixed_run.results[0]
    expected_config = replace(
        config,
        output_root=tmp_path / "expected",
        palette_budget=max(4, initial_result.actual_palette_count),
        palette_colors=initial_result.actual_palette,
    )
    expected = PixelTileCompiler().compile(source_path, expected_config).final_path.read_bytes()

    assert fixed_result.status == "success"
    assert fixed_result.previous_result_id == initial_result.result_id
    assert fixed_result.final_path.read_bytes() == expected
    assert fixed_result.final_path != initial_result.final_path
    assert fixed_run.run_id != initial_run.run_id


def test_source_hash_mismatch_fails_one_item_without_writing_a_success(tmp_path: Path) -> None:
    source_path = tmp_path / "terrain.png"
    _source(source_path)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source_path],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    snapshot = service.prepare_initial_run(batch)
    source_path.write_bytes(source_path.read_bytes() + b"changed")

    run = service.execute(batch, snapshot)

    assert run.results[0].status == "failed"
    assert run.results[0].error_code == "source_hash_mismatch"
    assert not (run.run_root / "items" / batch.items[0].item_id / "final.png").exists()


def test_empty_and_over_budget_reference_palettes_are_not_selectable() -> None:
    with pytest.raises(ValueError, match="must contain at least one"):
        palette_id(())

    with pytest.raises(ValueError, match="at most 64"):
        palette_id(tuple((index, 0, 0) for index in range(65)))


def test_batch_failure_continues_to_the_next_source(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _source(first)
    _source(second)
    service = TerrainBatchService()
    batch = service.create_batch(
        [first, second],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    snapshot = service.prepare_initial_run(batch)
    second.unlink()

    run = service.execute(batch, snapshot)

    assert [result.status for result in run.results] == ["success", "failed"]
    assert run.results[1].error_code == "source_missing"
    assert run.status == "partial"
    assert run.results[0].final_path is not None
    assert run.results[0].final_path.is_file()


def test_batch_cancel_stops_before_the_next_item_but_keeps_completed_output(tmp_path: Path) -> None:
    sources = [tmp_path / f"source-{index}.png" for index in range(3)]
    for source in sources:
        _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        sources,
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    progress: list[str] = []

    run = service.run_initial(
        batch,
        on_progress=lambda update: progress.append(update.status),
        cancel_requested=lambda: len(progress) >= 2,
    )

    assert [result.status for result in run.results] == ["success", "cancelled", "cancelled"]
    assert run.status == "cancelled"
    assert run.results[0].final_path is not None
    assert run.results[0].final_path.is_file()
    assert not (run.run_root / "items" / batch.items[1].item_id / "final.png").exists()


def test_fixed_run_writes_palette_json_and_keeps_reference_colors_exact(tmp_path: Path) -> None:
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", palette_budget=24, debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    initial = service.run_initial(batch)
    reference = initial.results[0]

    fixed = service.run_fixed(
        batch,
        reference_result_id=reference.result_id,
        target_item_ids=[batch.items[0].item_id],
    )
    palette_manifest = json.loads((fixed.run_root / "palette.json").read_text(encoding="utf-8"))

    assert palette_manifest["palette_id"] == palette_id(reference.actual_palette)
    assert palette_manifest["reference_final_sha256"] == reference.final_sha256
    assert tuple(map(tuple, palette_manifest["colors"])) == reference.actual_palette
    assert fixed.results[0].config_snapshot["palette_colors"] == [list(color) for color in reference.actual_palette]
    assert set(fixed.results[0].actual_palette).issubset(set(reference.actual_palette))


def test_execution_snapshot_does_not_follow_later_item_or_palette_mutation(tmp_path: Path) -> None:
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", palette_budget=8, debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    initial = service.run_initial(batch)
    reference = initial.results[0]
    snapshot = service.prepare_fixed_run(
        batch,
        reference_result_id=reference.result_id,
        target_item_ids=[batch.items[0].item_id],
    )
    original_palette = snapshot.palette_colors
    batch.items[0].checked = False
    batch.items[0].config_snapshot = {"palette_budget": 64}

    assert snapshot.target_item_ids == (batch.items[0].item_id,)
    assert snapshot.palette_colors == original_palette
    assert snapshot.items[0].config_snapshot != batch.items[0].config_snapshot


def test_legacy_manifest_is_read_only_but_final_palette_remains_viewable(tmp_path: Path) -> None:
    manifest_root = tmp_path / "legacy"
    final_path = manifest_root / "trial" / "final.png"
    final_path.parent.mkdir(parents=True)
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((0, 0), (20, 30, 40, 255))
    image.putpixel((1, 0), (60, 70, 80, 255))
    image.save(final_path)
    manifest_path = manifest_root / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"inputs": [{"source": "missing.png", "final": "trial/final.png"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = sorted(path.relative_to(manifest_root).as_posix() for path in manifest_root.rglob("*"))

    batch = load_legacy_manifest(manifest_path)

    after = sorted(path.relative_to(manifest_root).as_posix() for path in manifest_root.rglob("*"))
    assert batch.read_only is True
    assert batch.items[0].source_path == manifest_root / "missing.png"
    assert batch.items[0].checked is False
    assert batch.items[0].latest_success is not None
    assert batch.items[0].latest_success.actual_palette == ((20, 30, 40), (60, 70, 80))
    assert before == after


def test_metadata_palette_mismatch_uses_final_pixels_and_warns(tmp_path: Path) -> None:
    final_path = tmp_path / "final.png"
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((0, 0), (20, 30, 40, 255))
    image.putpixel((1, 0), (60, 70, 80, 255))
    image.save(final_path)

    palette, warning = metadata_palette_matches_final(
        final_path,
        {"transformation": {"palette_colors": [[0, 0, 0]]}},
    )

    assert palette == ((20, 30, 40), (60, 70, 80))
    assert warning is not None


def test_legacy_manifest_hash_mismatch_is_visible_and_not_checked(tmp_path: Path) -> None:
    manifest_root = tmp_path / "legacy"
    source_path = manifest_root / "source.png"
    final_path = manifest_root / "final.png"
    manifest_root.mkdir()
    _source(source_path)
    Image.new("RGBA", (1, 1), (20, 30, 40, 255)).save(final_path)
    manifest_path = manifest_root / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "settings": {"purpose": "terrain"},
                "inputs": [{"source": "source.png", "source_sha256": "0" * 64, "final": "final.png"}],
            }
        ),
        encoding="utf-8",
    )

    batch = load_legacy_manifest(manifest_path)

    assert batch.items[0].checked is False
    assert "原画が前回から変更されています" in batch.items[0].warnings


def test_invalid_legacy_manifest_is_rejected_without_writing(tmp_path: Path) -> None:
    manifest_path = tmp_path / "broken.json"
    manifest_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(TerrainBatchManifestError):
        load_legacy_manifest(manifest_path)


def test_incomplete_previous_conditions_are_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    batch.items[0].runtime_config = None
    batch.items[0].config_snapshot = {"pixelization_mode": "nearest"}
    snapshot = service.prepare_initial_run(batch)

    run = service.execute(batch, snapshot)

    assert run.results[0].status == "failed"
    assert run.results[0].error_code == "conditions_unavailable"


def test_each_run_gets_a_new_empty_output_root(tmp_path: Path) -> None:
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )

    first = service.run_initial(batch)
    second = service.run_initial(batch)

    assert first.run_root != second.run_root
    assert first.results[0].final_path != second.results[0].final_path
    assert first.results[0].final_path.is_file()
    assert second.results[0].final_path.is_file()
