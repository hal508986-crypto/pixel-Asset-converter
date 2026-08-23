# Pixel Tile Compiler

SRPG用の高解像度画像を、意味・領域・境界を優先して64×64のMAPタイルへ再構成するMVPです。
単純な縮小ではなく、次の決定論的パイプラインを通します。

```text
load → normalize → background → smoothing → edges → SLIC regions
→ structural/semantic analysis → Tile IR → region-aware pixelize
→ palette quantize → cluster cleanup → seam metrics → export
```

## セットアップ

Python 3.12を正本仕様の対象としています。開発環境ではPython 3.10しか利用できなかったため、コードは3.10互換で実行できます。

```powershell
py -3.10 -m pip install -e ".[gui,dev]"
```

## CLI

```powershell
pixel-tile compile source.png --output output/source --palette 16 --tile-mode repeatable
# repeatable最適化を比較用に無効化
pixel-tile compile source.png --output output/source-off --tile-mode repeatable --no-repeat-opt
pixel-tile compile-map map.png --output e2e/map_context_test --cols 4 --rows 5 --palette 24 --context 1 --shared-palette
pixel-tile build-tileset assets/source/grass_master.png --output experiment/grass_tileset --variants 12 --edge-types 3 --palette 24 --map-cols 10 --map-rows 10
pixel-tile gui
```

出力ディレクトリには `final.png`、`ir.json`、`metadata.json`、baseline 2種、debug画像を保存します。入力画像は変更しません。repeatable tileでは `08_repeat_optimized.png` と `09_tile_preview.png` が追加され、metadataに `periodicity_risk_score`、`center_dominance_score`、`edge_continuity_score`、`corner_seam_score` を記録します。

### Repeatability optimization

`tile_mode=repeatable` かつ `repeat_opt_enabled=true` のときだけ、palette-preservingな後処理を行います。左右・上下端をwrap-awareに対応させ、既存paletteから端の対応色を選び、中心dominant clusterの外周だけを弱めます。blurや補間、新しいalpha値は使いません。

設定値は `repeat_opt_strength`、`repeat_opt_edge_band`、`center_suppression_strength` で調整できます。`directional` と `object` ではこの処理は発火しません。

### MAP-first context compiler

`compile-map` は高解像度MAP全体を先に解析し、4×5などのgridへ分割してから、周辺contextを判断材料に中央64×64だけをコンパイルします。`--shared-palette`（デフォルトON）ではMAP全体から決めた16/24/32色のpaletteを全tileで共有します。

同じ入力から次の3方式を出力し、`comparison.png` と `metrics.json` で比較できます。

```text
baseline_global.png   MAP全体の直接縮小・減色
independent_tiles.png context無しの独立tile compile
context_compiled.png  周辺context + shared palette + 境界補正
```

各実験ディレクトリには、`tiles/`、`map_layout.json`、`global_analysis.json`、`grid_overlay.png` も保存されます。grid overlayは確認用で、最終MAP画像には罫線を書き込みません。

### Tileset Source Compiler実験

`build-tileset` は1枚のMaterial Exemplarから、高解像度Source Tile群を作り、既存の `PixelTileCompiler` で64×64へ変換します。patch metadata、quiltのdebug、edge strip、Source Tile、pixel tile、契約付き10×10 MAP、A/B/C比較画像、metricsを同じ実験ディレクトリへ保存します。現在のmaterialは `grass` と `forest_canopy` に対応します。

```text
grass_master.png
→ Material validation / analysis
→ deterministic patch database
→ overlapping image quilting + minimum-error seam
→ Wang-style edge contracts / compatible edge strips
→ high-resolution source tiles
→ existing PixelTileCompiler / optional shared palette
→ contract-aware random MAP and comparison.png
```

Surfaceは `grass` と `forest_canopy` を対象にしています。`road`、`river`、`cliff`、transition、object、GraphCutは実装対象外です。

### Grass Source比較実験

