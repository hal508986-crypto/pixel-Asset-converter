# ADR-0001: MAP-first Compilerへ向けた実験知見

- 日付: 2026-08-22
- ステータス: Accepted（次の仮説検証方針として採用）
- 対象: Pixel Art MAP Compiler
- 性質: 実験知見。完成版の実装契約ではない

## 1. 背景と当初の仮説

高解像度の美麗画像をPixel Artへ変換する処理は、単純な縮小・減色ではなく、次のVisual Compilerとして扱えるのではないかと仮説を置いた。

```text
高解像度画像
  ↓
構造解析
  ↓
意味・重要度解析
  ↓
情報減算
  ↓
抽象化
  ↓
制約付きPixel化
  ↓
Pixel Art Grammar補正
```

責務は次のように分離する。

| 担当 | 責務 |
|---|---|
| LLM / VLM | 何を残すか、何を捨てるか、何を誇張するか、何であるか |
| 決定論的アルゴリズム | どのpixelに置くか、色の量子化、cluster整理、境界処理 |

最終Pixel ArtそのものをLLMに描かせず、画像を意味構造・IRへ変換し、決定論的アルゴリズムでPixel Artへ再符号化する。

## 2. MVPで確認した実装範囲

Pythonのみで64×64 SRPG MAP Tile Compilerを実装した。実際の処理系列は次のとおり。

```text
load
→ normalize
→ background
→ smoothing
→ edges
→ SLIC regions
→ structural / semantic analysis
→ Tile IR
→ region-aware pixelize
→ palette quantize
→ cluster cleanup
→ seam / repeat metrics
→ export
```

GUIでは、64×64表示、nearest-neighbor、整数ズーム、pixel grid、3×3反復preview、palette確認、metrics表示まで確認できた。

## 3. 観測した実験結果

### 3.1 美麗画像からのPixel Art化

森林画像の初期試験では、細かな葉・樹皮・陰影・高周波テクスチャが削減され、大きな樹冠cluster、暗部、幹、地面の色面へ再構成された。

これは単純縮小・単純減色とは異なるため、「高解像度画像を意味・構造ベースで減算・抽象化し、Pixel Artへ再符号化する」仮説は、現時点では成立する可能性が高い。

### 3.2 Repeatable Tile

森林画像をrepeatable tileとして3×3配置した結果、tile境界、中央ランドマーク、64px周期の反復感が強く現れた。

ここから、次の2つは別の問題だと判明した。

- 継ぎ目がないこと
- 繰り返し感がないこと

repeatable専用最適化として、seam補正、wrap-aware処理、center dominance抑制、repeatability metricを導入した。森林試験では次の改善を観測した。

```text
periodicity risk: 0.057529 → 0.039304
```

見た目でも境界感は改善した。一方、強いランドマークを持つ入力では64px周期の構図自体が反復するため、完全には解決しない。

### 3.3 草原・街道MAP

複数の草原tileと街道tileを独立に生成・コンパイルし、4×5 MAPへ配置した。

- 単体tileはそれぞれ成立した
- directional roadは上下方向の連続構造として認識できた
- MAP全体では草原tileの色・模様・密度差が64×64の四角い区画として認識された

したがって、単体で良いtileを複数作って単純配置しても、自然なMAPになるとは限らない。

## 4. 判断した知見

### 4.1 単体品質とMAP品質は別である

評価対象には `Tile Quality` だけでなく、隣接関係を含む `Tile Relationship Quality` が必要である。

### 4.2 seamとcontinuityは別である

隣接pixelが一致していても、明度、texture density、dominant cluster、地形の流れが異なればtile境界は認識される。pixel-level seamだけでなく、次の知覚・意味的連続性を評価する必要がある。

- palette continuity
- brightness continuity
- texture-density continuity
- cluster-size continuity
- edge-direction continuity
- semantic continuity

### 4.3 repeatable / directional / objectは別の問題である

| モード | 主な対象 | 重視するもの |
|---|---|---|
| repeatable | 草、砂、床、水 | 面の連続性、周期感の抑制 |
| directional | 道、川、崖、壁 | 方向、接続構造、端部の意味 |
| object | 木、岩、建物 | 単体ランドマークとしての成立 |

同一アルゴリズムですべてを処理すると破綻しやすい。既存のtile mode分離は維持する。

## 5. 決定: MAP-first Compilerを次の検証対象にする

