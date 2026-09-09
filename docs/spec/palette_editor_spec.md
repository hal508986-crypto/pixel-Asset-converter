---
title: 基準paletteエディタとプリセット
version: 1.2
date: 2026-09-10
project: pixelart-compiler
---

# 基準paletteエディタとプリセット

## 0. 実装担当への前提

本書は設計書であり、記載する追加機能は未実装。
既存の[palette契約](../../src/pixel_tile_compiler/palette_contract.py)を変更せず、その入力側を人の手に開く。

- G-1: 3節のスコープ表の範囲内で実装する。色の補間・近似・自動生成へ広げない。
- G-2: 受け入れ条件に対応する失敗テストを先に作り、最小実装後に責務分離を確認する。
- G-3/G-4: 新規依存はゼロ。PySide6・標準ライブラリのみ。
- G-5: 範囲・判定規則の変更は本書を更新してから実装する。
- `fable-judgment`、`tdd-first`、GUI変更は `ui-design-system` に従う。
- GUI文言は[GUI用語の正本](../../src/pixel_tile_compiler/gui/policy.py)と既存画面の語彙に合わせる。
  paletteは英字表記、色見本は「色見本」、取り込み元は「元絵」ではなく「出力palette」。
- **プリセットの色値は自作に限る。** 4節の方針を厳守する。

## 1. 目的

利用者がGUI上で色を選んでpaletteを組み、それを固定paletteとしてコンパイルに使えるようにする。
「レトロ感を狙って撃つ」ための操作を、外部ツールなしで完結させる。

## 2. 現状

| 部品 | 状態 |
|---|---|
| 固定paletteでのコンパイル | あり（`CompilerConfig.palette_colors`。色を水増ししない契約つき） |
| palette.json の読み込みと検証 | あり（`load_palette_json`、`palette_id` で同一性を担保） |
| 色見本の表示 | あり（`PaletteSwatchList`） |
| GUIからの読み込み導線 | あり（「基準palette」の読み込む／解除） |
| **palette.json の書き出し** | **無い**。[terrain_batch_service.py:698](../../src/pixel_tile_compiler/gui/terrain_batch_service.py) にベタ書きが1か所あるのみ |
| **色を編集するUI** | **無い** |
| **プリセット** | **無い** |

パイプライン側は一切変更せずに済む。追加が要るのは書き出し関数・エディタ・プリセットの3つ。

## 3. スコープ表

### やる

| ID | 内容 |
|---|---|
| S-1 | `palette_contract` に palette.json の書き出し関数を追加する |
| S-2 | 色見本をクリックで選び、色選択ダイアログで変更・追加・削除できるエディタを作る |
| S-3 | コンパイル結果の実測paletteをエディタへ取り込めるようにする |
| S-4 | 自作の色セットをプリセットとして同梱し、エディタから読み込めるようにする |
| S-5 | エディタで確定した色を、既存の「基準palette」としてコンパイルへ渡す |

### やらない

| ID | 内容 | 理由 |
|---|---|---|
| N-1 | 実機由来の色値の同梱 | 4.3節。自作の色値のみとする |
| N-2 | 実在ハード・ゲーム名をプリセット名に使う | 商標。一般名のみとする |
| N-3 | 外部配布paletteの取り込み機能（Lospec等） | 配布物にライセンスが付く。利用者が自分で palette.json を用意する経路は既にある |
| N-4 | 色の自動生成・補間・近似（グラデ生成、減色提案） | 「色を水増ししない」という既存契約と衝突する |
| N-5 | 色の並べ替え・ソート | palette契約は色を昇順へ正規化するため、順序は保存されない。見た目だけの整理になる |
| N-6 | 地形用途への適用 | 基準paletteは現在キャラクター系のみが対象。境界を広げない |
| N-7 | palette.json 以外の形式（.pal、.gpl、.aco等）の読み書き | 形式を増やす前に、まず自前形式で運用する |
| N-8 | 地形一括側の palette.json 書き出しの共通化 | 来歴フィールドを持つため形が異なる。4.1節 |

### いつか

| ID | 内容 |
|---|---|
| L-1 | 他ツール形式のインポート（N-7の解除） |
| L-2 | 地形用途への適用（N-6の解除） |

## 4. 設計

### 4.1 palette契約（既存をそのまま使う）

保存形式は既存の palette.json を変えない。

```json
{"schema_version": 1, "colors": [[15, 56, 15], ...], "palette_id": "<sha256>"}
```

- 色は `normalize_palette` で重複を除き昇順へ正規化される。**順序は保存されない**（N-5の根拠）。
- 1〜64色。範囲外は `ValueError`。
- 書き出しは新設の `save_palette_json(path, colors) -> Path` が行い、`validate_reference_palette` と
  `palette_id` を通してから書く。
- **地形一括側の書き出しは共通化しない。** あちらの palette.json は `reference_final_sha256`、
  `reference_result_id`、`created_at` という来歴フィールドを追加で持ち、原子的書き込みを使う。
  共通化すると来歴が落ちる。読み込み側（`load_palette_json`）は追加フィールドを無視するため、
  両者が同じファイルを読み書きできる関係は保たれる。

### 4.2 エディタの操作

`PaletteEditorDialog`（モーダル）で完結させる。設定タブは高さが限られるため、インラインには置かない。

