# 実装窓向けプロンプト: パーツ分割の「確定」が再解析で落ちる不具合

以下を修正してください。**調査は完了しており、原因も再現手順も特定済み**です。
このプロンプトだけで着手できるよう、観測した事実をすべて記載しています。

## 症状（利用者の報告）

GUIの分割方式「行ごとにパーツ単位で分割」（`row_alpha_components`）が、
**一度走らせると2回目以降うまく解析・分割できなくなる。**

## 再現手順（確認済み）

1. コマ割りアニメーションの元絵を読み込む
2. 分割方式に「行ごとにパーツ単位で分割」を選ぶ
3. 「パーツを解析」→ 各パーツをコマへ割り当て → 「割り当てを確定」→ コンパイル。**ここまでは成功する**
4. **設定を一切変えずに、もう一度「パーツを解析」を押す**
5. コンパイルすると「未解決のパーツがあります。色分けを確認して割り当てを確定してください。」で止まる

| | 1回目 | 4の再解析後 |
|---|---|---|
| 解析の結果 | `needs_assignment` | `resolved`（割り当ても cells も1回目と完全一致） |
| `_component_assignment_confirmed` | 確定できる | **False に落ちる** |
| 割り当て欄の案内 | 「パーツをコマへ分けました」 | **「割り当てを変更しました。再度割り当てを確定してください。」**（何も変更していない） |
| 下部の状態表示 | 成功 | 「パーツを解析し、コマ抽出プレビューを更新しました」＝**成功に見える** |
| コンパイル | 通る | **「未解決のパーツがあります」で停止** |

**最も質が悪い点**: 色分けオーバーレイもコマ抽出プレビューも「解決済み」の見た目のまま、
下部の状態表示は成功を伝えているのに、コンパイルだけが「未解決」と言って止まる。
画面のどこを見ても何が未解決なのか分からない。

回避策は「割り当てを確定」をもう一度押すこと。押せば通る。

## 原因（特定済み）

### 主因: 再解析が無条件に確定を落とす

[`src/pixel_tile_compiler/gui/main_window.py`](../src/pixel_tile_compiler/gui/main_window.py) の
`_apply_component_analysis`（1853行付近）:

```python
def _apply_component_analysis(self, result: ComponentSplitResult, *, confirmed: bool = False) -> None:
    self._component_analysis = result
    self._component_analysis_signature = self._animation_split_signature()
    self._component_assignment_confirmed = confirmed   # ← 既定 False。結果が resolved でも落とす
```

`analyze_component_assignments` は `confirmed` を渡さずにこれを呼ぶ（1916行・1968行）。
そのため、**中身がまったく同じ resolved な結果で上書きしても確定が外れる**。

### 副因: 案内文の分岐順が実態と合っていない

同じメソッドの1866行付近:

```python
elif self._component_assignment_overrides:
    self.animation_component_assignment_status.setText(
        "割り当てを変更しました。再度割り当てを確定してください。"
    )
elif result.status == "resolved":
    self.animation_component_assignment_status.setText(
        "パーツをコマへ分けました。確定するとコンパイルできます。"
    )
```

`_component_assignment_overrides` は「利用者が自動判定と違う割り当てを選んだ差分」を保持する辞書で、
**確定後も残り続ける**。そのため何も変更していなくても「割り当てを変更しました」と表示される。
`resolved` を見る分岐はその後ろなので到達しない。

## 修正の方針

**再解析の結果が `resolved` で、かつ割り当てが直前の確定内容と同一なら、確定を維持する。**

- `_apply_component_analysis` に「前回と同じ確定済み内容か」を判定する責務を持たせる。
  比較対象は少なくとも「各成分の `component_id` → `frame_id` の対応」と `cells`。
  オーバーレイ画像や `reason` のような表示用の値では比較しない。
- 案内文の分岐は「実態」を見る順に直す。`_component_assignment_overrides` が空でないことは
  「変更された」ことを意味しない。**確定後に変更されたか**で判定すること
  （例: 確定時点の割り当てスナップショットを持ち、それとの差分で判定する）。
- 割り当てが実際に変わったとき、および分割方式・列数・行数・元絵が変わったときは
  **これまでどおり確定を落とす**。ここを緩めてはいけない。既存の
  `test_main_window_component_assignment_change_requires_reconfirmation`
  （`tests/test_gui.py`）が守っている挙動なので、必ず緑のまま通すこと。

## あわせて直すもの

### 潜在不具合: コンパイル側の復旧経路が自己矛盾している

`compile_image` の2728〜2735行付近:

```python
if self._component_analysis_signature != current_signature:
    self.analyze_component_assignments(sync=True)      # ← ここで確定が落ちる
if self._component_analysis is not None:
    if self._component_analysis.status != "resolved" or not self._component_assignment_confirmed:
        self.status.setText("未解決のパーツがあります。色分けを確認して割り当てを確定してください。")
        return
```

署名がずれていたら再解析して直そうとするのに、その再解析自身が直後に要求する確定フラグを落とすため、
**この復旧経路は絶対に成功しない**。現状は「署名がずれる操作は必ず確定も落とす」ため表面化しないが、
主因を直すと同時にここも整合させること。

## 触ってはいけないもの

- **分割アルゴリズムそのもの**。`src/pixel_tile_compiler/sheet/component_split.py` と
  `src/pixel_tile_compiler/sheet/alpha_projection.py` は正しく動いている（下記「確認済み」参照）。
  今回の不具合はGUIの状態管理に閉じている。**解析結果を変えてはいけない。**
