"""Configuration contract for the character palette/density Study."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixel_tile_compiler.pixelizer.character_detail import CharacterDetailLevel


DEFAULT_PALETTE_BUDGETS = (16, 24, 32)
DEFAULT_DETAIL_LEVELS: tuple[CharacterDetailLevel, ...] = ("sparse", "balanced", "detailed")


@dataclass(frozen=True)
class CharacterStudyCase:
    case_id: str
    source: Path
    review_features: tuple[str, ...] = ()


@dataclass
class CharacterPaletteDensityStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/character_palette_density_study"))
    palette_budgets: tuple[int, ...] = DEFAULT_PALETTE_BUDGETS
    detail_levels: tuple[CharacterDetailLevel, ...] = DEFAULT_DETAIL_LEVELS
    cases: tuple[CharacterStudyCase, ...] = ()
    frame_width: int = 54
    frame_height: int = 54
    bottom_margin: int = 7
    background_mode: str = "auto"
    outline: str = "off"
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.palette_budgets = tuple(int(value) for value in self.palette_budgets)
        self.detail_levels = tuple(str(value) for value in self.detail_levels)  # type: ignore[assignment]
        self.cases = tuple(self.cases)
        if not self.palette_budgets or any(value not in {16, 24, 32} for value in self.palette_budgets):
            raise ValueError("palette_budgets must contain only 16, 24, or 32")
        if len(set(self.palette_budgets)) != len(self.palette_budgets):
            raise ValueError("palette_budgets must not contain duplicates")
        if not self.detail_levels or any(value not in {"sparse", "balanced", "detailed"} for value in self.detail_levels):
            raise ValueError("detail_levels must contain sparse, balanced, or detailed")
        if len(set(self.detail_levels)) != len(self.detail_levels):
            raise ValueError("detail_levels must not contain duplicates")
        if not self.cases:
            raise ValueError("character palette density study needs at least one case")
        if any(not case.case_id or not case.source for case in self.cases):
            raise ValueError("each character study case needs an id and source")
        if self.frame_width < 1 or self.frame_height < 1:
            raise ValueError("character frame dimensions must be positive")
        if self.bottom_margin < 0:
            raise ValueError("bottom_margin must be non-negative")
        if self.background_mode not in {"auto", "alpha", "color"}:
            raise ValueError("background_mode must be auto, alpha, or color")
        if self.outline not in {"off", "black", "white"}:
            raise ValueError("outline must be off, black, or white")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "CharacterPaletteDensityStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study", mapping))
        character = dict(study.get("character", {}))
        raw_cases = study.get("cases", [])
        cases = tuple(
            CharacterStudyCase(
                case_id=str(item["id"]),
                source=_resolve(root, item["source"]),
                review_features=tuple(str(value) for value in item.get("review_features", [])),
            )
            for item in raw_cases
        )
        outline_value = character.get("outline", "off")
        if outline_value is False:
            outline_value = "off"
        return cls(
            output_root=_resolve(root, study.get("output_root", "e2e/character_palette_density_study")),
            palette_budgets=tuple(study.get("palette_budgets", DEFAULT_PALETTE_BUDGETS)),
            detail_levels=tuple(study.get("detail_levels", DEFAULT_DETAIL_LEVELS)),  # type: ignore[arg-type]
            cases=cases,
            frame_width=int(character.get("frame_width", 54)),
            frame_height=int(character.get("frame_height", 54)),
            bottom_margin=int(character.get("bottom_margin", 7)),
            background_mode=str(character.get("background_mode", "auto")),
            outline=str(outline_value),
            seed=int(study.get("seed", 42)),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "output_root": str(self.output_root),
                "palette_budgets": list(self.palette_budgets),
                "detail_levels": list(self.detail_levels),
                "cases": [
                    {
                        "id": case.case_id,
                        "source": str(case.source),
                        "review_features": list(case.review_features),
                    }
                    for case in self.cases
                ],
                "character": {
                    "frame_width": self.frame_width,
                    "frame_height": self.frame_height,
                    "bottom_margin": self.bottom_margin,
                    "background_mode": self.background_mode,
                    "outline": self.outline,
                },
                "seed": self.seed,
            }
        }


def load_character_study_config(path: Path) -> CharacterPaletteDensityStudyConfig:
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
        raise ValueError("character palette density config must contain a mapping")
    mapping["config_path"] = str(path)
    return CharacterPaletteDensityStudyConfig.from_mapping(mapping, base_dir=path.parent)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path
