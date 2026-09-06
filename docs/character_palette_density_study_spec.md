# Character Palette × Detail Density Study 実装仕様

作成日: 2026-09-07  
対象基準: `main` / `5d5a63e` (`Implement character compiler improvements`)

## 1. 目的

キャラクターMAP駒の64×64出力について、現在の「32色・nearest・object」を単一の正解として固定せず、次の2軸を分離して比較する。

- Palette Budget: `16 / 24 / 32`
- Detail Density: `sparse / balanced / detailed`

最小比較は3×3の9出力とする。目的は「最も綺麗な1枚を自動選択すること」ではなく、キャラクター識別点を保ちながら、64×64で必要な色数と微細クラスタ量の関係を観測可能にすることである。

このStudyは、今後のキャラクター用Compiler presetを決めるための実験であり、既存の地形向けPixel Grammar Studyをそのままキャラクターへ流用しない。

## 2. 現在の基準実装

現行のキャラクター経路には以下が既に実装されている。

```text
load
→ background resolution
→ visible bbox extraction
→ 54×54 frame内へaspect-ratio preserving fit
→ 64×64 canvasへ水平中央・bottom anchor配置
→ nearest path
→ color conditioning
→ visible RGB only palette quantization
→ optional outline
→ export / validation metadata
```

関連箇所:

- `src/pixel_tile_compiler/config.py`
  - `compiler_config_for_purpose("character")`
  - `character_frame_width=54`
  - `character_frame_height=54`
  - `character_bottom_margin=7`
- `src/pixel_tile_compiler/pixelizer/character.py`
  - `fit_character_to_canvas`
  - `nearest_pixelize`
  - `add_outline`
- `src/pixel_tile_compiler/pixelizer/palette.py`
  - 透明RGBをpalette選定へ入れない `quantize_palette`
- `src/pixel_tile_compiler/pipeline/compiler.py`
  - `nearest/object` のキャラクター経路
- `tests/test_character_pixelizer.py`

Study追加によって、既存のデフォルト出力を変えてはいけない。

## 3. 仮説

### H1: Palette Budget

64×64のMAP駒では32色が常に必要とは限らない。16色で識別点が保てるキャラでは、色面が整理されてゲーム内視認性が上がる可能性がある。一方で、髪・肌・衣装・装飾の色相が近いキャラでは24〜32色が必要になる可能性がある。

### H2: Detail Density

「情報量」は色数だけでは決まらない。同じ24色でも、1〜数pxの低コントラストな色クラスタを大量に残す出力と、大きな色面へ整理した出力では読みやすさが異なる。

ここでDetail Densityは「人物の大きさ」「bbox」「alpha silhouette」を変更するパラメータではなく、**減色後の可視RGBクラスタをどこまで残すか**を表す。

### H3: 最適値はキャラ種別で変わる

少なくとも次の種類で推奨値が変わる可能性がある。

- 武器なし・小柄な人型
- 長髪・大きな頭部装飾
- 槍・斧など長物を持つ人型
- 騎乗ユニット
- 大盾などシルエットの大きいユニット

したがって、初回のアリア1体で全キャラ共通presetを確定しない。

## 4. 重要な設計判断

### 4.1 地形用 `pixel_grammar.apply_density` を直接使わない

既存の `src/pixel_tile_compiler/pixel_grammar/density.py` は、Gaussian blur / contrast / sharpness / unsharp maskとrepeatability用設定を組み合わせる地形・material appearance向けの実験実装である。

キャラクターでは次の理由から、その処理をそのまま使わない。

- alpha silhouetteを不変にしたい
- 目・リボン・靴など数pxの高コントラスト識別点を保護したい
- `object/nearest` ではrepeatability controlは無関係
- palette budgetとの効果を分離して観測したい

`Sparse / Balanced / Detailed` という語彙は共有してよいが、キャラクターでは別の変換契約を持つ。

### 4.2 Detail Densityは減色後に適用する

初回Studyでは、detail passはpalette quantization後、outline前に適用する。

```text
background
→ fit / anchor
→ nearest
→ color conditioning
→ palette quantization
→ character detail pass   ← NEW
→ optional outline
→ export
```

理由:

- 色数を固定してから微細クラスタだけを比較できる
- detail passが新しい色を生成しない
- alpha/bboxを固定しやすい
- 16/24/32色の効果とdensityの効果を分離できる

