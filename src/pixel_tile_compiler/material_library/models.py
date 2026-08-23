"""Persistent data contracts for material source candidates and families."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

MaterialStatus = Literal["candidate", "accepted", "warning", "rejected"]


@dataclass(frozen=True)
class TargetPixelSpec:
    """Expected visible feature scale after reduction to a 64px tile."""

    min_px: float
    preferred_px: float
    max_px: float

    def __post_init__(self) -> None:
        if not (0 < self.min_px <= self.preferred_px <= self.max_px):
            raise ValueError("target pixel scale must satisfy 0 < min <= preferred <= max")

    def as_dict(self) -> dict[str, float]:
        return {"min_px": self.min_px, "preferred_px": self.preferred_px, "max_px": self.max_px}


@dataclass(frozen=True)
class SourceConstraints:
    perspective: str = "forbidden"
    lighting_bias: str = "neutral"
    landmark_allowed: bool = False
    macro_pattern_allowed: bool = False
    directionality: str = "none"
    preferred_stationarity: float = 0.70

    def __post_init__(self) -> None:
        if not 0.0 <= self.preferred_stationarity <= 1.0:
            raise ValueError("preferred_stationarity must be between 0 and 1")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationMetadata:
    generator: str = "not_generated"
    generation_date: str | None = None
    prompt_version: str = "material-library-v0.1"
    source_dimensions: tuple[int, int] | None = None
    seed: int | None = None
    status: str = "prompt_only"

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["source_dimensions"] = list(self.source_dimensions) if self.source_dimensions else None
        return values


@dataclass(frozen=True)
class HumanReview:
    """Human assessment slot; automation must not invent a rating."""

    rating: int | None = None
    accepted: bool | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise ValueError("human review rating must be between 1 and 5")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MaterialSourceCard:
    version: str = "0.1"
    source_id: str = ""
    material_id: str = ""
    material_class: str = "surface"
    source_file: str = ""
    prompt_file: str = ""
    generation: GenerationMetadata = field(default_factory=GenerationMetadata)
    target_pixel_spec: TargetPixelSpec = field(default_factory=lambda: TargetPixelSpec(2, 4, 8))
    constraints: SourceConstraints = field(default_factory=SourceConstraints)
    fingerprint: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    compiler: dict[str, Any] | None = None
    status: MaterialStatus = "candidate"
    human_review: HumanReview = field(default_factory=HumanReview)

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id is required")
        if not self.material_id:
            raise ValueError("material_id is required")
        if self.status not in {"candidate", "accepted", "warning", "rejected"}:
            raise ValueError(f"unknown material source status: {self.status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_id": self.source_id,
            "material_id": self.material_id,
            "material_class": self.material_class,
            "source_file": self.source_file,
            "prompt_file": self.prompt_file,
            "generation": self.generation.as_dict(),
            "target_pixel_spec": self.target_pixel_spec.as_dict(),
            "constraints": self.constraints.as_dict(),
            "fingerprint": self.fingerprint,
            "validation": self.validation,
            "compiler": self.compiler,
            "status": self.status,
            "human_review": self.human_review.as_dict(),
        }

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "MaterialSourceCard":
        generation = dict(values.get("generation") or {})
        dimensions = generation.get("source_dimensions")
        if dimensions is not None:
            generation["source_dimensions"] = tuple(int(value) for value in dimensions)
        target = dict(values.get("target_pixel_spec") or {})
        constraints = dict(values.get("constraints") or {})
        review = dict(values.get("human_review") or {})
        return cls(
            version=str(values.get("version", "0.1")),
            source_id=str(values["source_id"]),
            material_id=str(values["material_id"]),
            material_class=str(values.get("material_class", "surface")),
            source_file=str(values.get("source_file", "")),
            prompt_file=str(values.get("prompt_file", "")),
            generation=GenerationMetadata(**generation),
            target_pixel_spec=TargetPixelSpec(**target),
            constraints=SourceConstraints(**constraints),
            fingerprint=values.get("fingerprint"),
            validation=values.get("validation"),
            compiler=values.get("compiler"),
            status=values.get("status", "candidate"),
            human_review=HumanReview(**review),
        )


@dataclass(frozen=True)
class SourceGateReport:
    source_id: str
    material_id: str
    status: MaterialStatus
    accepted_for_probe: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "material_id": self.material_id,
            "status": self.status,
            "accepted_for_probe": self.accepted_for_probe,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class MaterialFamily:
    material_id: str
    material_class: str
    accepted_sources: tuple[str, ...]
    preferred_source: str | None
    recommended_feature_scale_px: float
    recommended_renderer: str
    recommended_palette_budget: int

    def as_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["accepted_sources"] = list(self.accepted_sources)
        return values


def path_value(value: Path | str) -> str:
    return str(value)
