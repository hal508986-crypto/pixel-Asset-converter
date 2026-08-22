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
pixel-tile gui
```

出力ディレクトリには `final.png`、`ir.json`、`metadata.json`、baseline 2種、debug画像8種を保存します。入力画像は変更しません。

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
