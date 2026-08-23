# ADR-0002: SRPG用Pixel Art Tileset生成を Surface / Network / Transition の責務分離と Semantic Edge Contract で構成する

- Status: Accepted
- Date: 2026-08-23
- Decision owners: pixelart-compiler
- Scope: 汎用SRPG MAP Tileset生成
- Related experiments:
  - Grass Source Study
  - Forest Canopy Source Study
  - Transition / Network Edge Contract Study

## Context

`pixelart-compiler` では、高解像度画像を単純縮小・減色するのではなく、次のVisual CompilerとしてPixel Art化する方式を検証してきた。

```text
Source Image
↓
Visual / Structural Analysis
↓
Semantic Analysis
↓
IR
↓
Information Reduction
↓
Region-aware Pixelization
↓
Palette Quantization
↓
Pixel Art Grammar
↓
64×64 Pixel Art
```

初期実験では、高解像度の森林画像から、微細な葉、樹皮、高周波texture、細かな明暗を削減しながら、樹冠mass、幹、大きな明暗面、地形として重要な境界を保持できることを確認した。

この結果から、次の基本仮説を採用した。

> Pixel Art化は単なる画像縮小ではなく、限られたpixel予算に対する意味情報の再符号化である。

## Problem

Pixel Art Compiler単体では、64×64の単独画像として成立するMAP Tileを生成できる。しかし、複数TileをMAPとして組み合わせると、単独Tile品質だけでは説明できない問題が発生した。

### 1. 単独Tile品質とMAP品質は一致しない

個別生成したgrass tileを4×5 MAPへ配置した結果、色調差、brightness差、texture density差、dominant cluster差によって、64×64単位のgridが視覚的に現れた。

```text
Good Tile × N
≠
Good Map
```

MAP品質には、`Tile Quality` に加えて `Tile Relationship Quality` が必要である。

### 2. Seamの一致だけでは不十分

隣接辺のRGBやLab色差が小さくても、cluster scale、brightness、texture frequency、semantic structure、feature directionが異なれば境界は知覚される。

したがって、単純なpixel-level seam correctionだけではMAPとしての連続性を保証できない。必要なのは、perceptual continuity、structural continuity、semantic continuityである。

### 3. Materialの種類によって接続問題の性質が異なる

実験を通じて、すべてのMAP素材を同一アルゴリズムで扱うのは適切ではないと判断した。

#### Surface

例: grass、soil、sand、stone、snow、forest canopy

面として広がるmaterial。重要なのはtexture consistency、palette consistency、cluster continuity、periodicity controlである。

#### Network

例: road、river、wall、cliff line

入口・出口・方向・幅など、topologyを持つ構造。重要なのはconnector、direction、feature position、feature width、junction continuityである。

#### Transition

例: grass ↔ forest、grass ↔ dirt road、forest ↔ dirt road、water ↔ land

異なるsemantic/material間を接続する境界。重要なのはmaterial pair、boundary orientation、boundary continuity、transition width、semantic clarityである。

#### Object

例: tree、rock、house、bush、fence object

groundとは独立したoverlay的アセット。本ADR時点では本格実装対象外だが、Surface / Network / Transitionとは別責務として扱う。

## Experimental Findings

### Grass Source Study

8種類のGrass Sourceを比較した。Sourceの性質によって最終Tileset品質に差が生じた。

有望だった条件:

- 適度な局所variation
- 強い中心構図がない
- 大きな低周波patternがない
- tuftが疎〜中密度
- brightness / texture densityが空間的に安定している

問題になった条件:

- dense tuft
- illustrative composition
- 大きなmacro pattern
- 強い自己相関

これにより、Source Materialの品質がTileset品質を大きく左右することが確認された。

### Forest Canopy Source Study

Grassより接続性の難しいContinuous Forest Canopyについて8 Sourceを比較した。

上位:

1. `forest_src_02_uniform_medium` — 0.8646
2. `forest_src_03_uniform_dense` — 0.8576
3. `forest_src_05_medium_clusters` — 0.8539

