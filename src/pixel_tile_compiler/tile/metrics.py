"""Compilation metric aggregation."""

from dataclasses import asdict

from .seam import SeamMetrics


def seam_metrics_dict(metrics: SeamMetrics) -> dict[str, float | None]:
    """Serialize seam metrics with the names used by metadata and GUI."""
    return asdict(metrics)