| 操作 | 動作 |
|---|---|
| 色見本をクリック | その色を選択状態にする |
| 色を追加 | 色選択ダイアログを開き、選んだ色を末尾へ追加。重複色は追加しない |
| 選択色を変更 | 選択中の色を色選択ダイアログで置き換える |
| 選択色を削除 | 選択中の色を取り除く。最後の1色は削除できない |
| 出力paletteから取り込む | 直近のコンパイル結果の実測paletteで置き換える |
| プリセット | 選ぶとその色セットで置き換える |
| 読み込む… / 保存… | palette.json の入出力 |
| OK / キャンセル | OKで確定した色を基準paletteへ渡す。キャンセルで破棄 |

上限64色に達したら「色を追加」を無効化する。

### 4.3 プリセットのライセンス方針

**同梱するプリセットの色値はすべて自作とする。** 実機の実測値も、外部配布paletteの値も使わない。
名前は一般名のみとし、実在するハード・ゲーム・企業の名称を含めない（N-1・N-2）。

同梱するもの:

| プリセット名 | 色数 | 狙い |
|---|---|---|
| 4色グリーン | 4 | 単色系の最小構成 |
| 4色モノクロ | 4 | 明度だけで組む |
| 8色ベーシック | 8 | 原色寄りの最小セット |
| 16色アース | 16 | 土・草・石などの地形向き |
| 32色パステル | 32 | 彩度低めの中間色 |

色値は `gui/palette_presets.py` にデータとして持ち、出典は「本リポジトリで作成」と明記する。
`LICENSE/asset-provenance.md` の生成対象に入るため、追加後は目録を再生成する。

### 4.4 GUI配置と文言

「palette・保存先」タブの基準palette区画に「編集…」ボタンを足し、ダイアログを開く。
既存の「読み込む」「解除」はそのまま残す。

| 場所 | 文言 |
|---|---|
| 起動ボタン | `編集...` |
| ダイアログ表題 | `基準paletteを編集` |
| プリセット欄 | `プリセット` |
| 操作ボタン | `色を追加` / `選択色を変更` / `選択色を削除` / `出力paletteから取り込む` |
| 入出力ボタン | `読み込む...` / `保存...` |
| 色数表示 | `{n}色 / 上限64色` |

## 5. Phase

単一Phaseで完了させる。

- Done定義: 6節のテストが緑、全体回帰が緑、1440×900と800×600で表示崩れなし
- バジェット: 新規依存 0 / 新規ファイル 2（`gui/palette_presets.py`、`gui/palette_editor.py`）/ 変更LOC 500以内

## 6. テスト先行計画

| テスト名 | 観点 |
|---|---|
| `test_save_palette_json_round_trips_through_the_contract` | 書き出したものを `load_palette_json` が読める |
| `test_save_palette_json_rejects_an_invalid_palette` | 0色・65色・範囲外RGBで `ValueError` |
| `test_every_preset_satisfies_the_palette_contract` | 全プリセットが `validate_reference_palette` を通る |
| `test_preset_names_avoid_hardware_and_product_names` | プリセット名に禁止語（実機名の一覧）が含まれない |
| `test_palette_editor_adds_changes_and_removes_colors` | 追加・変更・削除が色一覧へ反映される |
| `test_palette_editor_refuses_duplicate_colors` | 既にある色は追加されない |
| `test_palette_editor_keeps_at_least_one_color` | 最後の1色は削除できない |
| `test_palette_editor_stops_adding_at_the_upper_limit` | 64色で追加が無効になる |
| `test_palette_editor_loads_a_preset` | プリセット選択で色が置き換わる |
| `test_palette_editor_imports_the_output_palette` | 渡した実測paletteで置き換わる |
| `test_main_window_opens_the_palette_editor_and_applies_the_result` | OKで基準paletteが設定され、コンパイル設定へ渡る |
| `test_main_window_palette_editor_cancel_keeps_the_current_palette` | キャンセルで変更されない |

色選択ダイアログ（`QColorDialog`）はテストでは開かず、色を返す部分を差し替え可能にして検証する。

## 7. 依存とライセンス

新規依存はゼロ。プリセットの色値は自作のため第三者ライセンスは発生しない（4.3節）。
新規ファイル追加で追跡ファイル数が変わるため、`python scripts/generate_licenses.py --check` が
落ちる。Phase完了時に目録を再生成してコミットへ含める。

## 8. リリース前チェックリスト

| ID | 項目 | 状態 |
|---|---|---|
| RC-01 | プリセットの色値がすべて自作であることを目視で確認する | **完了**。`gui/palette_presets.py` の値はすべて本作業で作成。実機実測値・外部配布paletteは不使用 |
| RC-02 | プリセット名に実機名・製品名が含まれないことを確認する | **完了**。`test_preset_names_avoid_hardware_and_product_names` で禁止語を機械的に検査 |

## 9. ハンドオフ

| 行き先 | 条件 |
|---|---|
| `tdd-first` | 実装。6節のテストから書く |
| `ui-design-system` | ダイアログの設計と表示確認 |
| `license-audit` | 将来 N-3（外部配布paletteの取り込み）を解除する場合は必ず通す |

## 10. 変更履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-10 | 初版 |
| 1.1 | 2026-09-10 | 実装時の調査で、地形一括側の palette.json が来歴フィールドを持つと判明。共通化をN-8として範囲外にした |
| 1.2 | 2026-09-10 | 実装完了。RC-01/02を完了にした |
