# ADR-0004: Output Canvas と Tile Size を別契約にする

- Date: 2026-09-07
- Status: Proposed
- Scope: 単体画像コンパイル、特にキャラクター `object/nearest` 経路の可変出力サイズ
- Related: [ADR-0003 Logical MAP-first Map Visual Bake](./0003-map-visual-bake.md)、[Character Palette × Detail Density Study](../character_palette_density_study_spec.md)

## 1. 背景

現在の `PixelTileCompiler` は `CompilerConfig.width=64` / `height=64` を持つ一方、
`CompilerConfig.__post_init__` で `(64, 64)` 以外を拒否している。
`TileIR` も `width: Literal[64]` / `height: Literal[64]` で固定されている。

しかし、このリポジトリには異なる意味の「64」が共存している。

1. **単体画像の最終出力解像度**
   - キャラクターや単体objectを何pxのPNGへ再構成するか。
   - 今回は `64×64` と `128×128` を比較したい。
2. **SRPG MAPの論理セル／配備単位**
   - `MapCompilerConfig.tile_size=64`
   - MAP Visual Bake、Tileset、Sheet split、Road/Riverなどの64pxセル契約。

ADR-0003では、MAP側の64pxを「画像の品質上限」ではなく、論理MAPの1セルに対応する
**配備単位**として明示している。

したがって、キャラクターのネイティブ128px化を実現するために、リポジトリ中の
`64` を一括置換したり、`tile_size` を128へ変更したりしてはいけない。

## 2. 判断

### 2.1 `CanvasSpec` を単体コンパイラの出力契約として導入する

単体画像の最終ラスタサイズは `CanvasSpec(width, height)` で表す。

概念形:

```python
@dataclass(frozen=True)
class CanvasSpec:
    width: int = 64
    height: int = 64

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height
```

既定値は `64×64` のままにし、既存利用者の出力を変えない。

`CanvasSpec` は「ゲーム上の1セルが何pxか」を意味しない。
あくまで、**今回の1回の単体コンパイルで何pxのPNGを生成するか**を意味する。

### 2.2 `tile_size=64` は別契約として維持する

次の領域は今回の可変Canvas化の対象外とする。

- `MapCompilerConfig.tile_size`
- MAP-first compilerのcell/chunk size
- generation-first Sheetのsplit/normalize tile size
- reusable Tilesetのtile size
- Pixel Grammar / Pixel Hierarchyの64×64 Study
- Road Graph / River Graphの64×64 network tile
- Mixed Palette Studyの64×64 tile

これらは単体Character Canvasとは別のドメイン契約である。

Phase 1では、非64 Canvasを許可するのは原則として
`purpose=character` / `tile_mode=object` / `pixelization_mode=nearest` の経路だけとする。
地形のregion-aware経路を128へ一般化することは別の変更として扱う。

### 2.3 128×128は64×64の2倍拡大ではない

128出力は次の経路で**元入力から直接**再コンパイルする。

```text
source
→ background / alpha resolution
→ visible bbox
→ 128用character frameへfit
→ sourceから128 canvasへnearest sampling
→ palette quantization
→ 128用detail-density pass
→ optional outline
→ 128×128 PNG
```

禁止する実装:

```text
source → 64×64 compile → 2x nearest upscale → 128×128
```

また、内部で一度64×64へ落としてから128へ戻す経路も禁止する。
128では128のpixel budgetを使い、髪、目、衣装、陰影などの情報をより細かい論理pixelへ
再配分できることを目的とする。

### 2.4 Analysis Canvas と Output Canvas を分離する

現行の `work_size=256` は解析用の正規化サイズであり、最終PNGのサイズではない。

```text
Analysis Canvas: work_size × work_size
Output Canvas:   CanvasSpec.width × CanvasSpec.height
```

Characterの`nearest/object`ラスタ化は、現在と同様に解析用画像を最終画素のソースにせず、
背景解決済みの元画像から直接fitする。

これにより、`work_size=256` と `output=128×128` を独立して扱う。

### 2.5 Character layoutは64px基準から決定論的に解決する

現在の採用候補値:

```text
reference canvas: 64×64
character frame:  54×54
bottom margin:     7px
```

