# Pixel Tile Compiler 作業ハンドオフ

最終更新: 2026-08-27

この文書は、次のセッションが最初に読む作業引き継ぎである。MAP全体の画像生成・分割実験に加え、
キャラクター画像を抽出して64×64のピクセルアートへコンパイルする実験の現在地と再現手順も記録する。

## 1. 現在地

- リポジトリ: `E:\ena-dri\repos\pixelart-compiler`
- 作業開始時のHEAD: `80cf85b add material source library and pixel studies`
- 作業開始時点で`main`と`origin/main`は一致
- 作業ツリーには、今回の作業以前からの実装・E2E成果物・未追跡ファイルが多数ある
- それらを削除、reset、checkout、広範囲stageしないこと
- 今回追加した判断は[`ADR-0003`](./adr/0003-map-visual-bake.md)に記録した

既存の主要な研究系統は次のとおり。

```text
Material Source Library
Palette Budget / Architecture
Surface / Network / Transition
Logical MAP → context-aware 64×64 compile
Generation-first Sheet → deterministic crop / split
```

これらは同じ問題ではない。再利用可能な汎用Tileset経路と、MAP専用Visual経路を混同しない。

## 2. 今回固定した仮説

MAPのギミックや配置が先に決まっているなら、画像生成をMAPの後段へ置く方が作者意図に近い。

```text
Logical MAP
  ↓
Visual Intent / semantic mask / anchors
  ↓
一枚の高解像度MAP画像
  ↓
MAP全体サイズへ正規化
  ↓
64×64 chunks
```

ここでの64×64は、画像生成時に解像度を落とす指定ではない。ゲーム側のセル／配備単位であり、
生成されたMAP画像を最後に分割する。

生成画像はMAP専用であり、汎用Tilesetへ自動昇格させない。Logical MAPは常に正本で、
画像が論理MAPを変更してはいけない。

## 3. 最小実験の進め方

### Round 0: 1枚で経路を通す

1. 小さな固定MAPを1枚選ぶ。まずは草原・道・川など少数の意味に限定する。
2. MAPのセル、Region、経路、重要位置をPortableなVisual Intentへ変換する。
3. 実際の画像生成結果を1枚取り込む。疑似画像・既存画像の色変換・決定論的偽候補は使わない。
4. 入力画像を不変コピーとして保存する。
5. MAP全体を`map_width × 64`、`map_height × 64`へ正規化する。
6. 64×64へ決定論的に分割し、全体画像とChunkの比較成果物を保存する。

### Round 1: 揺らぎを見る

Round 0で寸法・分割・成果物の経路を確認してから、同一Visual Intentによる少数の独立生成結果を
比較する。候補数や条件はRound 0の成果物を見て決める。最初から32候補などへ拡張しない。

## 4. 期待する成果物

実験ディレクトリは次の形を候補とする。実装時に既存Asset Package契約との重複を確認する。

```text
e2e/map_visual_bake/<study_id>/
├─ map_visual_intent.json
├─ generation_request.json
├─ source_raw.png
├─ map_normalized.png
├─ chunks/
│  ├─ 0_0.png
│  └─ ...
├─ manifest.json
├─ validation/
└─ review/
```

`manifest.json`には、Visual Intent Hash、元画像SHA-256、正規化後Hash、MAPサイズ、
cell_size、crop policy、generator/model、request ID、seed（提供される場合）、
生成時刻、Chunk一覧を記録する。

## 5. 受入観測

### 自動で確認するもの

- `map_normalized.png`の寸法が`width × 64`、`height × 64`
- Chunk数が`width × height`
- すべてのChunkが64×64
- 欠落・重複・座標ずれがない
- 入力画像と生成物のHashがmanifestと一致する
- Logical MAPとVisual Intentの入力データが生成処理で変更されていない

### Human Reviewで確認するもの

- 草原・道・川などの大域配置が意図通りか
- 道や川がセル境界で不自然に切れていないか
- 森や水域などのRegionが過度に細切れになっていないか
- 全体画像では自然でも、64×64分割後に違和感が増えないか
- 色、明度、彩度、密度がMAP全体で統一されているか

自動metricだけで採用候補を確定しない。生成結果が論理MAPと一致しない場合は、
失敗理由を記録して次の構造マスク／Prompt／生成条件の判断材料にする。

## 6. 実装時の境界

- 特定のEnadri-SRPG-Maker Project形式へ直接依存しない。最初はPortableなJSON／画像マスク境界で試す。
- `compile-map`の既存context-aware経路と、新しい「一枚絵生成後の分割」経路を同一視しない。
- 既存の`generation`／`sheet`実装を再利用できるか確認する。ただし、既存Sheet契約がMAP専用の
  Visual IntentやRegion追従を表せない場合は、無理に流用せず境界を分ける。
- ChunkはMAP専用の派生物であり、Material Libraryの汎用Sourceへ自動登録しない。
- 透明Overlay、Runtimeでの一枚絵直接表示、Staleの再生成UIは最小実験後に判断する。
- 新しい`layer`や`height`の論理概念は追加しない。

## 7. 次に読むもの

