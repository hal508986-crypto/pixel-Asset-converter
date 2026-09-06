"""Compile the canonical TilesetSpec into a generation request JSON."""

from __future__ import annotations

from .spec import GenerationRequest, TilesetSpec


class GenerationRequestCompiler:
    """Translate semantic asset intent into a stable generator-facing contract."""

    def compile(self, spec: TilesetSpec) -> GenerationRequest:
        material_rules = {
            material_id: material.model_dump(mode="json")
            for material_id, material in spec.materials.items()
        }
        network_rules = {
            network_id: network.model_dump(mode="json")
            for network_id, network in spec.network_contracts.items()
        }
        tile_manifest = [
            {
                "id": tile.id,
                "cell": {"column": tile.cell[0], "row": tile.cell[1]},
                "semantic": tile.semantic,
                "material": tile.material,
                "network": tile.network,
                "connectors": list(tile.connectors),
                "description": tile.description,
                "variation_strength": tile.variation_strength,
            }
            for tile in sorted(spec.tiles, key=lambda item: (item.cell[1], item.cell[0]))
        ]
        strict_negative_constraints = tuple(
            dict.fromkeys(
                (
                    *spec.generation.negative_constraints,
                    "do not change the logical cell count",
                    "do not merge adjacent logical cells",
                    "do not put text or labels into the image",
                )
            )
        )
        network_summary = ", ".join(
            f"{network_id}: {', '.join(contract.allowed_topologies)}"
            for network_id, contract in spec.network_contracts.items()
        ) or "none"
        final_instruction = (
            f"Generate exactly a {spec.grid_columns}x{spec.grid_rows} logical tileset sheet for {spec.tileset_id}. "
            f"Every logical cell is an independent {spec.tile_size_px}x{spec.tile_size_px} game tile after splitting. "
            "Keep the grid invisible, preserve the shared visual language, and obey the tile manifest exactly. "
            f"Network topology contracts: {network_summary}."
        )
        return GenerationRequest(
            task="generate_game_tileset_sheet",
            intent=(
                "Create one coherent generated sheet that can be mechanically split into reusable game assets; "
                "the compiler will validate and package the result."
            ),
            output_contract={
                "format": "PNG",
                "logical_grid": True,
                "grid_columns": spec.grid_columns,
                "grid_rows": spec.grid_rows,
                "tile_size_px": spec.tile_size_px,
                "requested_width": spec.generation.requested_width,
                "requested_height": spec.generation.requested_height,
            },
            art_direction=spec.art_direction.model_dump(mode="json"),
            palette_direction=spec.art_direction.palette_direction,
            shared_surface_contract=(spec.shared_edge_contract.model_dump(mode="json") if spec.shared_edge_contract else None),
            shared_edge_contract=(spec.shared_edge_contract.model_dump(mode="json") if spec.shared_edge_contract else None),
            feature_scale_contract={
                material_id: material.feature_scale for material_id, material in spec.materials.items()
            },
            material_contracts=material_rules,
            network_contracts=network_rules,
            tile_manifest=tile_manifest,
            global_consistency_rules=(
                "one art direction across the complete sheet",
                "same material vocabulary across all related tiles",
                "no visible grid, gutter, border, or cell labels",
            ),
            strict_negative_constraints=strict_negative_constraints,
            split_contract={
                "strategy": "equal_grid",
                "columns": spec.grid_columns,
                "rows": spec.grid_rows,
                "crop_policy": spec.postprocess.crop_policy,
                "normalization": spec.postprocess.normalize_mode,
            },
            final_instruction=final_instruction,
        )
