"""Strict, structural acceptance checks for compiled sheet tiles.

The checks in this module are deliberately narrower than an aesthetic score.
They validate only declared surface/network contracts, image integrity, and
the connector geometry visible in the final PNG.  Unsupported semantic roles
remain unverified and therefore cannot be accepted automatically.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
import math
from typing import Any

import numpy as np
from PIL import Image

from pixel_tile_compiler.generation.spec import EdgeContract, NetworkContract, TileSpec


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


def _foreground_mask(image: Image.Image) -> tuple[np.ndarray, float, bool]:
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
    return mask, round(threshold, 6), bool(mask.any())


def _edge_line(mask: np.ndarray, direction: str) -> np.ndarray:
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
    return line


def _connector_window(length: int, width_ratio: float) -> tuple[int, int]:
    span = max(1, min(length, int(math.ceil(length * width_ratio))))
    start = max(0, (length - span) // 2)
    return start, start + span


def _edge_coordinates(direction: str, start: int, end: int) -> np.ndarray:
    positions = np.arange(start, end, dtype=np.int32)
    if direction == "N":
        return np.column_stack((np.zeros_like(positions), positions))
    if direction == "E":
        return np.column_stack((positions, np.full_like(positions, -1)))
    if direction == "S":
        return np.column_stack((np.full_like(positions, -1), positions))
    if direction == "W":
        return np.column_stack((positions, np.zeros_like(positions)))
    raise ValueError(f"unsupported connector direction: {direction}")


def _resolve_edge_coordinates(mask: np.ndarray, direction: str, start: int, end: int) -> np.ndarray:
    coordinates = _edge_coordinates(direction, start, end)
    if direction == "E":
        coordinates[:, 1] = mask.shape[1] - 1
    elif direction == "S":
        coordinates[:, 0] = mask.shape[0] - 1
    return coordinates


def _connected_components(mask: np.ndarray) -> tuple[np.ndarray, list[int]]:
    labels = np.full(mask.shape, -1, dtype=np.int32)
    sizes: list[int] = []
    height, width = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if labels[y, x] != -1:
            continue
        label = len(sizes)
        queue = deque([(int(y), int(x))])
        labels[y, x] = label
        size = 0
        while queue:
            current_y, current_x = queue.popleft()
            size += 1
            for next_y, next_x in (
                (current_y - 1, current_x),
                (current_y + 1, current_x),
                (current_y, current_x - 1),
                (current_y, current_x + 1),
            ):
                if 0 <= next_y < height and 0 <= next_x < width and mask[next_y, next_x] and labels[next_y, next_x] == -1:
                    labels[next_y, next_x] = label
                    queue.append((next_y, next_x))
        sizes.append(size)
    return labels, sizes


def _uniform_background_evidence(image: Image.Image) -> dict[str, Any]:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    reference = rgba[0, 0].tolist()
    is_uniform = bool(np.all(rgba == rgba[0, 0]))
    return {
        "rule": "uniform_background",
        "status": "accepted" if is_uniform else "unverified",
        "uniform_rgba": is_uniform,
        "reference_rgba": [int(value) for value in reference],
    }


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
    *,
    empty_tile_rule: str | None = None,
) -> dict[str, Any]:
    mask, threshold, observable = _foreground_mask(image)
    empty_evidence: dict[str, Any] = {
        "rule": empty_tile_rule,
        "status": "not_applicable",
    }
    empty_verified = False
    if not observable and empty_tile_rule == "uniform_background":
        empty_evidence = _uniform_background_evidence(image)
        empty_verified = empty_evidence["status"] == "accepted"
    checks: dict[str, Any] = {}
    invalid: list[str] = []
    for direction, expected_id in expected_ids.items():
        line = _edge_line(mask, direction)
        start, end = _connector_window(len(line), width_ratio)
        inside = line[start:end] if expected_id is not None else np.zeros(0, dtype=bool)
        outside = np.concatenate((line[:start], line[end:])) if expected_id is not None else line
        coverage = float(inside.mean()) if inside.size else 0.0
        leakage = float(outside.mean()) if outside.size else 0.0
        side_status = "accepted"
        if not observable and not empty_verified:
            side_status = "unverified"
        if expected_id is not None and observable and coverage < 1.0:
            side_status = "rejected"
            invalid.append(f"{direction}: connector coverage {coverage:.6f} < 1.0")
        if expected_id is not None and observable and leakage > 0.0:
            side_status = "rejected"
            invalid.append(f"{direction}: connector leakage {leakage:.6f} > 0.0")
        if expected_id is None and observable and leakage > 0.0:
            side_status = "rejected"
            invalid.append(f"{direction}: non-connector leakage {leakage:.6f} > 0.0")
        checks[direction] = {
            "expected_id": expected_id,
            "observed_id": expected_id if side_status == "accepted" else None,
            "window": [start, end],
            "window_width_px": end - start,
            "coverage": round(coverage, 6),
            "leakage": round(leakage, 6),
            "status": side_status,
        }
    corners = {
        "NW": bool(mask[0, 0]),
        "NE": bool(mask[0, -1]),
        "SW": bool(mask[-1, 0]),
        "SE": bool(mask[-1, -1]),
    }
    if observable and any(corners.values()):
        invalid.append("foreground is present at a forbidden corner")

    connectivity: dict[str, Any] = {
        "status": "accepted" if empty_verified else "unverified" if not observable else "accepted",
        "connectivity": "4-neighbor",
        "component_count": 0,
        "declared_component_ids": [],
        "issues": [],
    }
    if observable:
        labels, component_sizes = _connected_components(mask)
        declared_components: set[int] = set()
        connectivity_issues: list[str] = []
        for direction, expected_id in expected_ids.items():
            if expected_id is None:
                continue
            line = _edge_line(mask, direction)
            start, end = _connector_window(len(line), width_ratio)
            coordinates = _resolve_edge_coordinates(mask, direction, start, end)
            edge_labels = {
                int(labels[y, x])
                for y, x in coordinates
                if mask[y, x]
            }
            declared_components.update(edge_labels)
            if len(edge_labels) > 1:
                connectivity_issues.append(f"{direction}: connector window has multiple components")
            for component_id in edge_labels:
                interior = labels[1:-1, 1:-1] == component_id if min(mask.shape) > 2 else np.zeros((0, 0), dtype=bool)
                if not interior.any():
                    connectivity_issues.append(f"{direction}: connector does not reach an interior pixel")
        if len(declared_components) > 1:
            connectivity_issues.append("declared connectors do not share one 4-neighbor component")
        for component_id, component_size in enumerate(component_sizes):
            if component_id not in declared_components:
                connectivity_issues.append(f"component {component_id} with {component_size}px is not attached to a declared connector")
        connectivity = {
            "status": "rejected" if connectivity_issues else "accepted",
            "connectivity": "4-neighbor",
            "component_count": len(component_sizes),
            "declared_component_ids": sorted(declared_components),
            "issues": connectivity_issues,
        }
        invalid.extend(connectivity_issues)

    if not observable and not empty_verified:
        invalid.append("foreground mask is unobservable from image colors")
    status = "accepted" if empty_verified else "unverified" if not observable else "rejected" if invalid else "accepted"
    return {
        "status": status,
        "threshold": threshold,
        "foreground_observable": observable,
        "empty_evidence": empty_evidence,
        "edges": checks,
        "corners": corners,
        "connectivity": connectivity,
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


def _shared_edge_line(image: Image.Image, direction: str) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    if direction == "E":
        return rgba[:, -1, :]
    if direction == "W":
        return rgba[:, 0, :]
    if direction == "S":
        return rgba[-1, :, :]
    if direction == "N":
        return rgba[0, :, :]
    raise ValueError(f"unsupported shared edge direction: {direction}")


def _validate_shared_edge_contract(
    tile_specs: Sequence[TileSpec],
    final_images: Mapping[str, Image.Image],
    contract: EdgeContract | None,
) -> dict[str, Any]:
    surface_specs = [tile for tile in tile_specs if tile.semantic == "surface"]
    if not surface_specs:
        return {
            "status": "not_applicable",
            "reason": "no_surface_tiles",
            "directions": ["E/W", "S/N"],
            "checked_pair_count": 0,
            "issues": [],
        }
    if contract is None:
        return {
            "status": "unverified",
            "reason": "shared_edge_contract_missing",
            "directions": ["E/W", "S/N"],
            "checked_pair_count": 0,
            "issues": ["surface tiles require an explicit shared edge contract"],
        }
    if contract.role != "surface":
        return {
            "status": "rejected",
            "reason": "surface_contract_role_mismatch",
            "directions": ["E/W", "S/N"],
            "checked_pair_count": 0,
            "contract_role": contract.role,
            "issues": [f"surface tiles require a surface contract, got role={contract.role}"],
        }
    if contract.validation_mode != "exact_rgb":
        return {
            "status": "unverified",
            "reason": "machine_readable_edge_rule_missing",
            "directions": ["E/W", "S/N"],
            "checked_pair_count": 0,
            "issues": ["surface contract does not declare validation_mode=exact_rgb"],
            "safe_zone_ratio": contract.safe_zone_ratio,
        }
    materials = {tile.material for tile in surface_specs}
    if len(materials) != 1 or None in materials:
        return {
            "status": "unverified",
            "reason": "surface_material_pairs_not_declared",
            "directions": ["E/W", "S/N"],
            "checked_pair_count": 0,
            "materials": sorted(material for material in materials if material is not None),
            "issues": ["exact edge comparison needs one declared surface material or an explicit material-pair contract"],
        }

    mismatch_reports: list[dict[str, Any]] = []
    allowed_pairs: list[str] = []
    issues: list[str] = []
    directions = (("E/W", "E", "W"), ("S/N", "S", "N"))
    for direction_name, first_edge, second_edge in directions:
        for first in surface_specs:
            for second in surface_specs:
                pair_id = f"{first.id}->{second.id}:{direction_name}"
                allowed_pairs.append(pair_id)
                first_image = final_images.get(first.id)
                second_image = final_images.get(second.id)
                if first_image is None or second_image is None:
                    issue = f"{pair_id}: missing final image"
                    issues.append(issue)
                    mismatch_reports.append({"pair": pair_id, "status": "rejected", "issues": [issue]})
                    continue
                first_line = _shared_edge_line(first_image, first_edge)
                second_line = _shared_edge_line(second_image, second_edge)
                if first_line.shape != second_line.shape:
                    issue = f"{pair_id}: shared edge dimensions differ"
                    issues.append(issue)
                    mismatch_reports.append({"pair": pair_id, "status": "rejected", "issues": [issue]})
                    continue
                equal_pixels = np.all(first_line == second_line, axis=1)
                mismatch_count = int(np.count_nonzero(~equal_pixels))
                pair_status = "accepted" if mismatch_count == 0 else "rejected"
                pair_issue = [] if pair_status == "accepted" else [f"{pair_id}: {mismatch_count} edge pixels differ"]
                issues.extend(pair_issue)
                if pair_status != "accepted":
                    mismatch_reports.append(
                        {
                            "pair": pair_id,
                            "first_tile": first.id,
                            "second_tile": second.id,
                            "direction": direction_name,
                            "first_edge": first_edge,
                            "second_edge": second_edge,
                            "pixels_checked": int(equal_pixels.size),
                            "mismatched_pixels": mismatch_count,
                            "corners_included": True,
                            "status": pair_status,
                            "issues": pair_issue,
                        }
                    )
    return {
        "status": "rejected" if issues else "accepted",
        "reason": "exact_rgb_shared_edge_contract_checked",
        "directions": ["E/W", "S/N"],
        "allowed_pairs": allowed_pairs,
        "checked_pair_count": len(allowed_pairs),
        "mismatch_pair_count": len(mismatch_reports),
        "safe_zone_ratio": contract.safe_zone_ratio,
        "boundary_pixels_checked": 1,
        "corners_included": True,
        "mismatches": mismatch_reports,
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
        empty_tile_rule = (
            network_contract.empty_tile_rule
            if not tile_spec.connectors and "EMPTY" in network_contract.allowed_topologies
            else None
        )
        input_mask = _validate_mask(
            source_image,
            expected_ids,
            width_ratio,
            empty_tile_rule=empty_tile_rule,
        )
        final_mask = _validate_mask(
            final_image,
            expected_ids,
            width_ratio,
            empty_tile_rule=empty_tile_rule,
        )
        connection_ids = {
            "status": final_mask["status"],
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
    shared_edge_contract: EdgeContract | None = None,
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
    shared_edges = _validate_shared_edge_contract(tile_specs, final_images, shared_edge_contract)
    if shared_edges["status"] not in {"accepted", "not_applicable"}:
        issues.append("shared_edge_contract")
    return {
        "status": "rejected" if issues else "accepted",
        "checked_tile_count": len(reports),
        "issues": issues,
        "tiles": reports,
        "shared_edge_validation": shared_edges,
        "adoption_status": "provisional_not_approved" if not issues else "rejected_not_adopted",
    }
