"""Canvasサイズ・palette上限・detail_level・背景・構図の5軸比較Study。

仕様: docs/spec/canvas_scale_and_palette_budget_spec.md
特に2節（実測）・4.4節（検証指標）・4.5節（背景と構図）・6節P0（テスト一覧）を正典とする。
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.palette_contract import extract_final_palette
from pixel_tile_compiler.pixelizer.character_detail import CharacterDetailLevel
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .metrics import sha256_file

_VALID_DETAIL_LEVELS = {"sparse", "balanced", "detailed"}
_VALID_BACKGROUND_MODES = {"auto", "alpha", "color"}
_VALID_COMPOSITION_MODES = {"single_frame", "pre_aligned"}
_CANVAS_MIN_SIDE = 16
_CANVAS_MAX_SIDE = 512
_PALETTE_MIN = 4
_PALETTE_MAX = 64

# 比較シートの表示規約（4.5節の比較シート契約）。
_DISPLAY_BOX = 256
_LABEL_HEIGHT = 20
_SHEET_BACKGROUND = (20, 20, 24, 255)
_LABEL_BACKGROUND = (0, 0, 0, 220)
_LABEL_TEXT_COLOR = (255, 255, 255, 255)


@dataclass
class CanvasScaleStudyConfig:
    """Canvasサイズ×palette上限×detail_level×背景×構図の5軸マトリクスstudy設定。"""

    source: Path
    output_root: Path = field(default_factory=lambda: Path("e2e/canvas_scale_study"))
    canvas_sizes: tuple[tuple[int, int], ...] = ((64, 64), (128, 128), (256, 256))
    palette_budgets: tuple[int, ...] = (16, 24, 36, 48)
    detail_levels: tuple[CharacterDetailLevel, ...] = ("balanced",)
    background_modes: tuple[str, ...] = ("auto",)
    composition_modes: tuple[str, ...] = ("single_frame",)
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.source = Path(self.source)
        self.output_root = Path(self.output_root)
        self.canvas_sizes = tuple((int(size[0]), int(size[1])) for size in self.canvas_sizes)
        self.palette_budgets = tuple(int(value) for value in self.palette_budgets)
        self.detail_levels = tuple(str(value) for value in self.detail_levels)  # type: ignore[assignment]
        self.background_modes = tuple(str(value) for value in self.background_modes)
        self.composition_modes = tuple(str(value) for value in self.composition_modes)
        self.seed = int(self.seed)

        if not self.canvas_sizes:
            raise ValueError("canvas_sizesは空にできません")
        for width, height in self.canvas_sizes:
            if not (_CANVAS_MIN_SIDE <= width <= _CANVAS_MAX_SIDE) or not (
                _CANVAS_MIN_SIDE <= height <= _CANVAS_MAX_SIDE
            ):
                raise ValueError(
                    f"canvas_sizesの各辺は{_CANVAS_MIN_SIDE}以上{_CANVAS_MAX_SIDE}以下である必要があります: "
                    f"({width}, {height})"
                )
        if len(set(self.canvas_sizes)) != len(self.canvas_sizes):
            raise ValueError("canvas_sizesに重複があります")

        if not self.palette_budgets:
            raise ValueError("palette_budgetsは空にできません")
        for value in self.palette_budgets:
            if not (_PALETTE_MIN <= value <= _PALETTE_MAX):
                raise ValueError(
                    f"palette_budgetsは{_PALETTE_MIN}以上{_PALETTE_MAX}以下である必要があります: {value}"
                )
        if len(set(self.palette_budgets)) != len(self.palette_budgets):
            raise ValueError("palette_budgetsに重複があります")

        if not self.detail_levels:
            raise ValueError("detail_levelsは空にできません")
        if any(value not in _VALID_DETAIL_LEVELS for value in self.detail_levels):
            raise ValueError("detail_levelsはsparse/balanced/detailedのいずれかである必要があります")
        if len(set(self.detail_levels)) != len(self.detail_levels):
            raise ValueError("detail_levelsに重複があります")

        if not self.background_modes:
            raise ValueError("background_modesは空にできません")
        if any(value not in _VALID_BACKGROUND_MODES for value in self.background_modes):
            raise ValueError("background_modesはauto/alpha/colorのいずれかである必要があります")
        if len(set(self.background_modes)) != len(self.background_modes):
            raise ValueError("background_modesに重複があります")

        if not self.composition_modes:
            raise ValueError("composition_modesは空にできません")
        if any(value not in _VALID_COMPOSITION_MODES for value in self.composition_modes):
            raise ValueError("composition_modesはsingle_frame/pre_alignedのいずれかである必要があります")
        if len(set(self.composition_modes)) != len(self.composition_modes):
            raise ValueError("composition_modesに重複があります")

        if self.seed < 0:
            raise ValueError("seedは非負である必要があります")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "CanvasScaleStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study", mapping))
        if "source" not in study:
            raise ValueError("canvas scale studyにはsourceが必要です")
        default = cls(source=Path("."))
        return cls(
            source=_resolve(root, study["source"]),
            output_root=_resolve(root, study.get("output_root", default.output_root)),
            canvas_sizes=tuple(tuple(item) for item in study.get("canvas_sizes", default.canvas_sizes)),
            palette_budgets=tuple(study.get("palette_budgets", default.palette_budgets)),
            detail_levels=tuple(study.get("detail_levels", default.detail_levels)),  # type: ignore[arg-type]
            background_modes=tuple(study.get("background_modes", default.background_modes)),
            composition_modes=tuple(study.get("composition_modes", default.composition_modes)),
            seed=int(study.get("seed", default.seed)),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "source": str(self.source),
                "output_root": str(self.output_root),
                "canvas_sizes": [list(size) for size in self.canvas_sizes],
                "palette_budgets": list(self.palette_budgets),
                "detail_levels": list(self.detail_levels),
                "background_modes": list(self.background_modes),
                "composition_modes": list(self.composition_modes),
                "seed": self.seed,
            }
        }


def load_canvas_scale_study_config(path: Path) -> CanvasScaleStudyConfig:
    """JSONまたはYAMLのcanvas scale study configを読み込む。"""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        mapping = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("YAML configにはPyYAMLが必要です。JSON configを使用してください") from exc
        mapping = yaml.safe_load(raw)
    if not isinstance(mapping, dict):
        raise ValueError("canvas scale study configはmappingである必要があります")
    mapping["config_path"] = str(path)
    return CanvasScaleStudyConfig.from_mapping(mapping, base_dir=path.parent)


@dataclass(frozen=True)
class CanvasScaleStudyResult:
    output_root: Path
    manifest_path: Path
    metrics_path: Path
    sheet_path: Path


class CanvasScaleStudyRunner:
    """Canvasサイズ×palette上限×detail×背景×構図の直積を1セルずつ実行するrunner。"""

    def run(self, config: CanvasScaleStudyConfig) -> CanvasScaleStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        if not config.source.exists():
            raise FileNotFoundError(f"canvas scale Study source does not exist: {config.source}")

        save_json(config.to_dict(), root / "config.json")
        source_hash = sha256_file(config.source)
        with Image.open(config.source) as source_image:
            source_info = {
                "size": list(source_image.size),
                "mode": source_image.mode,
                "format": source_image.format,
            }
        save_json(
            {
                "source": str(config.source),
                "source_sha256": source_hash,
                **source_info,
            },
            root / "source.json",
        )

        compiler = PixelTileCompiler()
        cell_records: list[dict[str, Any]] = []
        metric_records: list[dict[str, Any]] = []
        images_by_cell: dict[str, Image.Image] = {}

        combinations = itertools.product(
            config.canvas_sizes,
            config.palette_budgets,
            config.detail_levels,
            config.background_modes,
            config.composition_modes,
        )
        for canvas_size, budget, detail, background, composition in combinations:
            cell_id = _cell_id(canvas_size, budget, detail, background, composition)
            cell_root = root / "cells" / cell_id
            compiler_config = compiler_config_for_purpose(
                "character",
                output_root=cell_root,
                canvas=CanvasSpec(*canvas_size),
                palette_budget=budget,
                character_detail_level=detail,
                background_mode=background,
                character_input_mode=composition,
                seed=config.seed,
                debug_enabled=False,
            )
            started_at = time.perf_counter()
            result = compiler.compile(config.source, compiler_config)
            elapsed_seconds = time.perf_counter() - started_at

            with Image.open(result.final_path) as final:
                final_rgba = final.convert("RGBA")
            images_by_cell[cell_id] = final_rgba

            metrics = _cell_metrics(final_rgba, canvas_size, budget, elapsed_seconds)
            cell_records.append(
                {
                    "cell_id": cell_id,
                    "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
                    "palette_budget": budget,
                    "detail_level": detail,
                    "background_mode": background,
                    "composition_mode": composition,
                    "final_path": str(result.final_path.relative_to(root)),
                }
            )
            metric_records.append(
                {
                    "cell_id": cell_id,
                    "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
                    "palette_budget": budget,
                    "detail_level": detail,
                    "background_mode": background,
                    "composition_mode": composition,
                    **metrics,
                }
            )

        manifest = {
            "source": str(config.source),
            "source_sha256": source_hash,
            "config": config.to_dict()["study"],
            "cells": cell_records,
        }
        manifest_path = root / "manifest.json"
        save_json(manifest, manifest_path)

        metrics_path = root / "metrics" / "canvas_scale_metrics.json"
        save_json({"cells": metric_records}, metrics_path)

        sheet_path = _write_comparison_sheet(root, config, images_by_cell)
        _write_summary(root, config, metric_records)

        return CanvasScaleStudyResult(root, manifest_path, metrics_path, sheet_path)


def _cell_id(
    canvas_size: tuple[int, int],
    budget: int,
    detail: str,
    background: str,
    composition: str,
) -> str:
    return f"{_canvas_label(canvas_size)}_{budget}_{detail}_{background}_{composition}"


def _canvas_label(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def _cell_metrics(
    image: Image.Image,
    canvas_size: tuple[int, int],
    palette_budget: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    rgba = image.convert("RGBA")
    width, height = canvas_size
    palette = extract_final_palette(rgba)
    bbox = rgba.getchannel("A").getbbox()
    if bbox is not None:
        left, top, right, bottom = bbox
        occupancy_height = round((bottom - top) / height, 6) if height else 0.0
        occupancy_width = round((right - left) / width, 6) if width else 0.0
        subject_bbox = list(bbox)
    else:
        occupancy_height = 0.0
        occupancy_width = 0.0
        subject_bbox = None
    return {
        "measured_palette": len(palette),
        "uniform_2x2_ratio": _two_by_two_uniform_block_ratio(rgba),
        "subject_bbox": subject_bbox,
        "occupancy_height": occupancy_height,
        "occupancy_width": occupancy_width,
        "edge_color_changes": _edge_color_changes(rgba),
        "elapsed_seconds": round(elapsed_seconds, 6),
    }


def _two_by_two_uniform_block_ratio(image: Image.Image) -> float:
    """2x2ブロックが全て同色である割合。native_resolution.pyの同名関数と同じ定義。"""
    rgba = image.convert("RGBA")
    if rgba.width < 2 or rgba.height < 2:
        return 0.0
    total = (rgba.width // 2) * (rgba.height // 2)
    uniform = 0
    for y in range(0, rgba.height - 1, 2):
        for x in range(0, rgba.width - 1, 2):
            pixels = [rgba.getpixel((x + dx, y + dy)) for dy in range(2) for dx in range(2)]
            uniform += int(len(set(pixels)) == 1)
    return round(uniform / total, 6) if total else 0.0


def _edge_color_changes(image: Image.Image) -> float:
    """隣接画素（右隣・下隣）で色が変わる割合。可視画素どうしのペアのみを分母にする。"""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    total = 0
    changed = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if x + 1 < width:
                nr, ng, nb, na = pixels[x + 1, y]
                if na != 0:
                    total += 1
                    if (r, g, b) != (nr, ng, nb):
                        changed += 1
            if y + 1 < height:
                nr, ng, nb, na = pixels[x, y + 1]
                if na != 0:
                    total += 1
                    if (r, g, b) != (nr, ng, nb):
                        changed += 1
    return round(changed / total, 6) if total else 0.0


def _display_scale_factor(width: int, height: int, box: int = _DISPLAY_BOX) -> int:
    """比較シートの拡大率。表示箱をboxとし、max(1, box // max(width, height))。"""
    return max(1, box // max(width, height))


def _normalize_cell_for_display(image: Image.Image, canvas_size: tuple[int, int], box: int = _DISPLAY_BOX) -> Image.Image:
    """全セルを同じ表示サイズへ整数倍で正規化する。拡大は必ずNEAREST。"""
    scale = _display_scale_factor(*canvas_size, box=box)
    width, height = canvas_size
    return image.convert("RGBA").resize((width * scale, height * scale), Image.NEAREST)


def _write_comparison_sheet(
    root: Path,
    config: CanvasScaleStudyConfig,
    images_by_cell: dict[str, Image.Image],
) -> Path:
    columns = list(
        itertools.product(
            config.palette_budgets,
            config.detail_levels,
            config.background_modes,
            config.composition_modes,
        )
    )
    row_display_sizes = {
        canvas_size: (
            canvas_size[0] * _display_scale_factor(*canvas_size),
            canvas_size[1] * _display_scale_factor(*canvas_size),
        )
        for canvas_size in config.canvas_sizes
    }
    cell_width = max(size[0] for size in row_display_sizes.values())
    row_heights = [row_display_sizes[canvas_size][1] + _LABEL_HEIGHT for canvas_size in config.canvas_sizes]
    sheet_width = cell_width * len(columns)
    sheet_height = sum(row_heights)

    sheet = Image.new("RGBA", (sheet_width, sheet_height), _SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    y_offset = 0
    for canvas_size, row_height in zip(config.canvas_sizes, row_heights):
        for column_index, (budget, detail, background, composition) in enumerate(columns):
            cell_id = _cell_id(canvas_size, budget, detail, background, composition)
            display_image = _normalize_cell_for_display(images_by_cell[cell_id], canvas_size)
            x = column_index * cell_width
            sheet.paste(display_image, (x, y_offset + _LABEL_HEIGHT))
            # 既定フォントは空白が詰まって見えるため、区切りを明示する。
            label = " / ".join(
                (_canvas_label(canvas_size), f"p{budget}", detail, background, composition)
            )
            draw.rectangle((x, y_offset, x + cell_width - 1, y_offset + _LABEL_HEIGHT - 1), fill=_LABEL_BACKGROUND)
            draw.text((x + 2, y_offset + 2), label, fill=_LABEL_TEXT_COLOR)
        y_offset += row_height

    return save_png(sheet, root / "previews" / "comparison_sheet.png")


def _write_summary(root: Path, config: CanvasScaleStudyConfig, metric_records: list[dict[str, Any]]) -> None:
    lines = [
        "# Canvasサイズ×palette上限 比較Study",
        "",
        f"- 元絵: `{config.source}`",
        f"- Canvasサイズ: {', '.join(_canvas_label(size) for size in config.canvas_sizes)}",
        f"- palette上限: {', '.join(str(v) for v in config.palette_budgets)}",
        f"- detail_level: {', '.join(config.detail_levels)}",
        f"- 背景: {', '.join(config.background_modes)}",
        f"- 構図: {', '.join(config.composition_modes)}",
        "",
        "## Canvas × palette 指標表（uniform_2x2_ratio）",
        "",
    ]
    header = "| Canvas \\ palette | " + " | ".join(str(v) for v in config.palette_budgets) + " |"
    separator = "|---|" + "---|" * len(config.palette_budgets)
    lines.append(header)
    lines.append(separator)
    for canvas_size in config.canvas_sizes:
        label = _canvas_label(canvas_size)
        cells = []
        for budget in config.palette_budgets:
            matches = [
                record
                for record in metric_records
                if tuple(record["canvas"].values()) == canvas_size and record["palette_budget"] == budget
            ]
            if matches:
                values = ", ".join(f"{m['uniform_2x2_ratio']:.3f}" for m in matches)
            else:
                values = "-"
            cells.append(values)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## 備考")
    lines.append("")
    lines.append("- uniform_2x2_ratioが高いほど「拡大されただけ」に近い（4.4節）。")
    lines.append("- detail_level・背景・composition_modeが複数ある場合、同じセルに`,`区切りで並べている。")
    lines.append("- 判断はP2の手動QA手順（仕様7節）で行う。本表は機械的な集計に留める。")

    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
