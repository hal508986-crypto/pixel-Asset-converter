---
title: Canvasサイズ上方向拡張とpalette上限の再設計
version: 1.3
date: 2026-09-09
project: pixelart-compiler
---

# Canvasサイズ上方向拡張とpalette上限の再設計

## 0. 実装担当への前提

本書は設計書であり、記載する追加機能は未実装。
[戦闘アニメーション仕様](battle_animation_compiler_spec.md)の共通変換・共有palette・出力命名を継承する。
既存の64×64/128×128の出力契約は変更しない。本書が扱うのは選択肢の追加と検証基盤のみ。

- G-1: 3節のスコープ表の範囲内で実装する。地形・MAPタイルへ拡張しない。
- G-2: 受け入れ条件に対応する失敗テストを先に作り、最小実装後に責務分離を確認する。
- G-3/G-4: 新規依存・モデル・素材・フォントはゼロ。既存の画像処理基盤のみを使う。
- G-5: 範囲・予算・判定規則の変更は本書を更新してから実装する。
- `fable-judgment`、コード変更時の `tdd-first`、GUI変更時の `ui-design-system` に従う。
- GUI文言は [GUI用語の正本](../../src/pixel_tile_compiler/gui/policy.py) と既存画面の語彙に合わせる。
  新しい語を作らない（元絵 / 出力Canvasサイズ / palette上限（色数） / パーツ / 割り当て）。
- 元絵・既存出力・他者の変更を保持する。実素材検証は既存出力と別のディレクトリへ書く。
- 本書作成の依頼は、コード実装・コミット・プッシュの依頼を含まない。

## 1. 目的

出力Canvasを64×64・128×128から上方向（256×256まで）と非正方（2:1・1:2・16:9・4:3）へ広げ、
サイズに応じたpalette上限（現在の既定24色）の妥当性を、再現可能な比較検証で決められるようにする。

## 2. 実測で確定した現状

2026-09-09に `e2e/character_native_resolution_study_20260907/source_snapshot.png`（1254×1254）で実測。

### 2.1 コアは既に任意サイズへ対応している

`--purpose character --tile-mode object --pixelization nearest` の経路で、
256×256 / 256×128 / 128×256 / 224×126 / 224×168 / 384×384 / 512×512 がいずれも成功する。
palette上限も既に4〜64を受け付ける。**塞いでいるのはGUIだけ**。

| 制約箇所 | 内容 | 本書での扱い |
|---|---|---|
| `gui/policy.py` `GUI_CHARACTER_CANVAS_SIZES` | `((64,64),(128,128))` のみ | **解放する（P1）** |
| `gui/policy.py` `resolve_character_gui_profile` | 上記以外で `ValueError` | **解放する（P1）** |
| `config.py:98` | 非64Canvasは object/nearest 限定 | 据え置き。キャラ経路は該当しない |
| `config.py:191` `tile_size` | MAPタイルは64固定（28ファイル254箇所） | **やらない（3節）** |
| `character_study/native_resolution.py` `_CANVAS_SIZES` | `((64,64),(128,128))` 固定 | **可変にする（P0）** |
| `character_study/config.py` `palette_budgets` | `{16,24,32}` のみ許可 | **4〜64へ解放する（P0）** |

### 2.2 `work_size` は出力に影響しない

`normalize_image` は解析前に元絵を `work_size × work_size` の正方へLANCZOSリサイズするが、
object/nearest 経路の最終出力には効かない。256×256出力と384×384出力の双方で、
`work_size=256` と `work_size=512` の結果は **差異0画素**。解像度の天井は存在しない。

### 2.3 非正方では短辺が実効解像度を決める

被写体bboxのCanvas占有率（palette36）:

| Canvas | 被写体bbox | 縦占有 | 横占有 |
|---|---|---|---|
| 64×64 | 36×54 | 84% | 56% |
| 128×128 | 71×108 | 84% | 55% |
| 256×256 | 142×216 | 84% | 55% |
| 256×128 (2:1) | **71×108** | 84% | 28% |
| 128×256 (1:2) | 108×164 | 64% | 84% |
| 224×126 (16:9) | 70×106 | 84% | 31% |
| 224×168 (4:3) | 94×142 | 85% | 42% |