1. [`ADR-0003`](./adr/0003-map-visual-bake.md)
2. [`ADR-0001`](./adr/0001-map-first-compiler-experiment.md)
3. [`ADR-0002`](./adr/0002-surface-network-transition-semantic-edge-contract.md)
4. `docs/generation_first_tileset_compiler.md`（作業ツリーにある既存設計・実装候補。commit状態を確認してから参照する）
5. `src/pixel_tile_compiler/generation/`、`src/pixel_tile_compiler/sheet/`

## 8. 注意

このハンドオフ作成時点では、MAP EditorからVisual Intentを取り込む実装、実画像生成Adapterとの接続、
MAP専用一枚絵のE2E、Human Reviewは未検証である。文書の存在を実装完了や品質保証と解釈しない。

## 9. キャラクター・ピクセルアート化実験（2026-08-27）

### 9.1 目的と判断

高精細な元絵をそのまま64×64へ縮小する方法も採用候補だが、キャラクター用コンパイラーでは、
輪郭・色数・細部を制御しながら抽象化することで、元絵の画風が変わってもゲーム内で並べたときの違和感を抑えられる。
現時点のユーザー判断は「ドット絵として十分成立する。高精細版も用途によっては採用候補」である。

### 9.2 固定したコンパイル設定

キャラクター／オブジェクトはMAP用のregion-aware経路と分け、次の設定を使う。

```text
width=64, height=64
palette_budget=32
tile_mode=object
semantic_provider=rule
seam_mode=inspect
repeat_opt_enabled=false
dither=off
background_mode=alpha
pixelization_mode=nearest
outline_color=black
work_size=256
smoothing_enabled=true
seed=42
debug_enabled=false
quantize_enabled=true
```

`nearest`は細部保持を優先した最近傍縮小で、透過背景を維持する。入力キャンバス端に可視ピクセルが接している場合は、
接している側だけ2pxの透明余白を追加してから縮小する。黒輪郭は可視領域の外側1pxへ追加する。
32色未満では細部の欠落が目立ちやすく、現在は32色を基準とする。

### 9.3 シート分割の再現手順

1. 入力シートをRGBAで読み、alpha `>32`を可視ピクセルとして8近傍連結成分を求める。
2. 面積20,000以上の主成分だけを採用し、各成分の外接矩形で切り出す。隣接キャラを固定セルの矩形で巻き込まない。
3. 切り出した成分を最大辺60pxになるよう最近傍で縮小し、64×64透明キャンバスの中央へ配置する。
4. 各64×64画像を`PixelTileCompiler`へ渡し、`final.png`を採用候補とする。
5. `overview_4x_nearest.png`で俯瞰確認し、各`final.png`の寸法・透過・輪郭端切れを検証する。

4×4シートでは、キャラごとの上端位置が異なるため、主成分のY座標だけで並べ替えない。画像のセル位置（行・列）で順序を確定する。

### 9.4 現在の生成済み成果物

生成物はリポジトリ外のGoogle Drive配下に保存している。生成元シート、分割済み64×64入力、コンパイル済み出力を分け、
前回版を上書きしない。

```text
G:\マイドライブ\習作\モック\asset\pixelart-compiler-output\
├─ character-source-recut-v2
├─ character-compiled-v4
├─ アリアx16-source-recut-v1
├─ アリアx16-compiled-v1
├─ コミリアx8_ミリオx8-source-recut-v2
├─ コミリアx8_ミリオx8-compiled-v2
├─ レサルドx4_トルクスx4_プルディアx4_プロビオx4-source-recut-v1
└─ レサルドx4_トルクスx4_プルディアx4_プロビオx4-compiled-v1
```

現行の正本は次の`64x64`配下にある。正本へ入れるのはコンパイル済み`final.png`のみで、debug・metadata・比較画像は入れない。

```text
G:\マイドライブ\習作\モック\asset\64x64\
├─ 汎用キャラ          82枚
├─ アリア              16枚
├─ コミリア             8枚
├─ ミリオ               8枚
├─ レサルド             4枚
├─ トルクス             4枚
├─ プルディア           4枚
└─ プロビオ             4枚
```

合計130枚。各正本PNGは64×64、alpha値は`0/255`のみ。今回のキャラクター追加分も、出力元`final.png`とのSHA-256一致を確認してから正本へコピーした。

### 9.5 実装上の入口

- `src/pixel_tile_compiler/pixelizer/character.py`: 最近傍縮小、端余白、輪郭
- `src/pixel_tile_compiler/config.py`: `pixelization_mode`、`outline_color`、色条件設定
- `src/pixel_tile_compiler/pipeline/compiler.py`: キャラクター経路の選択と最終輪郭
- `src/pixel_tile_compiler/cli.py`: `compile`の`--pixelization nearest`、`--outline black|white`
- `tests/test_character_pixelizer.py`: キャラクター経路、透過、端余白、輪郭のテスト

CLIで単体を再現する場合の最小例：

```powershell
pixel-tile compile character.png --output output/character --palette 32 --tile-mode object --pixelization nearest --outline black --no-repeat-opt
```

### 9.6 次回の運用

- 新しいキャラシートも、まず`pixelart-compiler-output`へ`source-recut-vN`／`compiled-vN`として出す。
- 俯瞰で採用状態を確認してから、ユーザーが明示した場合だけ`64x64`へ`final.png`を追加する。
- 元絵の切断位置が不自然な場合は、コンパイラーで補正せず元シートを直して再コンパイルする。
- 画風の比較では、正本候補の64×64版と高精細絵の直接縮小版を別軸として扱う。
