"""Stable contracts for multi-source material studies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceCandidate:
    source_id: str
    homogeneity: str
    brightness_variation: str
    tufts: str
    composition: str
    contrast: str
    source_path: Path | None = None
    prompt_file: Path | None = None
    generation: dict[str, Any] = field(default_factory=dict)
    canopy_density: str = "medium"
    cluster_scale: str = "medium"
    illustrative: bool = False

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["source_path"] = str(self.source_path) if self.source_path else None
        values["prompt_file"] = str(self.prompt_file) if self.prompt_file else None
        return values


@dataclass
class SourceStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/source_study"))
    source_root: Path = field(default_factory=lambda: Path("assets/source_experiments/source"))
    prompt_root: Path = field(default_factory=lambda: Path("assets/source_experiments/prompts"))
    candidates: tuple[SourceCandidate, ...] = ()
    material: str = "grass"
    generation_enabled: bool = False
    seed: int = 42
    tileset_options: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.source_root = Path(self.source_root)
        self.prompt_root = Path(self.prompt_root)
        if self.material not in {"grass", "forest_canopy"}:
            raise ValueError("source study material must be grass or forest_canopy")
        if len(self.candidates) < 1:
            raise ValueError("source study needs at least one candidate")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "SourceStudyConfig":
        root = Path(base_dir or ".")
        study = mapping.get("study", mapping)
        source_root = _resolve(root, study.get("source_root", "assets/source_experiments/source"))
        prompt_root = _resolve(root, study.get("prompt_root", "assets/source_experiments/prompts"))
        candidates: list[SourceCandidate] = []
        for item in study.get("source_candidates", []):
            source_path = item.get("source_file", item.get("source_path"))
            prompt_file = item.get("prompt_file")
            candidates.append(
                SourceCandidate(
                    source_id=str(item["id"]),
                    homogeneity=str(item.get("homogeneity", "moderate")),
                    brightness_variation=str(item.get("brightness_variation", "medium")),
                    tufts=str(item.get("tufts", "small sparse tufts")),
                    composition=str(item.get("composition", "no focal point")),
                    contrast=str(item.get("contrast", "medium")),
                    source_path=_resolve(root, source_path) if source_path else source_root / f"{item['id']}.png",
                    prompt_file=_resolve(root, prompt_file) if prompt_file else prompt_root / f"{item['id']}.txt",
                    generation=dict(item.get("generation", {})),
                    canopy_density=str(item.get("canopy_density", "medium")),
                    cluster_scale=str(item.get("cluster_scale", "medium")),
                    illustrative=bool(item.get("illustrative", False)),
                )
            )
        tileset = dict(study.get("tileset", {}))
        return cls(
            output_root=_resolve(root, study.get("output_root", "e2e/source_study")),
            source_root=source_root,
            prompt_root=prompt_root,
            candidates=tuple(candidates),
            material=str(study.get("material", "grass")),
            generation_enabled=bool(study.get("generation", {}).get("enabled", False)),
            seed=int(study.get("seed", tileset.get("seed", 42))),
            tileset_options=tileset,
            config_path=_resolve(root, mapping.get("config_path")) if mapping.get("config_path") else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "material": self.material,
                "source_root": str(self.source_root),
                "prompt_root": str(self.prompt_root),
                "output_root": str(self.output_root),
                "generation": {"enabled": self.generation_enabled},
                "seed": self.seed,
                "source_candidates": [candidate.as_dict() for candidate in self.candidates],
                "tileset": dict(self.tileset_options),
            }
        }


@dataclass(frozen=True)
class SourceStudyResult:
    output_root: Path
    records: tuple[dict[str, Any], ...]
    ranking: tuple[dict[str, Any], ...]
    skipped: tuple[dict[str, Any], ...]


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
