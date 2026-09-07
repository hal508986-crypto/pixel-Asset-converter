"""Native-resolution A/B/C study for a character source."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pixelizer.character_detail import CharacterDetailLevel
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .metrics import compare_visible_pixels, image_metrics, sha256_file


@dataclass
class NativeResolutionStudyConfig:
    """Configuration for the B24 native-resolution comparison."""

    source: Path
    output_root: Path = field(default_factory=lambda: Path("e2e/character_native_resolution_study"))
    palette_budget: int = 24
    detail_level: CharacterDetailLevel = "balanced"
    review_features: tuple[str, ...] = ()
    background_mode: str = "auto"
    outline: str = "off"
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.source = Path(self.source)
        self.output_root = Path(self.output_root)
        self.review_features = tuple(str(value) for value in self.review_features)
        self.palette_budget = int(self.palette_budget)
        self.detail_level = str(self.detail_level)  # type: ignore[assignment]
        self.background_mode = str(self.background_mode)
        self.outline = str(self.outline)
        self.seed = int(self.seed)
        if self.palette_budget != 24:
            raise ValueError("native resolution study uses the B24 palette budget of 24")
        if self.detail_level not in {"sparse", "balanced", "detailed"}:
            raise ValueError("detail_level must be sparse, balanced, or detailed")
        if self.background_mode not in {"auto", "alpha", "color"}:
            raise ValueError("background_mode must be auto, alpha, or color")
        if self.outline not in {"off", "black", "white"}:
            raise ValueError("outline must be off, black, or white")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "NativeResolutionStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study", mapping))
        character = dict(study.get("character", {}))
        if "source" not in study:
            raise ValueError("native resolution study needs a source")
        outline_value = character.get("outline", "off")
        if outline_value is False:
            outline_value = "off"
        return cls(
            source=_resolve(root, study["source"]),
            output_root=_resolve(root, study.get("output_root", "e2e/character_native_resolution_study")),
            palette_budget=int(study.get("palette_budget", 24)),
            detail_level=str(study.get("detail_level", "balanced")),  # type: ignore[arg-type]
            review_features=tuple(str(value) for value in study.get("review_features", [])),
            background_mode=str(character.get("background_mode", "auto")),
            outline=str(outline_value),
            seed=int(study.get("seed", 42)),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "source": str(self.source),
                "output_root": str(self.output_root),
                "preset": "b24",
                "palette_budget": self.palette_budget,
                "detail_level": self.detail_level,
                "review_features": list(self.review_features),
                "character": {
                    "background_mode": self.background_mode,
                    "outline": self.outline,
                },
                "seed": self.seed,
            }
        }


def load_native_resolution_study_config(path: Path) -> NativeResolutionStudyConfig:
    """Load a JSON or YAML native-resolution study configuration."""
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
        raise ValueError("native resolution study config must contain a mapping")
    mapping["config_path"] = str(path)
    return NativeResolutionStudyConfig.from_mapping(mapping, base_dir=path.parent)


@dataclass(frozen=True)
class NativeResolutionStudyResult:
    output_root: Path
    manifest_path: Path
    metrics_path: Path
    comparison_path: Path


class NativeResolutionStudyRunner:
    """Generate native 64, 64-upscaled control, and native 128 outputs."""

    _CANVAS_SIZES: tuple[tuple[int, int], ...] = ((64, 64), (128, 128))

    def run(self, config: NativeResolutionStudyConfig) -> NativeResolutionStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        if not config.source.exists():
            raise FileNotFoundError(f"native resolution Study source does not exist: {config.source}")

        save_json(config.to_dict(), root / "config.json")
        source_snapshot = root / "source_snapshot.png"
        shutil.copy2(config.source, source_snapshot)
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
                "source_snapshot": "source_snapshot.png",
                "source_sha256": source_hash,
                **source_info,
            },
            root / "source.json",
        )

        compiler = PixelTileCompiler()
        native_records: dict[str, dict[str, Any]] = {}
        native_images: dict[tuple[int, int], Image.Image] = {}
        for canvas_size in self._CANVAS_SIZES:
            label = _canvas_label(canvas_size)
            cell_root = root / "native" / label
            compiler_config = compiler_config_for_purpose(
                "character",
                output_root=cell_root,
                canvas=CanvasSpec(*canvas_size),
                palette_budget=config.palette_budget,
                character_detail_level=config.detail_level,
                background_mode=config.background_mode,
                outline_color=config.outline,
                seed=config.seed,
                debug_enabled=False,
            )
            result = compiler.compile(config.source, compiler_config)
            with Image.open(result.final_path) as final:
                native_images[canvas_size] = final.convert("RGBA")
                metrics = image_metrics(final, result.final_path, config.palette_budget)
            native_records[label] = {
                "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
                "final_path": str(result.final_path.relative_to(root)),
                "metadata_path": str((cell_root / "metadata.json").relative_to(root)),
                "character_layout": {
                    "frame_width": compiler_config.character_layout.frame_width,
                    "frame_height": compiler_config.character_layout.frame_height,
                    "bottom_margin": compiler_config.character_layout.bottom_margin,
                },
                **metrics,
            }

        control = native_images[(64, 64)].resize((128, 128), Image.Resampling.NEAREST)
        control_path = save_png(control, root / "controls" / "64x64_upscaled_to_128.png")
        save_png(native_images[(64, 64)], root / "previews" / "native_64_actual.png")
        save_png(native_images[(128, 128)], root / "previews" / "native_128_actual.png")
        control_metrics = image_metrics(control, control_path, config.palette_budget)
        native_128 = native_images[(128, 128)]
        changed, changed_ratio = compare_visible_pixels(native_128, control)
        metrics = {
            "preset": "b24",
            "palette_budget": config.palette_budget,
            "detail_level": config.detail_level,
            "source_sha256": source_hash,
            "native_128_equal_to_64_upscaled": native_128.tobytes() == control.tobytes(),
            "native_128_changed_pixels_vs_upscaled_64": changed,
            "native_128_changed_visible_ratio_vs_upscaled_64": changed_ratio,
            "native_64_two_by_two_uniform_block_ratio": _two_by_two_uniform_block_ratio(native_images[(64, 64)].resize((128, 128), Image.Resampling.NEAREST)),
            "native_128_two_by_two_uniform_block_ratio": _two_by_two_uniform_block_ratio(native_128),
            "control": control_metrics,
            "native_outputs": native_records,
        }
        metrics_path = root / "metrics" / "resolution_metrics.json"
        save_json(metrics, metrics_path)

        comparison_path = self._write_comparison_board(root, native_images, control)
        self._write_review_template(root, config)
        manifest = {
            "preset": "b24",
            "source": str(config.source),
            "source_snapshot": "source_snapshot.png",
            "source_sha256": source_hash,
            "review_features": list(config.review_features),
            "native_outputs": native_records,
            "control": {
                "path": str(control_path.relative_to(root)),
                "canvas": {"width": 128, "height": 128},
            },
            "metrics_path": str(metrics_path.relative_to(root)),
            "comparison_path": str(comparison_path.relative_to(root)),
            "human_review_only": True,
            "config": config.to_dict()["study"],
        }
        manifest_path = root / "manifest.json"
        save_json(manifest, manifest_path)
        return NativeResolutionStudyResult(root, manifest_path, metrics_path, comparison_path)

    def _write_comparison_board(
        self,
        root: Path,
        native_images: dict[tuple[int, int], Image.Image],
        control: Image.Image,
    ) -> Path:
        board = Image.new("RGBA", (128 * 3, 148), (20, 20, 24, 255))
        draw = ImageDraw.Draw(board)
        cells = (
            ("A native 64", native_images[(64, 64)].resize((128, 128), Image.Resampling.NEAREST)),
            ("B 64 nearest 2x", control),
            ("C native 128", native_images[(128, 128)]),
        )
        for column, (label, image) in enumerate(cells):
            left = column * 128
            board.paste(image.convert("RGBA"), (left, 20))
            draw.rectangle((left, 0, left + 127, 19), fill=(0, 0, 0, 220))
            draw.text((left + 3, 4), label, fill=(255, 255, 255, 255))
        return save_png(board, root / "previews" / "comparison_128_canvas.png")

    def _write_review_template(self, root: Path, config: NativeResolutionStudyConfig) -> None:
        cells = []
        for cell_id in ("native_64", "upscaled_64_control", "native_128"):
            cells.append(
                {
                    "cell_id": cell_id,
                    "features": {feature: None for feature in config.review_features},
                    "silhouette_readability": None,
                    "actual_size_readability": None,
                    "pixel_budget_usage": None,
                    "notes": None,
                    "selected": None,
                }
            )
        save_json(
            {
                "preset": "b24",
                "review_features": list(config.review_features),
                "cells": cells,
                "instructions": "Human review only; no automatic winner is assigned.",
            },
            root / "review" / "review_template.json",
        )


def _canvas_label(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def _two_by_two_uniform_block_ratio(image: Image.Image) -> float:
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


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
