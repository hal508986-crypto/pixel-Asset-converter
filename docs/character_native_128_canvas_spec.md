# Native Character Canvas 128×128 実装設計仕様

作成日: 2026-09-07  
対象基準: `main` / `39639fe`（PR #1 merge後）  
関連ADR: [`ADR-0004 Output Canvas と Tile Size を別契約にする`](./adr/0004-output-canvas-vs-tile-size.md)

## 1. 目的

`pixelart-compiler` の単体キャラクター経路を、64×64固定の出力から
**ネイティブ128×128を含む可変Canvasへ拡張する**。

ここでいう128×128は、64×64の完成品を2倍拡大したものではない。
高解像度のSourceから128×128のpixel budgetへ直接再構成し、64では落ちる髪・目・衣装・陰影などの
情報を128用のドット密度として保持できることを目的とする。

初回の品質比較条件は `B24` を想定する。

```text
B24 = palette 24 + balanced character detail
```

ただし、Canvas geometryとB24は独立した契約にする。
128×128が常に24色・balancedで最適であるとは固定しない。

---

## 2. 先に固定する用語

この変更では、以下の4概念を混同しない。

### 2.1 Source Image

入力となる高解像度画像。

```text
例: 1024×1024 RGBA PNG
```

### 2.2 Analysis Canvas

意味解析・region解析などの作業解像度。
現行の `work_size=256` が該当する。

```text
例: 256×256
```

最終PNGの解像度ではない。

### 2.3 Output Canvas

単体compileの最終ラスタ寸法。
今回 `CanvasSpec(width, height)` として新規に契約化する。

```text
64×64
128×128
128×96
```

### 2.4 Tile Size / Cell Size

SRPG MAP、reusable Tileset、Sheet splitなどで使う論理配備単位。
現行では主に64px。

```text
tile_size = 64
cell_size = 64
```

**Output Canvasとは別概念である。**

---

## 3. リポジトリ監査結果

2026-09-07の `main` を確認した結果、可変Canvas化に関係する箇所は次の通り。

### 3.1 直接のblocker

#### `src/pixel_tile_compiler/config.py`

`CompilerConfig` は `width=64`, `height=64` を持つが、`__post_init__` で
`(64, 64)` 以外を明示的にrejectしている。

またCharacter配置は次の固定値を持つ。

```text
character_frame_width  = 54
character_frame_height = 54
character_bottom_margin = 7
```

#### `src/pixel_tile_compiler/ir/schema.py`

`TileIR` が次で固定されている。

```python
width: Literal[64] = 64
height: Literal[64] = 64
```

#### `src/pixel_tile_compiler/ir/builder.py`

`build_tile_ir()` はconfigの出力寸法をIRへ渡しておらず、IRの64 defaultに依存している。

#### `src/pixel_tile_compiler/pipeline/compiler.py`

Characterの`fit_character_to_canvas()`呼出し自体はconfigのwidth/heightを使っているが、
Config側が64以外を拒否するため実質固定である。

さらにbaseline関数はdefault `(64, 64)` を持ち、export時にもsizeを明示していない。
可変Canvas化だけ行うと、`final.png=128×128`なのにbaselineだけ64×64という不整合が起きる。

#### `src/pixel_tile_compiler/cli.py`

`compile` に `--width` / `--height` がなく、help/docstringも64×64を前提としている。

#### `src/pixel_tile_compiler/gui/main_window.py`

PixelCanvasのgrid描画に

```text
size = 64 * zoom
range(65)
```

という64固定がある。
GUIはcore対応後の最後の段階で修正する。

#### `src/pixel_tile_compiler/semantic/prompts.py`

optional MCP semantic promptが `64x64 SRPG tile` と固定文言を持つ。
Character defaultはrule providerなので初回128の主要blockerではないが、Canvas契約の一貫性のため後で動的化する。

### 3.2 既に可変寸法へ適応しやすい箇所

#### `src/pixel_tile_compiler/pixelizer/character.py`

`nearest_pixelize()` と `fit_character_to_canvas()` は、既に引数として `size` / `canvas_size` / `frame_size` を受け取る。
固定defaultはあるが、アルゴリズム自体は64専用ではない。

#### `src/pixel_tile_compiler/pixelizer/spatial.py`