**256×128の被写体は71×108で、128×128と同一**。横に伸ばしても実効解像度は上がらず余白が増えるだけ。
単体の立ち絵で非正方を選ぶ意味は薄く、意味を持つのは戦闘アニメの移動保持・複数被写体・背景込みの場合。

### 2.4 サイズを上げると密度は下がり、palette上限では補償できない

`_two_by_two_uniform_block_ratio`（2×2ブロックが均一な割合。高いほど「拡大されただけ」に近い）:

| Canvas | p16 | p24 | p36 | p48 | p64 |
|---|---|---|---|---|---|
| 64×64 | 0.697 | 0.696 | 0.684 | 0.679 | 0.671 |
| 128×128 | 0.780 | 0.770 | 0.745 | 0.734 | 0.719 |
| 256×256 | 0.846 | 0.818 | 0.789 | 0.777 | 0.753 |

- 色数を上げると密度は上がる（均一率が下がる）が、**効きが小さい**（64×64で16→64色は0.697→0.671）。
- サイズを上げると密度は大きく下がる（64→256で+0.15）。
- **128×128も256×256も、64色まで上げても64×64/24色の密度（0.696）に届かない**（0.719 / 0.753）。

したがって「24→36が自然」は方向としては正しいが、**サイズ拡大による密度低下を色数だけでは補償できない**。
密度に効く因子として `character_detail_level`（sparse/balanced/detailed）を検証軸へ含める必要がある。
これはP0で検証する仮説であり、本書は結論を先取りしない。

### 2.5 検証素材はGit管理下にない

`git ls-files` の画像は **0件**。`/e2e/`、`/material_library/`、`/assets/` はすべて `.gitignore` 対象。
既存のstudyテストはいずれも `tmp_path` に合成画像を生成して回っている。

したがって検証は2層に分ける。混ぜてはならない（G-2）。

- **自動テスト**: 合成素材で「仕組みが動く」ことを検証する。CIで緑になる。
- **手動QA**: ローカルの実素材で「どの値が自然か」を判断する。CIでは走らない。

## 3. スコープ表

### やる

| ID | 内容 |
|---|---|
| S-1 | 解像度studyのCanvasリストを設定可能にし、非正方も扱えるようにする |
| S-2 | studyのpalette上限を4〜64へ解放する |
| S-3 | 検証軸に `character_detail_level` を加え、サイズ×色数×密度の3軸で比較する |
| S-4 | 表示サイズを正規化した比較シートと、メトリクスJSON・要約Markdownを生成する |
| S-5 | GUIの出力Canvasサイズにプリセット追加と自由入力を用意する |
| S-6 | 非正方選択時に「実効解像度は短辺で決まる」ことをGUIに表示する |
| S-7 | キャラクターアニメーションの足元固定配置でも同じCanvas選択肢を使えるようにする |
| S-8 | `compiler_config_for_purpose` が呼び出し側の `background_mode` を握り潰す問題を直す |
| S-9 | 背景の扱い（元絵の透明を使う／単色を自動透過／指定色を透過）を選べるようにする |
| S-10 | 構図（被写体を収める／画面全体をそのまま使う）を選べるようにする |
| S-11 | 背景と構図を検証軸へ加え、素材ごとにどの組み合わせが成立するかを記録する |
| S-12 | アニメーション経路でも単体と同じ「構図」を選べるようにする（画面全体でCanvasを埋め切る） |
| S-13 | palette上限とコマ間palette統一が足元固定モードで隠れている不具合を直す |

### やらない

