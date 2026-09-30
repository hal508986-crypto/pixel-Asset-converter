# pixel-Asset-converter

> **レビュー版:** このリポジトリはReview versionです。完全完成品ではなく、実験中・未確定・変更予定の機能を含みます。

高解像度の画像素材を、SRPGで扱いやすいピクセルアートへ変換する、決定論的なPythonツールです。
画像生成そのものではなく、入力画像の解析、領域・境界の整理、減色、64×64及び128×128化、検証、書き出しを担当します。

リポジトリ名は `pixel-Asset-converter`、Pythonパッケージ名とCLIコマンドは現在も
`pixel-tile-compiler` / `pixel-tile` です。

## できること

- 単体画像を64×64及び128×128のタイルまたはオブジェクト画像へ変換
- 高解像度MAPを周辺コンテキスト・共有パレット付きの64×64タイルへ変換
- キャラクターのアニメーションSheetを分割し、フレームごとに正規化
- Material ExemplarからTileset、境界契約、比較用MAPを生成
- GUIまたはCLIから同じコンパイラを実行

## 前提条件

- Python 3.10以上
- 入力画像（PNG / JPEG / WebP）
- GUIを使う場合は追加依存のPySide6
- 画像生成モデルやモデル重みは同梱しない。生成済みの入力画像を受け取って処理する

WindowsではPython Launcherを使います。別の環境では `py -3.10` を `python` などに読み替えてください。

## セットアップ

```powershell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[gui,dev]"
```

インストール後、CLIは `pixel-tile`、モジュール経由では `python -m pixel_tile_compiler` で起動できます。

## GUIの言語

GUIの画面、ボタン、設定項目、メッセージは日本語のみです。英語UIは提供していません。

## 最短の使い方

単体画像を64×64へ変換します。入力画像は変更されません。

```powershell
pixel-tile compile input.png --output output/input --palette 16
```

高解像度MAPを分割し、コンテキスト付きの比較結果を作ります。

```powershell
pixel-tile compile-map map.png --output output/map --cols 4 --rows 5 --palette 24 --context 1
```

キャラクターの待機Sheetを4フレームへ分割し、64×64へ揃えます。

```powershell
pixel-tile compile-character-animation character_idle_sheet.png `
  --output output/character_idle `
  --split-mode hybrid --cols 4 --rows 1
```

動画生成AIなどが出力したPNG連番（単色背景・不透明）を、背景除去→共通座標・共有パレットでコンパイルし、
全段階の画像と透過GIFまで一括で出力します。

```powershell
pixel-tile compile-character-frames frames_dir `
  --output output/action --width 128 --height 128 `
  --background-tolerance 45 --choke 1 --fps 24
```

- 背景色は全フレームの外周から1回だけ推定して全フレームで共有します（`--background-color #RRGGBB` で指定も可）。
- `--background-mode auto`（既定）は、緑・青緑など鮮やかな背景色では画像内の背景色を全部消し（`global`）、白・グレーでは外周に繋がる部分だけ消します（`connected`）。
- 縁に背景との混色が残るときは `--choke 1`（輪郭を1画素内側へ削る。画像端に接する輪郭は削りません）。
- 色のちらつき（パレット境界で色が行き来する）は、既定で抑えます（`--stabilize-margin 12`。`0` で無効）。前フレームで使った色が今の色より元の色から見てmargin以内にしか劣らなければ、前の色を維持します。輪郭とパレットは変わりません。上げすぎると本物の色の変化も潰すので、目安は12〜20です。
- 動画生成AIは余白を大きく取りがちで、全フレームがはみ出さない倍率（既定）だと、槍を大きく突き出すコマなどに引っ張られてキャラが小さくなります。大きさの決め方は3つあります。
  - **`--character-height 200`（推奨）**: アルファから測った**キャラ本体の高さ**（細い槍などは除きます）を基準に、本体の高さが指定のpxになる倍率にします。**倍率と足元は全フレーム固定**なので、踏み込みや跳躍で縦幅が変わってもキャラのサイズはブレず、同じキャラの別の動作でも同じ値にすればサイズが揃います。基準は `--height-reference median`（中央値・既定）/ `first`（最初のフレーム）/ フレーム番号（1始まり）。動画の中でカメラが寄る・大きく屈むなど元動画側でサイズが変わる素材では、基準を選ぶか手動で確認してください。
  - **`--canvas-auto`**（`--character-height` と併用）: その倍率で全フレームの張り出しが収まる**最小のCanvas**（四方2px）にします。見切れなし・余白ほぼなしで、全フレーム同サイズのままなのでAsepriteなどでの手直しに向きます（`--width/--height` は無視）。Canvasを固定した場合、収まらない部分は見切れます（警告とレポートの `clipped` に出ます）。
  - `--fit-percentile 70`: その割合のフレームが収まる倍率にして、極端に伸びるコマの見切れを許します（身長指定とは同時に使えません）。
