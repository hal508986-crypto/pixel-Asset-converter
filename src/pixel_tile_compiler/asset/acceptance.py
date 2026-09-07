"""Strict, structural acceptance checks for compiled sheet tiles.

The checks in this module are deliberately narrower than an aesthetic score.
They validate only declared surface/network contracts, image integrity, and
the connector geometry visible in the final PNG.  Unsupported semantic roles
remain unverified and therefore cannot be accepted automatically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from PIL import Image

from pixel_tile_compiler.generation.spec import NetworkContract, TileSpec


SUPPORTED_SEMANTICS = frozenset({"surface", "network"})
_DIRECTIONS = ("N", "E", "S", "W")


def _corner_samples(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    height, width = rgba.shape[:2]
    patch = max(1, min(8, height // 8, width // 8))
    corners = (
        rgba[:patch, :patch, :3],
        rgba[:patch, width - patch :, :3],
        rgba[height - patch :, :patch, :3],
        rgba[height - patch :, width - patch :, :3],
    )
    return np.concatenate([corner.reshape(-1, 3) for corner in corners], axis=0).astype(np.float32)


def _foreground_mask(image: Image.Image) -> tuple[np.ndarray, float]:
    """Derive a deterministic material mask from corner background samples.

    This is a structural probe, not a semantic segmentation claim.  The
    adaptive threshold is recorded so a failed gate can be investigated
    without pretending that colour alone is universally meaningful.
    """

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    samples = _corner_samples(image)
    background = np.median(samples, axis=0)
    distances = np.linalg.norm(samples - background, axis=1)
    threshold = max(1.0, float(np.percentile(distances, 95)) * 2.0)
    rgb_distance = np.linalg.norm(rgba[:, :, :3].astype(np.float32) - background, axis=2)
    mask = (rgba[:, :, 3] >= 128) & (rgb_distance > threshold)
    return mask, round(threshold, 6)


def _edge_body(mask: np.ndarray, direction: str) -> np.ndarray:
    if direction == "N":
        line = mask[0, :]
    elif direction == "E":
        line = mask[:, -1]
    elif direction == "S":
        line = mask[-1, :]
    elif direction == "W":
        line = mask[:, 0]
    else:
        raise ValueError(f"unsupported connector direction: {direction}")
    return line[1:-1] if len(line) > 2 else line


def _connector_window(length: int, width_ratio: float) -> tuple[int, int]:
    full_length = length + 2
    span = max(1, int(round(full_length * width_ratio)))
    full_start = max(0, (full_length - span) // 2)
    start = max(0, full_start - 1)
    return start, min(length, full_start + span - 1)


def _expected_connection_ids(tile_spec: TileSpec) -> dict[str, str | None]:
    connector_set = set(tile_spec.connectors)
    return {
        direction: tile_spec.network if direction in connector_set and tile_spec.network else None
        for direction in _DIRECTIONS
    }


def _validate_mask(
    image: Image.Image,
    expected_ids: Mapping[str, str | None],
    width_ratio: float,
) -> dict[str, Any]:
    mask, threshold = _foreground_mask(image)
    checks: dict[str, Any] = {}
    invalid: list[str] = []
    for direction, expected_id in expected_ids.items():
        line = _edge_body(mask, direction)
        start, end = _connector_window(len(line), width_ratio)
        inside = line[start:end]
        outside = np.concatenate((line[:start], line[end:]))
        coverage = float(inside.mean()) if inside.size else 0.0
        leakage = float(outside.mean()) if outside.size else 0.0
        side_status = "accepted"
        if expected_id is not None and coverage < 1.0:
            side_status = "rejected"
            invalid.append(f"{direction}: connector coverage {coverage:.6f} < 1.0")
        if expected_id is None and leakage > 0.0:
            side_status = "rejected"
            invalid.append(f"{direction}: non-connector leakage {leakage:.6f} > 0.0")
        checks[direction] = {
            "expected_id": expected_id,
            "observed_id": expected_id if coverage >= 1.0 else None,
            "window": [start, end],
            "coverage": round(coverage, 6),
            "leakage": round(leakage, 6),
            "status": side_status,
        }
    return {
        "status": "rejected" if invalid else "accepted",
        "threshold": threshold,
        "edges": checks,
        "issues": invalid,
    }


def _validate_final_png(image: Image.Image, palette_budget: int, tile_size: int = 64) -> dict[str, Any]:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    visible = rgba[:, :, 3] != 0
    colors = {tuple(int(value) for value in pixel) for pixel in rgba[:, :, :3][visible]}
    alpha_values = sorted(int(value) for value in np.unique(rgba[:, :, 3]))
    issues: list[str] = []
    if image.size != (tile_size, tile_size):
        issues.append(f"dimensions must be {tile_size}x{tile_size}, got {image.size[0]}x{image.size[1]}")
    if image.mode != "RGBA":
        issues.append(f"PNG mode must be RGBA, got {image.mode}")
    if len(colors) > palette_budget:
        issues.append(f"visible palette has {len(colors)} colors, budget is {palette_budget}")
    if any(value not in {0, 255} for value in alpha_values):
        issues.append(f"alpha must be binary 0/255, got {alpha_values}")
    return {
        "status": "rejected" if issues else "accepted",
        "size": [int(image.width), int(image.height)],
        "mode": image.mode,
        "palette_count": len(colors),
        "alpha_values": alpha_values,
        "issues": issues,
    }


def validate_tile_contract(
    tile_spec: TileSpec,
    source_image: Image.Image,
    final_image: Image.Image,
    *,
    palette_budget: int,
    network_contract: NetworkContract | None,
) -> dict[str, Any]:
    """Validate one source/final pair against its declared semantic role."""

    if tile_spec.semantic not in SUPPORTED_SEMANTICS:
        return {
            "status": "rejected",
            "status_reason": "unverified_semantic_role",
            "semantic": tile_spec.semantic,
            "connection_id_validation": {"status": "unverified", "issues": [tile_spec.semantic]},
            "input_mask": {"status": "unverified", "issues": [tile_spec.semantic]},
            "final_png": {"status": "unverified", "issues": [tile_spec.semantic]},
        }

    final_png = _validate_final_png(final_image, palette_budget)
    expected_ids = _expected_connection_ids(tile_spec)
    if tile_spec.semantic == "network":
        if network_contract is None:
            return {
                "status": "rejected",
                "status_reason": "missing_network_contract",
                "semantic": tile_spec.semantic,
                "connection_id_validation": {"status": "unverified", "issues": ["network contract is missing"]},
                "input_mask": {"status": "unverified", "issues": ["network contract is missing"]},
                "final_png": final_png,
            }
        width_ratio = network_contract.connector_width_ratio
        input_mask = _validate_mask(source_image, expected_ids, width_ratio)
        final_mask = _validate_mask(final_image, expected_ids, width_ratio)
        connection_ids = {
            "status": "accepted" if final_mask["status"] == "accepted" else "rejected",
            "expected": expected_ids,
            "observed": {
                direction: final_mask["edges"][direction]["observed_id"] for direction in _DIRECTIONS
            },
            "issues": final_mask["issues"],
        }
        statuses = (final_png["status"], input_mask["status"], final_mask["status"], connection_ids["status"])
        return {
            "status": "accepted" if all(status == "accepted" for status in statuses) else "rejected",
            "status_reason": "contract_checked",
            "semantic": tile_spec.semantic,
            "connection_id_validation": connection_ids,
            "input_mask": input_mask,
            "final_png": {**final_png, "connector_validation": final_mask},
        }

    return {
        "status": final_png["status"],
        "status_reason": "surface_png_checked",
        "semantic": tile_spec.semantic,
        "connection_id_validation": {"status": "not_applicable", "expected": expected_ids},
        "input_mask": {"status": "not_applicable", "issues": []},
        "final_png": final_png,
    }


def validate_masked_transition_tile(
    source_image: Image.Image,
    final_image: Image.Image,
    source_mask: np.ndarray,
    orientation: str,
    *,
    palette_budget: int,
    tile_size: int = 64,
) -> dict[str, Any]:
    """Validate a transition while keeping its explicit structural mask.

    The mask is the semantic source of truth.  RGB is used only to check that
    the final PNG still separates the two declared source materials; it is
    never used to invent a transition direction or material identity.
    """

    if orientation not in {"NS", "EW"}:
        return {
            "status": "rejected",
            "status_reason": "unsupported_transition_orientation",
            "input_mask": {"status": "unverified", "issues": [orientation]},
            "final_png": {"status": "unverified", "issues": [orientation]},
        }
    source_mask = np.asarray(source_mask, dtype=bool)
    source_rgba = np.asarray(source_image.convert("RGBA"), dtype=np.uint8)
    if source_mask.shape != source_rgba.shape[:2]:
        return {
            "status": "rejected",
            "status_reason": "input_mask_dimensions_mismatch",
            "input_mask": {
                "status": "rejected",
                "issues": [f"mask {source_mask.shape} does not match image {source_rgba.shape[:2]}"],
            },
            "final_png": {"status": "unverified", "issues": []},
        }
    if not source_mask.any() or source_mask.all():
        return {
            "status": "rejected",
            "status_reason": "input_mask_has_one_region",
            "input_mask": {"status": "rejected", "issues": ["transition mask must contain both regions"]},
            "final_png": {"status": "unverified", "issues": []},
        }

    axis = 0 if orientation == "NS" else 1
    source_transition_counts = np.count_nonzero(np.diff(source_mask.astype(np.int8), axis=axis), axis=axis)
    input_issues: list[str] = []
    if int(source_transition_counts.max()) > 1:
        input_issues.append("input transition mask has more than one boundary per scanline")
    input_report = {
        "status": "rejected" if input_issues else "accepted",
        "orientation": orientation,
        "dimensions": [int(source_mask.shape[1]), int(source_mask.shape[0])],
        "foreground_ratio": round(float(source_mask.mean()), 6),
        "max_scanline_transitions": int(source_transition_counts.max()),
        "issues": input_issues,
    }
    final_png = _validate_final_png(final_image, palette_budget, tile_size=tile_size)
    if final_image.size != (tile_size, tile_size):
        return {
            "status": "rejected",
            "status_reason": "final_png_dimensions_invalid",
            "input_mask": input_report,
            "final_png": {
                **final_png,
                "boundary_validation": {
                    "status": "unverified",
                    "issues": ["boundary validation requires the declared final tile dimensions"],
                },
            },
        }

    first_pixels = source_rgba[:, :, :3][source_mask]
    second_pixels = source_rgba[:, :, :3][~source_mask]
    first_colour = first_pixels.astype(np.float32).mean(axis=0)
    second_colour = second_pixels.astype(np.float32).mean(axis=0)
    expected_mask = np.asarray(
        Image.fromarray((source_mask.astype(np.uint8) * 255), mode="L").resize(
            (tile_size, tile_size), Image.Resampling.NEAREST
        ),
        dtype=np.uint8,
    ) >= 128
    final_rgba = np.asarray(final_image.convert("RGBA"), dtype=np.uint8)
    final_rgb = final_rgba[:, :, :3].astype(np.float32)
    first_distance = np.linalg.norm(final_rgb - first_colour, axis=2)
    second_distance = np.linalg.norm(final_rgb - second_colour, axis=2)
    observed_mask = first_distance <= second_distance
    agreement = float(np.mean(observed_mask == expected_mask))
    expected_boundary = _transition_positions(expected_mask, axis)
    observed_boundary = _transition_positions(observed_mask, axis)
    boundary_error = max(
        (abs(observed - expected) for observed, expected in zip(observed_boundary, expected_boundary)),
        default=0,
    )
    boundary_issues: list[str] = []
    if agreement < 0.95:
        boundary_issues.append(f"material-mask agreement {agreement:.6f} < 0.95")
    if boundary_error > 1:
        boundary_issues.append(f"boundary position error {boundary_error} > 1px")
    boundary_report = {
        "status": "rejected" if boundary_issues else "accepted",
        "agreement": round(agreement, 6),
        "max_boundary_error_px": int(boundary_error),
        "agreement_min": 0.95,
        "boundary_tolerance_px": 1,
        "issues": boundary_issues,
    }
    final_report = {**final_png, "boundary_validation": boundary_report}
    statuses = (input_report["status"], final_report["status"], boundary_report["status"])
    return {
        "status": "accepted" if all(status == "accepted" for status in statuses) else "rejected",
        "status_reason": "explicit_transition_mask_checked",
        "input_mask": input_report,
        "final_png": final_report,
    }


def _transition_positions(mask: np.ndarray, axis: int) -> list[int]:
    if axis == 0:
        positions = []
        for column in range(mask.shape[1]):
            false_pixels = np.flatnonzero(~mask[:, column])
            positions.append(int(false_pixels[0]) if false_pixels.size else int(mask.shape[0]))
        return positions
    positions = []
    for row in range(mask.shape[0]):
        false_pixels = np.flatnonzero(~mask[row, :])
        positions.append(int(false_pixels[0]) if false_pixels.size else int(mask.shape[1]))
    return positions


def validate_tileset_contract(
    tile_specs: Sequence[TileSpec],
    source_images: Mapping[str, Image.Image],
    final_images: Mapping[str, Image.Image],
    network_contracts: Mapping[str, NetworkContract],
    *,
    palette_budget: int,
) -> dict[str, Any]:
    """Validate every tile without converting unverified work into approval."""

    reports: dict[str, Any] = {}
    issues: list[str] = []
    for tile_spec in tile_specs:
        if tile_spec.id not in source_images:
            issues.append(f"missing source image: {tile_spec.id}")
            continue
        if tile_spec.id not in final_images:
            issues.append(f"missing final image: {tile_spec.id}")
            continue
        report = validate_tile_contract(
            tile_spec,
            source_images[tile_spec.id],
            final_images[tile_spec.id],
            palette_budget=palette_budget,
            network_contract=network_contracts.get(tile_spec.network or ""),
        )
        reports[tile_spec.id] = report
        if report["status"] != "accepted":
            issues.append(tile_spec.id)
    return {
        "status": "rejected" if issues else "accepted",
        "checked_tile_count": len(reports),
        "issues": issues,
        "tiles": reports,
        "adoption_status": "provisional_not_approved" if not issues else "rejected_not_adopted",
    }
