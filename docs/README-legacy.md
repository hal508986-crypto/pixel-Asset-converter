# Pixel Tile Compiler

> これは旧READMEの退避版です。現在の概要・セットアップは [README.md](../README.md) を参照してください。

SRPG用の高解像度画像を、意味・領域・境界を優先して64×64のMAPタイルへ再構成するMVPです。
単純な縮小ではなく、次の決定論的パイプラインを通します。

## ライセンス

プロジェクトのコードは [MIT License](../LICENSE.txt) です。依存パッケージの本文と監査結果は
[NOTICE.txt](../NOTICE.txt) と [LICENSE/](../LICENSE/) にあります。リポジトリ内の画像・実験成果物は
コードライセンスとは別の資産境界なので、公開前に [LICENSE/asset-provenance.md](../LICENSE/asset-provenance.md)
を確認してください。

ライセンス台帳の生成と検査は次で実行できます。

```powershell
py -3.10 scripts/generate_licenses.py
py -3.10 scripts/generate_licenses.py --check
```

未確認の依存や資産が残っている間、`--check` は非ゼロで終了し、確認対象を
`../LICENSE/blocked-and-review.txt` に出力します。

## 設計記録・作業引き継ぎ

- [作業ハンドオフ](HANDOFF.md)
- [ADR-0001: MAP-first Compiler](adr/0001-map-first-compiler-experiment.md)
- [ADR-0002: Surface / Network / Transition](adr/0002-surface-network-transition-semantic-edge-contract.md)
- [ADR-0003: Logical MAP-first Map Visual Bake](adr/0003-map-visual-bake.md)

現在は、再利用可能な汎用Tileset経路と、Logical MAPを先に確定して一枚絵を生成する
MAP専用Visual経路を並行して検証しています。

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
pixel-tile compile-generated-sheet --spec specs/grass_surface_v1.json --image sheet.png --output e2e/compiled-sheet --palette 16
pixel-tile gui
```

出力ディレクトリには `final.png`、`ir.json`、`metadata.json`、baseline 2種、debug画像を保存します。入力画像は変更しません。repeatable tileでは `08_repeat_optimized.png` と `09_tile_preview.png` が追加され、metadataに `periodicity_risk_score`、`center_dominance_score`、`edge_continuity_score`、`corner_seam_score` を記録します。

### キャラクター用ピクセル化

キャラクターやオブジェクトの細部を残したい場合は、region-aware経路とは別に最近傍縮小経路を選べます。

```powershell
pixel-tile compile character.png --output output/character --palette 32 --tile-mode object --pixelization nearest --outline black --no-repeat-opt
```

`--pixelization nearest` は入力画像を直接64×64へ最近傍縮小します。可視ピクセルが入力キャンバス端に接している場合は、その側だけ透明な2px余白を補ってから縮小し、頭頂や輪郭が端で詰まるのを防ぎます。`--outline black` または `--outline white` は透過部分の外側1pxだけに輪郭を追加します。輪郭を含む最終画像はRGBA PNGとして保存されます。既定値の `region` と `outline=off` は従来のMAPタイル経路のままです。

### 待機アニメーションSheetの共通トリミング

キャラクターアニメーションSheetは、GUIまたはCLIで分割方式を選べます。`fixed_grid` は列数・行数を明示する確実な方式、`alpha_gap_auto` はアルファ射影と透明ガターから列・行を推定する方式、`hybrid` は自動推定に失敗した場合だけ指定グリッドへフォールバックする方式です。自動分割では検出用の可視帯と保存用の等分グリッドを分け、元Sheet内の共通座標を保ったまま切り出します。細い装飾を検出用の空帯判定で捨てないため、分割後も全フレームの可視bboxから共通bboxと共通倍率を決めて、水平中央・足元アンカーで配置します。

```powershell
pixel-tile compile-character-animation character_idle_sheet.png `
  --output output/character_idle_animation `
  --split-mode hybrid --cols 4 --rows 1 `
  --alpha-threshold 16 --min-component-area 3 `
  --padding 1 --fit-width 54 --fit-height 54 --bottom-margin 6 `
  --min-gutter-width 2 --debug --character-detail balanced
```

`--frames 4` は従来互換で、`--cols 4 --rows 1` と同じです。出力には、共通配置前の `aligned_sheet.png`、減色後の `compiled_sheet.png`、最近傍8倍の `compiled_sheet_8x.png`、分割確認用の `detection_overlay.png`（`--debug` 時）、各フレームの `compiled/F1/final.png` を保存します。さらに `final_frames/F1_final.png` のように、分割後の `final.png` を1フォルダへリネーム集約します。`bbox_report.json` には各フレームの可視bbox、union bbox、共通倍率、配置bbox、足元アンカー、クリップ有無に加えて、検出モード、行列数、各セル、信頼度、fallback有無を記録します。

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

### Generated Sheet → 64pxコンパイル経路

`process-generated-sheet` は従来どおり分割・最近傍正規化だけを行います。`compile-generated-sheet` は明示的な別経路で、元Sheetの高解像度セルを保存したまま `PixelTileCompiler` へ渡し、全セル共通palette・`directional`設定で64×64へ変換します。manifestにはセルID・行列・切り出し領域・入力/出力hashを記録し、`validation/report.json` は接続ID、入力マスク、最終PNGを別々に検査します。`surface` の `shared_edge_contract` は `validation_mode: exact_rgb` を明示した場合だけ、全順序対・両方向の完成PNG境界を検査します。

`surface` と `network` の構造契約だけが初回自動検査の対象です。契約が不足している場合や共有辺が不一致の場合は、未検証のまま採用扱いにせず拒否します。`transition` など未対応のsemantic roleも同様です。検査が通っても、画風・境界の自然さ・MAPとしての読みやすさは人手確認が必要です。

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

出力 `e2e/pixel_grammar_study/` には、targetごとの3密度tile、Surface / Networkの10×10 preview、frequency band・clutter・readability・semantic fitness metrics、`summary/comparison_board.png`、`summary/recommended_profiles.json`、`summary/grammar_summary.md` を保存します。指標は仮説検証用の近似値であり、Pixel Artistによる最終確認を置き換えません。詳細は `pixel_grammar_study.md` を参照してください。

### 64×64 Flat / Structured / Volumetric Pixel Grammar Study

`study-pixel-hierarchy` は、前回のDetailedを高周波detailとして増やす方式を見直し、`Flat / Structured / Volumetric` の表現階層を比較します。VolumetricはStructuredを土台に、共通光源・広い面の明暗・material固有のdepth cue・contact shadow・highlightを追加するVolumePassです。Network geometry、Transition boundary、Object silhouetteは変更せず、既存のRendererとPixel Compilerを再利用します。

```powershell
py -3.12 -c "import sys; sys.path.insert(0, 'src'); from pixel_tile_compiler.cli import app; app()" study-pixel-hierarchy --config experiments/pixel_hierarchy_study.yaml
```

出力 `e2e/pixel_hierarchy_study/` には、30サンプルの比較tile、Surface / Networkの10×10 MAP、Tree / Rockの5×5 preview、仮ユニットoverlay、hierarchy/noise/semantic/map metrics、`summary/comparison_board.png`、`summary/recommended_profiles.json`、`summary/hierarchy_summary.md` を保存します。詳細は `pixel_hierarchy_study.md` を参照してください。

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
