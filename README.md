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

全コマンドとオプションは次で確認できます。

```powershell
pixel-tile --help
pixel-tile compile --help
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

Use `pixel-tile --help` and `pixel-tile <command> --help` for all options.

### Outputs

A single-image compilation writes `final.png`, `ir.json`, `metadata.json`, two baseline images,
and debug images under the selected output directory. MAP, tileset, and animation commands add
comparison images, metrics, manifests, split reports, frame images, and previews as applicable.
Input images are not modified. Use `--output` to choose the destination.

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
