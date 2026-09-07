# 地形バッチ・基準パレット再コンパイル ハンドオフ

更新日: 2026-09-07

## 対象と実装範囲

`docs/terrain_batch_palette_implementation_prompt.md` と `docs/spec/terrain_batch_palette_spec.md` に従い、地形専用のバッチ変換、実使用色の比較、基準パレット固定再コンパイルを追加した。

- `terrain_batch_model.py`: Qt非依存のバッチ、結果履歴、実行スナップショット。
- `terrain_batch_palette.py`: final PNGの可視RGB実測、基準色の検証、安定したパレットID、固定色設定。
- `terrain_batch_service.py`: 旧manifest読込、条件復元、原画hash確認、逐次実行、失敗継続、中止、runごとの保存。
- `terrain_batch_window.py`: 日本語GUI、画像追加・旧manifest読込、行選択と対象チェック、前後プレビュー、基準色表示、非同期実行。
- `main_window.py`: 地形時だけ「地形をまとめて変換」を表示し、単画像・キャラクター経路は既存のまま維持。
- `scripts/terrain_batch_palette_verify.py`: 既存試作manifestから10枚の固定パレット再検証を再現するスクリプト。

新規依存、画風変更、キャラクターバッチ、手動パレット編集、自動色補正は追加していない。

## 査読指摘3件への修正

- 画像追加後にGUIの変換方法・パレット色数・repeat設定を変更した場合、初回「まとめて変換」はその時点の設定を実行スナップショットへ取り込む。固定パレット再変換は、画面の変更値ではなく初回結果の有効条件を引き継ぐ。
- 比較欄に「統一前／統一後」を追加した。固定結果の`previous_result_id`から統一前finalを解決し、原画表示とは別に切り替えられる。
- 一覧のパレット列を実色のRGBスウォッチへ変更した。各スウォッチにHEXを常時表示し、RGBとHEXをツールチップでも確認できる。基準選択前から各行の色を比較できる。

回帰テストでは、8色・領域設定への変更反映、固定再変換での前回条件維持、統一前後finalの切替、一覧スウォッチの色とRGB/HEX表示を確認する。

## 追加査読指摘への修正

- 変換完了時に一覧を再構築しても、同じセルの選択変更シグナルに依存せず、現在行の詳細・元絵・比較finalを明示的に再適用する。初回変換と固定パレット再変換のどちらでも、切替操作なしに最新finalを表示する。
- 回帰テスト `test_batch_completion_refreshes_selected_preview_without_row_switch` で、初回・固定再変換の完了後に選択行を切り替えず最新finalがプレビューへ反映されることを確認する。

## 実装上の契約

- 初期値は `元絵を保持` (`nearest`)、24色、繰り返し最適化無効。
- 基準パレットはdebug画像ではなく、選択したfinal PNGのalpha非0画素から得た重複なしRGB昇順集合。
- 実使用22色を24色へ補充しない。固定実行のコンパイラ予算だけは既存制約に合わせて `max(4, 基準色数)` とする。
- 固定実行は縮小済みfinalではなく原画を入力にし、実行開始時の対象、条件、原画hash、前回結果ID、基準色、基準final hashを凍結する。
- 原画欠落・hash不一致・条件復元不能は対象行を失敗にし、他行の処理は継続する。
- 中止は現在の1件を完了してから未着手行へ反映する。
- 出力はUUID由来の新しいbatch/run/item階層へ保存し、既存出力と旧試作manifestを上書きしない。
- 固定runには `palette.json` を保存し、パレットIDは `b"palette-rgb-v1\n" + RGB各1byte` のSHA-256で計算する。
- 旧形式manifestは読み取り専用で読み込み、原画がなくてもfinal閲覧とfinalからの色抽出を許可する。再実行対象には復元可能な条件と原画がある行だけを初期チェックする。

## GUI確認範囲

PySide6 `offscreen` で以下を検証した。

- 地形時のみバッチ入口を表示し、キャラクター時は非表示。
- 初期値がnearest / 24色 / repeat無効。
- 行選択と対象チェックが独立している。
- 基準未選択時の再変換無効、基準設定後の色数編集無効、基準解除。
- 一括変換と固定パレット再変換がQtイベント処理を止めずに完了する。
- 処理中は条件変更・追加・削除・基準変更を無効にし、一覧閲覧は継続する。

