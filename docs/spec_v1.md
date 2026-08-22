# 64×64 SRPG MAP Tile Pixel Art Compiler — MVP実装仕様書

## 0. Codexへの最上位指示

この仕様書に従って、Pythonのみで動作するMVPを実装すること。

目的は、通常の高解像度画像・イラスト・テクスチャを単純縮小するのではなく、

**画像解析 → 意味的/構造的抽象化 → 情報減算 → 64×64への再構成 → Pixel Art Grammar補正**

によって、SRPGで実際に使用可能な64×64pxのMAPタイルへ変換するツールを作ることである。

最終画像そのものをLLMに生成させてはならない。

LLM/Codex等は、

- 画像内の構造理解
- 地形/オブジェクト分類
- 残すべき特徴
- 捨てるべき特徴
- 誇張すべき特徴
- タイルとしての重要度

を判断する補助として利用する。

最終的なpixel配置、減色、cluster整理、境界処理は決定論的アルゴリズムで処理する。

LLM/MCPが利用できない環境でもRule-based fallbackにより完全動作すること。

---

# 1. MVPの確定要件

## 1.1 主対象

SRPG用のMAPタイル。

1タイルの最終出力サイズは固定で、

```text
64 × 64 px
```

とする。

主な対象例:

- 草地
- 土
- 石畳
- 砂
- 水
- 雪
- 岩
- 崖
- 森床
- 道
- 壁
- 床
- 屋根
- 建造物表面
- その他SRPGマップ上で1タイルとして使用する画像

MVPでは「キャラクタースプライト変換」は対象外。

---

# 2. MVPの最重要評価軸

最優先は、

> **SRPGのMAP素材として実際に使えるか**

とする。

単純な元絵再現度より以下を優先する。

1. 64×64でも地形の種類が認識できる
2. 情報量が過密にならない
3. 高解像度画像を縮小しただけの見た目にならない
4. Pixel Artとしてpixel clusterが整理されている
5. タイルを並べた際に破綻が確認しやすい
6. SRPGのユニットを上に配置しても地形が主張しすぎない
7. 地形同士の境界が認識しやすい
8. nearest-neighbor表示時に自然に見える

---

# 3. 入力仕様

## 3.1 対応画像

必須:

- PNG

可能なら対応:

- JPEG
- WebP

内部処理はすべてRGBAへ統一する。

---

## 3.2 背景

MVPでは以下のみ正式対応。

### A. 透過背景

```text
RGBA PNG
alpha = 0
```

### B. 単色背景

ユーザーが背景色を指定できる。

例:

```text
#FFFFFF
#00FF00
```

または自動推定。

---

## 3.3 推奨入力サイズ

最低:

```text
128 × 128
```

推奨:

```text
256 × 256 ～ 1024 × 1024
```

入力は正方形でなくてもよい。

ただし出力は64×64固定。

---

# 4. 出力仕様

必須出力:

```text
output/
├─ final.png
├─ ir.json
├─ metadata.json
└─ debug/
   ├─ 01_normalized.png
   ├─ 02_smooth.png
   ├─ 03_edges.png
   ├─ 04_regions.png
   ├─ 05_palette_preview.png
   ├─ 06_raw_pixelized.png
   ├─ 07_cluster_cleaned.png
   └─ 08_tile_preview.png
```

`final.png` は必ず:

```text
64×64
RGBA
nearest-neighbor前提
```

とする。

---

# 5. Palette仕様

MVPデフォルト:

```text
16 colors
```

設定可能範囲:

```text
4 ～ 32 colors
```

alpha完全透過色はpalette_budgetに含めなくてよい。

デフォルト:

```yaml
palette_budget: 16
```

---

# 6. 基本思想

本ツールは、

```text
High Resolution Image
↓
64×64 Image
```

という単純なresizeツールではない。

以下の構造を採用する。

```text
High Resolution Image
        ↓
Preprocess
        ↓
Visual Structure Analysis
        ↓
Semantic / Structural Analysis
        ↓
Tile IR
        ↓
Information Reduction
        ↓
Region-aware Pixelization
        ↓
Palette Quantization
        ↓
Pixel Cluster Cleanup
        ↓
Tile-specific Validation
        ↓
64×64 Pixel Art
```

