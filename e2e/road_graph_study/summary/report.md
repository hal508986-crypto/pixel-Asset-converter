# Logical Road Graph Study

固定seed=42、grid=10x10、tile=64x64。

## Ranking

1. graph_resolved — 0.993043
2. manual_baseline — 0.993043
3. graph_resolved_variants — 0.992691

## 実装上の確認

- Graphの隣接からN/E/S/W bitmaskを解決し、dead end、straight、curve、T、crossを同じresolverで扱った。
- 道路geometryはNetwork mask、materialは既存のdirt source、最終raster化は既存Pixel Compilerに分離した。
- grass/forest境界セルでは既存grass↔forest transitionを先に合成し、その上へroad maskを重ねた。
- `graph_fidelity_score` は論理edgeの両端が最終tile edgeに存在するかを近似評価する。
- graph/image fidelityは、road materialの色特徴による最終pixel maskと論理edgeを照合する。

## Source / Graph validation

- source validation: grass=True, forest_canopy=True, dirt_road=True
- graph warnings: none

## 推奨条件

- topologyは画像生成結果から推測せず、logical graphから決定する。
- 全topologyでroad width / center / edge contractを共有する。
- T / crossは各armのunionとjunction cleanupで作り、Pixel Compilerへ渡す。

## 既知の限界

- 現在のroad maskはgrid上の矩形stripeと円形junctionによる近似で、曲線道路や斜めedgeは対象外。
- graph fidelityは最終画像の意味理解ではなく、edge近傍の二値mask検査である。
- manual baselineは固定graphからmaterializedしたtopology列を使うため、人手入力の誤り比較ではなくresolverとの同一性比較である。
