"""compile-character-frames の出力を、段階ごとの「時間方向の安定性」で評価する。

使い方:
    python scripts/evaluate_frame_sequence.py <出力フォルダ> [--json 評価結果.json] [--static-delta 12]

指標（隣接フレーム間。すべて可視画素を対象）:
- 輪郭の変化率: 可視範囲の対称差 / 和集合。整列後と最終で差が無ければ、コンパイラは輪郭を変えていない。
- トグル率: A→B→A と行って戻る画素の割合（3フレームとも可視な画素中）。本物の動きではまず起きないので、
  ちらつきの直接の指標。「理想量子化」（整列後を共有パレットの最近色に置換）と比べて、コンパイラが増やしたか分かる。
- 準静止画素の切替率: 整列後のRGBがほぼ変わらない（各チャンネル差が static-delta 以下）のに、最終の色が変わった画素の割合。
  パレット境界での色の切り替わり（量子化のちらつき）を表す。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def _load(directory: Path) -> list[np.ndarray]:
    files = sorted(directory.glob("F*.png"))
    if not files:
        raise SystemExit(f"フレームが見つかりません: {directory}")
    return [np.asarray(Image.open(path).convert("RGBA")) for path in files]


def nearest_palette(frame: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """可視画素を共有パレットの最近色へ置換する（コンパイラの詳細整理を含まない理想の量子化）。"""
    out = frame.copy()
    visible = frame[..., 3] > 0
    rgb = frame[visible][:, :3].astype(np.int32)
    distance = ((rgb[:, None, :] - palette[None].astype(np.int32)) ** 2).sum(axis=2)
    out[visible, :3] = palette[distance.argmin(axis=1)]
    return out


def silhouette_change(frames: list[np.ndarray]) -> np.ndarray:
    visible = [frame[..., 3] > 0 for frame in frames]
    return np.array([(a ^ b).sum() / max(1, (a | b).sum()) for a, b in zip(visible, visible[1:])])


def color_change(frames: list[np.ndarray]) -> np.ndarray:
    values = []
    for a, b in zip(frames, frames[1:]):
        both = (a[..., 3] > 0) & (b[..., 3] > 0)
        values.append((a[both][:, :3] != b[both][:, :3]).any(axis=1).mean() if both.any() else 0.0)
    return np.array(values)


def toggle_rate(frames: list[np.ndarray]) -> np.ndarray:
    """A→B→A（前後は同色で、真ん中だけ違う）になった画素の割合。3フレームとも可視な画素が母数。"""
    values = []
    for a, b, c in zip(frames, frames[1:], frames[2:]):
        all_visible = (a[..., 3] > 0) & (b[..., 3] > 0) & (c[..., 3] > 0)
        if not all_visible.any():
            values.append(0.0)
            continue
        ra, rb, rc = (x[all_visible][:, :3] for x in (a, b, c))
        toggled = (ra == rc).all(axis=1) & (ra != rb).any(axis=1)
        values.append(toggled.mean())
    return np.array(values)


def quantization_flip_rate(aligned: list[np.ndarray], final: list[np.ndarray], delta: int) -> np.ndarray:
    """整列後のRGBがほぼ同じ画素のうち、最終の色が切り替わった割合。"""
    values = []
    for (a0, a1), (f0, f1) in zip(zip(aligned, aligned[1:]), zip(final, final[1:])):
        both = (a0[..., 3] > 0) & (a1[..., 3] > 0) & (f0[..., 3] > 0) & (f1[..., 3] > 0)
        near = both & (np.abs(a0[..., :3].astype(np.int16) - a1[..., :3].astype(np.int16)).max(axis=2) <= delta)
        if not near.any():
            values.append(0.0)
            continue
        values.append((f0[near][:, :3] != f1[near][:, :3]).any(axis=1).mean())
    return np.array(values)


def evaluate(root: Path, static_delta: int = 12) -> dict[str, object]:
    report = json.loads((root / "bbox_report.json").read_text(encoding="utf-8"))
    palette = np.array(report["final_palette"]["colors"], dtype=np.uint8)
    aligned, final = _load(root / "aligned_frames"), _load(root / "final_frames")
    ideal = [nearest_palette(frame, palette) for frame in aligned]

    def stats(values: np.ndarray) -> dict[str, float]:
        return {"mean": round(float(values.mean()), 4), "max": round(float(values.max()), 4)} if len(values) else {}

    return {
        "frames": len(final),
        "canvas": list(final[0].shape[1::-1]),
        "palette_colors": int(len(palette)),
        "static_delta": static_delta,
        "silhouette_change": {"aligned": stats(silhouette_change(aligned)), "final": stats(silhouette_change(final))},
        "color_change": {"ideal_quantization": stats(color_change(ideal)), "final": stats(color_change(final))},
        "toggle_rate": {"ideal_quantization": stats(toggle_rate(ideal)), "final": stats(toggle_rate(final))},
        "near_static_flip_rate": {
            "ideal_quantization": stats(quantization_flip_rate(aligned, ideal, static_delta)),
            "final": stats(quantization_flip_rate(aligned, final, static_delta)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path, help="compile-character-frames の出力フォルダ")
    parser.add_argument("--json", type=Path, help="評価結果のJSON保存先")
    parser.add_argument("--static-delta", type=int, default=12, help="準静止とみなす各チャンネル差の上限")
    args = parser.parse_args()
    result = evaluate(args.output, args.static_delta)
    print(f"{result['frames']}フレーム / Canvas {result['canvas']} / パレット {result['palette_colors']}色")
    for name, label in (
        ("silhouette_change", "輪郭の変化率"),
        ("color_change", "色の変化率"),
        ("toggle_rate", "トグル率 A→B→A"),
        ("near_static_flip_rate", "準静止画素の切替率"),
    ):
        for kind, value in result[name].items():  # type: ignore[union-attr]
            print(f"  {label:18s} {kind:18s} 平均 {value.get('mean', 0):.4f}  最大 {value.get('max', 0):.4f}")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
