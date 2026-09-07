"""Canonical Grass and Road TilesetSpec presets."""

from __future__ import annotations

from pixel_tile_compiler.transition_network.graph import NetworkTopology

from .spec import (
    ArtDirection,
    EdgeContract,
    GenerationSpec,
    MaterialSpec,
    NetworkContract,
    PostprocessSpec,
    TileSpec,
    TilesetSpec,
)


_ROAD_TOPOLOGIES = tuple(item for item in NetworkTopology if item is not NetworkTopology.EMPTY)


def build_grass_surface_spec(tileset_id: str = "grass_surface_v1") -> TilesetSpec:
    tiles = tuple(
        TileSpec(
            id=f"grass_surface_{row:02d}_{column:02d}",
            cell=(column, row),
            semantic="surface",
            material="grass",
            description="grass surface tile with interior-only variation and shared edge-safe base",
            variation_strength="medium" if (row + column) % 3 else "low",
        )
        for row in range(4)
        for column in range(4)
    )
    return TilesetSpec(
        tileset_id=tileset_id,
        grid_columns=4,
        grid_rows=4,
        art_direction=ArtDirection(
            visual_rules=(
                "top-down orthographic",
                "chunky coherent clusters",
                "controlled color",
                "no photographic noise",
                "invisible grid",
            ),
        ),
        materials={
            "grass": MaterialSpec(
                material_id="grass",
                feature_scale="medium",
                palette_hints=("dark green", "mid green", "yellow green highlight"),
                accepted_visual_vocabulary=("grass clusters", "subtle blades", "small tonal variation"),
                generation_rules=("same grass base across all cells", "variation stays inside cells", "shared edges remain safe"),
            )
        },
        shared_edge_contract=EdgeContract(
            edge_id="grass_shared_surface_edge",
            role="surface",
            shared_edge_rule="outer edge remains the same grass base without a visible grid line",
            validation_mode="exact_rgb",
        ),
        tiles=tiles,
        generation=GenerationSpec(
            adapter="mcp",
            requested_width=1024,
            requested_height=1024,
            negative_constraints=("text", "labels", "border", "gutter", "visible grid", "photographic noise"),
        ),
        postprocess=PostprocessSpec(crop_policy="center", normalize_mode="nearest"),
    )


def generate_network_tileset_spec(
    network: str = "road",
    tileset_id: str | None = None,
    material: str = "dirt",
) -> TilesetSpec:
    if network != "road":
        raise ValueError("the Phase 1 preset generator supports network='road'; river is deferred")
    resolved_id = tileset_id or "dirt_road_network_v1"
    tiles = tuple(
        TileSpec(
            id=f"{network}_{topology.value.lower()}",
            cell=(index % 4, index // 4),
            semantic="network",
            material=material,
            network=network,
            connectors=tuple(topology.value) if topology is not NetworkTopology.EMPTY else (),
            description=f"{network} network tile for topology {topology.value}",
            variation_strength="low",
        )
        for index, topology in enumerate((NetworkTopology.EMPTY, *_ROAD_TOPOLOGIES))
    )
    return TilesetSpec(
        tileset_id=resolved_id,
        grid_columns=4,
        grid_rows=4,
        art_direction=ArtDirection(
            visual_rules=(
                "top-down orthographic",
                "road connector centers align to exact cell edges",
                "chunky readable road shape",
                "same dirt material across all topologies",
                "invisible grid",
            ),
        ),
        materials={
            "grass": MaterialSpec(material_id="grass", feature_scale="medium", palette_hints=("green",)),
            "dirt": MaterialSpec(
                material_id="dirt",
                feature_scale="medium",
                palette_hints=("warm earth", "brown", "ochre"),
                accepted_visual_vocabulary=("compact dirt", "subtle stones", "controlled edge"),
                generation_rules=("road body is continuous", "no text or arrows", "connector width is stable"),
            ),
        },
        shared_edge_contract=EdgeContract(
            edge_id="road_network_edge",
            role="network_connector",
            shared_edge_rule="road connectors meet the center of the requested edge and continue cleanly",
        ),
        network_contracts={
            "road": NetworkContract(
                network_id="road",
                network_type="road",
                allowed_topologies=tuple(topology.value for topology in (NetworkTopology.EMPTY, *_ROAD_TOPOLOGIES)),
                connector_width_ratio=0.22,
                connector_rule="road body reaches every listed connector and no unlisted edge",
                empty_tile_rule="uniform_background",
            )
        },
        tiles=tiles,
        generation=GenerationSpec(
            adapter="mcp",
            requested_width=1024,
            requested_height=1024,
            negative_constraints=("text", "labels", "arrows", "visible grid", "gutter", "disconnected road"),
        ),
        postprocess=PostprocessSpec(crop_policy="center", normalize_mode="nearest"),
    )


def build_dirt_road_network_spec(tileset_id: str = "dirt_road_network_v1") -> TilesetSpec:
    return generate_network_tileset_spec("road", tileset_id=tileset_id)