- 他の分割方式（`fixed_grid` / `alpha_gap_auto` / `row_alpha_gap` / `hybrid`）の挙動。
- 新規依存の追加。Pillow・NumPy・PySide6・標準ライブラリのみ。
- 日本語UI。文言を英語化しない。

## 壊れていないと確認できていること（再調査は不要）

- **分割の中身は劣化しない。** 2回目の解析でも成分の割り当て `{1:'F1', 2:'F2', 3:'F1'}` と
  cells `((0,(3,5,28,34)), (1,(38,5,52,25)))` は1回目と完全一致した。
- **コンパイル出力もバイト一致。** 同じ素材・同じ設定で2回通し、生成された36ファイルすべての
  SHA-256が一致した。
- 同じ素材の連続再解析（確定を挟まず3回）、素材の差し替え、分割方式の往復（別方式へ移って戻る）は
  いずれも正しく動く。
- `analyze_component_split` にモジュールレベルのキャッシュやグローバル状態はない。入力に対して純粋。

## 副次的な論点（今回直すかは判断して報告すること）

**「解析し直す」が独立したやり直しにならない。**
`analyze_component_assignments` は `assignments=self._component_assignment_overrides` を
解析へ渡すため（1892行・1908行）、2回目の解析には前回の手動割り当てがそのまま入力される。
1回目が `needs_assignment` でも2回目は `resolved` で返るのはこのため。

同じ素材で割り当てを白紙からやり直したい場合、前回の判断が残り続ける（元絵を差し替えれば消える）。
これを「引き継ぐのが正しい」と見るか「解析は毎回まっさらであるべきで、引き継ぐなら別の導線が要る」と
見るかは設計判断。**勝手に変えず、どちらが妥当かを根拠付きで報告する**こと。
変える場合はGUI文言の追加が必要になるので、仕様書の更新が先。

## 最初に確認すること

1. `AGENTS.md` と適用スキル（`tdd-first` / `fable-judgment` / `ui-design-system`）を読み、
   現在の差分を確認する。他作業の変更を保全する。
2. 次を読む。
   - `src/pixel_tile_compiler/gui/main_window.py` の
     `_apply_component_analysis` / `analyze_component_assignments` /
     `confirm_component_assignments` / `_animation_split_signature` /
     `_on_component_assignment_changed` / `_invalidate_component_split` / `compile_image`
   - `src/pixel_tile_compiler/sheet/component_split.py` の `analyze_component_split`
     （読むだけ。変更しない）
   - `tests/test_gui.py` の `_component_assignment_source` /
     `_prepare_component_assignment_window` / `_assign_component_rows` と、
     `test_main_window_component_assignment_change_requires_reconfirmation`
3. 仕様の正本は `docs/spec/animation_component_split_spec.md`。
   挙動の変更が仕様の記述と食い違う場合は、**仕様書を先に更新してから実装する**。

## 実装の進め方

`tdd-first` に従う。Red → Green → Refactor。

先に書く失敗テスト（`tests/test_gui.py` へ追加。既存ヘルパーを使うこと）:

| テスト名 | 観点 |
|---|---|
| `test_reanalysis_keeps_the_confirmation_when_nothing_changed` | 解析→割り当て→確定→**再解析**の後も `_component_assignment_confirmed` が True のまま |
| `test_reanalysis_keeps_the_compile_path_open` | 上の状態でコンパイルが「未解決のパーツがあります」にならず成功する |
| `test_reanalysis_does_not_claim_the_assignment_changed` | 再解析後の `animation_component_assignment_status` が「割り当てを変更しました」にならない |
| `test_changing_an_assignment_still_requires_reconfirmation` | 割り当てを実際に変えたときは従来どおり確定が外れる（既存テストの補強。緩めないことの担保） |
| `test_changing_the_split_settings_still_clears_the_analysis` | 分割方式・列数・行数の変更で解析結果と確定が破棄される（既存挙動の回帰） |

再現用の素材とGUI操作は `tests/test_gui.py` の既存ヘルパーがそのまま使える。
非同期解析の待ち合わせは `window.wait_for_analysis()`。
**コンパイルの待ち合わせに `wait_for_analysis()` を使わないこと** ——
このメソッドは解析ワーカーしか待たず、コンパイルスレッドは待たない（調査中に踏んだ罠）。
コンパイルは `window._compile_thread is None` になるまで `app.processEvents()` で回すこと。

## 検証

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_gui.py tests/test_component_split.py tests/test_alpha_projection.py -q -p no:warnings
```

1. 上記を緑にしたうえで、全体回帰 `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -p no:warnings`
   （直近の基準値は **479 passed / 1 deselected**、約2分30秒）。
2. **出力が変わらないことを実測で示す。** 同じ素材・同じ設定で2回コンパイルし、
   生成PNGすべてのSHA-256が一致することを確認して報告に含める。
3. 編集した日本語ファイルに U+FFFD が混入していないことを確認する。
4. GUIはoffscreen（`QT_QPA_PLATFORM=offscreen`）で確認できる。
   ネイティブ操作での確認が未実施ならその範囲を明記する。

## 注意

- **コミット・プッシュはユーザーが言うまでしない。**
- ライセンス監査（`python scripts/generate_licenses.py --check`）は**この修正の前から exit 1**。
  今回の変更が原因ではないので、直そうとしないこと。新規ファイルを追加した場合のみ
  `python scripts/generate_licenses.py` で目録を再生成する。
- 報告は日本語で。変更ファイル・テスト結果・判断した点と不確実な点・残作業を書く。
