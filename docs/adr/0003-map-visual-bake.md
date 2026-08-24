# ADR-0003: Logical MAP-first Map Visual Bake

- Date: 2026-08-24
- Status: Proposed（E2E検証待ち）
- Scope: 論理MAPを入力にしたMAP専用Visual生成・正規化・64×64分割
- Nature: 仮説検証のための設計判断。完成版の実装契約ではない
- Related: [ADR-0001 MAP-first Compiler](./0001-map-first-compiler-experiment.md)、[ADR-0002 Surface / Network / Transition](./0002-surface-network-transition-semantic-edge-contract.md)

## 1. 背景

`pixelart-compiler`では、単体Tileを良く作ってからMAPへ並べる方式だけでは、
MAP全体の明度、色調、密度、道や川の連続性を安定して表現できないことを確認してきた。
ADR-0001では高解像度MAP全体を先に解析し、周辺Contextを持ったまま64×64へ変換する
MAP-first方式を次の検証対象として採用した。

一方、実際のMAP制作では、先に汎用Tilesetを選んでからMAPを組むとは限らない。
MAP上のギミック、道・川の経路、森の範囲、オブジェクト配置を先に確定し、
そのMAPの意匠に合わせてVisualを生成する方が、作者の意図に近い場合がある。

したがって、MAP専用Assetについては、次の順序を検証する。

```text
Logical MAP / ギミック / 配置
        ↓
MAP Visual Intent / 構造マスク
        ↓
一枚の高解像度MAP画像を生成
        ↓
MAP全体サイズへ正規化
        ↓
64×64単位へ分割
        ↓
MAP専用Visual Asset Package
```

## 2. 判断

### 2.1 論理MAPを正本とする

MAPの地形、経路、領域、マーカー、オブジェクトなどの論理情報を先に確定する。
生成画像から論理MAPを逆算したり、生成画像の都合で地形・ギミックを変更したりしない。

画像生成の失敗や意匠の不一致は、Visual側のValidation／Human Reviewで報告する。
Logical MAPを黙って書き換える自動補正は行わない。

### 2.2 一枚絵をMAP専用Visualの主入力とする

画像生成には、単なるMaterial Promptだけでなく、可能な範囲で次の構造情報を渡す。

- MAPの幅・高さと論理セル境界
- セルごとの地形・意味
- 道・川などの経路と接続
- 森・水域などのRegion
- 重要オブジェクトのアンカー位置
- 画風、Palette、光源、密度などのVisual Profile

生成画像にはグリッド線を表示しない。グリッドは構造条件であり、完成画像の意匠ではない。

### 2.3 64×64は解像度ではなく配備単位として維持する

論理MAPが`width × height`セルで、1セルのゲーム上のVisual単位が64pxの場合、
正規化後のMAP画像は次のサイズにする。

```text
canvas_width  = width  × 64
canvas_height = height × 64
```

64pxを先に16pxなどへ縮小してから組み立てる方式は採用しない。
64×64分割物は、汎用Tilesetではなく、完成したMAP画像をエンジンやAsset Packageの
入力形式へ変換したMAP専用Chunkとみなす。

### 2.4 汎用Tilesetとは別の経路として保持する

汎用Material Library、Surface / Network / Transitionの再利用可能なSource、
汎用Tileset Compilerは廃止しない。

ただし、一枚の生成MAPから得た64×64画像を、個別の汎用Terrain Assetへ自動昇格しない。
道の曲率、川岸、森の境界、周辺MAPの文脈がMAP固有の情報として混ざるためである。

```text
Reusable Tileset path:
  Material / Topology / Edge Contract → reusable 64×64 families

Map-specific path:
  Logical MAP → generated full MAP → MAP-specific 64×64 chunks
```

## 3. 入力・出力の境界

コンパイラーは特定のMAP Editorや特定のProject Repositoryへ直接依存しない。
外部Editorからは、最低限次の情報を持つPortableなVisual Intentへ変換して渡す。

