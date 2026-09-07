"""Run the reproducible 3x3 character palette and detail density Study."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.config import compiler_config_for_purpose
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .config import CharacterPaletteDensityStudyConfig, CharacterStudyCase
from .metrics import compare_visible_pixels, image_metrics, sha256_file


@dataclass(frozen=True)
class CharacterPaletteDensityStudyResult:
    output_root: Path
    case_manifests: tuple[Path, ...]


class CharacterPaletteDensityStudyRunner:
    """Generate every configured character palette/detail cell."""

    def run(self, config: CharacterPaletteDensityStudyConfig) -> CharacterPaletteDensityStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        save_json(config.to_dict(), root / "config.json")
        manifests: list[Path] = []
        case_summaries: list[dict[str, Any]] = []
        for case in config.cases:
            manifest = self._run_case(config, root, case)
            manifests.append(manifest)
            case_summaries.append({"case_id": case.case_id, "manifest": str(manifest.relative_to(root))})
        save_json({"cases": case_summaries, "config": config.to_dict()}, root / "manifest.json")
        return CharacterPaletteDensityStudyResult(root, tuple(manifests))

    def _run_case(self, config: CharacterPaletteDensityStudyConfig, root: Path, case: CharacterStudyCase) -> Path:
        source = Path(case.source)
        if not source.exists():
            raise FileNotFoundError(f"character Study source does not exist: {source}")
        case_root = root / case.case_id
        case_root.mkdir(parents=True, exist_ok=True)
        source_snapshot = case_root / "source_snapshot.png"
        shutil.copy2(source, source_snapshot)
        source_hash = sha256_file(source)
        with Image.open(source) as source_image:
            source_info = {"size": list(source_image.size), "mode": source_image.mode, "format": source_image.format}
        save_json(
            {
                "case_id": case.case_id,
                "source": str(source),
                "source_snapshot": str(source_snapshot),
                "source_sha256": source_hash,
                "review_features": list(case.review_features),
                **source_info,
            },
            case_root / "source.json",
        )

        records: list[dict[str, Any]] = []
        compiler = PixelTileCompiler()
        for level in config.detail_levels:
            for budget in config.palette_budgets:
                cell_root = case_root / "matrix" / level / f"palette_{budget}"
                compiler_config = compiler_config_for_purpose(
                    "character",
                    output_root=cell_root,
                    palette_budget=budget,
                    character_frame_width=config.frame_width,
                    character_frame_height=config.frame_height,
                    character_bottom_margin=config.bottom_margin,
                    character_detail_level=level,
                    background_mode=config.background_mode,
                    outline_color=config.outline,
                    seed=config.seed,
                    debug_enabled=False,
                )
                result = compiler.compile(source, compiler_config)
                with Image.open(result.final_path) as final:
                    metrics = image_metrics(final, result.final_path, budget)
                records.append(
                    {
                        "cell_id": f"{level}_palette_{budget}",
                        "case_id": case.case_id,
                        "detail_level": level,
                        "palette_budget": budget,
                        "relative_final_path": str(result.final_path.relative_to(case_root)),
                        **metrics,
                    }
                )

        self._add_cross_density_metrics(case_root, records, config)
        save_json({"cells": records}, case_root / "metrics" / "matrix_metrics.json")
        self._write_comparison_boards(case_root, records, config)
        self._write_review_template(case_root, case, records)
        manifest = {
            "case_id": case.case_id,
            "source": str(source),
            "source_snapshot": "source_snapshot.png",
            "source_sha256": source_hash,
            "review_features": list(case.review_features),
            "config": config.to_dict()["study"],
            "cells": records,
        }
        manifest_path = case_root / "manifest.json"
        save_json(manifest, manifest_path)
        return manifest_path

    def _add_cross_density_metrics(
        self,
        case_root: Path,
        records: list[dict[str, Any]],
        config: CharacterPaletteDensityStudyConfig,
    ) -> None:
        detailed_by_budget = {
            record["palette_budget"]: record
            for record in records
            if record["detail_level"] == "detailed"
        }
        for record in records:
            detailed = detailed_by_budget[record["palette_budget"]]
            with Image.open(case_root / record["relative_final_path"]) as candidate, Image.open(
                case_root / detailed["relative_final_path"]
            ) as reference:
                record["alpha_equal_to_detailed_same_budget"] = (
                    candidate.convert("RGBA").getchannel("A").tobytes() == reference.convert("RGBA").getchannel("A").tobytes()
                )
                changed, ratio = compare_visible_pixels(reference, candidate)
            record["changed_visible_pixels_vs_detailed"] = changed
            record["changed_visible_ratio_vs_detailed"] = ratio

    def _write_comparison_boards(
        self,
        case_root: Path,
        records: list[dict[str, Any]],
        config: CharacterPaletteDensityStudyConfig,
    ) -> None:
        lookup = {(record["detail_level"], record["palette_budget"]): record for record in records}
        board = Image.new("RGBA", (64 * len(config.palette_budgets), 64 * len(config.detail_levels)), (20, 20, 24, 255))
        draw = ImageDraw.Draw(board)
        for row, level in enumerate(config.detail_levels):
            for column, budget in enumerate(config.palette_budgets):
                record = lookup[(level, budget)]
                with Image.open(case_root / record["relative_final_path"]) as image:
                    cell = image.convert("RGBA").resize((64, 64), Image.Resampling.NEAREST)
                left, top = column * 64, row * 64
                board.paste(cell, (left, top))
                label = f"{level[:1].upper()}{budget} {record['actual_palette_count']}c"
                draw.rectangle((left, top, left + 63, top + 9), fill=(0, 0, 0, 190))
                draw.text((left + 1, top + 1), label, fill=(255, 255, 255, 255))
        preview = board.resize((board.width * 8, board.height * 8), Image.Resampling.NEAREST)
        save_png(board, case_root / "previews" / "comparison_board.png")
        save_png(preview, case_root / "previews" / "comparison_board_8x.png")

    def _write_review_template(self, case_root: Path, case: CharacterStudyCase, records: list[dict[str, Any]]) -> None:
        rows = []
        for record in records:
            rows.append(
                {
                    "cell_id": record["cell_id"],
                    "detail_level": record["detail_level"],
                    "palette_budget": record["palette_budget"],
                    "features": {feature: None for feature in case.review_features},
                    "silhouette_readability": None,
                    "actual_size_readability": None,
                    "notes": None,
                    "selected": None,
                }
            )
        save_json(
            {
                "case_id": case.case_id,
                "review_features": list(case.review_features),
                "cells": rows,
                "instructions": "Human review only; no automatic winner is assigned.",
            },
            case_root / "review" / "review_template.json",
        )
