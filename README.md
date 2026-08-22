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