重要:

```text
画像を先に縮小してから考える
```

のではなく、

```text
何を残すか決めながら64×64へ落とす
```

こと。

---

# 7. アーキテクチャ

```text
pixel_tile_compiler/
│
├─ pyproject.toml
├─ README.md
│
├─ src/
│  └─ pixel_tile_compiler/
│     │
│     ├─ __init__.py
│     ├─ __main__.py
│     ├─ config.py
│     │
│     ├─ io/
│     │  ├─ loader.py
│     │  └─ exporter.py
│     │
│     ├─ preprocess/
│     │  ├─ normalize.py
│     │  ├─ background.py
│     │  └─ smoothing.py
│     │
│     ├─ analysis/
│     │  ├─ edges.py
│     │  ├─ colors.py
│     │  ├─ regions.py
│     │  ├─ saliency.py
│     │  └─ structural.py
│     │
│     ├─ semantic/
│     │  ├─ base.py
│     │  ├─ rule_based.py
│     │  ├─ mcp_provider.py
│     │  └─ prompts.py
│     │
│     ├─ ir/
│     │  ├─ schema.py
│     │  ├─ builder.py
│     │  └─ validator.py
│     │
│     ├─ pixelizer/
│     │  ├─ spatial.py
│     │  ├─ palette.py
│     │  ├─ rasterizer.py
│     │  └─ allocator.py
│     │
│     ├─ grammar/
│     │  ├─ clusters.py
│     │  ├─ isolated_pixels.py
│     │  ├─ diagonals.py
│     │  ├─ outlines.py
│     │  └─ dithering.py
│     │
│     ├─ tile/
│     │  ├─ seam.py
│     │  ├─ repeat_preview.py
│     │  └─ metrics.py
│     │
│     ├─ pipeline/
│     │  └─ compiler.py
│     │
│     ├─ gui/
│     │  ├─ app.py
│     │  ├─ main_window.py
│     │  ├─ canvas.py
│     │  ├─ settings_panel.py
│     │  └─ preview_panel.py
│     │
│     └─ cli.py
│
└─ tests/
   ├─ test_ir.py
   ├─ test_palette.py
   ├─ test_pixelizer.py
   ├─ test_clusters.py
   ├─ test_seam.py
   └─ test_pipeline.py
```

---

# 8. 技術スタック

Python:

```text
Python >= 3.12
```

使用ライブラリ:

```text
Pillow
numpy
opencv-python
scikit-image
scikit-learn
pydantic
PySide6
typer
rich
pytest
```

可能なら:

```text
scipy
```

を使用可能。

---

# 9. GUI

GUIはMVP必須。

PySide6を使用する。

---

# 10. GUIレイアウト

```text
┌─────────────────────────────────────────────────────┐
│ File  Convert  View                                 │
├────────────────┬─────────────────────┬──────────────┤
│ Settings       │ Main Canvas         │ Preview      │
│                │                     │              │
│ Input          │                     │ Before/After │
│ Palette        │                     │              │
│ Semantic Mode  │                     │ 3×3 Tile     │
│ Tile Mode      │                     │ Preview      │
│ Grammar        │                     │              │
│                │                     │              │
│ [Compile]      │                     │              │
├────────────────┴─────────────────────┴──────────────┤
│ x: 23 y: 41 | RGB: ... | Zoom: 800%                │
└─────────────────────────────────────────────────────┘
```

---

# 11. Pixel Canvas

MVP上の重要機能。

`QGraphicsView`を利用する。

必須:

- 64×64をpixel単位で表示
- nearest-neighbor
- smoothing禁止
- integer zoom
- pixel grid
- マウス位置のpixel座標表示
- pixel RGB/RGBA表示

ズーム:

```text
100%
200%
400%
800%
1600%
3200%
```

内部倍率:

```text
1x
2x
4x
8x
16x
32x
```

---

# 12. Pixel Grid

ユーザーがpixel単位を目視確認するため必須。

表示条件:

```text
zoom >= 4x
```

デフォルトON。

64×64の各pixel境界に罫線を表示する。

グリッドは画像データへ書き込まず、GUI overlayとして描画する。

推奨実装:

```python
QGraphicsView.drawForeground()
```