- ゲームエンジン向けに、各フレームを可視範囲へ切り詰めた画像と、足元（ピボット）からのオフセットも出力します（`trimmed_frames/` と `trim_manifest.json`。`--no-trim` で無効）。切り詰め画像の左上を「ピボット + `offset_from_pivot`」に置けば、Canvas上の位置にピクセル一致で戻ります。
- 透過済みの連番は `--no-key-background`、GIFが不要なら `--no-gif`、ループさせないなら `--play-once`。
- 入力は自然順（`f2` の次が `f10`）で読み込み、全フレームが同サイズである必要があります。

GUIからも同じ処理を使えます。GUIを起動し、用途を「キャラクター」または「コマ割りアニメーション」にすると出る
**「連番PNG→GIF」ボタン**から専用ウィンドウを開き、フォルダを選ぶだけで、背景色の推定結果（枚数・サイズ・外周の一致率）を確認してから、
設定（出力サイズ・パレット・背景の消し方・許容差・輪郭削り・ちらつき対策・fps・ループ）を選んで実行できます。処理中は進捗が出て、
**中止しても既存の出力は壊れません**。結果は透過の市松の上でGIFと同じ速度で再生して確認できます。
CLIとGUIは同じ処理（`frame_sequence_job`）を通るので、同じ設定なら同じ結果になります。

全コマンドとオプションは次で確認できます。

```powershell
pixel-tile --help
pixel-tile compile --help
pixel-tile compile-character-frames --help
```

## 得られる成果物

単体コンパイルでは、指定した出力ディレクトリに次の成果物を保存します。

- `final.png`：最終RGBA画像（既定64×64）
- `ir.json`：タイルの中間表現
- `metadata.json`：入力・設定・処理結果のメタデータ
- `baseline_nearest.png` / `baseline_bicubic_quantized.png`：比較用ベースライン
- `debug/`：正規化、領域、パレット、境界などの確認画像

MAP・Tileset・アニメーションの各コマンドは、これに加えて比較画像、`metrics.json`、
`manifest.json`、分割レポート、フレーム画像、64×64プレビューなどを出力します。
出力先は `--output` で指定できます。

`compile-character-frames`（PNG連番）は、段階ごとに1枚ずつ別ファイルで保存します。

- `source_frames/`：入力PNGのバイト不変コピー
- `keyed_frames/`：背景除去後（透過）
- `aligned_frames/`：共通座標・倍率で整列した後
- `compiled/F001/…`：各フレームのコンパイル結果
- `final_frames/`：最終フレーム（正本。GIFはここから作る派生物）
- `animation.gif`：全フレーム共通パレット・二値透過。書き出し後に再読込して画素一致を検証済み
- `bbox_report.json`：各段階のパス・sha256、背景色・許容差、GIFのfps・各フレーム時間

フレームIDは `F001` のようにゼロ埋めです。Sheetの総画素数が上限（16,777,216）を超える場合は、
Sheetとプレビューだけを作らず、フレーム画像とGIFは通常どおり出力します。

## 開発者向け

```powershell
py -3.10 -m pytest
py -3.10 scripts/generate_licenses.py --check
py -3.10 -m pytest tests/test_license_audit.py -m license_gate
```

通常のpytestでは環境依存のLICENSE再現性テストを除外し、`requirements-license-audit.txt`で
固定した環境で上記の `license_gate` と生成物チェックを実行します。主要コードは `src/`、
テストは `tests/`、仕様・設計記録は `specs/` と `docs/` にあります。
旧READMEに残る実験別の詳細手順は [docs/README-legacy.md](docs/README-legacy.md) に退避しています。

## ライセンス

コードは [MIT License](LICENSE.txt) です。依存パッケージのライセンス本文と監査結果は
[NOTICE.txt](NOTICE.txt) と [LICENSE/](LICENSE/) を参照してください。

## English

`pixel-Asset-converter` is a deterministic Python tool for converting high-resolution image
materials into pixel-art assets for SRPGs. It analyzes input images, organizes regions and
boundaries, reduces palettes, creates 64×64 or 128×128 outputs, validates the result, and
exports PNG/JSON artifacts. It does not include an image-generation model or model weights.

The repository name is `pixel-Asset-converter`; the current Python package and CLI command
remain `pixel-tile-compiler` and `pixel-tile`.

> **Review version:** This is not a finished product. It includes experimental and incomplete
> areas, and behavior or interfaces may change as development continues.

### Features

- Convert a single image into a 64×64 or 128×128 tile/object image.
- Compile a high-resolution MAP into context-aware 64×64 tiles with a shared palette.
- Split a character animation sheet and normalize each frame.
- Build tilesets, edge contracts, and comparison MAPs from a Material Exemplar.
- Run the same compiler from the CLI or the optional GUI.

