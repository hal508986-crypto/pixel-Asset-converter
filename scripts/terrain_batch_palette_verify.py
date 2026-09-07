"""Reproduce a fixed-palette terrain batch from an existing trial manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pixel_tile_compiler.gui.terrain_batch_service import TerrainBatchService, load_legacy_manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_before_after(batch, run, output_root: Path) -> tuple[Path, dict[str, object]]:
    rows: list[tuple[Path, Path, Path]] = []
    records: list[dict[str, object]] = []
    for item, result in zip(batch.items, run.results):
        if result.status != "success" or result.final_path is None:
            continue
        before = item.result_by_id(result.previous_result_id) if result.previous_result_id else None
        if before is None or before.final_path is None:
            continue
        rows.append((item.source_path, before.final_path, result.final_path))  # type: ignore[arg-type]
        records.append(
            {
                "item_id": item.item_id,
                "display_name": item.display_name,
                "source": str(item.source_path) if item.source_path else None,
                "source_sha256": _sha256(item.source_path) if item.source_path else None,
                "before_final": str(before.final_path),
                "before_sha256": _sha256(before.final_path),
                "after_final": str(result.final_path),
                "after_sha256": _sha256(result.final_path),
                "after_actual_palette": [list(color) for color in result.actual_palette],
                "after_actual_palette_count": result.actual_palette_count,
                "fixed_palette_subset": set(result.actual_palette).issubset(set(run.snapshot.palette_colors)),
            }
        )

    panel_size = (64, 64)
    sheet = Image.new("RGBA", (panel_size[0] * 3, panel_size[1] * len(rows)), (0, 0, 0, 255))
    for row, (source_path, before_path, after_path) in enumerate(rows):
        for column, path in enumerate((source_path, before_path, after_path)):
            with Image.open(path) as opened:
                image = opened.convert("RGBA")
            image.thumbnail(panel_size, Image.Resampling.NEAREST)
            panel = Image.new("RGBA", panel_size, (0, 0, 0, 0))
            panel.paste(image, ((panel_size[0] - image.width) // 2, (panel_size[1] - image.height) // 2))
            sheet.paste(panel, (column * panel_size[0], row * panel_size[1]))
    comparison_path = output_root / "comparison_before_after.png"
    sheet.resize((sheet.width * 4, sheet.height * 4), Image.Resampling.NEAREST).save(comparison_path)
    return comparison_path, {"rows": records}


def run(manifest_path: Path, output_root: Path) -> Path:
    output_root = output_root.resolve()
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        raise FileExistsError(f"出力先は存在しないか空である必要があります: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    legacy = load_legacy_manifest(manifest_path)
    service = TerrainBatchService()
    batch = service.make_writable(legacy, output_root=output_root)
    reference = next((item.latest_success for item in batch.items if item.latest_success is not None), None)
    if reference is None:
        raise ValueError("基準にできる成功済みfinal.pngがありません")
    target_ids = [item.item_id for item in batch.items]
    run_result = service.run_fixed(
        batch,
        reference_result_id=reference.result_id,
        target_item_ids=target_ids,
    )
    if run_result.status != "success":
        raise RuntimeError(f"一括再変換が完了しませんでした: {run_result.status}")

    comparison_path, comparison = _write_before_after(batch, run_result, output_root)
    manifest = {
        "schema_version": 1,
        "input_manifest": str(manifest_path.resolve()),
        "batch_manifest": str(batch.manifest_path) if batch.manifest_path else None,
        "run_manifest": str(run_result.run_root / "run_manifest.json"),
        "palette_manifest": str(run_result.run_root / "palette.json"),
        "reference_result_id": reference.result_id,
        "reference_palette": [list(color) for color in run_result.snapshot.palette_colors],
        "reference_palette_id": run_result.snapshot.palette_id,
        "comparison_before_after": str(comparison_path),
        "source_preserving": True,
        "settings": {
            "purpose": "terrain",
            "pixelization_mode": "nearest",
            "palette_budget": 24,
            "repeat_opt_enabled": False,
        },
        "comparison": comparison,
    }
    manifest_path_out = output_root / "comparison_manifest.json"
    manifest_path_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path_out


def main() -> None:
    parser = argparse.ArgumentParser(description="試作manifestから地形固定パレット一括再変換を再現します")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.manifest, args.output))


if __name__ == "__main__":
    main()
