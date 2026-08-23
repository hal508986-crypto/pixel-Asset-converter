"""Cross-source scoring and human-readable study summary helpers."""

from __future__ import annotations

from typing import Any, Sequence


def rank_source_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort records by score and add stable rank values."""
    ranked = sorted(records, key=lambda record: (-float(record.get("score", 0.0)), str(record.get("source_id", ""))))
    return [dict(record, rank=index + 1) for index, record in enumerate(ranked)]


def score_source_record(
    source_metrics: dict[str, Any],
    tileset_metrics: dict[str, Any],
    material: str = "grass",
) -> tuple[float, list[str]]:
    """Use interpretable bounded penalties; no claim of statistical optimality."""
    source = source_metrics.get("metrics", source_metrics)
    penalties = {
        "edge": float(tileset_metrics.get("edge_discontinuity_score", 0.0)),
        "brightness": float(tileset_metrics.get("brightness_discontinuity_score", 0.0)),
        "palette": float(tileset_metrics.get("palette_discontinuity_score", 0.0)),
        "periodicity": float(tileset_metrics.get("periodicity_risk", 0.0)),
        "grid": float(tileset_metrics.get("grid_visibility_score", 0.0)),
        "landmark": float(source.get("large_landmark_risk", 0.0)),
        "low_frequency": float(source.get("low_frequency_pattern_strength", 0.0)),
        "autocorrelation": float(source.get("autocorrelation_peak_risk", 0.0)),
    }
    if material == "forest_canopy":
        penalties.update(
            {
                "fragmentation": float(source.get("canopy_fragmentation_score", 0.0)),
                "large_mass": float(source.get("large_mass_dominance_score", 0.0)),
                "continuity_break": float(tileset_metrics.get("canopy_mass_break_risk", 0.0)),
            }
        )
        score = 1.0 - (
            0.16 * penalties["edge"]
            + 0.10 * penalties["brightness"]
            + 0.06 * penalties["palette"]
            + 0.13 * penalties["periodicity"]
            + 0.10 * penalties["grid"]
            + 0.08 * penalties["landmark"]
            + 0.09 * penalties["low_frequency"]
            + 0.07 * penalties["autocorrelation"]
            + 0.07 * penalties["fragmentation"]
            + 0.08 * penalties["large_mass"]
            + 0.06 * (1.0 - float(tileset_metrics.get("cluster_continuity_score", 0.0)))
        )
    else:
        score = 1.0 - (
            0.22 * penalties["edge"]
            + 0.16 * penalties["brightness"]
            + 0.12 * penalties["palette"]
            + 0.18 * penalties["periodicity"]
            + 0.14 * penalties["grid"]
            + 0.08 * penalties["landmark"]
            + 0.06 * penalties["low_frequency"]
            + 0.04 * penalties["autocorrelation"]
        )
    texture_variance = float(source.get("texture_density_variance", 0.0))
    if texture_variance < 0.0005:
        score -= 0.04 if material == "grass" else 0.02
    elif texture_variance > 0.04:
        score -= 0.03 if material == "grass" else 0.02
    reasons: list[str] = []
    if penalties["landmark"] > 0.55:
        reasons.append("focal/landmark risk is high")
    if penalties["low_frequency"] > 0.35 or penalties["autocorrelation"] > 0.75:
        reasons.append("macro pattern or autocorrelation is high")
    if texture_variance < 0.0005:
        reasons.append("texture variation is too low")
    if material == "forest_canopy":
        if penalties["fragmentation"] > 0.65:
            reasons.append("canopy is too fragmented")
        if penalties["large_mass"] > 0.55:
            reasons.append("large canopy mass dominance is high")
        if float(tileset_metrics.get("cluster_continuity_score", 0.0)) < 0.6:
            reasons.append("canopy cluster continuity is low")
    if penalties["periodicity"] < 0.2 and penalties["grid"] < 0.2:
        reasons.append("low periodicity and grid visibility")
    return max(0.0, min(1.0, score)), reasons


def study_summary_markdown(ranking: Sequence[dict[str, Any]], material: str = "grass") -> str:
    top = list(ranking[:3])
    worst = list(ranking[-3:]) if ranking else []
    forest = material == "forest_canopy"
    lines = [
        "# Forest Canopy Source Study Summary" if forest else "# Grass Source Study Summary",
        "",
        "## Top sources",
        "",
    ]
    lines.extend(f"- {item['source_id']}: score={item.get('score', 0):.4f} — {', '.join(item.get('reasons', [])) or 'balanced source'}" for item in top)
    lines.extend(("", "## Worst sources", ""))
    lines.extend(f"- {item['source_id']}: score={item.get('score', 0):.4f} — {', '.join(item.get('reasons', [])) or 'higher source risk'}" for item in worst)
    lines.extend(
        (
            "",
            "## Observed source conditions",
            "",
            *(() if forest else (
                "- Moderate local variation is useful when it does not introduce a focal macro pattern.",
                "- High brightness variation, visible tufts, and illustrative composition increase macro-pattern risk.",
                "- Very homogeneous sources reduce grid risk but can become visually flat and lose texture variation.",
                "- In this run, grass_src_08_illustrative ranked first because its compiled variants scored well on the current periodicity/grid terms despite higher source macro-pattern indicators; this is a metric interaction, not a visual-quality verdict.",
            )),
            *(() if not forest else (
                "- Small-to-medium overlapping canopy clusters preserve local variation while keeping neighboring tiles visually connected.",
                "- A single large canopy mass raises low-frequency, landmark, and mass-break risk even when the source looks scenic.",
                "- Highly fragmented canopy breaks can look noisy and reduce silhouette continuity at tile boundaries.",
                "- The ranking is metric-based; confirm forest readability with human review before freezing weights.",
            )),
            "- This is an experiment ranking, not a general perceptual quality score; recalibrate weights after human review.",
        )
    )
    return "\n".join(lines) + "\n"
