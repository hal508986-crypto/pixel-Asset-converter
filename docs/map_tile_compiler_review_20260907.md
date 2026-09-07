# MAPタイル初回実装の査読と実生成試行

対象: 2026-09-07の作業ツリー差分。HEAD `481978dc`。前回調査と初回実装プロンプトに照合。
MAP関連の追跡済み差分に加え、未追跡の `asset/acceptance.py`、`tests/test_map_tile_initial.py` と初回ハンドオフを確認した。GUI関連の別作業は本査読の判定対象外。製品コードの修正は行っていない。

## 判定

高解像度セル→共有パレット→64pxコンパイルの接続と、自己反復補正の誤適用除去は改善されている。ただし初回受け入れ条件の構造検証が不足しており、採用ゲートの完成としては差し戻し。

### P1: 非接続辺と接続幅の違反を見逃す

`src/pixel_tile_compiler/asset/acceptance.py::_validate_mask` は、非接続辺でも中央ウィンドウを除いたoutsideだけで漏れを判定する。また接続辺ではcoverageのみを拒否条件にし、outsideへの漏れを拒否しない。

正しいNS道路を入力にし、完成画像へ東向きの枝を加えてもaccepted。NS道路の幅を15pxから31pxへ広げてもaccepted。非接続辺は中央を含む辺全体を検査し、接続辺は許可窓の外側も検査する必要がある。四隅は `_edge_body` で除外されるため、その検証も別途必要。

### P1: 両端が残れば、途中で切れた道も合格する

同じNS道路の中央6行を背景色で消してもaccepted。`_validate_mask` は外周しか見ず、内部の連結性を確認しない。接続端が同じ連結成分に属することや、宣言した道路形状の保持を完成PNGで検証する必要がある。入力マスクの合格では代替できない。

### P1: surfaceの共有辺契約がゲートに渡されない

`validate_tile_contract` のsurface分岐はPNG寸法・色数・アルファだけでacceptedになる。`compile_generated_sheet` は `spec.shared_edge_contract` をゲートへ渡さず、`validate_tileset_contract` も隣接ペアを検証しない。

既存のgrass specに対して、タイルごとに緑と黄色の単色画像を交互に割り当ててもタイルセット全体がaccepted。必須の共有辺条件が未検証なのに、全体をacceptedにしないこと。比較対象となる許可ペアと四隅を列挙し、PNG整合性と接続整合性の状態を分離する必要がある。

## 再現と既存テスト

再現スクリプト: [reproduce_review.py](../e2e/map_tile_review_trial_20260907/reproduce_review.py)

```powershell
py -3.10 e2e/map_tile_review_trial_20260907/reproduce_review.py
```

観測値は [review_probes.json](../e2e/map_tile_review_trial_20260907/review_probes.json)。4負例すべてaccepted。

今回実行した既存テストは99 passed in 65.81s。これは上記の負例をカバーしていることを意味しない。

```powershell
py -3.10 -m pytest -q -o addopts= tests/test_map_tile_initial.py tests/test_generation_first_pipeline.py tests/test_repeatability.py tests/test_tileset.py tests/test_transition_network.py tests/test_map_compiler.py tests/test_cli.py tests/test_pipeline.py tests/test_canvas_spec.py tests/test_character_pixelizer.py
```

## 1枚の実生成→コンパイル

内蔵image_genで草地シートを1枚生成。プロンプトは既存 `specs/grass_surface_v1.json` に合わせ、4×4、地面のみ、中程度の草の塊、均一な照明、共通の辺、安全帯、文字・罫線・ガターなしを指定した。

- [使用した生成プロンプト](../e2e/map_tile_review_trial_20260907/generation_prompt.txt)
- [不変保存した原画](../e2e/map_tile_review_trial_20260907/source_raw.png)
- [コンパイル成果物](../e2e/map_tile_review_trial_20260907/compiled/manifest.json)
- [比較画像](../e2e/map_tile_review_trial_20260907/comparison_2x.png): 左=最近傍縮小のみ、中央=同じ共有16色で減色、右=現行コンパイル。各パネルは256×256を最近傍2倍表示。
- [並べ替えプレビュー](../e2e/map_tile_review_trial_20260907/shuffled_3x.png): seed=42で16枚の順序を変更。

実際の生成サイズは1254×1254（指定は1024×1024）。既存のcenter cropで1252×1252へ正規化され、313×313のセルから64×64を16枚生成した。元画像のハッシュ不変を確認。各タイルは11〜15色、共通予算16色。ゲートはaccepted、採用状態はprovisional。

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
py -3.10 -m pixel_tile_compiler compile-generated-sheet --spec specs/grass_surface_v1.json --image e2e/map_tile_review_trial_20260907/source_raw.png --output e2e/map_tile_review_trial_20260907/compiled --palette 16 --no-debug
```

再実行時は非空出力先を避け、別ディレクトリにすること。生成リクエスト中のseed=42は実際の画像生成器が受理したseedではなく、今回のコンパイル・比較配置の設定である。生成モデル名や未提供のseedは推測していない。

## 元絵品質についての評価

今回の原画は草の塊として読める一方、指定したセル端の安全帯を厳密に守ったシートにはなっていない。自由に再配置するタイル素材としては入力側にも課題がある。実際、並べ替え後にはセル境界で草の塊が途切れる。

同じ原画・同じ16色パレットでの比較では、最近傍＋減色は葉の明暗を比較的残すが、現行の領域ベースコンパイルは大きく平坦化した。これは今回の1入力に対する目視評価であり、最近傍方式が常に優れるという結論ではない。

「良い元絵は重要」という見立ては妥当。ただし、元絵の見栄えだけでは品質を担保できず、64pxで残る形、タイル境界への適合、変換時の情報保持を揃える必要がある。次の実装では、同じ原画に対する共有パレット付き最近傍経路とregion経路を比較できるようにし、採用ゲートの負例4件を先に回帰化することを推奨する。

SRPG実機表示、ゲーム用正本アセットへの配備、美観の採用承認は未実施。