| ID | 内容 | 理由 |
|---|---|---|
| N-1 | 地形・MAPタイルのサイズ拡張 | `tile_size=64` が28ファイル254箇所。tileset境界契約・seam・transitionの再設計になる |
| N-2 | 512×512以上のGUI提供 | 動作はするが用途が未定。CLIからは可能なまま残す |
| N-3 | 非正方での被写体の長辺基準拡大（`fit_within` の数値をGUIで直接指定） | S-10の「画面全体をそのまま使う」で余白の問題は解消するため、数値指定は当面不要 |
| N-4 | 元絵のアップスケール・補筆 | 本コンパイラの責務外 |
| N-5 | palette上限64超 | `config.py` と `character_animation.py` の既存契約 |
| N-6 | CIでの実素材検証 | 素材がGit管理外（2.5節） |
| N-7 | `work_size` の変更 | 出力に影響しないことを実測済み（2.2節） |
| N-8 | `background_mode="auto"` の推定アルゴリズム改良 | 4.5節のとおり、全面が絵の素材は構図側で解決する。推定器には触らない |

### いつか

| ID | 内容 |
|---|---|
| L-1 | 地形タイルの128×128対応（N-1の解除） |
| L-2 | 非正方での被写体配置ポリシー（N-3の解除）。複数被写体・背景込みの用途が決まってから |
| L-3 | サイズ別の推奨palette上限をGUIの既定値へ反映する（P2の結論待ち） |

## 4. 設計

### 4.1 Canvasサイズ契約

- GUIのプリセット: 64×64 / 128×128（推奨） / 256×256 / 256×128 / 128×256 / 224×126 (16:9) / 224×168 (4:3)
- 自由入力: 幅・高さを個別指定。範囲は 16〜512。既存の `animation_width/height` と同じ流儀。
- `resolve_character_gui_profile` は正方限定の検査をやめ、`min(width, height) >= 16` と
  `max(width, height) <= 512` のみを検査する。`palette_budget=24` の既定は変えない。
- 出力ディレクトリ名は既存の `character_{w}x{h}_b24` 形式を維持する（`build_output_path` は変更不要）。

### 4.2 非正方の扱い

開放するが、誤解を招かないよう**実効解像度を明示する**（S-6）。

- 短辺が実効解像度を決めることをGUIに常時表示する。
- 表示文言は「実効解像度は短辺で決まります（256×128の被写体は128×128相当）」。
- `fit_within` の比率は現状（`_scale_half_up(54, 辺, 64)`）を維持する。変更はN-3。

### 4.3 palette上限契約

- コア・GUIとも4〜64のまま変更しない。既定24も変更しない。
- P2の検証結果に応じて既定値を見直すが、それはL-3として別途決める。

### 4.4 検証指標

| 指標 | 定義 | 読み方 |
|---|---|---|
| `measured_palette` | `extract_final_palette` の色数 | 上限どおり使われたか |
| `uniform_2x2_ratio` | `_two_by_two_uniform_block_ratio` | 高いほど「拡大されただけ」 |
| `subject_bbox` / `occupancy` | アルファbboxとCanvas占有率 | 実効解像度と余白 |
| `edge_color_changes` | 隣接画素で色が変わる割合 | ドットの切り替わり頻度 |
| `elapsed_seconds` | 1件あたりの所要 | 実用上の上限を見る |

`edge_color_changes` のみ新規。他は既存実装を再利用する。

### 4.6 アニメーション経路での構図

アニメーション経路は per-frame のコンパイルで `character_input_mode="pre_aligned"` を
常にハードコードしている（[character_animation.py](../../src/pixel_tile_compiler/pixelizer/character_animation.py)）。
そのため単体経路のような「被写体を収める／画面全体」の切り替えは、`character_input_mode` ではなく
`CharacterAnimationConfig` の `fit_within` と `bottom_margin` で表す。

| 構図 | fit_within | bottom_margin |
|---|---|---|
| 被写体を収める（余白あり） | `profile.fit_within`（54/64比） | `profile.bottom_margin`（7/64比） |
| 画面全体をそのまま使う（余白なし） | Canvasサイズと同じ | 0 |

単体経路と同じ「構図」の語彙で出す。利用者から見て同じ選択が同じ結果になるようにするため。

