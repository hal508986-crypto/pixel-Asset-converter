# 64x64 Flat / Structured / Volumetric Pixel Grammar Study

This study tests hierarchy depth rather than high-frequency detail. Volumetric is implemented as Structured followed by a coherent semantic VolumePass.
All modes reuse the same source, topology, renderer input, palette and seed.

## Recommended modes

- `grass`: **flat** (overall 0.735)
- `forest_canopy`: **flat** (overall 0.697)
- `dirt_road`: **structured** (overall 0.751)
- `river`: **structured** (overall 0.757)
- `grass_forest`: **volumetric** (overall 0.722)
- `grass_road`: **structured** (overall 0.707)
- `grass_river`: **structured** (overall 0.742)
- `forest_river`: **structured** (overall 0.729)
- `tree_object`: **volumetric** (overall 0.751)
- `rock_object`: **flat** (overall 0.706)

## Answers to the study questions

1. The previous Detailed failure is treated as a noise baseline; this study checks whether volume can rise while high-frequency noise stays controlled.
2. High-frequency detail is represented by noise risk and isolated/micro cluster ratios, while high-fidelity hierarchy is represented by major mass, medium cluster, shading and depth metrics.
3. The most useful volume cues are broad plane lighting, semantic edge/contact shadow and a restrained highlight; pixel speckle is intentionally excluded.
4. Network and transition modes must preserve masks first; objects benefit most visibly from silhouette plus contact shadow, while surfaces benefit from coherent cluster lighting.
5. A future default should keep Structured as the base grammar and expose VolumePass as a semantic renderer stage, not as generic sharpening.
6. Future Source Generators should provide major masses, medium clusters, material planes and stable light/depth cues rather than more microtexture.

## Limitations

- Metrics are approximate and must be checked against the comparison board.
- The 10x10 previews use representative tile repetition to make map clutter and grid visibility observable.
- Tree and rock remain procedural study samples, not a general object compiler.