または独自Canvas Widget。

---

# 13. Canvas描画制約

画像拡大時:

```python
Qt.FastTransformation
```

を使用。

禁止:

```text
SmoothTransformation
Bilinear
Bicubic
```

GUI表示が画像そのものを補間しないこと。

---

# 14. GUIプレビュー

最低3種類表示する。

## Source

入力画像。

## Result

64×64。

## Tiled 3×3

```text
64×3 = 192px

192×192
```

同じタイルを3×3に配置する。

目的:

- 継ぎ目確認
- 模様の繰り返し感確認
- MAP上での密度確認

---

# 15. Tile Mode

以下の3種類を定義する。

```text
repeatable
directional
object
```

## repeatable

草、砂、石、床、水など。

同一タイルを繰り返して利用。

seam検査を有効にする。

## directional

道、崖、壁など。

上下左右の境界が異なる可能性がある。

強制seam補正はしない。

## object

木、岩、建物パーツなど。

背景透過または単色上のオブジェクト。

---

# 16. MVPではAutotileを作らない

重要。

MVPでは、

```text
草地→道
草地→崖
水→陸
```

等の自動接続タイル生成は実装しない。

将来機能とする。

ただしIR設計は将来のAutotile生成に拡張可能にする。

---

# 17. Preprocess

## 17.1 Normalize

入力画像を内部作業サイズへ正規化。

```text
WORK_SIZE = 256×256
```

とする。

入力が大きい場合:

```text
LANCZOS
```

で256まで縮小してよい。

これは最終pixelizationではなく解析用。

---

# 18. Background処理

alpha付きの場合:

alphaを使用。

単色背景の場合:

RGB距離で背景マスク生成。

設定:

```yaml
background:
  mode: auto | alpha | color
  color: "#FFFFFF"
  tolerance: 12
```

---

# 19. Smoothing

目的:

高解像度画像特有の細かいテクスチャ、ノイズ、グラデーションを減算する。

デフォルト候補:

```text
bilateral filter
```

OpenCV:

```python
cv2.bilateralFilter()
```

エッジを維持しながら内部変化を平滑化する。

設定:

```yaml
smoothing:
  enabled: true
  method: bilateral
  diameter: 7
  sigma_color: 40
  sigma_space: 40
```

---

# 20. Visual Structure Analysis

最低以下を抽出する。

## Edge

Canny。

```python
cv2.Canny()
```

## Color Regions

Lab空間へ変換してクラスタリング。

## Superpixel

SLIC。

```python
skimage.segmentation.slic()
```

推奨初期値:

```yaml
slic:
  segments: 128
  compactness: 10
```

---

# 21. なぜSuperpixelを使用するか

1pixelごとではなく、

```text
意味的に似た色・領域
```

を一塊として扱うため。

例えば草地なら、

```text
細かい葉の一本一本
```

ではなく、

```text
明るい草
暗い草
影
土
```

程度の大きな面へ抽象化する。

---

# 22. Structural Analysis

各regionについて最低以下を計算。

```text
id
bbox
area
centroid
mean_rgb
mean_lab
contrast
edge_density
saliency
touches_border
neighbor_regions
```

---

# 23. SRPG用Semantic Interpretation

キャラクター向けの、

```text
face
hair
eyes
```

等は使用しない。

MAPタイル向けに以下へ置換。

候補semantic label:

```text
ground
grass
soil
sand
stone
water
snow
rock
cliff
road
floor
wall
roof
wood
foliage
shadow
highlight
object
background
unknown
```

---

# 24. SemanticProvider

抽象interfaceを作る。

```python
class SemanticProvider(Protocol):

    def analyze(
        self,
        image: Image.Image,
        structural_data: StructuralAnalysis,
        config: CompilerConfig,
    ) -> SemanticAnalysis:
        ...
```

実装:

```text
RuleBasedSemanticProvider
McpSemanticProvider
```

---

# 25. RuleBasedSemanticProvider

MCP無しでも必ず動作する。

最低以下を判断。

```text
region importance
preserve
discard
merge candidate
texture priority
edge priority
```

---

# 26. MCP / Codex連携

MCP側は必須依存にしない。

`McpSemanticProvider`をadapterとして実装する。

