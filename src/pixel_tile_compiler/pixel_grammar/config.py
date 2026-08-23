"""Configuration for the Pixel Grammar Study."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profiles import DensityLevel


DEFAULT_TARGETS = (
    "grass",
    "forest_canopy",
    "dirt_road",
    "river",
    "grass_forest",
    "grass_road",
    "grass_river",
    "forest_river",
    "tree_object",
    "rock_object",
)


@dataclass
class PixelGrammarStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/pixel_grammar_study"))
    targets: tuple[str, ...] = DEFAULT_TARGETS
    levels: tuple[DensityLevel, ...] = tuple(DensityLevel)
    sources: dict[str, Path] = field(default_factory=dict)
    target_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tile_size: int = 64
    source_size: int = 256
    palette_budget: int = 28
    shared_palette: bool = True
    pixelize: bool = True
    generate_demo_maps: bool = True
    map_width: int = 10
    map_height: int = 10
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.sources = {str(key): Path(value) for key, value in self.sources.items()}
        self.targets = tuple(str(target) for target in self.targets)
        self.levels = tuple(DensityLevel(level) for level in self.levels)
        if not self.targets:
            raise ValueError("pixel grammar study needs at least one target")
        unknown = set(self.targets).difference(DEFAULT_TARGETS)
        if unknown:
            raise ValueError(f"unsupported pixel grammar targets: {sorted(unknown)}")
        if self.tile_size != 64:
            raise ValueError("pixel grammar study tile_size is fixed at 64")
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.map_width < 1 or self.map_height < 1:
            raise ValueError("map dimensions must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "PixelGrammarStudyConfig":
        root = Path(base_dir or ".")
        study = mapping.get("study", mapping)
        pixel = dict(study.get("pixel", mapping.get("pixel", {})))
        maps = dict(study.get("maps", mapping.get("maps", {})))
        sources = {
            str(key): _resolve(root, value)
            for key, value in dict(study.get("sources", mapping.get("sources", {}))).items()
        }
        target_specs = {
            str(key): dict(value)
            for key, value in dict(study.get("target_specs", mapping.get("target_specs", {}))).items()
        }
        levels = study.get("levels", [level.value for level in DensityLevel])
        return cls(
            output_root=_resolve(root, study.get("output_root", "e2e/pixel_grammar_study")),
            targets=tuple(study.get("targets", DEFAULT_TARGETS)),
            levels=tuple(DensityLevel(level) for level in levels),
            sources=sources,
            target_specs=target_specs,
            tile_size=int(study.get("tile_size", 64)),
            source_size=int(pixel.get("source_size", study.get("source_size", 256))),
            palette_budget=int(pixel.get("palette_budget", study.get("palette_budget", 28))),
            shared_palette=bool(pixel.get("shared_palette", True)),
            pixelize=bool(pixel.get("enabled", study.get("pixelize", True))),
            generate_demo_maps=bool(maps.get("enabled", study.get("generate_demo_maps", True))),
            map_width=int(maps.get("width", 10)),
            map_height=int(maps.get("height", 10)),
            seed=int(study.get("seed", mapping.get("seed", 42))),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "output_root": str(self.output_root),
                "targets": list(self.targets),
                "levels": [level.value for level in self.levels],
                "sources": {key: str(value) for key, value in sorted(self.sources.items())},
                "target_specs": self.target_specs,
                "tile_size": self.tile_size,
                "maps": {
                    "enabled": self.generate_demo_maps,
                    "width": self.map_width,
                    "height": self.map_height,
                },
                "pixel": {
                    "source_size": self.source_size,
                    "palette_budget": self.palette_budget,
                    "shared_palette": self.shared_palette,
                    "enabled": self.pixelize,
                },
                "seed": self.seed,
            }
        }


def load_pixel_grammar_config(path: Path) -> PixelGrammarStudyConfig:
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
        raise ValueError("pixel grammar config must contain a mapping")
    mapping["config_path"] = str(path)
    return PixelGrammarStudyConfig.from_mapping(mapping, base_dir=path.parent)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