docstringはfixed 64x64となっているが、実装は `ir.width` / `ir.height` を使って出力を生成している。
Phase 1ではTerrainの非64化は行わないものの、IRのdimensionを緩和しても構造上は追従できる。

#### `src/pixel_tile_compiler/tile/repeat_preview.py`

previewは `tile.width` / `tile.height` から生成しており、サイズ依存のhardcodeはない。

#### `src/pixel_tile_compiler/tile/repeatability.py`

metrics/optimizationは画像実寸からheight/widthを取得しており、64固定ではない。
Character `object` ではrepeatability自体を適用しない。

### 3.3 今回変更してはいけない64px契約

次は64を持つが、単体Output Canvasとは意味が違う。

- `MapCompilerConfig.tile_size=64`
- `map/metrics.py` のMAP cell size
- `sheet/normalizer.py` のgeneration-first tile normalize
- `sheet/splitter.py` のSheet cell output
- `pixel_grammar/config.py` の64×64 Study
- `pixel_hierarchy/config.py` の64×64 Study
- `mixed_palette_study.py` の64×64 tile
- `transition_network/road_graph.py` の64×64 network tile
- `transition_network/river_study.py` の64×64 network tile
- reusable Tilesetのtile size

これらへ `CanvasSpec` を横流ししない。

---

## 4. 現在のPR #1との関係

PR #1はmerge済みで、mainには次の設計文書が存在する。

```text
docs/character_palette_density_study_spec.md
docs/character_palette_density_implementation_prompt.md
```

ただし、現在のmainにはまだ以下の実装はない。

```text
CharacterDetailLevel
character_detail.py
study-character-palette-density
```

したがって、この128対応は次の依存関係で扱う。

```text
Canvas core ───────────────┐
                           ├→ Native 128 B24 Study
Character Detail実装 ─────┘
```

Canvas coreは先に独立して実装可能である。
`B24` presetとresolution-aware detail thresholdは、PR #1で定義したDetail Density実装が入った後に接続する。

---

## 5. `CanvasSpec` 契約

### 5.1 データ型

候補実装:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CanvasSpec:
    width: int = 64
    height: int = 64

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("canvas dimensions must be positive")

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height
```

Canvasにstyleやpaletteを入れない。

禁止:

```python
CanvasSpec(width=128, height=128, palette=24, detail="balanced")
```

geometryだけを保持する。

### 5.2 `CompilerConfig` への統合

推奨形:

```python
@dataclass
class CompilerConfig:
    output_root: Path = ...
    canvas: CanvasSpec = field(default_factory=CanvasSpec)
    ...

    @property
    def width(self) -> int:
        return self.canvas.width

    @property
    def height(self) -> int:
        return self.canvas.height
```

内部利用箇所が少ないため、正本は `canvas` へ移す。
`width` / `height` のread-only propertyはmigration補助として残してよい。

現在 `CompilerConfig(width=64, height=64)` を直接使うrepo内呼出しが存在しないことを実装開始時に再確認する。
外部利用互換性を優先する場合は、1 releaseだけdeprecated constructor shimを設けてもよいが、
新規コードは必ず `CanvasSpec` を使う。

### 5.3 metadata互換

`config.as_dict()` は最低限次を持つ。

```json
{
  "canvas": {
    "width": 128,
    "height": 128
  }
}
```

既存の解析スクリプトが `config.width` / `config.height` をJSON上で参照している可能性を考慮し、
移行期間は互換aliasを残してよい。

```json
{
  "canvas": {"width": 128, "height": 128},
  "width": 128,
  "height": 128
}
```

ただし正本は`canvas`であることをコメントとdocsに明記する。

---

## 6. Phase 1の許可範囲

可変Output Canvasを全経路へ一気に広げない。

初回は次だけを正式対応する。

```text
purpose = character
tile_mode = object
pixelization_mode = nearest
```

64以外のCanvasでこれを満たさない場合は明示的にrejectする。

概念形:

```python
if config.canvas.size != (64, 64):
    if not (
        config.tile_mode == "object"
        and config.pixelization_mode == "nearest"
    ):
        raise ValueError(
            "non-64 output canvas is currently supported only for object/nearest compilation"
        )
