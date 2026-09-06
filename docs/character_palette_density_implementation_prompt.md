# Character Palette × Detail Density Study 実装担当への依頼

作成日: 2026-09-07  
対象仕様: [`character_palette_density_study_spec.md`](./character_palette_density_study_spec.md)

以下を実装担当への依頼文として使用する。

## 依頼

`pixelart-compiler` に、キャラクターMAP駒向けの `16 / 24 / 32 colors × sparse / balanced / detailed` の3×3比較Studyを実装してください。

目的は、現在の64×64 character compileを壊さずに、palette budgetとmicro-detail retentionを独立して比較できる実験経路を追加することです。

開始時に現在のHEAD、作業ツリー、適用される指示ファイルを確認し、既存の未コミット変更があれば保全してください。仕様は `docs/character_palette_density_study_spec.md` を正本として扱ってください。

この依頼では、まず再現可能なStudyと回帰テストを完成させます。結果を見ずにproduction defaultを変更しないでください。

## 現在の前提

基準mainにはキャラクター向け改善が既に入っています。

- `compiler_config_for_purpose("character")`
- `nearest / object / no repeat-opt / dither off`
- 背景解決後のvisible bbox fit
- `54×54` frame候補
- `bottom_margin=7`
- visible RGBのみを使うpalette quantization
- optional outlineのpalette budget予約
- GUIの「キャラクター」用途

関連実装:

```text
src/pixel_tile_compiler/config.py
src/pixel_tile_compiler/pixelizer/character.py
src/pixel_tile_compiler/pixelizer/palette.py
src/pixel_tile_compiler/pipeline/compiler.py
src/pixel_tile_compiler/cli.py
src/pixel_tile_compiler/gui/main_window.py
tests/test_character_pixelizer.py
```

既存の `src/pixel_tile_compiler/pixel_grammar/density.py` は地形/material向けです。Gaussian blur / sharpen / repeatability controlをキャラクターへそのまま流用しないでください。

## 必須の設計条件

### 1. 現行出力を互換基準にする

`CharacterDetailLevel = sparse / balanced / detailed` を追加する場合、defaultは `detailed` としてください。

同じinput・同じ既存configで `detailed` を明示しない場合も、現在のmainとpixel-identicalなcharacter outputになることを回帰テストで固定してください。

地形経路の出力を変更しないでください。

### 2. Detail Densityでalpha silhouetteを変えない

Detail passはpalette quantization後、outline前に適用してください。

```text
background
→ fit / anchor
→ nearest
→ color conditioning
→ palette quantization
→ character detail pass
→ outline
→ export
```

`sparse / balanced / detailed` の違いで変更してよいのはvisible RGBだけです。

変更禁止:

- canvas size
- alpha mask
- visible bbox
- placement
- bottom anchor
- outline policy

transparent pixelは評価・置換対象にしないでください。

### 3. 新しい色を生成しない

Detail passは既存paletteの色同士を整理する処理にしてください。補間、blur、anti-alias、新規RGB生成は禁止です。

### 4. 面積だけで小さい特徴を消さない

単純な「2px以下を全部消す」処理は禁止です。

目、リボン、靴、武器先端などは1〜数pxでもキャラクター識別に重要です。

初回MVPでは次の保護を入れてください。

- alpha silhouette境界に接するcomponentは保護
- 周囲の置換候補色と十分なRGB contrastを持つcomponentは保護
- 低コントラストのmicro componentだけを隣接palette colorへ統合
- tie-breakは決定論的

新しい色空間ライブラリは追加せず、既存依存で完結してください。

## 初期profile

数値は1か所のprofile定義へ集約し、テスト可能にしてください。

初期候補:

```text
detailed:
  cleanupなし

balanced:
  low-contrast component area <= 2px を整理候補
  silhouette boundaryは保護
  high-contrast featureは保護

sparse:
  low-contrast component area <= 4px を整理候補
  silhouette boundaryは保護
  high-contrast featureは保護
```

contrast thresholdは実装時に明示的な定数/profile値として置いてください。実画像を見て調整可能な構造にし、関数内へmagic numberを散らさないでください。

## 実装候補

責務を地形grammarから分離してください。

候補:

```text
src/pixel_tile_compiler/pixelizer/character_detail.py
```

API例:

```python
@dataclass(frozen=True)
class CharacterDetailProfile:
    max_low_contrast_component_area: int
    contrast_keep_threshold: float


def simplify_character_detail(
    image: Image.Image,
    level: CharacterDetailLevel | str,
) -> Image.Image:
    ...
```

実装は4-neighbor componentで十分です。置換先はcomponent外周に接するvisible RGBの接触数を数え、最多色を選び、同数の場合は決定論的に解決してください。

## CompilerConfig

仕様に沿って必要最小限のfieldを追加してください。

候補:

```python
CharacterDetailLevel = Literal["sparse", "balanced", "detailed"]
character_detail_level: CharacterDetailLevel = "detailed"
```

unknown valueはrejectしてください。

初回PRではGUI control追加は不要です。既存GUIのcharacter経路を壊さないでください。

CLI `compile` に `--character-detail` を追加する場合もdefaultは `detailed` とし、terrainでは無視または明示的に非適用としてください。Study runnerから直接configを組み立てられるなら、CLI `compile` 側の追加は必須ではありません。

## Study Runner

既存の実験runnerの構造を参考に、キャラクター専用Studyを追加してください。

候補:

```text
src/pixel_tile_compiler/character_study/
├─ __init__.py
├─ config.py
├─ metrics.py
└─ runner.py
```

CLI:

```powershell
pixel-tile study-character-palette-density --config experiments/character_palette_density_study.yaml
```

`--output` でconfigのoutput rootを上書きできるようにしてください。

configは最低でも以下を表現してください。

```yaml
study:
  output_root: ../e2e/character_palette_density_study
  palette_budgets: [16, 24, 32]
  detail_levels: [sparse, balanced, detailed]
  cases:
    - id: aria_front
      source: ../path/to/source.png
      review_features: [eyes, side_hair, navy_ribbon, cape, boots]
  character:
    frame_width: 54
    frame_height: 54
    bottom_margin: 7
    background_mode: auto
    outline: off
  seed: 42
```

個人環境の絶対パスをunit testへ埋め込まないでください。テスト用sourceはtmp_pathで生成してください。

## 3×3出力

各caseについて、必ず9cellを生成してください。

```text
                 16 colors     24 colors     32 colors
sparse              A1            A2            A3
balanced            B1            B2            B3
detailed            C1            C2            C3
```

出力契約:

```text
<output>/<case_id>/
├─ source_snapshot.png
├─ source.json
├─ matrix/
│  ├─ sparse/palette_16/final.png
│  ├─ sparse/palette_24/final.png
│  ├─ sparse/palette_32/final.png
│  ├─ balanced/...
│  └─ detailed/...
├─ previews/comparison_board.png
├─ previews/comparison_board_8x.png
├─ metrics/matrix_metrics.json
├─ review/review_template.json
└─ manifest.json
```

source snapshotは元ファイルを変更せずコピーしてください。manifestへsource SHA-256を保存してください。

## metrics

自動metricで「一番綺麗」を決めないでください。

最低限記録:

```text
palette_budget
actual_palette_count
palette_utilization
visible_bbox
visible_pixel_count
alpha_unique_values
alpha_binary
alpha_equal_to_detailed_same_budget
changed_visible_pixels_vs_detailed
changed_visible_ratio_vs_detailed
output_sha256
```

可能ならdetail passから以下も記録してください。

```text
low_contrast_components_removed
high_contrast_components_protected
boundary_components_protected
```

## comparison board

- 3 rows = sparse / balanced / detailed
- 3 columns = 16 / 24 / 32
- 各cellに `level / budget / actual RGB count`
- 64×64実寸のboard
- nearest 8倍確認用board

拡大previewにSmooth interpolationを使わないでください。

## review template

Human Review用JSONを出してください。自動でscoreを埋めない項目を残して構いません。

アリア初回で見る観点:

```text
eyes
side_hair
navy_ribbon
cape_vs_dress_separation
gold_trim_noise
boots
silhouette_readability
actual_size_readability
```

将来別キャラでも使えるよう、configの `review_features` をmanifest/review templateへ引き継いでください。

## テストを先に追加する

最低限、以下の失敗テストを先に置いてから実装してください。

### character detail unit tests

1. `detailed` identity
2. sparse/balancedでalpha bytes完全一致
3. transparent RGBは変更対象外
4. low-contrast 1px islandはbalanced/sparseで整理される
5. high-contrast 1px featureは保護される
6. silhouette boundary componentは保護される
7. replacementはinput palette内のみ
8. deterministic

### pipeline regression

1. default character outputが現行と同じ
2. terrain path unaffected
3. outlineありでもvisible RGB budgetを超えない
4. 16/24/32でbudgetを守る

### study runner

1. synthetic sourceから9枚生成
2. 9枚すべて64×64
3. 同budgetの3densityでalpha完全一致
4. manifestに9cell
5. boardと8x board生成
6. source hash保存

既存テストを弱めたり削除して通さないでください。

## 実画像確認

unit test完了後に、実際のアリア生成原画でStudyを1回実行してください。

既存docsで使っている候補パス:

```text
G:\マイドライブ\習作\output\imagegen\アリア_MAP駒_64x64_初案\アリア_MAP駒_正面_生成原画_v3.png
```

存在しない環境では失敗扱いにせず「実画像未検証」と報告してください。repo内testを外部Drive依存にしないでください。

確認する特徴:

- 目
- 前髪/側面の髪
- 濃紺リボン
- ケープ
- 金縁
- 白衣装
- 靴

`ImageMagick`版とのpixel完全一致を合格条件にしないでください。

## 完了条件

- 3×3 Studyを1 commandで再現できる
- current default character outputは変わらない
- terrain behaviorは変わらない
- densityでalpha/bbox/placementが変わらない
- detail passが新色を生成しない
- palette budgetを守る
- 9cell board + 8x board + metrics + manifest + review templateが生成される
- unit/pipeline/study testsが追加される
- 実行したtest commandと結果を報告する
- 実画像を実行できた場合は出力先を報告する
- 未検証事項を明示する

## 今回やらないこと

以下を同じ変更へ混ぜないでください。

- GUIでの9cell selector
- animation frame batch
- shared palette across animation frames
- common scale / anchor across sprite sheets
- semantic segmentation
- AIによるfeature detection
- auto winner selection
- production defaultを16/24/32やsparse/balancedへ変更すること
- terrain Pixel Grammar / Hierarchyの再設計

## 完了報告フォーマット

最後に次を短く報告してください。

```text
変更:
- ...

テスト:
- command: result

Study:
- output: ...
- generated cells: 9/9

互換性:
- default character: unchanged / differenceあり
- terrain: unchanged / differenceあり

実画像:
- 実行済み / 未検証
- 観測: ...

残課題:
- ...
```

コミット・pushはこの依頼に含めません。実装と検証が完了した状態で止め、差分をレビューできるようにしてください。