MAP tileを個別生成し、後から組み合わせる方式には構造的限界があると判断した。次の実験では、MAP全体を先に生成・設計し、その大域的文脈を保持したまま各tileへ分割・コンパイルする方式を検証する。

```text
高解像度MAP全体
  ↓
MAP全体構造解析
  ↓
地形・道・森林・水などのSemantic解析
  ↓
共有palette / style解析
  ↓
MAPをgrid分割
  ↓
各tileについて周辺contextを取得
  ↓
context付き局所Compiler
  ↓
中央64×64だけ確定
  ↓
全tile再結合
  ↓
MAP全体補正
```

この決定は現行MVPの単体tile compilerを直ちに置き換えるものではない。MAP用途に対する次の実験・設計方向として採用する。

## 6. Context付きTile Compile

中央tile Eを処理する場合、Eだけを入力にしない。

```text
A B C
D E F
G H I
```

のように周辺領域を含めて解析し、Eの64×64だけを最終出力する。これにより、道の流れ、草地の濃淡、森の密度、境界、隣接tileのpaletteを考慮できる。

## 7. Shared Palette

各tileを完全独立で減色すると、grass Aが黄緑中心、grass Bが青緑中心になるような微妙なpalette差が生じ、境界が見えやすくなる。

MAP-first方式では、先に `MAP Global Palette` を決め、全tileが共有する方式を検討する。候補は次の2つ。

- MAP全体で24色程度を共有
- terrain family単位でpaletteを共有

paletteの具体的な色数・分割単位は次の実験で決める。

## 8. MAP専用Asset

MAP-first方式で生成される64×64 tileは、必ずしも汎用tilesetである必要はない。

```text
grass_01
grass_02
grass_03
```

のようなMAP専用tile群を許容する。汎用tileset生成とは別に、章・MAP単位で最適化されたasset群生成を成立させる。SRPGではこの用途が有効と考える。

## 9. 技術的な現時点の整理

| 状態 | 判定 |
|---|---|
| 美麗画像 → 情報減算 → Pixel Art | 成立しそう |
| Pixel Art → repeatability optimization | ある程度成立。risk改善を観測済み |
| 単体tile群 → 単純配置 → 自然なMAP | 不十分 |
| MAP全体 → context付きcompile → 再構成 | 次に検証 |

## 10. 次の実験計画

4×5 MAPを対象に、高解像度状態でMAP全体を作成し、意味的に4 columns × 5 rowsへ分割する。各tileは周囲のcontextを参照しながら64×64へ変換し、最終的に256×320pxへ再構成する。

比較対象は次の3方式とする。

| 方式 | 内容 |
|---|---|
| A | MAP全体を単純に256×320へ縮小 |
| B | MAP全体を分割し、各tileを独立Compilerへ入力 |
| C | MAP全体を分割し、context付きCompilerへ入力 |

最低限、次の観測を比較する。

- MAP全体の視覚的な区画感
- 道・地形の方向性
- tile間のpalette / brightness / texture continuity
- 3×3および4×5配置時の周期感
- 64×64単体の地形認識性

## 11. 未解決事項

- contextの取得範囲を隣接1tile、3×3、または可変にするか
- 中央64×64の確定方法と重複領域の扱い
- MAP Global Paletteとterrain family paletteのどちらを採用するか
- MAP全体補正をどの段階で行うか
- semantic continuityをどのメトリクスで測るか
- directional tileの接続パターンとautotileへの拡張方法

## 12. 結論

Pixel Art MAP生成は「tile画像生成問題」ではなく、「MAP全体の視覚構造を64×64単位の離散表現へコンパイルする問題」として扱う方が自然である、という仮説へ更新した。

```text
LLM / VLM = 意味、優先順位、抽象化方針
Algorithm = 座標、pixel、palette、cluster、edge、seam
```

今後は、単体tileの品質向上だけでなく、MAP全体の文脈、共有palette、tile間continuityをCompilerの入力・評価対象へ昇格させる。

## 13. 参照成果物

- [MVP仕様書](../../spec_v1.md)
- [4×5試験MAP](../../e2e/maps/test_map_4x5.png)
- [4×5 MAPレイアウト](../../e2e/maps/test_map_4x5_layout.json)
- [採用tileとmetrics](../../e2e/maps/used_tiles.json)
- [repeatable改善の実装](../../src/pixel_tile_compiler/tile/repeatability.py)