### 4.3 silhouetteは変更禁止

Detail passはRGBだけを変更し、alphaは1画素も変更しない。

同一palette budget内で `sparse / balanced / detailed` の次を完全一致させる。

- 64×64 canvas
- alpha mask
- visible bbox
- bottom anchor
- outline policy

## 5. Detail Densityの初期契約

### detailed

現行のキャラクター出力をそのまま返す。追加cleanupなし。

`detailed` は互換性基準であり、現行mainと同じ入力・同じ設定ならpixel-identicalであることを回帰テストする。

### balanced

低コントラストの微小RGBクラスタだけを整理する。

初期候補:

- 4-neighbor connected component
- component area `<= 2px`
- componentがalpha silhouette境界に接している場合は変更しない
- 周囲の主色とのRGB距離が十分大きい場合は識別点として保護する
- 置換先は隣接visible pixelで最も接触数の多いpalette color
- tie-breakは決定論的に行う

### sparse

`balanced` より強く低コントラストmicro detailを整理する。

初期候補:

- component area `<= 4px`
- silhouette境界は保護
- 高コントラストcomponentは保護
- 置換先は既存palette colorのみ

面積閾値・contrast thresholdはStudy用profileとして明示し、magic numberを散在させない。

### 高コントラスト識別点の保護

目、濃色リボン、靴、武器先端などは1〜数pxでも重要である。単純なsmall-component removalは行わない。

候補component colorと置換候補colorのRGB Euclidean distance、または既存依存だけで実装できる同等の決定論的距離を用い、一定以上の差があるcomponentは保持する。

初回MVPでは新規の色空間依存ライブラリを追加しない。

## 6. Config契約

`CompilerConfig` にキャラクター専用のdetail levelを追加する。

候補:

```python
CharacterDetailLevel = Literal["sparse", "balanced", "detailed"]

character_detail_level: CharacterDetailLevel = "detailed"
```

デフォルトは必ず `detailed` とし、現行mainのキャラクター出力を変えない。

地形経路ではこの値を無視する。

未知値は `CompilerConfig.__post_init__` でrejectする。

初回PRではGUIへ新しいcontrolを追加しない。Study runnerと必要ならCLI `compile` の明示optionから利用できればよい。

## 7. 実装境界

新規責務はキャラクター用helperへ置く。

候補:

```text
src/pixel_tile_compiler/pixelizer/character_detail.py
```

公開API候補:

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

次の条件を満たすこと。

- input/outputはRGBA
- size不変
- alpha完全不変
- RGB変更はvisible pixelのみ
- 新色を生成しない
- deterministic
- `detailed` はcopy相当のidentity
- completely transparent imageで失敗しない

既存のterrain向け `cleanup_pixel_clusters` を意味変更しない。

## 8. Study Runner

新規Studyは既存の `study-pixel-grammar` とは分ける。

候補command:

```text
pixel-tile study-character-palette-density --config <yaml> [--output <dir>]
```

候補module:

```text
src/pixel_tile_compiler/character_study/
├─ __init__.py
├─ config.py
├─ metrics.py
└─ runner.py
```

既存Studyの実装様式を再利用してよいが、地形semantic roleやrepeatability metricへ依存させない。

## 9. Study Config

初回schemaは複数caseを扱える形にする。

```yaml
study:
  output_root: ../e2e/character_palette_density_study
  palette_budgets: [16, 24, 32]
  detail_levels: [sparse, balanced, detailed]
  cases:
    - id: aria_front
      source: ../path/to/aria_source.png
      review_features:
        - eyes
        - side_hair
        - navy_ribbon
        - cape
        - boots
  character:
    frame_width: 54
    frame_height: 54
    bottom_margin: 7
    background_mode: auto
    outline: off
  seed: 42
```

repo外の絶対パスも受理してよいが、単体テストはrepo内で生成するsynthetic fixtureを使う。個人環境の `G:\...` をテストへ固定しない。

## 10. 出力契約

1 caseあたり次を生成する。

```text
<output_root>/<case_id>/
├─ source_snapshot.png
├─ source.json
├─ matrix/
│  ├─ sparse/
│  │  ├─ palette_16/final.png
│  │  ├─ palette_24/final.png
│  │  └─ palette_32/final.png
│  ├─ balanced/
│  │  └─ ...
│  └─ detailed/
│     └─ ...
├─ previews/
│  ├─ comparison_board.png
│  └─ comparison_board_8x.png
├─ metrics/
│  └─ matrix_metrics.json
├─ review/
│  └─ review_template.json
└─ manifest.json
```

