# 64px MAPタイルコンパイル 初回実装ハンドオフ

実装日: 2026-09-07

## 今回の範囲

調査結果と実装プロンプトの初回範囲 1〜4 を、汎用Tilesetの既存経路へ追加した。

- `repeatable` は単体の自己反復専用として維持した。
- Edge Contract、Network、Transition、固定配置MAPの内部コンパイルは `directional` を使い、自己反復補正を適用しないようにした。
- 既存の `process-generated-sheet` は分割のみの互換経路として残した。
- `compile-generated-sheet` を追加し、元Sheetの高解像度セルを64px化前に `PixelTileCompiler` へ渡す経路を作った。
- 共有palette、セルID、行・列、切り出し領域、入力・出力hashを別ファイルとmanifestへ記録した。
- surface/network と明示的マスク付き transition の構造検査、64×64・RGBA・palette・alpha・接続位置・幅・非接続辺漏れ・境界位置を別段階で検査するゲートを追加した。
- 未対応semantic roleは未検証のまま合格にせず拒否する。自動美観スコアによるapproved化はしない。

新しい保存形式を別のTilesetSpecへ増設せず、既存Specを正本にし、実装経路固有の値は `compile_settings.json` と `validation/report.json` に閉じた。これにより既存の分割出力を再解釈しない。

## 再実行

パッケージ未インストールのPython 3.10では、リポジトリの `src` を明示する。

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
py -3.10 -m pytest -q -o addopts= tests/test_map_tile_initial.py tests/test_generation_first_pipeline.py tests/test_repeatability.py tests/test_tileset.py tests/test_transition_network.py tests/test_map_compiler.py tests/test_cli.py tests/test_pipeline.py
py -3.10 -m pixel_tile_compiler compile-generated-sheet --spec specs/grass_surface_v1.json --image assets/source/grass_bright_1024.png --output e2e/map_tile_compiler_initial_20260907/grass_surface --palette 16 --no-debug
py -3.10 -m pixel_tile_compiler compile-generated-sheet --spec specs/dirt_road_network_v1.json --image assets/source/road_dirt_1024.png --output e2e/map_tile_compiler_initial_20260907/road_network_compiled --palette 16 --no-debug
```

出力ディレクトリは既存成果物と衝突しない新規ディレクトリを指定する。実装経路は非空の出力先を拒否する。

## 観測済みの検証

- 初回範囲・transition関連: `18 passed`（Python 3.10、最終PNG誤サイズ拒否の回帰を含む）。
- 全体回帰: `207 passed in 91.51s`（Python 3.10）。
- `grass_surface`: 1024×1024の既存PNGを4×4セルとして処理し、16枚の64×64 PNG、256×256固定MAP、4倍最近傍プレビュー、比較画像、manifest、設定、検証レポートを生成した。構造ゲートは `accepted`、採用状態は `provisional_not_approved`。
- `road_network_compiled`: 既存の1024×1024画像を4×4へ分割したが、各セルは道路topology付きSheetとして作られた入力ではないため、接続ID・入力マスク・最終PNGの契約ゲートが `rejected` になった。出力は検査用に保存したが採用済みとは扱わない。
- 接続負例: `road_ns` の北側接続画素を背景色へ変更した画像は、最終PNGのconnector coverage違反として拒否した。
- 64pxへの先行縮小を避ける回帰として、128×128の高解像度セルを保存した後に64×64へ変換することを確認した。

## 出力

実行済み成果物:

- [草地コンパイル結果](../e2e/map_tile_compiler_initial_20260907/grass_surface/)
- [道路入力の拒否結果](../e2e/map_tile_compiler_initial_20260907/road_network_compiled/)

主なファイルは `source_cells/`、`tiles/`、`map_compiled.png`、`previews/map_compiled_4x_nearest.png`、`comparison/split_only_vs_compiled.png`、`manifest.json`、`compile_settings.json`、`validation/report.json`。

## 未検証・後続

- 実画像の画風、境界の自然さ、MAPの読みやすさは人手レビューが必要で、自動ゲートでは判定していない。
- 既存の画像を等分割しただけの入力では、道路topologyや草土境界の意味を推定して採用しない。topology付きの高解像度Sheetまたは明示的マスク入力が次の課題。
- 明示的なNS/EW入力マスクを使うtransitionの最終境界ゲートは検証済み。SheetのTileSpecだけでtransitionを表す経路、内角・外角・孤立部分は未検証として後続に残した。
- SRPG側の実在地形ID、移動コスト、地形効果、レイヤー、実機表示には接続していない。
- GUIのタイル群取り込み・隣接プレビューは後続範囲であり、今回の画像処理はCLI／コアへ置いた。