### 4.5 背景と構図の契約

ゲームアセットとして広く使うため、**背景と構図をそれぞれ選べるようにする**。
現在はキャラクター用途に `background_mode="auto"` と被写体フィットが固定されており、
利用者が選ぶ手段がない。

#### 実測（2026-09-09、G:\マイドライブ の14素材、128×128 / palette24）

| 素材 | A 現行（auto＋被写体フィット） | B 背景込み（alpha＋全面） |
|---|---|---|
| 10 細線（柵＋空） | 可視7% / 4色 **破綻** | 可視100% / 24色 **正常** |
| 12 余白ゼロ（甲冑クローズアップ） | 可視4% / 2色 **破綻** | 可視100% / 24色 **正常** |
| 13 不透明背景（青ベタ＋飛行船） | 可視19% / 9色 **正常**（背景を除去） | 可視100% / 23色 **正常**（背景を保持） |
| 09 パターン背景（市松＋小物） | 可視54% / 23色（市松が残る） | 可視100% / 24色 |
| 03/04 透過済みの立ち絵・荷車 | 可視17%/25% | 可視22%/31%（余白が減る） |

10と12は「背景と被写体の境界が定義できない絵」で、`auto` の外周連結フラッドフィルが
絵の大半を除去する。**構図を「画面全体」にすれば完全に解消する**（可視100%・色数フル活用）。
13は `auto` が正しく機能しており、切り抜きが欲しい用途ではAが正解。
つまり**どちらが正しいかは素材ではなく用途で決まる**ため、選択肢として与える。

#### 契約

2つの軸を独立に選ばせる。既存の設定値をそのまま使い、新しい処理は作らない。

| 軸 | 選択肢 | 対応する既存設定 |
|---|---|---|
| 背景 | 元絵の透明をそのまま使う | `background_mode="alpha"` |
| | 単色背景を自動で透過にする（既定） | `background_mode="auto"` |
| | 指定した色を透過にする | `background_mode="color"` + `background_color` |
| 構図 | 被写体を中央下寄せで収める（既定） | `character_input_mode="single_frame"` |
| | 画面全体をそのまま使う | `character_input_mode="pre_aligned"` |

- 「画面全体をそのまま使う」を選ぶと `fit_within` と `bottom_margin` を通らないため、
  **余白は発生しない**。2.3節の非正方の余白も、4.2節の注記も、この構図では当てはまらない。
- `compiler_config_for_purpose` は `overrides.update()` で呼び出し側の指定を上書きしている。
  背景と構図については上書きせず、指定があればそれを尊重する（S-8）。既定値は変えない。
- CLIには `--background` が既にある。`character_input_mode` は未露出なので追加する。

## 5. Phase分割とバジェット

### P0: 検証基盤と背景・構図の解放（歩く骨格）

Canvasリスト・palette上限・背景・構図を設定可能にし、合成素材で比較シートとメトリクスが出るところまで。
`compiler_config_for_purpose` の握り潰し（S-8）はここで直す。GUIより先にコア側を通す。

- Done定義: 自動テスト緑、dead-code検査ゼロ、合成素材で成果物が生成される
- バジェット: 新規依存 0 / 新規ファイル 2以内 / 変更LOC 500以内

### P1: GUI解放

Canvasプリセットと自由入力、非正方の注記、背景と構図の選択。

- Done定義: 自動テスト緑、1440×900と800×600で表示崩れなし
- バジェット: 新規依存 0 / 新規ファイル 0 / 変更LOC 350以内

### P2: 実素材での比較検証と結論

ローカル実素材でP0の基盤を回し、結論を `docs/canvas_scale_study.md` に記録する。

- Done定義: 3素材以上で回し、サイズ別の推奨palette上限と `detail_level` の推奨を根拠付きで記述
- バジェット: コード変更なし（設定ファイルと文書のみ）

## 6. テスト先行計画

### P0

