# 地形「元絵を保持」GUI実装ハンドオフ

更新日: 2026-09-07

## 対象

`docs/terrain_source_preserving_implementation_prompt.md` に基づき、地形用途だけに「元絵を保持」経路を追加した。既存のコア処理を複製せず、既存の `pixelization_mode="nearest"` と `_pixelize_source` をGUIから選択する構成である。

キャラクター用途の既存挙動と、CLI/MAPの既定値は変更していない。画風変更は行っていない。

## GUI実装

- 地形の変換方法:
  - `元絵を保持` (`nearest`)
  - `領域を整理` (`region`)
- 地形の初期値は `元絵を保持`。
- 地形の繰り返し最適化は初期値を無効化。
- キャラクター用途では地形専用の変換方法・palette・繰り返し最適化を非表示にする。
- 保存先をGUIで指定できる。
- 地形出力先は、変換方法・palette数・repeat設定を含む名前にする。
  - 例: `terrain_64x64_nearest_24c_repeat-off`
- メタデータに、入力画像のパス・SHA-256・寸法、変換方法、palette予算、実際のpalette数・色、repeat設定・適用結果を記録する。

## 固定原画比較

固定原画:

`C:\Users\s_sah\Downloads\ChatGPT Image 2026年9月7日 14_20_38.png`

- 寸法: `1254x1254`
- SHA-256: `69f9fea5f1a65ce606ca0ef434e45d4d0d0472e5390d2188f5f064cb343245d3`
- 出力寸法: `64x64`
- 共通palette: 固定原画のRGBA画素から `extract_palette` で決定的に抽出した24色
- 比較対象:
  1. 無制限色の直接nearest縮小リファレンス
  2. `nearest` / 24色 / repeat無効
  3. `region` / 24色 / repeat無効
  4. `nearest` / 24色 / repeat有効

比較成果物は `.gitignore` 対象のため、ローカル生成物として保存している。

- 比較ルート: `E:\ena-dri\repos\pixelart-compiler\e2e\terrain_source_preserving_20260907`
- 固定原画コピー: `e2e\terrain_source_preserving_20260907\source\fixed_source.png`
- 4面比較: `e2e\terrain_source_preserving_20260907\comparison_4way_4x.png`
- 条件とhashの記録: `e2e\terrain_source_preserving_20260907\comparison_manifest.json`
- 各variantの64x64画像、4倍nearest preview、3x3 repeat preview、metadataは各variant配下にある。

比較結果の実測:

| variant | mode | repeat | 実palette数 | final SHA-256 |
| --- | --- | ---: | ---: | --- |
| source preserving | nearest | off | 22 | `5689c052790589034cbf7c2f36b39188e9e65ac7a334a3cb094ea807247c11e8` |
| region | region | off | 13 | `4faac0f2fd953292d3a4e997c8364f745a34db29d9c9551169589740b225d53e` |
| source preserving | nearest | on | 21 | `e240fcd8f860142a52791bb038f3dacbe69434b2d55a4f1901fce0d8f9288c4b` |
| GUI既定相当 | nearest | off | 24 | `086efc195da643e548ec3565cc947962f0387b63b915849a45f08c7282d1c7af` |

目視比較では、leaf-tip highlight、dark root shadow、grass clusterの読みやすさ、孤立ノイズ、3x3 repeat seamを確認対象とした。今回の記録はこの固定原画1枚に対する比較であり、`nearest` が一般に常に優れるという判定はしていない。

固定原画での観察メモ:

- 無制限nearestと `nearest` / 24色 / repeat無効は、細かなleaf-tip highlightと暗いroot shadow、grass clusterを相対的に多く保持した。
- `region` / 24色 / repeat無効は、同じ共通paletteでも大きな領域が平滑化され、細かなhighlightと孤立したclusterが減った。実palette数も13色だった。
- `nearest` / 24色 / repeat有効は、細かなclusterを残したまま3x3に反復できた。3x3 previewでは、タイル境界に人工的な線状seamは目視確認されなかった。
- GUI既定相当はGUI実コンパイルとSHA-256一致し、比較スクリプト側では実palette数24だった。

## GUI固定原画検証

PySide6の `offscreen` 環境で `MainWindow` を生成し、固定原画を読み込み、地形・`元絵を保持`・24色・repeat無効で実コンパイルした。

- GUI出力: `e2e\terrain_source_preserving_20260907\gui_default\ChatGPT Image 2026年9月7日 14_20_38\terrain_64x64_nearest_24c_repeat-off\final.png`
- GUI出力と比較スクリプトの「GUI既定相当」出力はSHA-256一致。
- GUI metadataで `pixelization_mode=nearest`、`repeat_opt_enabled=false`、`repeat_opt_applied=false`、実palette数24を確認。
- ウィジェット検証では、地形選択時に `元絵を保持` が表示・選択され、キャラクター選択時に地形変換方法が非表示になることを確認。

この環境ではネイティブアプリ操作用のComputer Use対象が提供されなかったため、GUIの実行確認はPySide6 `offscreen` によるウィジェット生成・コンパイル・画像保存で行った。実ウィンドウを手操作したという意味ではない。

## 検証結果

- focused regression: `62 passed in 16.19s`
- full suite: `226 passed in 90.84s`
- `py -3.10 -m compileall -q src tests scripts`: passed
- `git diff --check`: passed
- 実装対象ファイルのU+FFFD検査: 該当なし

再比較コマンド:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
py -3.10 scripts/terrain_source_preserving_compare.py `
  --source 'C:\Users\s_sah\Downloads\ChatGPT Image 2026年9月7日 14_20_38.png' `
  --output 'e2e\terrain_source_preserving_20260907'
```

この作業ではcommit/pushは行っていない。既存の別作業の変更と、`.gitignore`対象の生成物は保全している。

## 再査読指摘への修正

- `build_output_path()` は、地形の変換方法・palette・repeat設定を省略した旧形式呼び出しで `terrain_64x64` を即時返却する。`None` を変換方法検証へ渡さない。
- 比較スクリプトは、既存の非空出力ディレクトリを `FileExistsError` で拒否する。既存記録を上書きせず、空ディレクトリまたは未作成の出力先だけを受け付ける。
- 回帰テストとして、旧形式呼び出しの互換性と非空ディレクトリ拒否を追加した。

再査読後の固定原画比較ルート:

- `E:\ena-dri\repos\pixelart-compiler\e2e\terrain_source_preserving_rereview_20260907`
- GUI実行画面: `e2e\terrain_source_preserving_rereview_20260907\gui_default\gui_after_compile.png`
- GUI出力は、同一条件の比較スクリプト出力とSHA-256一致 (`086efc195da643e548ec3565cc947962f0387b63b915849a45f08c7282d1c7af`)。
- 全回帰: `228 passed in 89.02s`