下位には、dark variation、illustrative、接続しにくいsmall cluster系が入った。

Forest用に以下の指標を追加した。

- `canopy_cluster_scale_score`
- `canopy_fragmentation_score`
- `large_mass_dominance_score`
- `cluster_continuity_score`
- `canopy_mass_break_risk`

良いForest Sourceの条件は、次のものが有望と判断された。

- 中程度の樹冠密度
- 小〜中規模のcluster
- 低〜中brightness variation
- 大きな中心massがない
- clusterが細かく分断されすぎない

Sourceは単に均質であればよいのではなく、位置によって統計的性質が大きく変わらない「stationaryなmaterial」であることが重要である。

### Transition / Network Edge Contract Study

Surfaceとしてgrass、forest_canopy、Networkとしてdirt_road、Transitionとしてgrass ↔ forest、grass ↔ road、forest ↔ roadを実装・比較した。

追加した主要機能:

- Semantic Edge Contract
- road connector masks
- NS / EW / NE / NW / SE / SW
- grass-road transition
- forest-road transition
- grass-forest transition
- contract-aware MAP assembly

同一seed・同一layoutで、次の3方式を比較した。

- `surface_only`
- `independent_handoff`
- `contract_aware`

最終ranking:

1. `road_demo_grass / contract_aware` — 0.983090
2. `road_demo_forest / contract_aware` — 0.982221
3. `transition_demo / contract_aware` — 0.973082

Worst 3はすべて `surface_only` baselineだった。`independent_handoff` は3ケースすべてで契約不一致が発生した。

この結果から、Network / Transitionでは、個別Tileを後から接続する方式よりも、接続契約をSource生成時点から持つcontract-aware方式が有効と判断した。

実験時点の自動検証:

```text
pytest: 56 passed
compileall: pass
git diff --check: pass
UTF-8 replacement character: none
```

品質保証gateについては本実験では実行していない。

## Decision

汎用SRPG MAP Tileset生成アーキテクチャを、以下の責務分離で構成する。

```text
Tileset Specification
        ↓
Material Source Layer
        ↓
Tileset Source Compiler
        ↓
Semantic Edge Contracts
        ↓
Pixel Compiler
        ↓
64×64 Tiles
        ↓
Contract-aware Assembly
```

Tileset Source Compiler内部では、Tileを以下の分類として明示的に扱う。

- Surface
- Network
- Transition
- Object

### Decision 1: Surface / Network / Transition を別責務として扱う

同一生成アルゴリズムへ統合しない。

#### Surface

```text
Material Exemplar
↓
Validation
↓
Patch Sampling
↓
Texture Synthesis
↓
Edge-compatible Source Variants
↓
Pixel Compiler
```

主な最適化対象:

- material consistency
- texture scale
- palette
- low-frequency pattern
- periodicity
- cluster continuity

#### Network

```text
Network Topology
↓
Connector Mask
↓
Surface Material + Network Material
↓
High-resolution Source Tile
↓
Pixel Compiler
```

画像生成AIに完成した道路Tileを直接生成させる方式は基本としない。roadの場合、NS、EW、NE、NW、SE、SWなどのtopologyを先に決定し、そのmaskへroad materialを適用する。

原則は次のとおりとする。

> Structure First, Texture Second

#### Transition

```text
Material A
+
Material B
+
Boundary Specification
↓
Transition Source Synthesis
↓
Pixel Compiler
```

Material AとMaterial Bを単純に隣接させるのではなく、Transition自体を独立したTile Familyとして持つ。

### Decision 2: Edge ContractをSemantic Interfaceとして扱う

Edge Contractを単純な文字列IDやRGB境界一致として扱わない。EdgeはTile間のinterfaceである。

最低限、次を表現可能にする。

- semantic
- material
- feature_type
- feature_center
- feature_width
- brightness band
- texture density

概念例:

```json
{
  "semantic": "network",
  "material": "dirt_road",
  "feature_type": "road",
  "feature_center": 0.5,
  "feature_width": 0.22
}
```

このContractを利用して、隣接可能性、network continuity、transition continuity、MAP assemblyを制御する。

