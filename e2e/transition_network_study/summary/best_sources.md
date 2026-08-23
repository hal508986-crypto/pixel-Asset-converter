# Transition / Network Edge Contract Study

固定ソース、固定seed=42、同じfamily layoutで、surface-only / independent handoff / contract-awareを比較した。

## Ranking

- top 3: road_demo_grass/contract_aware (0.983), road_demo_forest/contract_aware (0.982), transition_demo/contract_aware (0.973)
- worst 3: road_demo_forest/surface_only (0.760), road_demo_grass/surface_only (0.760), transition_demo/surface_only (0.760)

## 良い接続の条件

- road NS/EW の意味を外側edge profileに持たせ、同じroad feature同士だけを接続する。
- transition NS/EW は material_a/material_b の順序を固定し、外側素材を明示する。
- forest-road は forest base に road mask と dirt material を合成した実物で評価する。
- independent handoff は見た目が近くても、境界中心・幅・素材がずれると break risk が増える。

## 既知の限界

- mask / edge profile は近似的な数値指標で、地形意味理解や完全なWang graphではない。
- cornerは構造先行の矩形stripe＋junctionであり、曲率最適化はまだ行っていない。
- transition boundaryのpixel-level最適化は、現在は既存PixelTileCompilerに委譲している。
