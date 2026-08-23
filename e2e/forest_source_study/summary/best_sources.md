# Forest Canopy Source Study Summary

## Top sources

- forest_src_02_uniform_medium: score=0.8646 — texture variation is too low, low periodicity and grid visibility
- forest_src_03_uniform_dense: score=0.8576 — texture variation is too low, low periodicity and grid visibility
- forest_src_05_medium_clusters: score=0.8539 — canopy cluster continuity is low, low periodicity and grid visibility

## Worst sources

- forest_src_04_small_clusters: score=0.8480 — texture variation is too low, low periodicity and grid visibility
- forest_src_07_dark_variation: score=0.8304 — macro pattern or autocorrelation is high, low periodicity and grid visibility
- forest_src_08_illustrative: score=0.8231 — macro pattern or autocorrelation is high, large canopy mass dominance is high, low periodicity and grid visibility

## Observed source conditions

- Small-to-medium overlapping canopy clusters preserve local variation while keeping neighboring tiles visually connected.
- A single large canopy mass raises low-frequency, landmark, and mass-break risk even when the source looks scenic.
- Highly fragmented canopy breaks can look noisy and reduce silhouette continuity at tile boundaries.
- The ranking is metric-based; confirm forest readability with human review before freezing weights.
- This is an experiment ranking, not a general perceptual quality score; recalibrate weights after human review.
