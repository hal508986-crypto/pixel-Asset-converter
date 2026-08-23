# Generic Network Graph → River Renderer Study

固定seed=42、grid=10x10、tile=64x64。

## Ranking

1. river_renderer — 0.946223
2. river_renderer_variants — 0.946103
3. road_like_river_baseline — 0.933926

## 結論

- NetworkGraph、NetworkEdge、NetworkType、TopologyResolver、connector bitmask、Graph validationはRoadと共通利用した。
- River固有の追加はNetworkTypeRules、directed flow、merge/split/cycle validation、River geometry、Bank、Water contractである。
- Road-like baselineと比較することで、Graph coreは共通でもRendererはsemanticごとに必要であることを確認する。
- RiverはNESW crossをrejectし、T topologyはincoming 2 + outgoing 1のmergeとして解釈した。

## Comparison metrics

| method | graph fidelity | flow fidelity | river continuity | bank continuity | edge width contract | merge clarity | score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| river_renderer | 1.000000 | 1.000000 | 0.992448 | 0.985851 | 0.581787 | 1.000000 | 0.946223 |
| river_renderer_variants | 1.000000 | 1.000000 | 0.992448 | 0.985764 | 0.585449 | 1.000000 | 0.946103 |
| road_like_river_baseline | 1.000000 | 1.000000 | 1.000000 | 1.000000 | 0.875000 | 1.000000 | 0.933926 |

## Architectural answers

- Q1: RoadからはNetworkGraph、NetworkEdge、NetworkType、connector bitmask、TopologyResolver、Graph validation、Pixel Compiler assemblyを再利用した。
- Q2: coreにはdirected edge、NetworkTypeRules、incoming/outgoing flow、flow-aware SemanticEdgeProfileを追加した。
- Q3: directed edgeとRulesはRiver以外にも、Railの方向、Canalの流向、Wallの許可Topologyなどへ一般化できる。
- Q4: River固有なのはbank生成、flow-aware geometry、curveの自然化、merge appearance、water materialである。
- Q5: Wall/Canal/Cliff lineへはGraph・Topology・Contract・Pixel Compiler境界を再利用し、geometry/material/transitionだけをRendererとして差し替えられる。

## Source / Graph validation

- source validation: bank=True, forest_canopy=True, grass=True, water=True
- graph warnings: none

## 既知の限界

- Flowの判定はgrid directed edgeに限定し、自由曲線・水理シミュレーション・分水路最適化は行わない。
- Curveとmergeは高解像度のquadratic pathと可変幅strokeによる近似である。
- Water検出は色特徴による近似で、River意味理解の完全な代替ではない。
- Bank materialは今回dirt系Sourceを分離入力として使うが、専用bank source studyは未実施。