これを64固定値として散在させず、参照レイアウトから出力Canvasへ解決する。

初期規則は各軸独立の比例スケールとする。

```text
frame_width  = round_half_up(54 * canvas_width  / 64)
frame_height = round_half_up(54 * canvas_height / 64)
bottom       = round_half_up( 7 * canvas_height / 64)
```

したがって:

```text
64×64   → frame 54×54,   bottom 7
128×128 → frame 108×108, bottom 14
128×96  → frame 108×81,  bottom 11
```

人物そのものの縦横比は `fit_character_to_canvas` で維持する。
長方形Canvasでも、Canvasの縦横比に人物を引き伸ばしてはいけない。

### 2.6 Palette/Detail preset と Canvas geometry を分離する

`B24` は次のスタイルpresetとして扱う。

```text
palette_budget = 24
character_detail_level = balanced
```

`B24` に `64×64` や `128×128` を含めない。

```text
B24 + CanvasSpec(64, 64)
B24 + CanvasSpec(128, 128)
```

を同じpresetで比較可能にする。

なお、2026-09-07時点のmainには、PR #1で追加された
Character Palette × Detail Densityの**仕様書**は存在するが、
`CharacterDetailLevel` とStudy実装そのものはまだ存在しない。
したがって、B24の実装はdetail-density実装との依存関係を明示して進める。

### 2.7 Detail Densityの空間閾値を解像度依存にする

64px向けのcomponent面積閾値を128pxへそのまま持ち込まない。

短辺を基準とする線形scaleを

```text
s = min(canvas_width / 64, canvas_height / 64)
```

とし、面積閾値は `s²` でスケールする。

例:

```text
64×64 balanced base area <= 2px → 128×128では <= 8px
64×64 sparse   base area <= 4px → 128×128では <= 16px
```

RGB contrast thresholdは空間解像度ではなく色距離の閾値なのでスケールしない。
シルエット境界保護などのsemantic ruleも維持する。

`128×64` のような長方形で面積閾値を2倍密度として扱わないため、短辺scaleを採用する。

## 3. 互換性

- `CanvasSpec()` は64×64。
- 既存の無指定compileはpixel-identicalな64×64出力を維持する。
- `TileIR` のdefault width/heightは64を維持する。
- MAP/Sheet/Tilesetの`tile_size=64`は変更しない。
- 既存の64×64 test fixtureを128へ書き換えない。
- metadataにはOutput CanvasとAnalysis Canvasを区別して記録する。

## 4. 実装順序

1. 現行64×64出力を回帰テストでfreezeする。
2. `CanvasSpec` とIRの可変dimension契約を導入する。
3. pipeline / baseline / CLIをCanvasSpecへ接続する。
4. Character layoutを解像度依存で解決する。
5. 128×128 native compileをsynthetic fixtureで検証する。
6. Character Detail Density実装を解像度依存にする。
7. `B24` presetを追加する。
8. 64-native / 128-native / 64→128 upscale controlのStudyを行う。
9. 最後にGUIを同じCanvas契約へ接続し、長方形を扱う。

## 5. 非目標

このADR単体では以下を決めない。

- 地形タイルを128×128へ移行すること
- SRPG MAPのcell sizeを128へ変更すること
- 既存Asset Packageの64px chunk契約を変更すること
- 128pxで最適なPalette Budgetを24色と断定すること
- 128pxで最適なDetail Densityをbalancedと断定すること
- GUIを先に独自実装すること

128でのB24は最初の比較条件であり、品質評価の結果によって16/24/32色やdensityを再Studyする。

## 6. 成功条件

- `compile --purpose character --width 128 --height 128` が元入力から直接128×128を生成できる。
- 64×64の既定出力は変更されない。
- 128の生成経路に64×64 raster intermediateが存在しない。
- IR、final、baseline、metadataのOutput Canvas寸法が一致する。
- 128×128のcharacter layoutは初期規則で108×108 frame / bottom 14になる。
- 長方形Canvasでも人物のaspect ratioを維持する。
- MAP/Sheet/Tilesetの`tile_size=64`契約は維持される。
- B24とCanvas geometryが独立して組み合わせ可能である。
