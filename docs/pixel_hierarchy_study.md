# 64×64 Flat / Structured / Volumetric Pixel Grammar Study

前回の `Sparse / Balanced / Detailed` は、Detailedを高周波detailの追加として扱っていた。
この実験では、情報量ではなく表現階層を比較する。

```text
Flat
  major mass / silhouette / topology
Structured
  Flat + medium cluster + material structure
Volumetric
  Structured + coherent VolumePass
```

VolumetricのVolumePassは、1pixelノイズを増やさず、共通光源（既定はupper-left）、
広い面の明暗、semantic edge/contact shadow、material固有のhighlightを追加する。
Network mask、Transition boundary、Object silhouetteは既存Rendererの出力を再利用し、
Pixel Compilerの前にgeometryを変更しない。

## 実行

```powershell
pixel-tile study-pixel-hierarchy `
  --config experiments/pixel_hierarchy_study.yaml `
  --output e2e/pixel_hierarchy_study
```

## 出力

- `tiles/<target>/<mode>/tile.png`: 最終64×64 tile
- `tiles/<target>/<mode>/structured_base.png`: Volumetricの入力となったStructured画像
- `maps/`: Surface / Networkの10×10、640×640 preview
- `objects/`: Tree / Rockの5×5 preview
- `unit_overlays/`: 仮ユニットを重ねたreadability確認用preview
- `metrics/hierarchy_metrics.json`: hierarchy / noise / semantic / map metrics
- `summary/comparison_board.png`: target×mode比較board
- `summary/recommended_profiles.json`: targetごとの推奨modeと軸別winner
- `summary/hierarchy_summary.md`: Q1〜Q6を含む実験レポート

## Metricsの読み方

`major_mass_preservation_score`、`medium_cluster_structure_score`、`shading_layer_score`、
`depth_readability_score`を階層品質として見て、`micro_cluster_ratio`、
`isolated_pixel_ratio`、`high_frequency_noise_risk`をノイズ側として見る。

ランキングは単一値だけで判断せず、tile quality、map readability、semantic fitness、
hierarchy quality、noise riskを分離して保存する。数値は仮説検証用の近似値であり、
Pixel Artistによる目視評価を置き換えない。
