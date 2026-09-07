# ADR-0005: キャラクター高解像度版はNative Canvasを正本候補にする

- Date: 2026-09-07
- Status: Accepted for Character high-resolution experiments
- Scope: Character `object/nearest` の64×64 / 128×128出力とB24 Study
- Related: [ADR-0004 Output Canvas と Tile Size を別契約にする](./0004-output-canvas-vs-tile-size.md)、[Character Palette × Detail Density Study](../character_palette_density_study_spec.md)、[Native Character Canvas 128仕様](../character_native_128_canvas_spec.md)

## 1. 背景

64×64の完成画像をNearestで2倍拡大すると、ファイル寸法は128×128になるが、
128×128のpixel budgetを新しい情報の配置には使えない。

今回、同一の元絵・同一のB24条件で次の比較を実施した。

```text
A: native 64×64
B: AをNearest 2xしたcontrol
C: 元絵から直接compileしたnative 128×128
```

元絵は上書きせず、Study内へsnapshotとして保存した。

## 2. 判断

### 2.1 128×128 Characterは元絵から直接compileする

高解像度版のCharacter assetは、次のNative経路を正本候補とする。

```text
Source
→ background / alpha resolution
→ visible bbox
→ 128用Character frameへfit
→ Sourceから128×128へNearest raster
→ palette quantization
→ 128用detail density
→ export
```

次の経路は高解像度版の生成方法として採用しない。

```text
Source → 64×64 final → Nearest 2x → 128×128
```

64×64出力は互換性と比較対象として引き続き保持する。64を128へ拡大したものを、
128 Nativeの代替物や正本とは扱わない。

### 2.2 CanvasとStyle presetは分離する

`B24`は次のStyle presetであり、Canvas geometryを含めない。

```text
B24 = palette 24 + balanced character detail
```

したがって、次は同じB24を共有できる。

```text
B24 + CanvasSpec(64, 64)
B24 + CanvasSpec(128, 128)
B24 + CanvasSpec(128, 96)
```

この実験結果だけで、24色・balancedを全Characterの最終defaultとは断定しない。
複数CharacterでのHuman Reviewを経てから、production presetの適用範囲を決める。

### 2.3 MAP/Tilesetの64px契約は変更しない

この判断は単体CharacterのOutput Canvasに限定する。
`MapCompilerConfig.tile_size=64`、MAP cell、Sheet split、Tileset、Network tileなどの
論理配備単位を128へ変更しない。

## 3. 実験結果

### 3.1 Palette × Detail Density Study

アリア元絵を64×64で16/24/32色とsparse/balanced/detailedの3×3で比較した。

- B24のbalanced cellは実パレット24色だった。
- visible bboxは `[14, 3, 50, 57]`。
- visible pixel countは `1360`。
- 同一palette budget内でalpha maskは一致した。
- B24 balancedはdetailedに対してvisible pixelの整理が発生したが、輪郭は維持された。

この結果を初回のNative Resolution比較条件として採用した。

### 3.2 Native Resolution Study

同一元絵・同一B24でA/B/Cを生成した。

| 条件 | 出力 | visible bbox | visible pixels | 実パレット |
| --- | --- | --- | ---: | ---: |
| A native 64 | 64×64 | `[14, 3, 50, 57]` | 1360 | 24 |
| B 64 nearest 2x | 128×128 | Aを2倍 | A由来 | A由来 |
| C native 128 | 128×128 | `[28, 6, 99, 114]` | 5328 | 24 |

機械比較では、CはBと画素完全一致しなかった。

```text
native_128_equal_to_64_upscaled = false
changed_pixels = 4954
changed_visible_ratio = 0.929805
64 control 2x uniform block ratio = 1.0
native 128 2x uniform block ratio = 0.769531
```

これはNative 128が64×64の単純な再拡大へ退化していないことの構造的な証拠である。
自動metricだけで美的な勝者を確定したものではない。

### 3.3 Layout / Source integrity

128×128では次のlayoutが解決された。

```text
frame = 108×108
bottom margin = 14
analysis canvas = 256×256
```

元絵とStudy snapshotのSHA-256は一致した。

```text
1C372899322970B8F19F53926908FF410CF46DFADFE6CB1AE2F01A5B5E608F59
```

## 4. 実装・運用ルール

- 高解像度Characterを作るときは、元絵から対象Canvasへ直接compileする。
- 64版と128版を比較する場合は、同一Source・同一B24でA/B/Cを保存する。
- `native_128 != upscaled_64`を構造テストで確認する。
- 128の品質採否は、目・髪・リボン・ケープ・衣装・ブーツを100%表示でHuman Reviewする。
- 自動metricは候補の異同とpixel budget利用の観測に使い、採用winnerの自動決定には使わない。
- 元絵はimmutable sourceとして扱い、Native出力で上書きしない。
- GUIはCore/CLIのCanvas契約を利用し、GUI独自の解像度計算を作らない。

## 5. 成果物

実験成果物は次に保存する。

```text
e2e/character_palette_density_study_20260907/
e2e/character_native_resolution_study_20260907/
```

`e2e/`成果物は再生成可能なためGit管理外とし、再現用設定と実装・テストはGit管理する。

Native Resolution Studyの比較ボードは、
`e2e/character_native_resolution_study_20260907/previews/comparison_128_canvas.png` にある。

## 6. 未決事項

- 128×128のproduction defaultを全Characterへ適用するか。
- 16/24/32色のどれをCharacter種別ごとに採用するか。
- sparse/balanced/detailedの最終profile。
- GUIでの任意Canvas入力とRectangular PixelCanvas表示。

これらは複数CharacterのStudyとHuman Review後に別途判断する。
