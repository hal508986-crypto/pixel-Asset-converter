"""Controlled prompt matrix for the four v0.1 material families."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


COMMON = (
    "high-resolution top-down orthographic material exemplar, reusable SRPG tileset source, "
    "texture-synthesis-friendly continuous field, no perspective, no focal point, no composition center, "
    "no hero object, no large landmark, no cast shadow, no scenic illustration, no text, no border"
)


MATERIALS: dict[str, dict[str, Any]] = {
    "grass": {
        "material_class": "surface",
        "renderer": "surface",
        "target": {"min_px": 2, "preferred_px": 4, "max_px": 8},
        "template": "top-down continuous grass material field, small tufts, moderate density, subtle local variation, no path, no flowers, no rocks, no trees, no objects",
        "candidates": [
            {"id": "grass_v01_uniform_dark", "scale": "fine", "homogeneity": "high", "contrast": "low", "variation": "subtle"},
            {"id": "grass_v02_uniform_mid", "scale": "fine", "homogeneity": "high", "contrast": "medium", "variation": "subtle"},
            {"id": "grass_v03_uniform_light", "scale": "fine", "homogeneity": "medium", "contrast": "low", "variation": "subtle"},
            {"id": "grass_v04_sparse_tufts", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "grass_v05_patchy_mid", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "grass_v06_bright_variation", "scale": "medium", "homogeneity": "low", "contrast": "high", "variation": "strong"},
            {"id": "grass_v07_dense_tufts", "scale": "small", "homogeneity": "low", "contrast": "high", "variation": "strong"},
            {"id": "grass_v08_illustrative", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong", "illustrative": True},
        ],
    },
    "dirt": {
        "material_class": "surface",
        "renderer": "road_material",
        "target": {"min_px": 3, "preferred_px": 6, "max_px": 12},
        "template": "top-down continuous dirt and soil material field, compact granular texture, reusable tile source, no road shape, no wheel tracks, no stones as isolated objects, no path composition",
        "candidates": [
            {"id": "dirt_v01_fine_compact", "scale": "fine", "homogeneity": "high", "contrast": "low", "variation": "subtle"},
            {"id": "dirt_v02_fine_warm", "scale": "fine", "homogeneity": "high", "contrast": "medium", "variation": "subtle"},
            {"id": "dirt_v03_small_grain", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "dirt_v04_medium_soil", "scale": "medium", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "dirt_v05_dry_variation", "scale": "medium", "homogeneity": "low", "contrast": "high", "variation": "moderate"},
            {"id": "dirt_v06_dark_earth", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "dirt_v07_large_patches", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong"},
            {"id": "dirt_v08_illustrative", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong", "illustrative": True},
        ],
    },
    "water": {
        "material_class": "surface",
        "renderer": "river_material",
        "target": {"min_px": 3, "preferred_px": 5, "max_px": 10},
        "template": "top-down continuous water material exemplar, calm low-amplitude ripples, subtle local variation, reusable river surface source, no shoreline, no bank, no rocks, no bridge, no waterfall, no foam landmark, no strong directional scene",
        "candidates": [
            {"id": "water_v01_calm_uniform", "scale": "fine", "homogeneity": "high", "contrast": "low", "variation": "subtle"},
            {"id": "water_v02_calm_blue", "scale": "small", "homogeneity": "high", "contrast": "low", "variation": "subtle"},
            {"id": "water_v03_soft_ripples", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "water_v04_medium_ripples", "scale": "medium", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "water_v05_bright_reflection", "scale": "medium", "homogeneity": "low", "contrast": "high", "variation": "strong"},
            {"id": "water_v06_dark_depth", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "water_v07_directional_current", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong", "directionality": "horizontal"},
            {"id": "water_v08_scenic_reflection", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong", "illustrative": True},
        ],
    },
    "stone": {
        "material_class": "surface",
        "renderer": "surface_structured",
        "target": {"min_px": 4, "preferred_px": 7, "max_px": 14},
        "template": "top-down continuous stone paving material field, repeated but irregular flat stone texture, reusable tileset source, no single rock object, no wall, no path layout, no perspective",
        "candidates": [
            {"id": "stone_v01_fine_grain", "scale": "fine", "homogeneity": "high", "contrast": "low", "variation": "subtle"},
            {"id": "stone_v02_small_blocks", "scale": "small", "homogeneity": "high", "contrast": "medium", "variation": "subtle"},
            {"id": "stone_v03_medium_slabs", "scale": "medium", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "stone_v04_irregular_slabs", "scale": "medium", "homogeneity": "medium", "contrast": "high", "variation": "moderate"},
            {"id": "stone_v05_light_masonry", "scale": "small", "homogeneity": "medium", "contrast": "medium", "variation": "moderate"},
            {"id": "stone_v06_dark_masonry", "scale": "small", "homogeneity": "medium", "contrast": "high", "variation": "moderate"},
            {"id": "stone_v07_large_cracks", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong"},
            {"id": "stone_v08_illustrative", "scale": "large", "homogeneity": "low", "contrast": "high", "variation": "strong", "illustrative": True},
        ],
    },
}


def prompt_matrix(materials: tuple[str, ...] = ("grass", "dirt", "water", "stone")) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for material in materials:
        family = MATERIALS[material]
        for candidate in family["candidates"]:
            rows.append({
                "id": candidate["id"],
                "material_id": material,
                "material_class": family["material_class"],
                "feature_scale": candidate["scale"],
                "homogeneity": candidate["homogeneity"],
                "contrast": candidate["contrast"],
                "local_variation": candidate["variation"],
                "directionality": candidate.get("directionality", "none"),
                "illustrative": bool(candidate.get("illustrative", False)),
                "target_pixel_spec": dict(family["target"]),
            })
    return rows


def render_prompt(material_id: str, candidate: dict[str, Any]) -> str:
    family = MATERIALS[material_id]
    avoid = "Avoid all perspective, focal composition, center subject, isolated object, landmark, cast shadow, border, text, and scene layout."
    scale = candidate.get("scale", candidate.get("feature_scale", "medium"))
    homogeneity = candidate.get("homogeneity", "medium")
    contrast = candidate.get("contrast", "medium")
    variation = candidate.get("variation", candidate.get("local_variation", "subtle"))
    return (
        f"{COMMON}. {family['template']}. "
        f"Feature scale: {scale}; homogeneity: {homogeneity}; "
        f"contrast: {contrast}; local variation: {variation}; "
        f"directionality: {candidate.get('directionality', 'none')}. {avoid}"
    )


def write_prompt_set(root: Path, materials: tuple[str, ...] = ("grass", "dirt", "water", "stone")) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    matrix = prompt_matrix(materials)
    (root / "prompt_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in matrix:
        (root / f"{row['id']}.txt").write_text(render_prompt(row["material_id"], row) + "\n", encoding="utf-8")
    return matrix


def best_prompt_template(material_id: str) -> str:
    family = MATERIALS[material_id]
    return (
        f"Use case: reusable SRPG {material_id} Material Exemplar\n"
        "Asset type: high-resolution top-down orthographic continuous material field\n"
        f"Primary request: {family['template']}\n"
        "Composition: no focal point, no composition center, stationary reusable field\n"
        "Variation: subtle local variation, stable texture density, controlled medium frequency\n"
        "Constraints: texture-synthesis-friendly, no perspective, no external cast shadow\n"
        "Avoid: hero subject, landmark, scenic illustration, path or embedded transition, large macro bands\n"
    )