複数のGrass Source候補を同じCompiler条件で比較する実験ランナーも提供しています。既定の8候補、構造化プロンプト、Source validation、single-repeat / independent variants / Source Compiler tilesetのA/B/C、指標ランキング、比較montageを一括生成します。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-sources --config experiments/source_study.yaml
```

主な出力は `e2e/source_study/summary/source_ranking.json`、`best_sources.md`、`best_prompt_template.txt`、`montage.png` です。ランキングは今回の重み付きヒューリスティックによる実験内比較であり、一般的な知覚品質の保証ではありません。生成モデルを使わず、`assets/source_experiments/source/` に置いた画像を固定入力として再実行することもできます。

### Continuous Forest Canopy Source Study

`forest_canopy` は、単独の木や森林床ではなく、上から見た連続樹冠面をMaterial Exemplarとして比較する実験です。8候補のdensity / cluster scale / homogeneity / brightness variation / compositionを固定prompt matrix化し、forest向けのcluster scale・fragmentation・large mass dominance・cluster continuityをmetricsへ追加しています。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-forest-sources --config experiments/forest_source_study.yaml
```

主な入力は `assets/forest_source_experiments/`、出力は `e2e/forest_source_study/` です。`study-sources` でも同じconfigを実行できます。森林のrankingも実験内の重み付きヒューリスティックであり、景観としての美しさや実ゲームでの最終品質を保証するものではありません。

### Transition / Network Edge Contract Study

`study-transition-network` は、grass / continuous forest canopy / dirt roadを対象に、surface、network、transitionを同じseed・同じ10×10 layoutで比較します。既存の単純な `EdgeContract` は維持し、network connector・transition boundary・外側materialを表すsemantic edge profileを追加しています。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-transition-network --config experiments/transition_network_study.yaml
```

実験の出力は `e2e/transition_network_study/` に、`families/surface`、`families/network/dirt_road`、`families/transition`、`maps`、`metrics`、`manifest.json`、`summary/method_ranking.json`、`summary/montage.png` として保存されます。roadはmask-firstでNS/EW/NE/NW/SE/SWのcurveを生成し、forest baseのroad handoffも必ず実行します。

方向規約は、road `NS`=北南に接続、`EW`=東西に接続、transition `NS`=material_aが北・material_bが南、`EW`=material_aが西・material_bが東です。比較方式は surface-only、independent handoff、contract-aware の3つです。

初回実行時は、設定で指定した `assets/road_source_experiments/source/dirt_road_master.png` を用意してください。再現性のため、grass/forestも source studyの実在PNGを固定参照します。road sourceの推奨生成promptは `e2e/transition_network_study/summary/best_prompt_template.txt` に出力されます。

### Logical Road Graph → Network Tile Study

`study-road-graph` は、道路のTopologyを人手で並べる代わりに、論理Graphの隣接からN/E/S/W connector bitmaskを解決します。dead end、NS/EW直線、4種類のcurve、4種類のT junction、NESW crossを同じNetwork abstractionで扱い、grass/forestの境界では既存transitionを先に合成してからroad materialを重ねます。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-road-graph --config experiments/road_graph_study.yaml
```

入力は `experiments/road_graph_10x10.json` と `experiments/road_surface_10x10.json` です。出力 `e2e/road_graph_study/` には、`logical_graph_preview.png`、`topology_debug.png`、`source_tiles/`、`pixel_tiles/`、3方式（manual baseline / graph resolved / graph resolved + variants）のMAP、`manifest.json`、`metrics.json`、`summary/report.md` を保存します。`graph_fidelity_score` は論理edgeが最終64×64画像の両端へ到達しているかを近似検査します。

### Generic Network Graph → River Renderer Study

`study-river-graph` は、Roadで利用している `NetworkGraph`、directed `NetworkEdge`、connector bitmask、`NetworkTopologyResolver` を再利用し、River固有のflow・merge制約、quadratic curve geometry、可変幅water body、bank transitionを追加します。Road-like stripe baseline、River Renderer、variants付きRiver Rendererを同じ10×10 Graphで比較します。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-river-graph --config experiments/river_network_study.yaml
```

入力は `experiments/river_graph_10x10.json`、`experiments/river_surface_10x10.json`、Water Material Exemplar `assets/river_source_experiments/source/water_master.png` です。出力 `e2e/river_network_study/` には、directed Graph preview、topology/flow debug、centerline・body・bank mask、3方式の640×640 MAP、manifest、River metrics、比較レポートを保存します。RiverではNESW crossとdirected cycleをrejectし、T topologyをincoming 2 + outgoing 1のmergeとして扱います。

### 64×64 Pixel Grammar Study

`study-pixel-grammar` は、既存のSurface / Network / Transition / Object sampleを同じ条件で `Sparse / Balanced / Detailed` に通し、64×64での情報密度とsemantic readabilityの関係を比較します。NetworkのmaskやTransitionのboundary geometryは固定し、density変換はmaterial appearance側へ適用します。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-pixel-grammar --config experiments/pixel_grammar_study.yaml
```

