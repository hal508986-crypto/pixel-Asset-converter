"""地形とキャラクターで共有する可視RGB paletteの契約。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from pixel_tile_compiler.config import CompilerConfig


def normalize_palette(colors: Iterable[Iterable[int]]) -> tuple[tuple[int, int, int], ...]:
    """RGBの三つ組を重複のない昇順tupleへ正規化する。"""
    normalized: set[tuple[int, int, int]] = set()
    for color in colors:
        values = tuple(int(channel) for channel in color)
        if len(values) != 3 or any(channel < 0 or channel > 255 for channel in values):
            raise ValueError("palette colors must contain RGB triples between 0 and 255")
        normalized.add(values)
    return tuple(sorted(normalized))


def extract_final_palette(image_or_path: Image.Image | Path | str) -> tuple[tuple[int, int, int], ...]:
    """final PNGの可視画素からRGB集合を実測する。デバッグ画像からは推測しない。"""
    if isinstance(image_or_path, Image.Image):
        image = image_or_path.convert("RGBA")
        return normalize_palette(
            pixel[:3]
            for pixel in image.getdata()
            if pixel[3] != 0
        )
    with Image.open(Path(image_or_path)) as opened:
        image = opened.convert("RGBA")
        return normalize_palette(
            pixel[:3]
            for pixel in image.getdata()
            if pixel[3] != 0
        )


def validate_reference_palette(colors: Iterable[Iterable[int]]) -> tuple[tuple[int, int, int], ...]:
    """共有RGB paletteを検証する。実測色の水増しは行わない。"""
    normalized = normalize_palette(colors)
    if not normalized:
        raise ValueError("reference palette must contain at least one visible color")
    if len(normalized) > 64:
        raise ValueError("reference palette must contain at most 64 colors")
    return normalized


def palette_id(colors: Iterable[Iterable[int]]) -> str:
    """正規化したRGB paletteに対する契約安定IDを返す。"""
    normalized = validate_reference_palette(colors)
    payload = b"palette-rgb-v1\n" + bytes(channel for color in normalized for channel in color)
    return hashlib.sha256(payload).hexdigest()


def load_palette_json(path: Path | str) -> tuple[tuple[int, int, int], ...]:
    """地形batchが出力したpalette.jsonを読み込み、契約を検証する。"""
    palette_path = Path(path)
    try:
        payload = json.loads(palette_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"palette.jsonを読み込めません: {palette_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("palette.jsonの形式が不正です")
    if payload.get("schema_version") != 1:
        raise ValueError("palette.jsonのschema_versionが不正です")
    raw_colors = payload.get("colors")
    if not isinstance(raw_colors, list):
        raise ValueError("palette.jsonにcolorsがありません")
    try:
        colors = validate_reference_palette(raw_colors)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"palette.jsonのcolorsが不正です: {exc}") from exc
    declared_id = payload.get("palette_id")
    if not isinstance(declared_id, str) or declared_id != palette_id(colors):
        raise ValueError("palette.jsonのpalette_idが色一覧と一致しません")
    return colors


def save_palette_json(path: Path | str, colors: Iterable[Iterable[int]]) -> Path:
    """RGB paletteをpalette.jsonとして書き出す。契約を通ったものだけを書く。"""
    normalized = validate_reference_palette(colors)
    palette_path = Path(path)
    palette_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "colors": [list(color) for color in normalized],
        "palette_id": palette_id(normalized),
    }
    palette_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return palette_path


def fixed_palette_config(
    config: CompilerConfig,
    colors: Iterable[Iterable[int]],
    output_root: Path | str,
) -> CompilerConfig:
    """有効な条件を保ったまま固定paletteだけを差し替える。"""
    normalized = validate_reference_palette(colors)
    return replace(
        config,
        output_root=Path(output_root),
        palette_budget=max(4, len(normalized)),
        palette_colors=normalized,
    )


def metadata_palette_matches_final(
    final_path: Path | str,
    metadata: dict[str, Any] | None,
) -> tuple[tuple[tuple[int, int, int], ...], str | None]:
    """metadataのpaletteがfinal画像と一致するときだけ採用する。"""
    measured = extract_final_palette(final_path)
    if metadata is None:
        return measured, None
    if "transformation" not in metadata:
        return measured, None
    transformation = metadata.get("transformation")
    if not isinstance(transformation, dict):
        return measured, "metadata palette is invalid; colors were measured from final.png"
    raw = transformation.get("palette_colors")
    if raw is None:
        return measured, None
    try:
        from_metadata = normalize_palette(raw)
    except (TypeError, ValueError):
        return measured, "metadata palette is invalid; colors were measured from final.png"
    if from_metadata != measured:
        return measured, "metadata palette differs from final.png; colors were measured from final.png"
    return measured, None


__all__ = [
    "extract_final_palette",
    "fixed_palette_config",
    "load_palette_json",
    "save_palette_json",
    "metadata_palette_matches_final",
    "normalize_palette",
    "palette_id",
    "validate_reference_palette",
]