```json
{
  "map_width": 10,
  "map_height": 10,
  "cell_size": 64,
  "cells": [],
  "regions": [],
  "paths": [],
  "anchors": [],
  "visual_profile": {}
}
```

上記はE2E実験用の概念形であり、現時点で固定する保存Schemaではない。

生成・取込み後の成果物は、少なくとも次の情報を追跡可能にする。

```text
map_visual_intent.json
source_raw.png
map_normalized.png
chunks/<x>_<y>.png
manifest.json
validation/
review/
```

`manifest.json`には、Visual IntentのHash、元画像のSHA-256、正規化後画像のHash、
MAPサイズ、cell_size、crop policy、generator/model、promptまたはrequest ID、
提供される場合はseed、生成日時、分割結果を記録する。

論理MAPのSnapshotやVisual Intentが変わった場合、既存VisualはStaleとして扱う。
自動で上書きせず、明示的な再生成・再取込みを要求する。

## 4. 画像生成への期待値

画像生成が追従すべき優先順位を次のように置く。

1. MAP全体の大域構図
2. 地形Regionと道・川の大まかな経路
3. 重要なオブジェクトの位置とシルエット
4. Materialの密度、明度、彩度、画風
5. 個々の樹木・岩・細部の配置

Promptだけではセル単位の厳密な追従を保証できない。構造マスクや参照画像を使っても、
生成結果が論理MAPと一致するとは仮定しない。

## 5. 最小E2E検証

実験量を増やす前に、固定した小さなMAPで次を確認する。

### Round 0: 経路確認

- 1枚の固定Logical MAPを用意する
- 草原・道・川など、少数の意味だけを含める
- 1枚の実生成画像を取り込む
- MAP全体サイズへ正規化する
- 64×64へ分割し、全体画像と分割後画像を比較する

### Round 1: 生成揺らぎ確認

Round 0の経路が通った後、同じVisual Intentから少数の独立生成結果を比較する。
候補数はRound 0の成果物を見て決め、自動ランキングだけで採用を確定しない。

最低限、次を観測する。

- MAP画像の幅・高さが`width × 64`、`height × 64`になっている
- Chunk数が`width × height`で、各Chunkが64×64である
- Chunkの欠落、重複、順序ずれがない
- 道・川・Regionが意図した大域構造を保っている
- MAP固有の境界や細部が、分割によって不自然に壊れていない
- Logical MAPやVisual Intentが生成処理によって変更されていない

寸法・Hash・Chunk完全性は自動検証し、意匠・意味・自然さはHuman Reviewを併用する。

## 6. 非目標

- 汎用Tilesetの廃止
- 生成画像からLogical MAPを自動生成する逆方向Pipeline
- 生成結果による地形・ギミックの自動変更
- 64×64 Chunkの汎用Terrain Assetへの自動昇格
- 現時点でのEnadri-SRPG-Makerへの直接依存
- Domain上の`layer`、`height`、透明Overlayなどの新しい論理概念の追加
- 画像生成の完全なセル単位一致の保証

透明背景のOverlayや、Runtimeで一枚絵を直接表示するかChunkを表示するかは、
最小E2Eの結果を見て別途判断する。

## 7. 未検証・保留

- 実際の画像生成AdapterへVisual Intentをどの形式で渡すか
- 構造マスクが生成結果へ与える追従効果
- MAP全体生成と64×64分割後のsemantic fidelity
- Chunk境界における地形・光・色・特徴の連続性
- 一枚絵をRuntime表示する場合の性能とAsset管理
- 生成AssetのLicense、provenance、配布可否

本ADRは、上記をE2Eで観測するための方針を固定する。実装完了や品質保証を意味しない。

## 8. 参照

- [ADR-0001: MAP-first Compilerへ向けた実験知見](./0001-map-first-compiler-experiment.md)
- [ADR-0002: Surface / Network / Transition / Semantic Edge Contract](./0002-surface-network-transition-semantic-edge-contract.md)
- [README: MAP-first context compiler](../../README.md)
- [Pixel Tile Compiler仕様](../spec_v1.md)