### Decision 3: Source Materialを第一級の入力資産として扱う

画像生成AIに完成Tileを直接生成させる方式を主経路としない。画像生成AIの主責務はMaterial Exemplarの生成とする。

例:

- grass_master
- forest_canopy_master
- dirt_master
- stone_master

Source MaterialはCompiler投入前にValidationする。

### Decision 4: Source Acceptance Gateを設ける

Sourceを無条件で利用しない。Material Typeごとに、`accepted`、`warning`、`rejected`を判定可能にする。

共通評価候補:

- `brightness_spatial_variance`
- `color_spatial_variance`
- `texture_density_variance`
- `center_dominance_score`
- `large_landmark_risk`
- `low_frequency_pattern_strength`
- `autocorrelation_peak_risk`

Material固有指標も許可する。Forestでは、`canopy_cluster_scale_score`、`canopy_fragmentation_score`、`large_mass_dominance_score`、`cluster_continuity_score`、`canopy_mass_break_risk`を用いる。

### Decision 5: Materialごとに最適な情報周波数を持たせる

すべてのmaterialに同じtexture densityやperiodicity penaltyを適用しない。

```text
grass:
  preferred_frequency = low_to_medium
  periodicity_penalty = high
forest:
  preferred_frequency = medium
  periodicity_penalty = high
brick_wall:
  preferred_frequency = structured_medium
  periodicity_penalty = low
roof:
  preferred_frequency = structured_medium
  periodicity_penalty = low
```

反復がsemanticとして自然なmaterialに対して、一律にperiodicityを悪とみなさない。

### Decision 6: Pixel Compilerは最終描画責務を維持する

Tileset Source CompilerがPixel Art自体を描画しない。責務は以下の通り固定する。

```text
LLM / Source Generator
= 意味・設計・Material Exemplar

Tileset Source Compiler
= Topology・Transition・Edge Contract・High-resolution compatible sources

Pixel Compiler
= 情報減算・抽象化・Palette・Pixel Cluster・64×64 rasterization

MAP Assembler
= Edge Contractに基づく配置
```

## Rationale

### 1. 各問題を適切な抽象度へ分離できる

画像生成AIへ「接続可能な64×64の道路Pixel Artを作れ」とすべてを要求する必要がない。

```text
LLM:       NS roadである
Algorithm: NS maskを生成
Material:  road textureを適用
Compiler:  64×64へ変換
```

### 2. 決定論的に接続性を保証しやすい

Network topologyやEdge Contractをalgorithm側で管理するため、生成AI特有の座標・接続ズレに依存しにくい。

### 3. Source Qualityを独立して改善できる

Pixel Compilerを変更せず、Source Prompt、Source Validation、Material Exemplarだけを改善できる。Grass / Forest Studyでこの効果が確認された。

### 4. 再利用可能なTilesetへ拡張できる

`grass family`、`forest family`、`road family`、`transition family`を独立資産として生成できるため、複数MAPへ再利用可能である。

## Alternatives Considered

### Alternative A: Image Generatorに完成Tileを個別生成させる

却下。

理由:

- variant間drift
- palette drift
- texture density drift
- 接続性保証困難
- 64px grid感が出る

### Alternative B: 1枚のTileだけをrepeatする

却下。

理由:

- 強いperiodicity
- macro pattern
- 同じランドマークの繰り返し

### Alternative C: Seam補正だけで解決する

却下。

pixel-level seamが小さくても、semantic / brightness / texture / clusterの不連続が残る。

### Alternative D: MAP全体を生成してContext付きで分割する

却下ではなく、別用途として維持する。これはMAP Compilerとして有効だが、Tile再利用性が低いため、Reusable Tileset Compilerの代替にはしない。将来的に両方を併存可能とする。

## Consequences

### Positive

- Surface / Network / Transitionごとの責務が明確になる
- 接続性をalgorithmで制御できる
- Source Prompt改善とPixel Algorithm改善を分離できる
- 複数MAPで再利用可能なTile Familyを作れる
- Network / Transitionを体系的に追加できる
- Material固有metricsを追加しやすい
- 将来的なAutotile / Wang graphへ拡張可能

