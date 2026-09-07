# MAPタイル査読修正ハンドオフ

実装日: 2026-09-07
対象commit: `6adb597c` の後続作業（査読修正は未commit・未push）

## 再査読対応（2026-09-07）

再査読のP1/P2を修正した。画風、原画、コンパイル方式、region経路は変更していない。

### P1: 道路16種の正常パッケージ

`NetworkContract.empty_tile_rule="uniform_background"` を追加し、契約上の `EMPTY` かつ全RGBAが同一の単色背景だけを、空道路の検証根拠として受理するようにした。

- `road_empty` は入力・完成PNGの両方で単色背景を確認できた場合だけaccepted。
- 非EMPTY道路で前景が観測できない場合は従来どおり `unverified` となり、パッケージ全体をacceptedにしない。
- 既存プリセットの道路16種（EMPTY、N/E/S/W、NS/EW、4曲線、4 T字、NESW）を生成した正常なsource/final画像のセットを `validate_tileset_contract` に渡し、16タイル全体がacceptedになる正例テストを追加した。

### P2: 契約role不一致

surfaceタイルが存在する場合、`shared_edge_contract.role` が `surface` 以外なら `not_applicable` にせず `rejected` とするようにした。検査ペア数0のまま全体acceptedへ流れる回避経路を閉じた。

`role="network"` に改変したsurface契約を渡す負例を追加し、`surface_contract_role_mismatch`、checked pair 0、全体rejectedを確認した。

### 再査読回帰

前回の4負例は引き続き次の結果になった。

```json
{
  "extra_E_connector": "rejected",
  "overwide_NS": "rejected",
  "disconnected_middle": "rejected",
  "incompatible_surface_edges": "rejected"
}
```

## 今回の検証結果

- 新規・関連査読テスト: `14 passed in 8.30s`
- 関連回帰: `113 passed in 67.86s`
- 全体回帰: `221 passed in 92.45s`
- `py -3.10 -m compileall -q src tests e2e/map_tile_review_fix_20260907`: 成功
- `git diff --check`: 問題なし（改行コード変換の警告のみ）
- UTF-8置換文字 `U+FFFD`: 検出なし

査読修正はこの時点でも未commit・未push。既存のGUI作業、生成済みOutput、未関連ドキュメントは保全している。

## 修正内容

査読記録の4負例を現行コードで再現し、採用ゲートへ接続した。

- Networkの辺全体を検査し、非接続辺の中央も含めて漏れを拒否する。
- 接続辺は、入力画像・完成64px画像それぞれの解像度で `ceil(edge_length × connector_width_ratio)` を許可幅とする。`road` の `0.22` は64pxで15px、128pxで29pxになる。許可窓外の漏れ、位置ずれ、幅超過を拒否する。
- 四隅を辺検査から除外せず、中心接続契約に対する前景を拒否する。
- 完成PNGの4近傍連結成分を調べ、宣言された接続端が同一成分であり、内部へ到達し、未接続の孤立成分がないことを検査する。
- 色から前景を観測できない場合は `unverified` とし、空マスクを根拠にacceptedへしない。
- 既存の `EdgeContract` に `validation_mode="exact_rgb"` を追加した。これは既存JSONと互換な任意フィールドで、数値条件を文章から推測せず、grass契約側が明示的に宣言する最小拡張である。
- surfaceは東西・南北の全16×16組、計512の再配置可能なペアを、接触辺全体と四隅を含めて比較する。比較詳細は不一致ペアだけを保存する。
- `compile-generated-sheet` は検査用成果物を残したうえで、採用ゲートがrejectedなら終了コード1を返す。`validate_asset_package` も同じゲートを再実行する。

## Red → Green

修正前の `e2e/map_tile_review_trial_20260907/reproduce_review.py` は4件すべて `accepted` だった。

修正後は次の4件がすべて `rejected` になった。

