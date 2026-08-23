"""Configuration contracts for the hierarchy study."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixel_tile_compiler.pixel_grammar.config import DEFAULT_TARGETS
from pixel_tile_compiler.pixel_grammar.profiles import HierarchyMode


@dataclass(frozen=True)
class LightingConfig:
    direction: tuple[float, float] = (-1.0, -1.0)
    strength: float = 0.5

    def __post_init__(self) -> None:
        if len(self.direction) != 2:
            raise ValueError("lighting direction must contain x and y")
        if self.direction == (0.0, 0.0):
            raise ValueError("lighting direction must not be zero")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("lighting strength must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {"direction": list(self.direction), "strength": self.strength}


@dataclass
class PixelHierarchyStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/pixel_hierarchy_study"))
    targets: tuple[str, ...] = DEFAULT_TARGETS
    modes: tuple[HierarchyMode, ...] = tuple(HierarchyMode)
    sources: dict[str, Path] = field(default_factory=dict)
    target_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    lighting: LightingConfig = field(default_factory=LightingConfig)
    tile_size: int = 64
    source_size: int = 256
    palette_budget: int = 28
    shared_palette: bool = True
    pixelize: bool = True
    generate_demo_maps: bool = True
    map_width: int = 10
    map_height: int = 10
    object_preview_size: int = 5
    generate_unit_preview: bool = True
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.sources = {str(key): Path(value) for key, value in self.sources.items()}
        self.targets = tuple(str(target) for target in self.targets)
        self.modes = tuple(HierarchyMode(mode) for mode in self.modes)
        if not self.targets:
            raise ValueError("pixel hierarchy study needs at least one target")
        unknown = set(self.targets).difference(DEFAULT_TARGETS)
        if unknown:
            raise ValueError(f"unsupported hierarchy targets: {sorted(unknown)}")
        if not self.modes:
            raise ValueError("pixel hierarchy study needs at least one mode")
        if self.tile_size != 64:
            raise ValueError("pixel hierarchy study tile_size is fixed at 64")
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.map_width < 1 or self.map_height < 1:
            raise ValueError("map dimensions must be positive")
        if not 3 <= self.object_preview_size <= 9:
            raise ValueError("object_preview_size must be between 3 and 9")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "PixelHierarchyStudyConfig":
        root = Path(base_dir or ".")
        study = mapping.get("study", mapping)
        pixel = dict(study.get("pixel", {}))
        maps = dict(study.get("maps", {}))
        lighting_raw = dict(study.get("lighting", {}))
        sources = {str(key): _resolve(root, value) for key, value in dict(study.get("sources", {})).items()}
        target_specs = {str(key): dict(value) for key, value in dict(study.get("target_specs", {})).items()}
        modes = study.get("hierarchy_modes", [mode.value for mode in HierarchyMode])
        return cls(
            output_root=_resolve(root, study.get("output_root", "e2e/pixel_hierarchy_study")),
            targets=tuple(study.get("targets", DEFAULT_TARGETS)),
            modes=tuple(HierarchyMode(mode) for mode in modes),
            sources=sources,
            target_specs=target_specs,
            lighting=LightingConfig(
                direction=tuple(float(value) for value in lighting_raw.get("direction", (-1, -1))),
                strength=float(lighting_raw.get("strength", 0.5)),
            ),
            tile_size=int(study.get("tile_size", 64)),
            source_size=int(pixel.get("source_size", 256)),
            palette_budget=int(pixel.get("palette_budget", 28)),
            shared_palette=bool(pixel.get("shared_palette", True)),
            pixelize=bool(pixel.get("enabled", True)),
            generate_demo_maps=bool(maps.get("enabled", True)),
            map_width=int(maps.get("width", 10)),
            map_height=int(maps.get("height", 10)),
            object_preview_size=int(study.get("object_preview_size", 5)),
            generate_unit_preview=bool(study.get("generate_unit_preview", True)),
            seed=int(study.get("seed", mapping.get("seed", 42))),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "output_root": str(self.output_root),
                "targets": list(self.targets),
                "hierarchy_modes": [mode.value for mode in self.modes],
                "sources": {key: str(value) for key, value in sorted(self.sources.items())},
                "target_specs": self.target_specs,
                "lighting": self.lighting.to_dict(),
                "tile_size": self.tile_size,
                "maps": {"enabled": self.generate_demo_maps, "width": self.map_width, "height": self.map_height},
                "object_preview_size": self.object_preview_size,
                "generate_unit_preview": self.generate_unit_preview,
                "pixel": {
                    "source_size": self.source_size,
                    "palette_budget": self.palette_budget,
                    "shared_palette": self.shared_palette,
                    "enabled": self.pixelize,
                },
                "seed": self.seed,
            }
        }


def load_pixel_hierarchy_config(path: Path) -> PixelHierarchyStudyConfig:
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
        raise ValueError("pixel hierarchy config must contain a mapping")
    mapping["config_path"] = str(path)
    return PixelHierarchyStudyConfig.from_mapping(mapping, base_dir=path.parent)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