通信方式をcoreへ直接埋め込まない。

---

# 27. MCPへ送る内容

可能なら:

- 入力画像
- 構造解析JSON
- tile_mode
- palette_budget

画像入力が利用できない場合は、

- dominant colors
- region data
- edges
- image description

だけでも利用可能にする。

---

# 28. MCPプロンプト目的

LLMに「ドット絵を描け」と指示しない。

指示:

> この画像を64×64のSRPG用Pixel Art MAP tileへ変換するために、どの視覚情報を保持し、どの情報を削除・統合し、どの地形的特徴を強調すべきか解析せよ。

---

# 29. MCP出力

必ずJSON。

例:

```json
{
  "tile_type": "grass",
  "tile_mode": "repeatable",
  "global": {
    "texture_density": 0.45,
    "edge_priority": 0.35,
    "contrast_priority": 0.50,
    "recommended_palette": 12
  },
  "regions": [
    {
      "region_id": 12,
      "semantic": "grass",
      "importance": 0.8,
      "preserve": [
        "large_color_mass",
        "directional_texture"
      ],
      "discard": [
        "fine_blades",
        "micro_gradient"
      ],
      "merge_with": [13, 16]
    }
  ]
}
```

---

# 30. MCPレスポンス検証

Pydanticでvalidation。

不正JSON:

```text
↓
1回再試行
↓
失敗
↓
RuleBasedSemanticProvider
```

ツール全体は停止しない。

---

# 31. Tile IR

IRを処理の正本とする。

Pydanticで実装。

```python
class TileIR(BaseModel):
    version: str = "0.1"

    width: int = 64
    height: int = 64

    tile_type: str
    tile_mode: Literal[
        "repeatable",
        "directional",
        "object"
    ]

    palette_budget: int

    global_style: GlobalStyle

    regions: list[RegionIR]
```

---

# 32. GlobalStyle

```python
class GlobalStyle(BaseModel):

    texture_density: float

    contrast_strength: float

    edge_strength: float

    dithering: Literal[
        "off",
        "minimal",
        "ordered"
    ]

    outline_mode: Literal[
        "off",
        "selective"
    ]

    seam_mode: Literal[
        "off",
        "inspect",
        "correct"
    ]
```

各float:

```text
0.0 ～ 1.0
```

---

# 33. RegionIR

```python
class RegionIR(BaseModel):

    id: int

    semantic: str

    importance: float

    area_ratio: float

    dominant_color: tuple[int, int, int]

    preserve_edges: bool

    preserve_texture: bool

    texture_priority: float

    merge_candidates: list[int]

    discard_micro_detail: bool
```

---

# 34. Information Reduction

最重要処理のひとつ。

高解像度の情報をそのまま64pxへ持ち込まない。

以下を減算対象とする。

```text
細かなグラデーション
微小色差
1px未満になる模様
高周波ノイズ
写真的テクスチャ
細すぎる線
小さすぎる領域
意味の薄いハイライト
意味の薄い影
```

---

# 35. Region Merge

似ている隣接regionを統合する。

条件:

```text
Lab color distance
+
semantic similarity
+
edge importance
```

を使用。

重要edgeを跨いでは統合しない。

---

# 36. 色距離

RGB距離ではなく可能な範囲で、

```text
CIELAB
```

を利用する。

scikit-image:

```python
skimage.color.rgb2lab
```

---

# 37. Spatial Pixelization

最終64×64へ変換。

単純resizeのみを使用してはならない。

最低以下の2段階。

```text
Region abstraction
↓
64×64 rasterization
```

---

# 38. 基本アルゴリズム

最初のMVPでは以下でよい。

### Step 1

256×256上でregion mapを生成。

### Step 2

regionごとに代表色を決定。

### Step 3

region mapを64×64へ縮約。

その際、

```text
単純平均
```

ではなく、

64pxの各cellで最も重要なregionを優先。

score:

```text
score =
coverage
×
importance
×
edge_weight
```

---

# 39. 64px Cell Selection

元画像4×4領域が64pxの1pixelへ対応する。

単純平均ではなく候補regionから代表regionを決める。

概念:

```python
score = (
    coverage_ratio
    * region.importance
    * semantic_weight
)
```