| テスト名 | 観点 |
|---|---|
| `test_native_resolution_study_accepts_configured_canvas_sizes` | Canvasリストが設定から読まれ、既定は現行の64/128のまま |
| `test_native_resolution_study_accepts_non_square_canvas` | 256×128を指定して成果物が出る |
| `test_native_resolution_study_rejects_out_of_range_canvas` | 16未満・512超で `ValueError` |
| `test_character_study_config_accepts_palette_budgets_up_to_64` | 36・48・64が通り、65で `ValueError` |
| `test_character_study_config_still_rejects_duplicate_budgets` | 既存の重複検査が生きている |
| `test_study_metrics_include_uniform_ratio_and_occupancy` | 指標JSONに4.4節の項目が揃う |
| `test_edge_color_changes_is_higher_for_a_noisy_image` | 新規指標が密度の差を検出する |
| `test_comparison_sheet_normalizes_display_scale` | 64は4倍・128は2倍・256は等倍で同じ表示サイズに揃う |
| `test_character_purpose_keeps_the_caller_background_mode` | `compiler_config_for_purpose("character", background_mode="alpha")` が握り潰されない（S-8の回帰） |
| `test_character_purpose_keeps_the_caller_input_mode` | 同様に `character_input_mode` が尊重される |
| `test_character_purpose_defaults_are_unchanged` | 指定しなければ従来どおり `auto` / `single_frame` |
| `test_full_frame_composition_keeps_every_visible_pixel` | 全面が不透明な素材で、`pre_aligned` なら可視率が落ちない（10・12の破綻の回帰） |
| `test_full_frame_composition_leaves_no_margin` | `pre_aligned` の出力bboxがCanvas全面と一致する |
| `test_subject_composition_still_anchors_to_the_bottom_frame` | `single_frame` の既存の余白・足元固定が変わらない |

### P1

| テスト名 | 観点 |
|---|---|
| `test_character_gui_profile_accepts_the_new_presets` | 256×256・256×128・224×126が通る |
| `test_character_gui_profile_rejects_out_of_range_canvas` | 16未満・512超で `ValueError` |
| `test_main_window_exposes_extended_canvas_presets` | プリセットがGUIに並ぶ |
| `test_main_window_custom_canvas_size_drives_the_profile` | 自由入力の幅・高さが出力Canvasへ反映される |
| `test_main_window_warns_effective_resolution_for_non_square` | 非正方選択時に短辺の注記が出る |
| `test_main_window_exposes_background_and_composition_choices` | 背景3択と構図2択がGUIに並ぶ |
| `test_main_window_background_choice_reaches_the_compiler_config` | 選択がそのまま設定へ渡る |
| `test_main_window_hides_the_margin_note_for_full_frame_composition` | 画面全体構図では非正方の注記を出さない（余白が発生しないため） |
| `test_main_window_fits_in_a_small_window_without_clipping` | 既存テストが緑のまま |

### P2

自動テストなし。7節の手動QA手順で判断する。

## 7. 手動QA手順（P2）

1. ローカルの実素材から、色数の多寡・明暗・細部量が異なるキャラクターを3件以上選ぶ。
2. `character_scale_study.json` にCanvasリスト（64/128/256、256×128）、palette上限（16/24/36/48/64）、
   `detail_level`（sparse/balanced/detailed）、背景と構図の組み合わせを設定する。
3. studyを実行し、`e2e/canvas_scale_study_<日付>/` へ出力する。既存の出力先を上書きしない。
4. 比較シートを等倍で目視し、次を記録する。
   - 64×64/24色を基準に、各サイズで「同等の密度に見える」色数と `detail_level`
   - 色数を上げても密度が戻らないサイズがあるか
   - 非正方で余白が実用上問題になるか
   - 素材ごとに、切り抜き構図と画面全体構図のどちらが成立するか
5. 結論を `docs/canvas_scale_study.md` に、素材名・設定・指標値・判断理由とともに書く。

## 8. GUI文言