### Negative

- 単純な画像変換ツールよりarchitectureが大きくなる
- Materialごとの専用ルールが必要
- Transition combinationが増える
- Network topology数が増える
- Edge Contract schemaの管理が必要
- 評価指標がmaterial依存になる
- fully genericな「どんな画像でもTileset化」は目標から遠ざかる

ただし、これは汎用Tileset品質を確保するために許容する。

## Known Limitations

### Metrics

多くの評価指標は近似であり、人間のPixel Artistによる評価を完全には代替しない。

### Forest segmentation

Otsu threshold + connected components等の近似であり、実際の樹木認識ではない。

### Road corners

現状はrectangle stripe + junctionベースであり、自然な曲線道路や複雑なcornerは未対応である。

### Wang completeness

完全なWang graphや全接続組み合わせは未実装である。

### GraphCut

高度なSource seam optimizationは未実装である。

### Object

tree / rock / building等のoverlay familyは本ADR時点では未実装である。

### Gameplay semantics

walkability、height、terrain cost等はTileset生成にはまだ利用していない。

## Follow-up Decisions / Experiments

優先順は以下とする。

### Phase 1: Road topology拡張

追加候補:

- NES
- NSW
- NEW
- SEW
- NESW

T字・十字を追加する。

### Phase 2: Transition boundary改善

現在のrectangular / simple boundaryから、irregular boundary、curved boundary、noisy natural edgeへ拡張する。

### Phase 3: River

Roadより厳しいNetwork対象としてRiverを追加する。

追加課題:

- 流れ方向
- river width
- bank
- water/land transition
- bridgeとのinteraction

### Phase 4: Object Overlay

tree、rock、bush、house、fenceをgroundから分離し、Object Familyとして扱う。

### Phase 5: Full Tileset Graph

Surface / Network / Transition / Objectを統合した接続graphを構築する。

```text
Tile Spec
↓
Compatible Tile Graph
↓
MAP Layout
```

## Architectural Outcome

現時点で採用するVisual Compiler構造を以下とする。

```text
User Intent
      ↓
LLM / Tileset Designer
      ↓
Tileset Specification
      ↓
Material Source Generator
      ↓
Source Acceptance Gate
      ↓
Tileset Source Compiler
      │
      ├─ Surface
      ├─ Network
      ├─ Transition
      └─ Object
      ↓
Semantic Edge Contract
      ↓
Pixel Art Compiler
      ↓
64×64 Reusable Tile Families
      ↓
Contract-aware MAP Assembly
```

別系統として、次のMAP専用系統も維持する。

```text
High-resolution Complete MAP
↓
Context-aware MAP Compiler
↓
Map-specific 64×64 Tiles
```

前者は再利用可能Tileset向け、後者は章・MAP専用アセット向けとする。両者を同一問題として無理に統合しない。

## Final Decision

`pixelart-compiler` における汎用MAP Tile生成は、画像を個別にPixel Art化して後から組み合わせる問題として扱わない。

代わりに、Material Sourceを設計し、Surface / Network / Transition / Objectというsemantic roleごとにSource Tileを構築し、Semantic Edge Contractによって接続可能性を保証した上でPixel Artへコンパイルする問題として扱う。

今回のTransition / Network実験においてContract-aware方式が全ケースで最高評価となったため、本方式を今後の汎用Tileset Compilerの基礎architectureとして採用する。

## References

- [Transition / Network study config](../../experiments/transition_network_study.yaml)
- [Transition / Network study manifest](../../e2e/transition_network_study/manifest.json)
- [Method ranking](../../e2e/transition_network_study/summary/method_ranking.json)
- [Study montage](../../e2e/transition_network_study/summary/montage.png)
- [Semantic contracts](../../src/pixel_tile_compiler/transition_network/contracts.py)
- [Network compiler](../../src/pixel_tile_compiler/transition_network/network.py)
- [Transition compiler](../../src/pixel_tile_compiler/transition_network/transition.py)
