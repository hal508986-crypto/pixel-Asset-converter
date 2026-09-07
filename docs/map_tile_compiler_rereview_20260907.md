# MAPタイル査読対応の再査読

対象: HEAD `6adb597c` に対するMAP関連の作業ツリー差分と `tests/test_map_tile_review_fix.py`。GUIの別作業は対象外。製品コードの変更なし。

## 判定

前回の4負例はすべてrejectedとなった。辺全体・四隅・4近傍連結・共有辺512ペア・CLI終了コードの改善を確認。ただし以下2点が残り、採用ゲート完成としては再修正が必要。

## P1: 既存道路セットがEMPTYを含むため合格不能

`asset/acceptance.py` の `_validate_mask` は、前景なしならunverified、前景ありなら宣言接続に属さない成分を拒否する。接続がゼロのタイルはどちらの場合も合格しない。

一方 `generation/presets.py` は道路16種に `road_empty` を含め、NetworkContractでもEMPTYを許可している。正常な単色背景のroad_emptyを `validate_tile_contract` に渡すとrejected/input_mask=unverified。新規テストもこの拒否を期待しており、既存プリセット全体の正例がない。

対応: 道があるはずなのに見えない状態と、契約上道がないEMPTYを区別する。背景・明示マスクなど、空であることを検証する根拠を定義し、通常道路の観測不能を合格にする抜け道は作らない。既存プリセット全16種を使う正常パッケージの正例を追加する。

## P2: 契約role不一致で共有辺検証を回避する

`asset/acceptance.py` の `_validate_shared_edge_contract` は、surfaceタイルが存在してもcontract.roleがsurface以外ならnot_applicableを返す。集約側はnot_applicableを合格として許可する。

再現: grassプリセットをmodel_dumpし、shared_edge_contract.roleだけnetworkへ変更してTilesetSpec.model_validateで読み直す。緑と黄色の単色タイルを交互に渡すと、全体accepted、共有辺not_applicable、検査ペア0となる。roleは文字列で、この不整合は入力時にも拒否されない。

対応: surfaceがある場合の契約欠落・role不一致はrejectedまたはunverifiedとして全体合格を禁止する。not_applicableはsurfaceが存在しない場合に限定するか、Spec側で不整合を拒否する。

## 今回の実行確認

- 既存 `reproduce_review.py`: 余分な東接続、幅超過、中央断線、surface不適合の4件すべてrejected。
- `tests/test_map_tile_review_fix.py tests/test_map_tile_initial.py tests/test_generation_first_pipeline.py tests/test_repeatability.py`: **36 passed in 17.38s**（Python 3.10）。
- EMPTYの正常背景拒否と、role不一致による検証回避はコア関数の直接呼び出しで再現。
- 全体テスト、実画像の再生成・再コンパイル、ゲーム実機は今回実行していない。

画風改善は本差分の対象外。前回の情報損失評価を今回のコードで改善したとは扱わない。