| 場所 | 文言 |
|---|---|
| 出力Canvasサイズ プリセット | `64 × 64` / `128 × 128（推奨）` / `256 × 256` / `256 × 128` / `128 × 256` / `224 × 126（16:9）` / `224 × 168（4:3）` / `自由入力` |
| 自由入力の行 | ラベル「幅 × 高さ」、範囲16〜512 |
| 非正方選択時の注記 | 「実効解像度は短辺で決まります（256×128の被写体は128×128相当）」 |
| 注記のツールチップ | 「横長Canvasは余白が増えるだけで描き込みは増えません。移動を保持する戦闘アニメや、複数の被写体を並べる場合に使ってください」 |
| 背景の扱い | `単色背景を自動で透過にする` / `背景を残す（元絵の透明はそのまま）` / `指定した色を透過にする` |
| 構図 | `被写体を収める（余白あり）` / `画面全体をそのまま使う（余白なし）` |
| 構図のツールチップ | 「画面全体をそのまま使うと、元絵の隅々まで出力へ入ります。背景ごと1枚のアセットにしたいときに選んでください」 |

## 9. 依存とライセンス

新規依存・モデル・素材・フォントはゼロ（G-3/G-4）。Pillow・NumPy・既存study基盤のみを使う。
`LICENSE/` の再生成は不要だが、**新規ファイル追加時は `python scripts/generate_licenses.py --check` が
落ちる**ため、Phase完了時に再生成してコミットに含める。

## 10. リリース前チェックリスト

| ID | 項目 | 状態 |
|---|---|---|
| RC-01 | サイズ別の推奨palette上限を決め、L-3としてGUI既定へ反映するか判断する | **完了**。palette上限は元絵の可視色数で決まるため、固定の既定値に意味がない。既定24を据え置き、L-3は見送り（[検証結果](../canvas_scale_study.md)） |
| RC-02 | `detail_level` が密度に効くか実素材で確認する（2.4節の仮説） | **完了。効かない**。効き幅0.001〜0.094でサイズの効き幅0.228〜0.536の3分の1以下。2.4節の仮説は否定された |
| RC-03 | 256×256以上での所要時間が実用範囲か確認する（実測では512×512で4.6秒） | **完了**。実用範囲 |
| RC-04 | 非正方の用途が固まったらN-3（`fit_within` 指定）を再検討する | [要確定] |
| RC-05 | 背景と構図の既定値を用途別に決める（キャラは切り抜き、アセットは画面全体か） | [要確定] |
| RC-06 | 画面全体構図でアニメーションのコマ揃え（共通bbox・足元固定）が成立するか確認する | **完了**。背景込み4コマの実素材で、Canvas256×256・bbox(0,0,256,256)・palette24色が全コマ一致。成立する |

## 11. ハンドオフ

| 行き先 | 条件 |
|---|---|
| `tdd-first` | 各Phaseの実装。6節のテストから書く |
| `ui-design-system` | P1のGUI変更 |
| `quality-gate` | 全Phase完了後の一括検証 |
| `license-audit` | 新規依存が発生した場合のみ（本書の想定では発生しない） |

## 12. 変更履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-09 | 初版。実測（2節）に基づきスコープを確定 |
| 1.1 | 2026-09-09 | 背景と構図を選択制にする決定を反映（4.5節、S-8〜S-11）。G:\マイドライブの14素材での実測を追加。10・12の破綻は構図側で解消することを確認し、`auto` の推定器改良はN-8として範囲外にした |
| 1.2 | 2026-09-09 | P2完了。RC-01/02/03を実測で決着させ、結論を[canvas_scale_study.md](../canvas_scale_study.md)へ分離。出力Canvasサイズは元絵の見かけの格子で、palette上限は元絵の可視色数で決まる |
| 1.3 | 2026-09-09 | 実機e2eの結果を反映。アニメーション経路にも構図を出し（S-12・4.6節）、足元固定でpalette上限が隠れる不具合を直した（S-13）。背景の選択肢は「背景を残す」を明示する表記へ変更。RC-06を完了にした |