最高scoreのregionを採用。

---

# 40. 重要境界

edge map上の強い境界は、

coverageが多少少なくても保存しやすくする。

目的:

- 道
- 崖
- 岩
- 水際
- 壁

などの輪郭を64pxで消さない。

---

# 41. Palette Quantization

空間pixelization後にpaletteを最終確定する。

候補:

```text
Median Cut
K-Means Lab
```

MVPデフォルト:

```text
K-Means Lab
```

---

# 42. Palette Weight

色の使用面積だけでなく、

```text
semantic importance
edge contribution
contrast
```

も重みにする。

小面積でも重要な境界色を消しにくくする。

---

# 43. Pixel Art Grammar

ここからが通常の画像縮小との差になる。

---

# 44. Isolated Pixel Removal

周囲8pixelの大半が異色である単独pixelを検出。

候補:

```text
1 pixel isolated
2 pixel micro cluster
```

削除対象。

ただし重要edgeに所属する場合は保護。

---

# 45. Connected Components

同色clusterごとにconnected-component解析。

OpenCV:

```python
cv2.connectedComponents()
```

またはscikit-image。

---

# 46. Micro Cluster

デフォルト:

```yaml
min_cluster_size: 2
```

面積:

```text
1px
```

は原則除去。

2pxは状況によって除去。

---

# 47. Cluster Replacement

小cluster除去時は、

単純に最頻色へ置換するのではなく、

周辺色のうちLab距離と面積を考慮して決定。

---

# 48. Diagonal Cleanup

Pixel Artで不自然になりやすい、

```text
1-1-1-1 staircase
```

や、

```text
不規則な斜線
```

を整理する。

MVPでは高度なartist ruleを作りすぎない。

最低:

- 1px突起除去
- 1px窪み除去
- 不規則な孤立斜線の平滑化

---

# 49. Anti-Aliasing

原則禁止。

最終画像に高解像度画像由来の中間色pixelが大量に残らないこと。

Pixel Artのため、

```text
subpixel AA
smooth edge
```

は使用しない。

---

# 50. Dithering

デフォルト:

```text
minimal
```

モード:

```text
off
minimal
ordered
```

MVPではFloyd-Steinbergをデフォルトにしない。

理由:

ノイズが増えPixel Artらしいclusterが壊れやすい。

必要ならBayer ordered ditheringを選択可能。

---

# 51. Repeatable Tile

`tile_mode = repeatable` の場合、

3×3プレビューとseam scoreを計算。

---

# 52. Seam Score

左右端:

```text
x = 0
x = 63
```

上下端:

```text
y = 0
y = 63
```

のLab色距離を測る。

例:

```text
horizontal_seam_score
vertical_seam_score
```

0が理想。

---

# 53. Seam Correction

MVPでは控えめに実装。

```text
seam_mode = off
inspect
correct
```

デフォルト:

```text
inspect
```

重要:

無条件に左右端を同一化しない。

画像を壊す可能性があるため。

---

# 54. correctモード

repeatable tileのみ使用可能。

端から数pixel幅:

```text
4px
```

程度をwrap-awareに再評価。

単純blurは禁止。

候補:

```text
opposite edge palette correspondence
+
cluster-aware reassignment
```

MVPでは完璧なseamless生成を求めない。

---

# 55. 3×3 Preview

必ずGUIに表示。

```text
Tile Tile Tile
Tile Tile Tile
Tile Tile Tile
```

ズームもnearest-neighbor。

---

# 56. SRPG Preview

可能ならMVPに追加。

64pxタイル中央へ簡易ダミーユニット表示。

例:

```text
24×32程度の単色/簡易シルエット
```

目的:

MAPタイルがユニットより視覚的に強すぎないか確認する。

実データへ合成しない。

GUIのみ。

優先度:

```text
Should
```

---

# 57. CLI

必須。

GUIだけにロジックを埋めない。

---

# 58. CLIコマンド

```bash
pixel-tile compile source.png
```

オプション:

```bash
pixel-tile compile source.png \
  --output ./output \
  --palette 16 \
  --tile-mode repeatable \
  --semantic rule \
  --seam inspect
```

---

# 59. Semantic Mode

```text
rule
mcp
```

デフォルト:

```text
rule
```

---

# 60. GUI起動

```bash
pixel-tile gui
```

---

# 61. Config

YAMLまたはJSON。

推奨:

```text
config.yaml
```

例:

```yaml
output:
  width: 64
  height: 64

palette:
  budget: 16

background:
  mode: alpha
  color: null
  tolerance: 12

preprocess:
  work_size: 256

smoothing:
  enabled: true
  method: bilateral
  diameter: 7
  sigma_color: 40
  sigma_space: 40

slic:
  segments: 128
  compactness: 10

grammar:
  remove_isolated_pixels: true
  min_cluster_size: 2
  diagonal_cleanup: true
  dithering: minimal

tile:
  mode: repeatable
  seam_mode: inspect

semantic:
  provider: rule
```

---

# 62. Determinism

同一入力・同一configなら、

原則同一結果。

K-Means等はseed固定。

```yaml
seed: 42
```

---

# 63. CompilerPipeline

エントリポイント。

```python
class PixelTileCompiler:

    def compile(
        self,
        source: Path,
        config: CompilerConfig,
    ) -> CompilationResult:
        ...
```

---

# 64. CompilationResult

```python
class CompilationResult(BaseModel):

    final_path: Path

    ir_path: Path

    debug_paths: dict[str, Path]

    metrics: CompilationMetrics
```

---

# 65. Pipeline実行順序

厳守:

```text
1. load
2. normalize
3. background mask
4. smoothing
5. edge analysis
6. color analysis
7. SLIC segmentation
8. structural analysis
9. semantic analysis
10. IR build
11. region merge
12. spatial pixelization
13. palette quantization
14. cluster cleanup
15. diagonal cleanup
16. optional dithering
17. seam inspection/correction
18. metrics
19. export
```

---

# 66. Debug Artifact

各主要工程を保存可能にする。

config:

```yaml
debug:
  enabled: true
```

GUIでは工程を切り替えて確認できると良い。

最低:

```text
Source
Regions
Raw Pixelized
Final
```

---

# 67. GUI Settings

左パネル:

### Input

```text
Open Image
```

### Background

```text
Alpha
Auto Color
Manual Color
```

### Palette

```text
4-32
default 16
```

### Tile Mode

```text
Repeatable
Directional
Object
```

### Semantic

```text
Rule
MCP
```

### Dither

```text
Off
Minimal
Ordered
```

### Seam

```text
Off
Inspect
Correct
```

### Compile

大きな実行ボタン。

---

# 68. GUI Results

右パネル:

```text
Palette
Metrics
3×3 Preview
```

Paletteは16色をswatch表示。

---

# 69. Metrics

最低以下を表示。

```text
actual_palette_count
isolated_pixel_count
micro_cluster_count
horizontal_seam_score
vertical_seam_score
```

---

# 70. MVP品質評価

同じ入力画像について以下を比較出力できること。

```text
A:
Nearest Neighbor resize

B:
Bicubic resize + quantization

C:
Pixel Tile Compiler
```

GUIの比較機能はMustではない。

CLI/debug出力でよい。

---

# 71. Baseline

必ず実装。

```python
generate_baseline_nearest()
generate_baseline_bicubic_quantized()
```

目的:

本手法が単なる縮小より改善しているか比較可能にする。

---

# 72. 自動テスト

pytest。

---

# 73. Test: Output Size

```python
assert image.size == (64, 64)
```

---

# 74. Test: Palette

設定16色なら、

```python
assert unique_color_count <= 16
```

透明色を除外してよい。

---

# 75. Test: Alpha

透過入力で、

alpha fringeが生成されないこと。

半透明pixelを原則作らない。

MVPではalpha:

```text
0
255
```

の二値を推奨。

---

# 76. Test: Determinism

同一入力を2回実行。

pixel-by-pixelで一致。

---

# 77. Test: IR

Pydantic validation。

---

# 78. Test: MCP Failure

MCPが、

- timeout
- invalid JSON
- exception

となっても、

RuleBased providerへfallbackし、最終PNGが生成されること。

---

# 79. Test: Grid

GUIテストまでは必須でない。

Canvas class単位で、

```text
zoom
pixel coordinate
grid spacing
```

を分離して実装する。

