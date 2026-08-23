"""Acceptance signals for a material exemplar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image

from .material import MaterialAnalysis, MaterialMetrics, analyze_material

ValidationStatus = Literal["accepted", "warning", "rejected"]


@dataclass(frozen=True)
class SourceValidationReport:
    status: ValidationStatus
    metrics: MaterialMetrics
    warnings: tuple[str, ...]
    analysis: MaterialAnalysis

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "metrics": self.metrics.as_dict(),
            "warnings": list(self.warnings),
            "analysis": self.analysis.as_dict(),
        }


def validate_material_source(image: Image.Image, palette_budget: int = 24, material: str = "grass") -> SourceValidationReport:
    """Classify a source as usable, risky, or unsuitable for generic surface tiles."""
    analysis = analyze_material(image, palette_budget=palette_budget, material=material)
    metrics = analysis.metrics
    warnings: list[str] = []
    if metrics.center_dominance > 0.55:
        warnings.append("center_dominance too high")
    if metrics.brightness_spatial_variance > 0.018:
        warnings.append("brightness spatial variance too high")
    if metrics.color_spatial_variance > 0.018:
        warnings.append("color spatial variance too high")
    if metrics.texture_density_variance > 0.018:
        warnings.append("texture density variance too high")
    if metrics.large_landmark_risk > 0.7:
        warnings.append("large landmark risk detected")
    rejected = (
        metrics.brightness_spatial_variance > 0.08
        or metrics.color_spatial_variance > 0.08
        or metrics.large_landmark_risk > 0.92
    )
    status: ValidationStatus = "rejected" if rejected else "warning" if warnings else "accepted"
    return SourceValidationReport(status, metrics, tuple(warnings), analysis)