```

これにより、Terrain/Map/Studyの既存64契約を暗黙に変えずにCharacterだけを拡張できる。

---

## 7. IR契約

### 7.1 `TileIR`

現在の

```python
width: Literal[64] = 64
height: Literal[64] = 64
```

を、positive integerへ緩和する。

候補:

```python
width: int = Field(default=64, ge=1)
height: int = Field(default=64, ge=1)
```

defaultは64を維持する。

### 7.2 Builder

`build_tile_ir()` は必ずOutput Canvasを明示する。

```python
return TileIR(
    width=config.canvas.width,
    height=config.canvas.height,
    ...
)
```

これにより、rasterizerがIR defaultへ暗黙依存しない。

### 7.3 IR version

初回では `version="0.1"` を維持してよい。

理由:

- 既存fieldの意味は変えない
- 64 defaultを維持する
- 64以外を「追加で受理」する緩和変更である

ただし、IRを外部永続formatとして正式固定する段階ではversioning policyを別ADRにする。

---

## 8. Character Layout契約

### 8.1 64基準のReference Layout

現行の採用候補をreferenceとして固定する。

```python
REFERENCE_CANVAS = CanvasSpec(64, 64)
REFERENCE_FRAME_WIDTH = 54
REFERENCE_FRAME_HEIGHT = 54
REFERENCE_BOTTOM_MARGIN = 7
```

### 8.2 解決済みLayout型

候補:

```python
@dataclass(frozen=True)
class CharacterLayout:
    frame_width: int
    frame_height: int
    bottom_margin: int
```

必要ならreferenceを持つPolicyを別型にする。

```python
@dataclass(frozen=True)
class CharacterLayoutPolicy:
    reference_canvas: CanvasSpec = CanvasSpec(64, 64)
    reference_frame: tuple[int, int] = (54, 54)
    reference_bottom_margin: int = 7

    def resolve(self, canvas: CanvasSpec) -> CharacterLayout:
        ...
