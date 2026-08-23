"""Coherent semantic volume pass for the hierarchy experiment."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from pixel_tile_compiler.pixel_grammar.profiles import HierarchyMode, PixelGrammarProfile


class VolumePass:
    """Add broad lighting and semantic depth cues without pixel noise."""

    def apply(
        self,
        image: Image.Image,
        profile: PixelGrammarProfile,
        target: str,
        semantic_mask: np.ndarray | None = None,
        boundary_mask: np.ndarray | None = None,
        silhouette_mask: np.ndarray | None = None,
        light_direction: tuple[float, float] = (-1.0, -1.0),
        light_strength: float = 0.5,
        seed: int = 42,
    ) -> Image.Image:
        del seed
        if profile.hierarchy_mode is not HierarchyMode.VOLUMETRIC:
            return image.convert("RGBA").copy()
        rgba = image.convert("RGBA")
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
        height, width = rgb.shape[:2]
        yy, xx = np.mgrid[0:height, 0:width]
        dx, dy = _normalize(light_direction)
        x = (xx / max(1, width - 1)) - 0.5
        y = (yy / max(1, height - 1)) - 0.5
        directional = dx * x + dy * y
        broad_light = np.clip(0.5 + directional * 0.9, 0.0, 1.0)
        amplitude = 25.0 * light_strength * profile.depth_cue_strength
        rgb += (broad_light - 0.5)[..., None] * amplitude

        mask_source = semantic_mask if semantic_mask is not None else boundary_mask if boundary_mask is not None else silhouette_mask
        mask = _fit_mask(mask_source, (height, width))
        edge = _edge_band(mask) if mask is not None else np.zeros((height, width), dtype=bool)
        if mask is not None:
            rgb[edge] -= 20.0 * profile.contact_shadow_strength
            light_edge = edge & (directional > 0.0)
            rgb[light_edge] += 12.0 * profile.highlight_strength
        else:
            rgb += _coherent_material_term(target, x, y, profile)

        if target in {"dirt_road", "river"} and mask is not None:
            rgb += _network_term(target, x, y, mask, profile)
        elif target in {"grass_forest", "grass_road", "grass_river", "forest_river"} and boundary_mask is not None:
            rgb += _transition_term(_fit_mask(boundary_mask, (height, width)), profile)
        elif target.endswith("_object") and silhouette_mask is not None:
            rgb += _object_term(_fit_mask(silhouette_mask, (height, width)), directional, profile)
            if target == "rock_object":
                rgb += _rock_term(_fit_mask(silhouette_mask, (height, width)), yy, height, profile)
        elif target == "forest_canopy":
            rgb += _forest_term(rgb, profile)

        output = np.clip(rgb, 0, 255).astype(np.uint8)
        return Image.fromarray(np.dstack([output, np.asarray(rgba.getchannel("A"), dtype=np.uint8)]), mode="RGBA")


def _coherent_material_term(target: str, x: np.ndarray, y: np.ndarray, profile: PixelGrammarProfile) -> np.ndarray:
    phase = np.cos((x + y) * np.pi * 3.0)[..., None]
    amplitude = 6.0 * profile.medium_cluster_strength
    if target in {"grass", "forest_canopy"}:
        return phase * amplitude
    return phase * (amplitude * 0.6)


def _network_term(target: str, x: np.ndarray, y: np.ndarray, mask: np.ndarray, profile: PixelGrammarProfile) -> np.ndarray:
    band = np.cos((y if target == "river" else x) * np.pi * 5.0)[..., None]
    term = band * (8.0 * profile.highlight_strength)
    return term * mask[..., None]


def _transition_term(mask: np.ndarray, profile: PixelGrammarProfile) -> np.ndarray:
    edge = _edge_band(mask)
    term = np.zeros((*mask.shape, 1), dtype=np.float32)
    term[edge] = -10.0 * profile.contact_shadow_strength
    return np.repeat(term, 3, axis=2)


def _object_term(mask: np.ndarray, directional: np.ndarray, profile: PixelGrammarProfile) -> np.ndarray:
    term = np.zeros((*mask.shape, 1), dtype=np.float32)
    term[mask] = (directional[mask, None] * 18.0 * profile.highlight_strength)
    return np.repeat(term, 3, axis=2)


def _rock_term(mask: np.ndarray, yy: np.ndarray, height: int, profile: PixelGrammarProfile) -> np.ndarray:
    """Separate a rock top plane from its lower side with broad bands."""
    normalized_y = yy / max(1, height - 1)
    term = (0.5 - normalized_y)[..., None] * 34.0 * profile.depth_cue_strength
    return np.repeat(term * mask[..., None], 3, axis=2)


def _forest_term(rgb: np.ndarray, profile: PixelGrammarProfile) -> np.ndarray:
    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    blurred = np.asarray(
        Image.fromarray(np.clip(luma, 0, 255).astype(np.uint8), mode="L").filter(ImageFilter.GaussianBlur(radius=5.0)),
        dtype=np.float32,
    )
    cavity = (blurred - luma)[..., None] * 0.22 * profile.contact_shadow_strength
    return np.repeat(cavity, 3, axis=2)


def _normalize(direction: tuple[float, float]) -> tuple[float, float]:
    length = float(np.hypot(direction[0], direction[1])) or 1.0
    return float(direction[0] / length), float(direction[1] / length)


def _fit_mask(mask: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray | None:
    if mask is None:
        return None
    if mask.shape == shape:
        return mask.astype(bool)
    resized = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize((shape[1], shape[0]), Image.Resampling.NEAREST)
    return np.asarray(resized, dtype=np.uint8) > 0


def _edge_band(mask: np.ndarray) -> np.ndarray:
    edge = np.zeros_like(mask, dtype=bool)
    edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    edge[1:, :] |= mask[1:, :] != mask[:-1, :]
    return edge & mask