出力 `e2e/pixel_grammar_study/` には、targetごとの3密度tile、Surface / Networkの10×10 preview、frequency band・clutter・readability・semantic fitness metrics、`summary/comparison_board.png`、`summary/recommended_profiles.json`、`summary/grammar_summary.md` を保存します。指標は仮説検証用の近似値であり、Pixel Artistによる最終確認を置き換えません。詳細は `docs/pixel_grammar_study.md` を参照してください。

### 64×64 Flat / Structured / Volumetric Pixel Grammar Study

`study-pixel-hierarchy` は、前回のDetailedを高周波detailとして増やす方式を見直し、`Flat / Structured / Volumetric` の表現階層を比較します。VolumetricはStructuredを土台に、共通光源・広い面の明暗・material固有のdepth cue・contact shadow・highlightを追加するVolumePassです。Network geometry、Transition boundary、Object silhouetteは変更せず、既存のRendererとPixel Compilerを再利用します。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-pixel-hierarchy --config experiments/pixel_hierarchy_study.yaml
```

出力 `e2e/pixel_hierarchy_study/` には、30サンプルの比較tile、Surface / Networkの10×10 MAP、Tree / Rockの5×5 preview、仮ユニットoverlay、hierarchy/noise/semantic/map metrics、`summary/comparison_board.png`、`summary/recommended_profiles.json`、`summary/hierarchy_summary.md` を保存します。詳細は `docs/pixel_hierarchy_study.md` を参照してください。

### Material Source Library v0.1

`build-material-library` は、`grass`、`dirt`、`water`、`stone` の4 material familyを同じ条件で登録・比較する実験です。各familyに8候補を用意し、Material Source Card、決定論的fingerprint、Source Gate、8 variant Compile Probe、10×10 Map Probe、5種類のfitness score、family別ranking、Top2 promotionまで一括で行います。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" build-material-library --config experiments/material_source_library_v01.yaml
```

実験成果物は `e2e/material_source_library_v01/`、昇格した永続ライブラリは `material_library/` です。後続Rendererはraw pathを直接参照せず、`MaterialLibrary.get_family()`、`get_preferred_source()`、`get_sources()`でmaterial IDから解決します。現在のCLIには外部t2i backendを接続していないため、既存のgrass/road/water/stone画像と決定論的adapterで32候補枠を埋め、prompt・generation metadata・人手レビュー枠を保存します。

## GUI

GUIはPySide6で、入力・64×64結果・3×3タイルプレビュー、整数ズーム、nearest-neighbor表示、4倍以上でのpixel grid、パレット設定を提供します。画像処理はGUIへ埋め込まず、CLIと同じ `PixelTileCompiler` を呼び出します。

## MCP optional setup

semantic層は `SemanticProvider` 境界で分離されています。MCP接続そのものは必須依存にせず、JSONを返すcallableを `CompilerConfig.semantic_callable` へ注入できます。失敗・不正JSON時はRule-based providerへフォールバックします。

## E2Eサンプル

Image 2.0で生成した森の入力を `assets/source/forest_1024.png` に保存し、プロンプトを `assets/prompts/forest_1024_image2.json` に残しています。

```powershell
pixel-tile compile assets/source/forest_1024.png --output e2e/forest/output --palette 16
```

実行済みの成果物は `e2e/forest/output/` にあります。

## テスト

```powershell
py -3.10 -m pytest
```

MVPの自動テストはIR、palette、region-aware pixelization、cluster cleanup、seam、pipeline、CLI、GUI状態を対象にしています。品質ゲート一括実行やリリース用ゲートは、この仮説検証段階では実行していません。

## Known limitations

- 実行環境でPython 3.12を確認できていません。
- SLIC失敗時は格子regionへフォールバックします。
- MCP transportの実接続は未実装で、adapterとJSON検証・fallbackまでです。
- autotile、複数タイル一括変換、キャラクタースプライト、学習モデルは対象外です。