---

# 80. Test: Seam

repeatableタイルのみseam metricが生成される。

directional/objectではnullableでもよい。

---

# 81. Logging

Python標準loggingを使用。

最低:

```text
INFO
DEBUG
WARNING
ERROR
```

---

# 82. Error Handling

入力画像不正:

ユーザー向けエラー。

MCP失敗:

fallback。

CVアルゴリズム失敗:

可能な範囲で単純処理へfallback。

---

# 83. 非機能要件

MVPではGPU不要。

CPUのみで動作。

目標:

```text
1画像 < 10秒程度
```

ただし厳密な性能要件ではない。

LLM通信時間を除く。

---

# 84. 保存

GUIでCompile後、

保存先指定可能。

デフォルト:

```text
./output/<source_stem>/
```

---

# 85. 元画像を破壊しない

入力画像には一切書き込まない。

---

# 86. ソースコード方針

- type hint必須
- public APIにdocstring
- module責務を分離
- GUIに画像処理ロジックを書かない
- MCPにcoreを依存させない
- magic numberをconfigへ寄せる

---

# 87. 開発フェーズ

## Phase 1 — Skeleton

実装:

```text
pyproject
package
config
CLI
image loader
exporter
tests
```

完了条件:

PNGをロードし64×64PNGとして保存できる。

---

# 88. Phase 2 — Baseline

実装:

```text
nearest baseline
bicubic + quantize baseline
```

完了条件:

比較画像生成可能。

---

# 89. Phase 3 — CV Analysis

実装:

```text
bilateral
Canny
SLIC
Lab colors
regions
```

完了条件:

debug画像生成。

---

# 90. Phase 4 — IR

実装:

```text
Pydantic schema
structural analysis
rule semantic provider
IR builder
```

完了条件:

任意入力から`ir.json`生成。

---

# 91. Phase 5 — Pixelizer

実装:

```text
region-aware downsample
weighted region selection
palette optimization
```

完了条件:

64×64、最大16色。

---

# 92. Phase 6 — Pixel Grammar

実装:

```text
isolated pixel removal
micro cluster cleanup
basic diagonal cleanup
```

完了条件:

rawとfinalの比較が可能。

---

# 93. Phase 7 — Tile Validation

実装:

```text
3x3 preview
seam score
```

完了条件:

repeatable tile評価可能。

---

# 94. Phase 8 — GUI

PySide6。

実装:

```text
MainWindow
Settings Panel
Canvas
Pixel Grid
Integer Zoom
Result Preview
3x3 Preview
Palette
Metrics
```

---

# 95. Phase 9 — MCP

最後に実装。

理由:

MCP無しでcoreの価値を検証できるようにする。

実装:

```text
SemanticProvider abstraction
McpSemanticProvider
JSON validation
fallback
```

---

# 96. MCP部分に関する重要制約

MCP環境固有のAPIをcoreへハードコードしない。

最低以下を切る。

```python
class SemanticProvider(Protocol):
    ...
```

したがってMCP以外にも将来的に、

```text
OpenAI
local VLM
Gemini
Claude
custom model
```

等へ差し替え可能。

---

# 97. MCP用system instruction案

```text
You are a semantic analysis component for a pixel-art map tile compiler.

Do not generate an image.
Do not specify individual final pixel coordinates.

Analyze the source image and its structural regions.

The final target is a 64x64 pixel-art tile intended for use in a tactical/SRPG map.

Identify:
- terrain type
- important large-scale shapes
- important boundaries
- visual features that must survive reduction
- fine details that should be discarded
- regions that may be merged
- suitable texture density
- suitable contrast level

Prefer map readability over photorealistic fidelity.

Return only valid JSON matching the supplied schema.
```

---

# 98. LLMにやらせないこと

禁止:

```text
64×64の全pixelをJSONで返す
```

禁止:

```text
base64 PNGを生成させる
```

禁止:

```text
画像そのものをLLM出力として採用
```

禁止:

```text
最終paletteを完全にLLM任せにする
```

---

# 99. LLMの責務

LLMは、

```text
意味
優先順位
抽象化方針
```

のみ。

---

# 100. Algorithmの責務

Algorithmは、

```text
座標
pixel
palette
cluster
edge
seam
```

