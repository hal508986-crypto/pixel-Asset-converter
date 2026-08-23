# 64×64 Pixel Grammar Study

この実験は、同じSource、palette budget、seed、semantic rendererを固定し、
`Sparse / Balanced / Detailed` の情報密度だけを比較する。

対象は次の4 semantic roleである。

- Surface: `grass`, `forest_canopy`
- Network: `dirt_road`, `river`
- Transition: `grass_forest`, `grass_road`, `grass_river`, `forest_river`
- Object sample: `tree_object`, `rock_object`

## 実行

```powershell
pixel-tile study-pixel-grammar `
  --config experiments/pixel_grammar_study.yaml `
  --output e2e/pixel_grammar_study
```

または、リポジトリcheckout直後は次のように実行できる。

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m pixel_tile_compiler.cli study-pixel-grammar `
  --config experiments/pixel_grammar_study.yaml
```

## 出力

- `tiles/<target>/<density>/tile.png`: 最終64×64 tile
- `tiles/<target>/<density>/source_composite.png`: Pixel Compiler投入前の比較用Source
- `tiles/<target>/<density>/metrics.json`: tile単位metrics
- `maps/<target>_<density>.png`: Surface / Networkの10×10反復preview
- `metrics/grammar_metrics.json`: 全target・全densityの集計
- `summary/comparison_board.png`: target×density比較board
- `summary/recommended_profiles.json`: targetごとの推奨density/profile
- `summary/grammar_summary.md`: 仮説検証用の解釈と限界

Density変換はNetworkのconnector maskやTransitionの境界geometryには適用せず、
material appearanceへ適用する。これにより、見た目の情報量とsemantic geometryを混同しない。

指標はfrequency band、clutter、readability、semantic fitness、edge continuityを中心とした近似値であり、
Pixel Artistによる視認評価を置き換えるものではない。