```

### 8.3 丸め規則

Python builtin `round()` のbanker's roundingへ無意識に依存しない。
正の整数についてhalf-upを明示する。

```python
def scale_half_up(value: int, target: int, reference: int) -> int:
    return (value * target + reference // 2) // reference
```

結果:

```text
64×64   → frame 54×54,   bottom 7
128×128 → frame 108×108, bottom 14
128×96  → frame 108×81,  bottom 11
96×128  → frame 81×108,  bottom 14
```

### 8.4 Placement

`fit_character_to_canvas()` の基本契約を維持する。

- visible bboxを抽出
- source aspect ratioを維持
- resolved frame内へ最大fit
- 水平中央
- bottom anchor
- transparent canvasへ配置

長方形Canvasでも人物を縦横にstretchしない。

### 8.5 Outline

outline widthはCharacter layoutの比例拡大へ自動連動させない。

現在の1px outlineは「空間寸法」ではなくPixel Grammar/Styleの一部である。
128だから自動で2pxへする、という判断は初回では行わない。

既定は`outline=off`なので、Native128 Studyでもoffを基準にする。
将来outlineの太さをStudyする場合は別parameterにする。

---

## 9. Native 128 rasterization契約

### 9.1 必須経路

128×128 characterは以下で生成する。

```text
load source
→ RGBA
→ background resolution
→ visible bbox extraction
→ 128用resolved frameへfit
→ sourceから128 canvasへNEAREST rasterize
→ color conditioning
→ palette quantization
→ character detail pass
→ optional outline
→ metrics / export
```

### 9.2 禁止する経路

```text
source → 64 final → resize 128
source → 64 intermediate → postprocess → 128
source → 64 bbox placement → 2x upscale
```

finalだけ128ならよい、とは判定しない。

### 9.3 Native性の検証

synthetic sourceに、64へ一度落とすと消えるが128では残る構造を置く。

例:

- 1〜2 source-pixel幅の縞
- 近接した高コントラスト点
- 64 samplingでは同じcellへ吸収される2特徴

同じsourceから

```text
A = native 128 compile
B = native 64 compile → nearest 2x
```

を生成し、fixture上で `A != B` になることをテストする。

これは「すべての実画像で必ず差が出る」という品質条件ではない。
実装が64 intermediateへ退化していないことを検出するための構造テストである。

---

## 10. Pipeline修正点

### 10.1 `_pixelize_source`

Character側は `config.canvas.size` とresolved layoutを使う。

概念形:

```python
layout = resolve_character_layout(config.canvas)

fitted = fit_character_to_canvas(
    background_resolved,
    canvas_size=config.canvas.size,
    frame_size=(layout.frame_width, layout.frame_height),
    bottom_margin=layout.bottom_margin,
    outline_width=...,
)
return nearest_pixelize(fitted, config.canvas.size)
```

`fit_character_to_canvas()` がすでにfinal canvasを生成するため、
その後のsame-size `nearest_pixelize` は実質identityになる。
実装時に責務を整理し、不要なら

```text
extract/fit/resize/place
```

を1つのCharacter rasterize helperへまとめてもよい。

ただし、既存64出力のpixel-identical regressionを先に固定してから行う。

### 10.2 Baseline

次のdefault64依存を除去する。

```python
generate_baseline_nearest(...)
generate_baseline_bicubic_quantized(...)
```

single-image pipelineから呼ぶときは必ず

```python
size=config.canvas.size
```

を渡す。

より安全にするならhelperの`size`を必須引数化する。
MAP compilerは既に`config.output_size`を明示しているので、その利用を壊さない。

### 10.3 Debug artifacts

Output raster系debug:

```text
raw_pixelized
color_conditioned
quantized
final
```

はOutput Canvas寸法を持つ。

Analysis系debug:

```text
normalized
smooth
edges
regions
```

はwork_size側の寸法を持ってよい。

metadataで両者を区別する。

---

## 11. `B24` preset

### 11.1 定義

Character Detail実装後、opt-in presetとして次を定義する。

```text
name = b24
palette_budget = 24
character_detail_level = balanced
```

Character purposeの既存安定設定も組み合わせる。

```text
tile_mode = object
pixelization_mode = nearest
repeat_opt_enabled = false
dither = off
background_mode = auto
outline = off
smoothing = false
```

### 11.2 Canvasを含めない

禁止:

```text
b24 = 128×128 + 24 colors + balanced
```

正しくは:

```text
CanvasSpec(128,128) + preset=b24
```

### 11.3 適用順序

config生成のprecedenceを曖昧にしない。

推奨:

```text
base defaults
→ purpose defaults
→ preset defaults
→ explicit user overrides
```

これにより、例えば

```text
--purpose character --preset b24 --palette 32
```

は明示overrideとして32を採用できる。

現行 `compiler_config_for_purpose()` はcharacter defaultを`overrides.update()`で後勝ちさせるため、
Preset導入時にresolverの責務を整理する。

候補:

```python
build_compiler_config(
    purpose="character",
    preset="b24",
    canvas=CanvasSpec(128, 128),
    explicit_overrides={...},
)
```

初回は既存関数を壊さず内部resolverを追加してもよい。

---

## 12. Resolution-aware Character Detail

この節はPR #1で定義したCharacter Detail実装が入った後に適用する。

### 12.1 64px基準値

PR #1の初期候補:

```text
balanced: low-contrast component area <= 2px
sparse:   low-contrast component area <= 4px
```

### 12.2 空間scale

```python
scale = min(canvas.width / 64.0, canvas.height / 64.0)
```

短辺を基準とする。

理由:

- 128×128は2倍の線形pixel density
- 128×64は短辺が64なので、2次元面積として4倍扱いしない
- 長方形の長辺だけでmicro component定義が過剰に緩くなるのを防ぐ

### 12.3 面積scale

```python
scaled_area = round_half_up(base_area * scale * scale)
```

少なくとも1pxにclampする。

初期値:

```text
               64×64   128×128
balanced          2        8
sparse            4       16
```

### 12.4 スケールしない値

- RGB contrast threshold
- palette budget
- alpha threshold
- semantic protection rule
- tie-break rule

色距離はCanvas geometryではない。

### 12.5 将来radiusを追加した場合

neighborhood radiusなどの「長さ」は線形scale `s`、
component areaなどの「面積」は `s²` で扱う。
magic numberを関数内へ散らさずprofile resolverに集約する。

---

## 13. CLI契約

### 13.1 `compile`

追加候補:

```text
--width INTEGER
--height INTEGER
```

default:

```text
--width 64
--height 64
```

例:

```powershell
pixel-tile compile aria.png `
  --purpose character `
  --width 128 `
  --height 128 `
  --palette 24 `
  --output output/aria_128
```

B24実装後:

```powershell
pixel-tile compile aria.png `
  --purpose character `
  --width 128 `
  --height 128 `
  --preset b24 `
  --output output/aria_128_b24
```

### 13.2 長方形

Core/CLIは初回から長方形を扱う。

```powershell
pixel-tile compile portrait.png `
  --purpose character `
  --width 128 `
  --height 96
```

### 13.3 Terrain guard

Phase 1では

```powershell
pixel-tile compile grass.png --purpose terrain --width 128 --height 128
```

を黙って実行しない。
明示的にunsupportedとして失敗させる。

### 13.4 Help text

app全体を「64x64専用」と表現しない。
ただしMAP系commandのhelpでは`tile_size=64`契約を維持する。

---

## 14. Semantic Adapter

`semantic/prompts.py` の64固定文言を、Output Canvasへ追従させる。

候補:

```python
def build_prompt(
    tile_mode: str,
    palette_budget: int,
    canvas: CanvasSpec,
) -> str:
    ...
```

またはwidth/heightを渡す。

Characterでは

```text
Target output canvas: 128x128
```

Terrain 64では従来通り64x64となる。

semantic providerが画像生成をするわけではなく、最終寸法の制約を判断材料として渡すだけである。

---

## 15. Metadata契約

`metadata.json` に最低限次を追加する。

```json
{
  "output_canvas": {
    "width": 128,
    "height": 128
  },
  "analysis_canvas": {
    "width": 256,
    "height": 256
  },
  "character_layout": {
    "frame_width": 108,
    "frame_height": 108,
    "bottom_margin": 14,
    "reference_canvas": [64, 64]
  },
  "native_resolution": true
}
```

`native_resolution=true` は「64経由ではなくsourceから対象Canvasへ直接rasterizeした」という
pipeline contractを示す。
画質が優れていることを意味しない。

既存の `config` snapshotも維持する。

---

## 16. Native Resolution Study

Core対応とCharacter Detail実装後、128版B24を次の最小Studyで比較する。

### 16.1 比較条件

同一Source、同一B24で3条件を作る。

```text
A: native 64×64
B: Aをnearestで128×128へ2x拡大したcontrol
C: native 128×128
```

重要なのは `B vs C`。

128-nativeが単なる2倍拡大と同じなら、native Canvas化の視覚的利益は観測できていない。
ただし自動metricだけで失敗判定はしない。

### 16.2 出力候補

```text
<output_root>/<case_id>/
├─ source_snapshot.png
├─ native/
│  ├─ 64x64/
│  │  ├─ final.png
│  │  └─ metadata.json
│  └─ 128x128/
│     ├─ final.png
│     └─ metadata.json
├─ controls/
│  └─ 64x64_upscaled_to_128.png
├─ previews/
│  ├─ native_64_actual.png
│  ├─ native_128_actual.png
│  └─ comparison_128_canvas.png
├─ metrics/
│  └─ resolution_metrics.json
├─ review/
│  └─ review_template.json
└─ manifest.json
```

### 16.3 自動metrics

最低限:

```text
canvas_width
canvas_height
resolved_frame_width
resolved_frame_height
bottom_margin
palette_budget
actual_palette_count
detail_level
visible_bbox
visible_pixel_count
alpha_binary
output_sha256
native_128_equal_to_64_upscaled
changed_pixels_vs_64_upscaled
changed_ratio_vs_64_upscaled
```

可能なら、128画像の2×2 blockがどの程度同色で埋まっているかを示す
`two_by_two_uniform_block_ratio` を補助metricにしてよい。
値が高いほど「64の単純2倍に近い可能性」を示すが、品質scoreとして使わない。

### 16.4 Human Review

アリアでは少なくとも次を見る。

- 左右の目の形
- 前髪・側面髪の内部明暗
- 濃紺リボンの形
- ケープと白い衣装の分離
- 金縁が線として読めるか、ノイズになるか
- スカートの陰影
- ブーツの輪郭と色差
- 128で増えたdetailが「情報」か「ノイズ」か
- 64 actualと128 actualをそれぞれ100%表示した場合の読みやすさ

このStudy完了後に、128で16/24/32色比較を追加するか判断する。
最初から全組合せへ拡張しない。

---

## 17. GUI設計

GUIはcore/CLIの後に接続する。
GUI独自のCanvas計算を作らない。

### 17.1 Width / Height

用途がCharacterの場合、幅・高さ入力を表示する。

```text
幅:  128
高さ: 128
```

正方形固定checkboxを将来追加してもよいが、Core契約は最初から長方形を許す。

### 17.2 PixelCanvas

現在の

```text
size = 64 * zoom
range(65)
```

を廃止する。

画像実寸またはCanvasSpecから

```text
vertical lines:   0..width
horizontal lines: 0..height
```

を描画する。

縦線の長さは `height * zoom`、横線の長さは `width * zoom`。

### 17.3 Coordinate clamp

`CanvasState.pixel_at()` は将来的にwidth/heightを持ち、

```text
0 <= x < width
0 <= y < height
```

へclampする。

現在のように下限だけclampして上限を無視する状態を残さない。

### 17.4 Rendering

Pixel previewは引き続きnearest / FastTransformationを使用し、SmoothPixmapTransformを使わない。

---

## 18. 変更対象matrix

### Coreで変更する

```text
src/pixel_tile_compiler/config.py
src/pixel_tile_compiler/ir/schema.py
src/pixel_tile_compiler/ir/builder.py
src/pixel_tile_compiler/pipeline/compiler.py
src/pixel_tile_compiler/pixelizer/character.py  # 必要最小限
src/pixel_tile_compiler/semantic/prompts.py      # Canvas整合
src/pixel_tile_compiler/semantic/mcp_provider.py # prompt引数変更時
src/pixel_tile_compiler/cli.py
```

### Detail実装後に変更する

```text
src/pixel_tile_compiler/pixelizer/character_detail.py
Character detail profile/resolver
B24 preset resolver
Character Study runner
```

### GUI Phaseで変更する

```text
src/pixel_tile_compiler/gui/canvas.py
src/pixel_tile_compiler/gui/main_window.py
```

### 今回のCanvas一般化で変更しない

```text
MapCompilerConfig.tile_size=64
sheet tile_size=64
reusable tileset tile_size=64
pixel grammar study tile_size=64
pixel hierarchy study tile_size=64
road/river network tile_size=64
mixed palette study tile_size=64
```

---

## 19. テスト仕様

TDDで次の順に追加する。

### 19.1 `CanvasSpec`

- defaultは64×64
- 128×128を受理
- 128×96を受理
- width/height <= 0をreject
- `.size` がtupleを返す

### 19.2 64互換freeze

既存Character synthetic fixtureを、変更前mainの出力と比較する。

最低限:

```text
final size
alpha bytes
visible bbox
palette count
output pixel bytesまたはSHA-256
```

可変Canvas化によって無指定64出力を変えない。

Terrainの既存64 regressionも通す。

### 19.3 IR

- default TileIRは64×64
- 128×128をround-trip可能
- 128×96をround-trip可能
- builderがCompilerConfig.canvasをIRへコピー

### 19.4 Character layout

固定期待値:

```text
64×64   -> frame 54×54,   bottom 7
128×128 -> frame 108×108, bottom 14
128×96  -> frame 108×81,  bottom 11
96×128  -> frame 81×108,  bottom 14
```

- 横長sourceでもaspect ratio維持
- 縦長sourceでもaspect ratio維持
- 完全透明sourceでも失敗しない
- top/bottom/sideに触れるsourceをclipしない

### 19.5 Pipeline 128

- `final.png` = 128×128
- `ir.json` width/height = 128
- `baseline_nearest.png` = 128×128
- `baseline_bicubic_quantized.png` = 128×128
- alphaはbinary
- actual visible RGB <= palette budget
- metadata output_canvas = 128×128
- resolved layout = 108×108 / bottom14

### 19.6 Rectangle

128×96で

- final = 128×96
- IR = 128×96
- baselineも128×96
- bboxがCanvas外へ出ない
- source aspect ratio維持

### 19.7 Scope guard

Phase 1で

```text
terrain + 128×128
repeatable + 128×128
region + 128×128
```

が明示的にrejectされること。

既存 `MapCompilerConfig(tile_size=128)` が引き続きrejectされること。

### 19.8 Native性

crafted fixtureで

```text
native_128 != nearest_upscale(native_64)
```

を確認する。

### 19.9 CLI

```text
compile --purpose character --width 128 --height 128
compile --purpose character --width 128 --height 96
```

が成功し、metadata/final寸法が一致する。

非64Terrainは失敗する。

### 19.10 Detail scaling（Detail実装後）

- 64 balanced area threshold = 2
- 128 balanced = 8
- 64 sparse = 4
- 128 sparse = 16
- contrast threshold不変
- alpha mask不変
- detailed identity維持

### 19.11 B24（Detail実装後）

- palette=24
- detail=balanced
- Canvasを変更しない
- 64/128どちらでも同じpresetを適用可能
- explicit CLI overrideがpresetより優先

### 19.12 GUI（最後）

- 64×64 grid
- 128×128 grid
- 128×96 grid
- pixel coordinate upper-bound clamp
- nearest preview

---

## 20. 実装Slice

大きな一括PRにせず、以下の順を推奨する。

### Slice A: Regression Lock

- 現行64Character outputをfreeze
- IR/CLI/terrainの64契約を確認

### Slice B: Canvas Core

- `CanvasSpec`
- `CompilerConfig.canvas`
- `TileIR` dimension緩和
- builder連携
- non64 scope guard

### Slice C: Native Character Raster

- resolution-aware Character layout
- pipeline/baselineのCanvas対応
- 128×128 / rectangle tests
- metadata
- native-vs-upscale structural test

### Slice D: CLI

- `--width` / `--height`
- help更新
- CLI regression

### Slice E: Detail + B24

PR #1仕様を実装済みにした後:

- resolution-aware detail thresholds
- B24 preset
- precedence resolver

### Slice F: Native Resolution Study

- 64-native
- 64→128 nearest control
- 128-native B24
- metrics / board / Human Review

### Slice G: GUI

- width/height controls
- rectangular PixelCanvas
- dynamic grid

---

## 21. 受け入れ条件

Core 128対応の完了条件:

- 既存無指定64×64出力がpixel-identicalに維持される。
- `CanvasSpec(128,128)` をCharacter `object/nearest`へ渡せる。
- 128 finalは元Sourceから直接生成され、64 raster intermediateを通らない。
- 128 final / IR / baseline / metadataの寸法が一致する。
- 128 Character layoutが108×108 frame / bottom14になる。
- 128×96など長方形Canvasでもaspect ratioとbottom anchorを維持する。
- 非64TerrainをPhase 1で黙って受理しない。
- MAP/Sheet/Tileset/Networkの`tile_size=64`契約を変更しない。
- 既存MAP・Tileset・Pixel Grammar・Road/Riverテストが回帰しない。
- 新しい画像処理dependencyを追加しない。

B24/Studyまでの完了条件:

- B24は24色+balancedとしてopt-inで利用できる。
- B24はCanvas sizeを内包しない。
- Detailの空間閾値が64/128で解像度依存になる。
- 64-native / 128-native / 64-upscale controlを同一Sourceで比較できる。
- Human Review前に自動metricだけで128の勝者を決めない。

---

## 22. 明示的な非目標

この設計で同時に行わないもの:

- `tile_size=64` の全面廃止
- MAPセルの128化
- terrain region-aware rendererの128対応
- generation-first Sheetの128 tile化
- reusable Tilesetの128化
- animation/sprite sheetの共通anchor設計
- outline幅の自動2倍化
- 128に最適なpaletteを24色と確定すること
- arbitrary large canvasの性能最適化

128 Characterで効果が確認できた後、必要なら別仕様で一般化する。

---

## 23. 完了報告に含めるもの

実装担当は最低限次を報告する。

```text
1. 変更したサイズ契約
2. 64互換testの結果
3. 128×128 testの結果
4. 長方形testの結果
5. native-vs-upscale fixtureの結果
6. IR / baseline / metadata寸法一致
7. MAP/Tile系64 contractの回帰結果
8. 実行したtest command
9. 未検証事項
10. Native 128 B24 Studyの出力先（Study実装後）
```

実画像の採否は実装完了条件と混同せず、Study/Human Reviewとして別に記録する。