各cellのmetadataには少なくとも以下を保存する。

- case id
- source SHA-256
- palette budget
- actual visible RGB count
- detail level
- frame size
- bottom margin
- visible bbox
- visible pixel count
- alpha unique values
- output SHA-256
- config snapshot

## 11. 比較metrics

自動metricで美的winnerを確定しない。

記録する候補:

- `actual_palette_count`
- `palette_utilization = actual / budget`
- `visible_bbox`
- `visible_pixel_count`
- `alpha_binary`
- `alpha_equal_to_detailed_same_budget`
- `changed_visible_pixels_vs_detailed`
- `changed_visible_ratio_vs_detailed`
- `unique_rgb_count`
- `low_contrast_components_removed`（取得できる場合）
- `high_contrast_components_protected`（取得できる場合）

最重要の機械条件は、density変更でalpha maskとbboxが変わらないことである。

## 12. Comparison Board

boardは次の固定配置にする。

```text
                 16 colors     24 colors     32 colors
sparse              A1            A2            A3
balanced            B1            B2            B3
detailed            C1            C2            C3
```

各cellに最低限 `density / budget / actual colors` を表示する。

実寸64×64版と、nearest 8倍版を両方生成する。8倍previewを最終assetとして扱わない。

## 13. Human Review

初回のアリアでは少なくとも次を確認する。

- 左右の目が読み取れるか
- 前髪と側面の髪の塊が潰れていないか
- 濃紺のリボンが髪へ吸収されていないか
- ケープと白い衣装が分離しているか
- 金縁がノイズ化していないか
- 靴が足元の暗部へ吸収されていないか
- 64×64実寸で頭だけが浮いて見えないか
- 8倍時だけ綺麗で、実寸では読めない出力になっていないか

採用判断は `review_template.json` へ記録できるようにし、後で複数caseを横断比較できる形にする。

## 14. テスト

最低限次を追加する。

### Unit

- `detailed` はinputのRGB/alphaを変更しない
- `sparse/balanced` でもalpha bytesが完全一致する
- transparent pixelのRGBを評価・置換対象にしない
- low-contrast 1px islandがprofile条件で整理される
- high-contrast 1px eye相当pixelが保護される
- silhouette boundary componentは保護される
- replacement colorは必ずinput palette内
- 同一inputは複数回実行してbyte-identical

### Pipeline regression

- default `character_detail_level="detailed"` で既存character fixture出力が変更されない
- terrain compile結果が変更されない
- outlineありでもpalette budget契約を破らない
- 16/24/32で `actual_palette_count <= budget`

### Study runner

synthetic character fixture 1枚から9cellが生成される。

- 9枚すべて64×64
- alphaは同budget内で3density完全一致
- manifestに全9cellが記録される
- comparison boardが生成される

## 15. 受け入れ条件

- 3×3 matrixを1 commandで再現できる
- `detailed` defaultで現行キャラクター出力を壊さない
- density passはalpha silhouette、bbox、placementを変更しない
- density passは新しいRGB colorを生成しない
- 16/24/32のbudgetを守る
- sparse/balancedが高コントラスト識別点を単純な面積だけで消さない
- comparison board実寸/8倍、manifest、metrics、review templateが揃う
- 地形向けPixel Grammar/Hierarchy Studyの既存挙動を変更しない
- 既存test suiteを通し、追加testで上記契約を固定する

## 16. 初回範囲外

- AIによる目・髪・装備のsemantic segmentation
- キャラごとの自動best profile選択
- GUIでの9cell比較
- animation frame間の共通palette / common anchor
- sprite sheet batch compile
- 16/24/32以外の自動探索
- perceptual metricだけでの自動採用
- 元画像のpixel-grid推定

これらは、アリアを含む複数caseのStudy結果を見てから別仕様として扱う。

## 17. 初回評価後の判断

最低でも次の4種類を比較してから、production presetを決める。

1. 小柄・武器なし
2. 長物武器
3. 大盾または大きい装飾
4. 騎乗

その時点で、たとえば `24 / balanced` が広く安定するならcharacter default候補とする。ただし、初回Studyの結果だけで現在の `detailed` defaultを変更しない。
