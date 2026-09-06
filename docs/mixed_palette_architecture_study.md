# Mixed Material Palette Architecture Study

`Palette Budget Study` の次の最小ラウンドです。目的は、同じ10×10 MAPに `grass / dirt / water / stone` が混在するとき、色数そのものではなく「どの色を共有し、どの色をMaterial固有にするか」がMAP品質へ与える影響を測ることです。

## 初回ラウンドの固定条件

- Real t2i Qualification StudyのTop SourceをMaterialごとにfreeze
- 10×10、1枚の固定Mixed MAP、64×64 tile
- Road Graph / River Graph / River geometry / Stone Objectを固定
- `dithering = off`
- Global Budget 24相当で4方式を比較
- 初回は1 variant。複数MAP、Core 0/2/6、自然予算、Unit overlayは成果物確認後に追加

比較する条件は次の4つです。

| 条件 | 配分 |
| --- | --- |
| `global_24` | MAP全体で24色を共有 |
| `per_material_6x4` | Materialごと6色、合計最大24色 |
| `semantic_ramp_6shade` | Materialごと役割付き6 shade |
| `shared_core4_material5` | Shared Core 4色 + Material Ramp 5色 |

## 実行

```powershell
python -m pixel_tile_compiler.cli study-mixed-palette-architecture `
  --config experiments/mixed_palette_architecture_study.yaml
```

出力先は `e2e/mixed_palette_architecture_study/` です。

主な成果物:

- `source_manifest.json`: Source ID、raw/normalized SHA、freeze済みSource
- `layout.json`: 固定MAP、Road/River graph、Material maskの記録
- `conditions/<condition>/full_map.png`: 条件ごとの全体MAP
- `conditions/<condition>/road_crop.png`
- `conditions/<condition>/river_crop.png`
- `conditions/<condition>/stone_object_crop.png`
- `conditions/<condition>/palette.png`: Shared Core / Material Rampの可視化
- `summary/comparison_board.png`: 条件名付きの比較board
- `summary/architecture_ranking.json`: 自動metricsの暫定順位
- `summary/harmony_identity_tradeoff.json`: HarmonyとIdentityの分離
- `summary/palette_efficiency.json`: 実効色数と効率
- `summary/recommended_architecture.json`: 自動推薦。人間評価前なので暫定
- `blind_review/board.png`: 条件名を隠したMAP board

## Metricsの読み方

総合scoreを結論として扱わず、以下を別々に確認します。

- `global_palette_harmony_score`
- `material_separation_score`
- `material_identity_loss_score`
- `road_background_separation_score`
- `river_background_separation_score`
- `object_background_separation_score`
- `map_readability_score`
- `palette_efficiency_score`
- `shared_color_utilization`
- `cross_material_redundant_color_ratio`

Road/Riverのcontinuityとbreak riskは固定Geometryの回帰値です。Architecture差の主な判定軸は、Geometryを壊さずに背景との分離・Material Identity・MAP全体のHarmonyがどう変わるかです。

## Blind Review

`blind_review/review.csv` に1〜5の評価を記入し、取り込めます。

```powershell
python -m pixel_tile_compiler.cli import-mixed-palette-review `
  --study e2e/mixed_palette_architecture_study `
  --csv e2e/mixed_palette_architecture_study/blind_review/review.csv
```

評価項目:

- `pixel_art_likeness`
- `map_harmony`
- `material_readability`
- `overall_map_quality`
- `favorite`
- `notes`

取り込みは条件recordへ評価を添付するだけで、Material LibraryやPalette Profileを自動昇格しません。

## 今回の成功条件

Shared Coreが勝つことではありません。4方式を同じ固定MAPで比較でき、Harmony・Material Identity・Semantic Readability・Palette Efficiencyのトレードオフを観測できれば、次の条件分岐へ進めます。