### Requirements and setup

- Python 3.10 or later
- An input image in PNG, JPEG, or WebP format
- PySide6 for the optional GUI

On Windows, use the Python Launcher. On other platforms, replace `py -3.10` with your Python command.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[gui,dev]"
```

After installation, run `pixel-tile` or `python -m pixel_tile_compiler`.

### Quick start

```powershell
pixel-tile compile input.png --output output/input --palette 16
pixel-tile compile-map map.png --output output/map --cols 4 --rows 5 --palette 24 --context 1
```

For a four-frame character sheet:

```powershell
pixel-tile compile-character-animation character_idle_sheet.png `
  --output output/character_idle `
  --split-mode hybrid --cols 4 --rows 1
```

For a numbered PNG sequence with a flat, opaque background (e.g. video-model output), one command removes
the background, compiles all frames with one shared layout and palette, and writes every intermediate stage plus a
transparent GIF:

```powershell
pixel-tile compile-character-frames frames_dir `
  --output output/action --width 128 --height 128 `
  --background-tolerance 45 --choke 1 --fps 24
```

The background colour is estimated once from all frames and shared. `--background-mode auto` (default) removes every
background-coloured pixel for vivid backgrounds (green, teal) and only border-connected ones for white/grey.
`--choke 1` shaves one pixel off the outline to drop background-blended edges (outlines touching the image border are kept).
Sizing: `--character-height 200` (recommended) measures the character body from the alpha channel (thin weapons are ignored) and picks the scale so the body is 200 px tall; scale and feet are fixed across frames, so crouches, lunges and jumps do not change the character's size, and the same value keeps different actions of one character at the same size (`--height-reference median|first|<frame number>`). `--canvas-auto` sizes the canvas to the tightest box that holds every frame at that scale (no clipping, minimal margins, same size for every frame). `--fit-percentile 70` instead fits 70% of frames and lets extreme ones clip. `trimmed_frames/` + `trim_manifest.json` (disable with `--no-trim`) hold each frame cropped to its visible box with offsets from the pivot (frame top-left = pivot + `offset_from_pivot`) for game engines.
`--stabilize-margin` (default 12, `0` disables) reduces colour flicker at palette boundaries: a pixel keeps its previous colour unless the new one is closer to the source by more than the margin. Silhouettes and the palette are unchanged.

The same job is available in the GUI: with the Character or Character Animation purpose, the **連番PNG→GIF** button opens a
dedicated window (folder pick with an estimated-background preview, all the settings above, progress, cancel that never
corrupts an existing output, and a playback preview on a checkerboard). CLI and GUI share one job layer, so identical
settings give identical results.

Use `pixel-tile --help` and `pixel-tile <command> --help` for all options.

### Outputs

A single-image compilation writes `final.png`, `ir.json`, `metadata.json`, two baseline images,
and debug images under the selected output directory. MAP, tileset, and animation commands add
comparison images, metrics, manifests, split reports, frame images, and previews as applicable.
Input images are not modified. Use `--output` to choose the destination.

`compile-character-frames` writes each stage as separate zero-padded files (`source_frames/`, `keyed_frames/`,
`aligned_frames/`, `compiled/F001/…`, `final_frames/`) plus `animation.gif` (shared palette, binary transparency,
re-decoded and verified pixel-for-pixel) and `bbox_report.json` with per-stage sha256 hashes. `final_frames/` is the
master; the GIF is derived from it.

### GUI language

The GUI is Japanese-only. Its screens, buttons, settings, and messages are intentionally provided
in Japanese; an English GUI is not currently available. The CLI and Python module entry points
remain available for users who prefer a command-driven workflow.

### Development and license

```powershell
py -3.10 -m pytest
py -3.10 scripts/generate_licenses.py --check
py -3.10 -m pytest tests/test_license_audit.py -m license_gate
```

The regular pytest run excludes the environment-dependent LICENSE reproducibility test. Run the
`license_gate` test and generated-output check in an environment pinned by
`requirements-license-audit.txt`. The source code is released under the [MIT License](LICENSE.txt). Third-party license texts and
the audit records are available in [NOTICE.txt](NOTICE.txt) and [LICENSE/](LICENSE/).

## アセットについて

画像・実験・コンパイル済みパッケージは生成成果物としてGitの追跡対象から外しています。
`.gitignore` で `assets/`、`e2e/`、`experiment/`、`experiments/e2e/`、
`experiments/material_library/`、`material_library/`、
`Output/`、`output/`、`unused/` を除外しています。既にGitで追跡されているファイルは、
`.gitignore` を変更しただけでは自動的に追跡解除されません。
アセットの利用条件はコードのMIT Licenseとは別に、生成元サービスの最新規約と個別素材の条件で確認します。
