"""Configuration for Material Source Library v0.1."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MaterialLibraryStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/material_source_library_v01"))
    library_root: Path = field(default_factory=lambda: Path("material_library"))
    version: str = "0.1"
    materials: tuple[str, ...] = ("grass", "dirt", "water", "stone")
    candidates_per_material: int = 8
    promote_top: int = 2
    generation_enabled: bool = False
    source_gate_enabled: bool = True
    allow_rejected_probe: bool = True
    compile_variants: int = 8
    palette_budget: int = 28
    shared_palette: bool = True
    map_columns: int = 10
    map_rows: int = 10
    seed: int = 42
    config_path: Path | None = None
    source_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.library_root = Path(self.library_root)
        if not self.materials:
            raise ValueError("at least one material family is required")
        if self.candidates_per_material < 1:
            raise ValueError("candidates_per_material must be positive")
        if not 1 <= self.promote_top <= self.candidates_per_material:
            raise ValueError("promote_top must be between 1 and candidates_per_material")
        if not 1 <= self.compile_variants <= 16:
            raise ValueError("compile_variants must be between 1 and 16")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.map_columns < 1 or self.map_rows < 1:
            raise ValueError("map dimensions must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "MaterialLibraryStudyConfig":
        root = Path(base_dir or ".")
        library = dict(mapping.get("library") or {})
        generation = dict(mapping.get("generation") or {})
        gate = dict(mapping.get("source_gate") or {})
        compile_probe = dict(mapping.get("compile_probe") or {})
        map_probe = dict(mapping.get("map_probe") or {})
        materials_value = library.get("materials", mapping.get("materials", ("grass", "dirt", "water", "stone")))
        materials = tuple(str(item) for item in materials_value)
        overrides: dict[str, dict[str, str]] = {}
        for material, values in dict(mapping.get("source_overrides") or {}).items():
            overrides[str(material)] = {str(key): str(_resolve(root, value)) for key, value in dict(values).items()}
        return cls(
            output_root=_resolve(root, mapping.get("output", mapping.get("output_root", "e2e/material_source_library_v01"))),
            library_root=_resolve(root, library.get("root", "material_library")),
            version=str(library.get("version", "0.1")),
            materials=materials,
            candidates_per_material=int(library.get("candidates_per_material", 8)),
            promote_top=int(library.get("promote_top", 2)),
            generation_enabled=bool(generation.get("enabled", False)),
            source_gate_enabled=bool(gate.get("enabled", True)),
            allow_rejected_probe=bool(gate.get("allow_rejected_probe", True)),
            compile_variants=int(compile_probe.get("variants", 8)),
            palette_budget=int(compile_probe.get("palette", compile_probe.get("palette_budget", 28))),
            shared_palette=bool(compile_probe.get("shared_palette", True)),
            map_columns=int(map_probe.get("columns", map_probe.get("width", 10))),
            map_rows=int(map_probe.get("rows", map_probe.get("height", 10))),
            seed=int(mapping.get("seed", 42)),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
            source_overrides=overrides,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "library": {
                "version": self.version,
                "root": str(self.library_root),
                "materials": list(self.materials),
                "candidates_per_material": self.candidates_per_material,
                "promote_top": self.promote_top,
            },
            "generation": {"enabled": self.generation_enabled},
            "source_gate": {"enabled": self.source_gate_enabled, "allow_rejected_probe": self.allow_rejected_probe},
            "compile_probe": {"variants": self.compile_variants, "palette": self.palette_budget, "shared_palette": self.shared_palette},
            "map_probe": {"columns": self.map_columns, "rows": self.map_rows},
            "seed": self.seed,
            "output": str(self.output_root),
            "source_overrides": self.source_overrides,
        }


def load_material_library_config(path: Path) -> MaterialLibraryStudyConfig:
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
        raise ValueError("material library config must contain a mapping")
    mapping["config_path"] = str(path)
    return MaterialLibraryStudyConfig.from_mapping(mapping, path.parent)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