この環境ではネイティブComputer Useの対象アプリが提供されなかったため、実ウィンドウを手操作した確認は未実施。キーボード操作、一覧スクロール、ネイティブDPIでのレイアウトは次の手動QAで確認する。

## 10枚の固定パレット再検証

入力は既存の `e2e/map_tiles_20260907_source_preserving_batch/batch_manifest.json` から読み込んだ。入力manifestの出力先へは書き込まず、別runへ複製して実行した。

実行コマンド:

```powershell
py -3.10 scripts/terrain_batch_palette_verify.py `
  --manifest e2e/map_tiles_20260907_source_preserving_batch/batch_manifest.json `
  --output e2e/terrain_batch_palette_20260907_rerun
```

検証結果:

- 10/10件成功。
- 条件: terrain / `nearest` / palette予算24 / repeat無効。
- 基準: `ChatGPT Image 2026年9月7日 16_01_36 (10).png` の既存final。
- 基準final SHA-256: `39db2cc447a8049866201b041c71c5a0144d74efcab3be8019922c988e2271d1`。
- 基準パレット: 実使用24色。
- パレットID: `581912fb79897afb0db264a3b54ea5e184b52181a3cb4eb8627203bf70e07bcf`。
- 全10件で固定後の実使用色が基準色集合の部分集合。
- 全10件で実行時の原画hashを記録。旧manifestの原画hashとの照合に欠落なし。
- 新runの状態は `success`、run結果数は10、`palette.json` と `run_manifest.json` を確認。

成果物:

- 比較manifest: `e2e/terrain_batch_palette_20260907_rerun/comparison_manifest.json`
- 前後比較画像: `e2e/terrain_batch_palette_20260907_rerun/comparison_before_after.png`
- batch manifest: `e2e/terrain_batch_palette_20260907_rerun/batch_*/batch_manifest.json`
- run manifest: `e2e/terrain_batch_palette_20260907_rerun/batch_*/runs/run_*/run_manifest.json`
- 固定パレット: `e2e/terrain_batch_palette_20260907_rerun/batch_*/runs/run_*/palette.json`

比較画像は各行の左から「原画の64px最近傍表示」「統一前の既存final」「共通パレットでの統一後final」。同一の64pxパネルを4倍最近傍で並べた。機械判定は色集合・hash・条件を対象とし、光源、陰影面積、描き込み密度、道幅、接続形状が揃うことまでは保証しない。今回の画像では共通色化後も各原画の道形状と描き込み差は残るため、色統一と見た目の統一は別評価とする。

## テストと品質確認

実行済み:

```powershell
py -3.10 -m pytest -q -o addopts= tests/test_terrain_batch.py tests/test_terrain_batch_gui.py tests/test_gui.py
py -3.10 -m pytest -q -o addopts=
py -3.10 -m compileall -q src scripts tests
git diff --check
```

結果: focused regression `33 passed in 6.26s`、full suite `251 passed in 95.58s`、compileall成功、`git diff --check`成功。

追加テストは、透明RGB除外、実使用色の順序、固定色の部分集合、4色未満の基準、原画再入力、hash不一致、条件不足、スナップショット不変、run衝突防止、失敗継続、中止、旧manifestの読み取り専用・final色抽出、GUIの操作状態を含む。

編集対象の日本語ファイルについてU+FFFD置換文字検査を行い、該当なし。

## 手動QAと残作業

- WindowsのネイティブGUIで10枚を追加し、一覧スクロール、行選択、対象チェック、基準設定、基準解除を確認する。
- 処理中に条件変更・削除・基準変更ができないこと、閲覧と中止ボタンが機能することを確認する。
- 小さいウィンドウ幅、DPI倍率、長い日本語ファイル名でボタンと比較欄が隠れないことを確認する。
- palette.jsonを移動・再読込した場合の運用UIは、現段階では読み取りと履歴保存の範囲である。

既存の別作業の変更（未追跡の `unused/` を含む）と既存の生成画像は保全している。
