"""Material-specific source acceptance gate."""

from __future__ import annotations

from PIL import Image

from .fingerprint import fingerprint_image
from .models import SourceGateReport, SourceConstraints, TargetPixelSpec


def evaluate_source_gate(
    image: Image.Image,
    material_id: str,
    target_pixel_spec: TargetPixelSpec,
    constraints: SourceConstraints | None = None,
    source_id: str = "",
    allow_rejected_probe: bool = True,
) -> SourceGateReport:
    """Classify a source as accepted, warning, or rejected.

    The gate intentionally remains conservative and explainable.  Rejected
    sources may still enter the compile probe when the experiment asks for a
    full comparison; promotion is the step that excludes them.
    """
    constraints = constraints or SourceConstraints()
    metrics = fingerprint_image(
        image,
        target_feature_scale_px=target_pixel_spec.preferred_px,
    )
    warnings: list[str] = []
    errors: list[str] = []
    if metrics["stationarity_score"] < constraints.preferred_stationarity - 0.20:
        warnings.append("low stationarity")
    if metrics["autocorrelation_peak_risk"] > 0.62 and not constraints.macro_pattern_allowed:
        warnings.append("high autocorrelation peak risk")
    if metrics["low_frequency_pattern_strength"] > 0.70 and not constraints.macro_pattern_allowed:
        warnings.append("strong low-frequency pattern")
    if metrics["center_dominance_score"] > 0.70 and not constraints.landmark_allowed:
        warnings.append("center dominance suggests a focal composition")
    if metrics["large_landmark_risk"] > 0.78 and not constraints.landmark_allowed:
        errors.append("large landmark risk")
    if metrics["lighting_bias_score"] > 0.68 and constraints.lighting_bias == "neutral":
        warnings.append("directional lighting bias")
    if metrics["feature_scale_error"] > 0.80:
        warnings.append("estimated feature scale is far from target")
    if material_id == "water" and metrics["orientation_bias"] > 0.75:
        warnings.append("strong directional texture bias for water material")
    if material_id == "stone" and metrics["high_frequency_energy"] > 0.78:
        warnings.append("stone texture may collapse into noisy pixels")
    if material_id == "grass" and metrics["texture_density_variance"] > 0.70:
        warnings.append("grass detail density varies strongly across patches")
    if material_id == "dirt" and metrics["low_frequency_pattern_strength"] > 0.80:
        warnings.append("dirt source has a large macro patch")
    status = "rejected" if errors else ("warning" if warnings else "accepted")
    return SourceGateReport(
        source_id=source_id,
        material_id=material_id,
        status=status,
        accepted_for_probe=status != "rejected" or allow_rejected_probe,
        warnings=tuple(warnings),
        errors=tuple(errors),
        metrics=metrics,
    )
