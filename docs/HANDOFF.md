# Pixel Tile Compiler 作業ハンドオフ

最終更新: 2026-08-24

この文書は、次のセッションが最初に読む作業引き継ぎである。今回の目的は、
Logical MAPを先に完成させ、その意匠を元にMAP全体の画像を生成し、必要に応じて
64×64へ分割する実験を小さく開始すること。

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
