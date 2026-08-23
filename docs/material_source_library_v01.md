# Material Source Library v0.1

## 目的

Material Exemplarを単発の入力画像ではなく、後続のSurface / Road / River Rendererから参照できる第一級の資産として管理する。v0.1では `grass`、`dirt`、`water`、`stone` を対象にする。

## 実験フロー

```text
Prompt Matrix
  → Source Candidate / Material Source Card
  → Fingerprint / Source Gate
  → Compile Probe (8 variants, 28-color budget)
  → Map Probe (10×10)
  → separate fitness scores
  → family ranking / Top2 promotion
  → MaterialLibrary resolver
```

## 保存形式

各候補は `e2e/material_source_library_v01/candidates/<material>/<source_id>/` に、`source.png`、`prompt.txt`、`source_card.json`、`fingerprint.json`、`validation.json`、`compile_probe/`、`record.json` を保存する。昇格候補は同じ構造で `material_library/<material>/accepted/` へコピーし、`family.json` とルートの `index.json` から参照する。

`HumanReview` は自動で埋めず、`rating: null`、`accepted: null`、`notes: null` のままにする。

## 指標

- Source: stationarity、brightness/color spatial variance、texture density variance、frequency bands、autocorrelation、orientation、center dominance、landmark risk、feature scale
- Compile: palette usage、readability、semantic fitness、edge continuity、grammar score
- Map: edge discontinuity、grid visibility、periodicity risk、map readability
- Summary: `source_quality_score`、`compile_fitness_score`、`map_fitness_score`、`downstream_fitness_score`、`library_fitness_score`

Source qualityとdownstream fitnessは別々に保持する。相関はmaterialごとに `n=8` の探索的な値として保存し、因果関係とは解釈しない。

## 既知の限界

現状のCLIは画像生成サービスを直接呼び出さない。入力assetがある候補は既存画像を決定論的に変換し、stone等で不足する場合はprocedural adapterを使う。そのため、今回の比較結果はSource管理・Compiler接続・再現性の検証として扱い、実画像生成品質の結論にはしない。採用前に人手レビューと、将来のt2i generation metadataの差し替えが必要である。