を担当。

---

# 101. 実装上の中心概念

このツールは画像フィルタではなく、

```text
Visual Compiler
```

として実装する。

対応関係:

```text
Source Image
    =
Source Code

CV / Semantic Analysis
    =
Parser / Semantic Analyzer

Tile IR
    =
Intermediate Representation

Information Reduction
    =
Optimizer

Pixelizer
    =
Code Generator

Pixel Grammar
    =
Target-specific Optimization

PNG
    =
Binary
```

---

# 102. MVPの明確な非目標

以下は実装しない。

```text
アニメーション
キャラクター
MAP自動生成
Autotile一括生成
複数タイル同時変換
専用AIモデル学習
Diffusion
GAN
画像生成モデル
Photoshop風編集
本格ペイントソフト
```

---

# 103. 将来対応

MVP成功後。

## Autotile Compiler

1枚から、

```text
center
N
S
E
W
NE
NW
SE
SW
```

などの接続タイル生成。

---

## Shared Palette

tileset全体でpalette統一。

---

## Tile Set Compiler

複数画像から、

```text
grass
road
water
cliff
```

を同一styleへ統一。

---

## SRPG Preview

実際のMAP上に配置して確認。

---

## Semantic Constraints

```text
walkable
blocked
water
wall
height
```

などゲームルール情報を使って見た目を最適化。

---

# 104. MVP Definition of Done

以下を全て満たした時点でMVP完成。

- Python 3.12で起動
- CLI動作
- PySide6 GUI動作
- PNG入力
- 透過背景入力
- 単色背景入力
- 64×64固定出力
- palette上限指定
- デフォルト16色
- CV解析
- SLIC region生成
- Tile IR生成
- Rule-based semantic解析
- region-aware pixelization
- palette quantization
- isolated pixel除去
- micro-cluster整理
- 基本diagonal cleanup
- 3×3 tiled preview
- seam metrics
- pixel grid
- integer zoom
- pixel coordinate表示
- debug intermediate image保存
- nearest baseline
- bicubic baseline
- pytest
- deterministic output
- MCP abstraction
- MCP失敗時fallback

---

# 105. MVP成功判定

最低5種類のMAP素材で検証する。

推奨:

```text
grass
stone
dirt
water
rock
```

各画像について、

```text
Nearest
Bicubic + quantization
Compiler
```

を並べる。

以下を目視判定。

```text
A. 地形が識別できる

B. 細かいノイズが減っている

C. 大きな形が残っている

D. 64×64 Pixel Artとして自然

E. 3×3で並べても視覚密度が破綻しない

F. SRPGマップ素材として利用したいと思える
```

最低、

```text
5サンプル中3以上
```

でCompiler版がBaselineより明確に良ければ、

仮説検証MVPとして成功。

---

# 106. 実装順序についてCodexへの指示

一気にGUIから作らない。

以下の順でcommit可能な単位に実装する。

```text
1. project skeleton
2. config/schema
3. image I/O
4. baseline conversion
5. preprocess
6. CV analysis
7. region structure
8. Tile IR
9. rule semantic analysis
10. region-aware pixelizer
11. palette quantization
12. pixel grammar
13. tile metrics
14. CLI integration
15. GUI canvas
16. grid/zoom
17. settings UI
18. previews
19. MCP adapter
20. integration tests
```

各段階でpytestを通す。

---

# 107. 完成時READMEに含めるもの

最低:

```text
概要
設計思想
インストール
CLI
GUI
MCP optional setup
Pipeline
Debug output
Known limitations
```

---

# 108. 最終的な思想

このMVPで検証する仮説は、

> 美麗な画像からPixel Artへの変換は、画像生成問題ではなく、意味を保持した情報圧縮・抽象化・再符号化問題として扱えるのではないか

というものである。

そのため、

```text
LLM/VLM
=
「何を描くべきか」を判断する

Algorithm
=
「限られた64×64のpixelでどう描くか」を決定する
```

という責務分離を採用する。

最終目標は、

```text
綺麗な画像を縮小した64px画像
```

ではなく、

```text
人間が64×64という制約を理解した上で再構成したように見える
SRPG用Pixel Art MAP Tile
```

を生成することである。