```json
{
  "extra_E_connector": "rejected",
  "overwide_NS": "rejected",
  "disconnected_middle": "rejected",
  "incompatible_surface_edges": "rejected"
}
```

同時に、NS直線、N端点、NE曲線、NES T字、NESW十字の正例はacceptedになった。入力マスクが正しくても完成PNGの中央を断線させたケースはrejectedになる。

## 同一原画での再検証

原画は変更・再生成せず、`e2e/map_tile_review_trial_20260907/source_raw.png` を使用した。

- 実寸: `1254×1254`
- SHA-256: `e915aa99197f33874a46bd3dba730f655c529df17bbd4881acf5e3d8a97cf12d`
- crop: `center`
- 切り出し: 4×4、各セル313×313、16セル。先頭セルのboxは `[1, 1, 314, 314]`
- コンパイル: 64×64、共有16色、seed=42、`directional`、自己反復補正なし
- 修正前: `accepted / provisional_not_approved`
- 修正後: `rejected / rejected_not_adopted`
- 修正後の共有辺: 512ペア検査、512ペア不一致
- `validate_asset_package`: `rejected`
- CLI: 状態rejected、終了コード1

今回の原画はセル端の安全帯を厳密には満たさないため、修正後に拒否されたこと自体が正しい検出結果であり、原画を合格させることは受入条件にしていない。

## 成果物

- `e2e/map_tile_review_fix_20260907/compiled_after_v3/`: 16タイル、manifest、source cell、設定、検証レポート、256px MAP、4倍最近傍プレビュー
- `e2e/map_tile_review_fix_20260907/comparison/acceptance_before_after.json`: 修正前後の判定差、hash、crop、共有パレット、512ペア結果
- `e2e/map_tile_review_fix_20260907/comparison/nearest_shared16_1x.png`: 同一切り出しを最近傍縮小後、同じ共有16色で減色した4×4配置
- `e2e/map_tile_review_fix_20260907/comparison/nearest_shared16_vs_region_2x.png`: 左が最近傍＋減色、右が現行region経路
- `e2e/map_tile_review_fix_20260907/comparison/shuffled_seed42_4x4_3x_nearest.png`: seed=42で再配置した3倍最近傍プレビュー
- `e2e/map_tile_review_fix_20260907/build_review_artifacts.py`: 上記比較成果物の再生成手順

目視では、最近傍＋減色側は元絵の葉の明暗・細部を多く保持し、現行region側は大きな塊へ簡略化している。同じ1原画に対する観測であり、最近傍経路が常に優れるという結論や自動美観承認ではない。

## 再実行

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
py -3.10 e2e/map_tile_review_trial_20260907/reproduce_review.py
py -3.10 -m pixel_tile_compiler compile-generated-sheet --spec specs/grass_surface_v1.json --image e2e/map_tile_review_trial_20260907/source_raw.png --output e2e/map_tile_review_fix_20260907/compiled_after_v3 --palette 16 --no-debug
py -3.10 e2e/map_tile_review_fix_20260907/build_review_artifacts.py
py -3.10 -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from pixel_tile_compiler.asset.pipeline import validate_asset_package; print(validate_asset_package(Path('e2e/map_tile_review_fix_20260907/compiled_after_v3')))"
```

## 検証結果と後続

- 新規査読修正テスト: `12 passed`（4負例、正例、空マスク、共有辺、CLI終了コードを含む）。
- 関連回帰: `111 passed in 72.35s`（Python 3.10）。
- 全体回帰: `219 passed in 92.46s`（Python 3.10）。
- compile時、保存後再検証、CLIのrejected判定は一致させた。
- 画風改善、region経路の廃止、最近傍経路の既定化は今回変更していない。
- 実画像の画風・境界自然さ・MAP可読性は引き続き人手レビューが必要。
- 複数素材の共有辺を許可する機械可読ペア契約、内角・外角・孤立形状のtransition、SRPG実機接続は後続課題。
