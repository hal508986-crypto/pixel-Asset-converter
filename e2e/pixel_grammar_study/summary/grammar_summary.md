# 64x64 Pixel Grammar Study

This experiment compares sparse, balanced and detailed reduction profiles under the same source, palette budget and seed.
The metrics are approximate instrumentation for hypothesis testing, not a replacement for pixel-artist review.

## Recommended profiles

- `grass`: **sparse** (score 0.693) — readability=0.713, semantic_fitness=0.754, clutter=0.173
- `forest_canopy`: **sparse** (score 0.640) — readability=0.592, semantic_fitness=0.752, clutter=0.188
- `dirt_road`: **sparse** (score 0.729) — readability=0.817, semantic_fitness=0.712, clutter=0.114
- `river`: **sparse** (score 0.736) — readability=0.786, semantic_fitness=0.784, clutter=0.159
- `grass_forest`: **sparse** (score 0.764) — readability=0.845, semantic_fitness=0.603, clutter=0.104
- `grass_road`: **detailed** (score 0.766) — readability=0.838, semantic_fitness=0.616, clutter=0.105
- `grass_river`: **sparse** (score 0.782) — readability=0.825, semantic_fitness=0.689, clutter=0.136
- `forest_river`: **detailed** (score 0.777) — readability=0.822, semantic_fitness=0.678, clutter=0.132
- `tree_object`: **sparse** (score 0.766) — readability=0.831, semantic_fitness=0.636, clutter=0.112
- `rock_object`: **sparse** (score 0.736) — readability=0.793, semantic_fitness=0.598, clutter=0.125

## Study interpretation

- Sparse should reduce clutter and protect large semantic masses, but can erase useful local variation.
- Balanced is the reference condition for comparing semantic readability against texture richness.
- Detailed preserves high-frequency information, but can make 64x64 surfaces noisy and weaken transitions or silhouettes.
- Network and transition geometry are kept outside the density transform; only the material appearance is changed.

## Known limitations

- Frequency bands and clutter are image statistics, not human perception.
- The map previews intentionally repeat the representative tile to expose grammar-level grid and periodicity behavior.
- Tree and rock are deterministic sample objects only; they do not introduce a general object compiler.

