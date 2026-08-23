"""Intentional material source prompt matrices and prompt persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_prompt_matrix(material: str = "grass") -> tuple[dict[str, Any], ...]:
    if material == "forest_canopy":
        return default_forest_prompt_matrix()
    return (
        {"id": "grass_src_01_uniform_dark", "homogeneity": "very homogeneous", "brightness_variation": "low", "tufts": "almost no obvious tufts", "composition": "no focal point", "contrast": "low"},
        {"id": "grass_src_02_uniform_mid", "homogeneity": "very homogeneous", "brightness_variation": "medium", "tufts": "almost no obvious tufts", "composition": "no focal point", "contrast": "low"},
        {"id": "grass_src_03_uniform_light", "homogeneity": "very homogeneous", "brightness_variation": "low", "tufts": "small sparse tufts", "composition": "no focal point", "contrast": "low"},
        {"id": "grass_src_04_sparse_tufts", "homogeneity": "moderately varied", "brightness_variation": "low", "tufts": "small sparse tufts", "composition": "slight natural variation", "contrast": "low"},
        {"id": "grass_src_05_patchy_mid", "homogeneity": "moderately varied", "brightness_variation": "medium", "tufts": "small sparse tufts", "composition": "slight natural variation", "contrast": "medium"},
        {"id": "grass_src_06_bright_variation", "homogeneity": "moderately varied", "brightness_variation": "high", "tufts": "small sparse tufts", "composition": "slight natural variation", "contrast": "medium"},
        {"id": "grass_src_07_dense_tufts", "homogeneity": "strongly varied", "brightness_variation": "medium", "tufts": "medium visible tufts", "composition": "slight natural variation", "contrast": "medium"},
        {"id": "grass_src_08_illustrative", "homogeneity": "strongly varied", "brightness_variation": "high", "tufts": "medium visible tufts", "composition": "more illustrative and image-like", "contrast": "high"},
    )


def default_forest_prompt_matrix() -> tuple[dict[str, Any], ...]:
    return (
        {"id": "forest_src_01_uniform_sparse", "canopy_density": "sparse", "cluster_scale": "small", "homogeneity": "highly homogeneous", "brightness_variation": "low", "illustrative": False, "composition": "no focal point", "contrast": "low"},
        {"id": "forest_src_02_uniform_medium", "canopy_density": "medium", "cluster_scale": "small", "homogeneity": "highly homogeneous", "brightness_variation": "low", "illustrative": False, "composition": "no focal point", "contrast": "low"},
        {"id": "forest_src_03_uniform_dense", "canopy_density": "dense", "cluster_scale": "medium", "homogeneity": "highly homogeneous", "brightness_variation": "low", "illustrative": False, "composition": "no focal point", "contrast": "low"},
        {"id": "forest_src_04_small_clusters", "canopy_density": "medium", "cluster_scale": "small", "homogeneity": "slightly varied", "brightness_variation": "medium", "illustrative": False, "composition": "subtle natural variation", "contrast": "medium"},
        {"id": "forest_src_05_medium_clusters", "canopy_density": "medium", "cluster_scale": "medium", "homogeneity": "slightly varied", "brightness_variation": "medium", "illustrative": False, "composition": "subtle natural variation", "contrast": "medium"},
        {"id": "forest_src_06_large_masses", "canopy_density": "dense", "cluster_scale": "large", "homogeneity": "strongly varied", "brightness_variation": "medium", "illustrative": False, "composition": "subtle natural variation", "contrast": "high"},
        {"id": "forest_src_07_dark_variation", "canopy_density": "medium", "cluster_scale": "medium", "homogeneity": "strongly varied", "brightness_variation": "high", "illustrative": False, "composition": "subtle natural variation", "contrast": "high"},
        {"id": "forest_src_08_illustrative", "canopy_density": "dense", "cluster_scale": "large", "homogeneity": "strongly varied", "brightness_variation": "high", "illustrative": True, "composition": "more illustrative and scenic", "contrast": "high"},
    )


def render_source_prompt(spec: dict[str, Any], material: str = "grass") -> str:
    if material == "forest_canopy":
        return _render_forest_prompt(spec)
    return "\n".join(
        (
            "Use case: material exemplar for a reusable SRPG grass tileset",
            "Asset type: high-resolution raster texture source",
            "Primary request: a 1024x1024 orthographic top-down grass material image",
            f"Homogeneity: {spec['homogeneity']}",
            f"Brightness variation: {spec['brightness_variation']}",
            f"Tufts: {spec['tufts']}",
            f"Composition: {spec['composition']}",
            f"Contrast: {spec['contrast']}",
            "Materials/textures: consistent grass texture scale, material-only surface, subtle local variation",
            "Constraints: seamless-friendly, suitable for texture synthesis and tileset source generation, uniform density, no center composition",
            "Avoid: perspective, focal point, large landmark, path, rocks, trees, flowers, buildings, objects, cast shadows, visible subject",
        )
    )


def _render_forest_prompt(spec: dict[str, Any]) -> str:
    return "\n".join(
        (
            "Use case: material exemplar for a reusable SRPG forest canopy tileset",
            "Asset type: high-resolution raster continuous canopy material source",
            "Primary request: a 1024x1024 orthographic top-down continuous forest canopy material image",
            f"Canopy density: {spec.get('canopy_density', 'medium')}",
            f"Canopy cluster scale: {spec.get('cluster_scale', 'medium')} canopy blobs and connected masses",
            f"Homogeneity: {spec.get('homogeneity', 'slightly varied')}",
            f"Brightness variation: {spec.get('brightness_variation', 'medium')}",
            f"Composition: {spec.get('composition', 'no focal point')}",
            f"Contrast: {spec.get('contrast', 'medium')}",
            f"Illustrative: {bool(spec.get('illustrative', False))}",
            "Materials/textures: continuous connected tree-canopy surface, reusable forest mass, consistent texture scale, subtle local variation",
            "Constraints: reusable tileset source friendly, suitable for texture synthesis and forest tileset source generation, no composition center",
            "Avoid: perspective, no focal point, no hero tree, hero tree, isolated single tree, no large landmark, no path, no clearing, forest floor, rocks, buildings, objects, external cast shadows",
        )
    )


def write_prompt_set(
    prompt_root: Path,
    matrix_path: Path | None = None,
    material: str = "grass",
    matrix: tuple[dict[str, Any], ...] | None = None,
) -> Path:
    prompt_root = Path(prompt_root)
    prompt_root.mkdir(parents=True, exist_ok=True)
    matrix = matrix or default_prompt_matrix(material=material)
    target = matrix_path or prompt_root.parent / "source_prompt_matrix.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(list(matrix), ensure_ascii=False, indent=2), encoding="utf-8")
    for spec in matrix:
        (prompt_root / f"{spec['id']}.txt").write_text(render_source_prompt(spec, material=material) + "\n", encoding="utf-8")
    return target
