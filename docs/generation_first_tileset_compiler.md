# Image-Generation-First Tileset Compiler

この実装では、Compilerの責務を「画像を下流で描き直すこと」から、
生成結果をゲーム用Tileset Assetとして確定・検証・保存することへ広げます。

## Phase 1–2 の経路

```text
TilesetSpec JSON
  ↓
GenerationRequestCompiler
  ↓
ImageGenerationAdapter
  ↓
sheet_raw.png（生成結果の不変コピー）
  ↓
Sheet validation / deterministic crop
  ↓
equal-grid split
  ↓
64×64 normalize
  ↓
manifest / validation / previews
  ↓
Tileset Asset Package
```

`TilesetSpec` が正本です。Promptは正本として保存せず、Specから再現可能な
`GenerationRequest`へコンパイルします。Material Library、Network topology、
shared edge contractは生成要求とAsset manifestの両方へ引き継ぎます。

## 仕様プリセット

- [`specs/grass_surface_v1.json`](../specs/grass_surface_v1.json)
  - Grass surface 4×4、16セル
- [`specs/dirt_road_network_v1.json`](../specs/dirt_road_network_v1.json)
  - Road network 4×4、16セル、4-neighbor topology

Roadの`NESW`は、NetworkTopologyのコネクタ契約をそのままTile manifestへ保存します。
将来のRiverは同じ契約を利用して追加しますが、Phase 1では未実装です。

## CLI

```powershell
pixel-tile compile-generation-request --spec specs/grass_surface_v1.json
pixel-tile process-generated-sheet `
  --spec specs/grass_surface_v1.json `
  --image <generated-sheet.png> `
  --output <asset-package>
pixel-tile validate-tileset <asset-package>
pixel-tile generate-tileset --spec specs/grass_surface_v1.json --output <asset-package>
```

`generate-tileset`のImageGenerationAdapterは現在MCP接続前の境界です。未設定時は
明示的にエラーで停止し、既存画像・色変換・決定論的adapterで候補を偽造しません。
外部生成済みSheetは`process-generated-sheet`で取り込めます。

## Asset Packageの保存契約

```text
generation_request.json
generation_manifest.json
tileset_spec.json
sheet_raw.png
sheet_normalized.png
sheet_normalization.json
tiles/
manifest.json
validation/report.json
previews/
```

`sheet_raw.png`は生成結果の不変コピーです。非分割サイズは設定されたcrop policy
（初期値はcenter）で決定論的に切り詰め、各セルを等分してから64×64へ正規化します。
処理済み画像は別ファイルへ保存し、生成元SHA-256・generator・model・寸法を
`generation_manifest.json`へ記録します。

## 検証の境界

Sheet構造、Grid分割、寸法、manifest完全性、タイル境界の最低限の指標、Grass repeat
preview、Road network previewを自動検証します。状態は`accepted`、`warning`、
`rejected`で表します。

Human Review、実MCP画像生成、再試行ポリシーの外部接続、River/Forest/Cliffなどは
後続フェーズです。既存のMaterial Library、Palette Study、Network/Transition実装は
削除せず、今回のgeneration-first経路から再利用できるようにしています。
