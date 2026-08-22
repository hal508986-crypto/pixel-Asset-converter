"""Stable result contracts for MAP-level compilation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MapMetrics:
    """Approximate discontinuity metrics measured at logical tile boundaries."""

    neighbor_color_discontinuity: float
    neighbor_brightness_discontinuity: float
    neighbor_texture_discontinuity: float
    map_grid_visibility_score: float


@dataclass(frozen=True)
class MapCompilationResult:
    """Artifacts produced for one MAP compilation variant."""

    final_path: Path
    tiles_dir: Path
    layout: dict[str, Any]
    metrics: MapMetrics
    global_analysis: dict[str, Any]


@dataclass(frozen=True)
class MapExperimentResult:
    """Artifacts produced by the required A/B/C MAP experiment."""

    output_root: Path
    baseline_global_path: Path
    independent_path: Path
    context_path: Path
    comparison_path: Path
    metrics_path: Path
    context_result: MapCompilationResult
