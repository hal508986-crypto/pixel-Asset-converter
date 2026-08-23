"""Separate source, compile, map, downstream and library fitness scoring."""

from __future__ import annotations

from typing import Any

import numpy as np


def score_record(record: dict[str, Any]) -> dict[str, float]:
    gate = record.get("gate", {})
    fingerprint = record.get("fingerprint", {})
    compile_metrics = record.get("compile_probe", {})
    source_quality = (
        0.28 * float(fingerprint.get("stationarity_score", 0.0))
        + 0.18 * (1.0 - float(fingerprint.get("center_dominance_score", 1.0)))
        + 0.16 * (1.0 - float(fingerprint.get("autocorrelation_peak_risk", 1.0)))
        + 0.14 * (1.0 - float(fingerprint.get("large_landmark_risk", 1.0)))
        + 0.14 * (1.0 - float(fingerprint.get("lighting_bias_score", 1.0)))
        + 0.10 * (1.0 - float(fingerprint.get("feature_scale_error", 1.0)))
    )
    if gate.get("status") == "warning":
        source_quality -= 0.04
    if gate.get("status") == "rejected":
        source_quality -= 0.18
    compile_fitness = float(compile_metrics.get("compile_fitness_score", 0.0))
    map_fitness = float(compile_metrics.get("map_fitness_score", 0.0))
    downstream = 0.58 * compile_fitness + 0.42 * map_fitness
    library_fitness = 0.34 * source_quality + 0.66 * downstream
    return {
        "source_quality_score": _clamp(source_quality),
        "compile_fitness_score": _clamp(compile_fitness),
        "map_fitness_score": _clamp(map_fitness),
        "downstream_fitness_score": _clamp(downstream),
        "library_fitness_score": _clamp(library_fitness),
    }


def rank_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        scores = score_record(item)
        item.update({key: round(value, 6) for key, value in scores.items()})
        scored.append(item)
    scored.sort(key=lambda item: (-float(item["library_fitness_score"]), str(item["source_id"])))
    for index, item in enumerate(scored, start=1):
        item["rank"] = index
    return scored


def source_downstream_correlations(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Exploratory Pearson correlations; null means no measurable variance."""
    downstream = np.asarray([float(item.get("downstream_fitness_score", 0.0)) for item in records], dtype=float)
    source_metrics = (
        "stationarity_score",
        "brightness_spatial_variance",
        "color_spatial_variance",
        "texture_density_variance",
        "center_dominance_score",
        "low_frequency_pattern_strength",
        "autocorrelation_peak_risk",
        "orientation_bias",
        "edge_density",
        "estimated_feature_scale_px",
    )
    result: dict[str, Any] = {"n": len(records), "interpretation": "exploratory", "correlations": {}}
    for metric in source_metrics:
        values = np.asarray([float(item.get("fingerprint", {}).get(metric, 0.0)) for item in records], dtype=float)
        if len(values) < 2 or np.std(values) < 1e-9 or np.std(downstream) < 1e-9:
            correlation = None
        else:
            correlation = float(np.corrcoef(values, downstream)[0, 1])
        result["correlations"][metric] = None if correlation is None else round(correlation, 6)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
