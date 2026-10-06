#!/usr/bin/env python
"""生成求福主锚点模板。

真实游戏：
    python tools/make_template.py --real <真实截图.jpg> -o templates/qiufu_real.png
    （自动在左列 y63%~74% 找金色圆形图标连通域，取中心 44% 方形做模板，
      打印 ref-width = 截图宽度，供 --ref-width 使用）

测试游戏（自有素材，无版权问题）：
    python tools/make_template.py --game -o templates/qiufu_test.png
    （从 fishing-game-verify/game/assets/qiufu.png 缩放 48px 取圆心 28px，
      ref-width = 540）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GAME_ASSET = ROOT.parent / "fishing-game-verify" / "game" / "assets" / "qiufu.png"


def load_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        sys.exit(f"读取失败: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def save_rgb(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))[1].tofile(str(path))
    print(f"已保存 {path}  尺寸 {arr.shape[1]}x{arr.shape[0]}")


def from_real(shot: Path) -> np.ndarray:
    img = load_rgb(shot)
    h, w = img.shape[:2]
    r, g, b = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    gold = ((r > 150) & (g > 110) & (b < 110)).astype(np.uint8)
    zone = np.zeros_like(gold)
    zone[int(.63 * h):int(.74 * h), :int(.18 * w)] = 1
    gold *= zone
    n, _, stats, _ = cv2.connectedComponentsWithStats(gold, 8)
    cands = [(stats[i][4], i) for i in range(1, n) if stats[i][4] > 300]
    if not cands:
        sys.exit("未在左列找到金色图标连通域（截图对吗？）")
    _, i = max(cands)
    x, y, ww, hh, _ = stats[i]
    cx, cy = x + ww // 2, y + hh // 2
    half = int(min(ww, hh) * 0.22)          # 中心 44% 方形，避开金边与背景
    print(f"图标连通域: x {x/w:.1%}~{(x+ww)/w:.1%}, y {y/h:.1%}~{(y+hh)/h:.1%}")
    return img[cy - half:cy + half, cx - half:cx + half]


def from_game_asset() -> np.ndarray:
    img = load_rgb(GAME_ASSET)
    img = cv2.resize(img, (48, 48), interpolation=cv2.INTER_AREA)
    return img[10:38, 10:38]                 # 28x28 圆内不透明区（冒烟测试同款）


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", type=Path, help="真实游戏截图路径")
    ap.add_argument("--game", action="store_true", help="从测试游戏素材生成")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    if bool(args.real) == bool(args.game):
        sys.exit("二选一：--real <截图> 或 --game")
    if args.real:
        shot = args.real if args.real.is_absolute() else Path.cwd() / args.real
        tpl = from_real(shot)
        save_rgb(tpl, args.out)
        print(f"ref-width = {load_rgb(shot).shape[1]}   "
              f"运行时加 --ref-width {load_rgb(shot).shape[1]}")
    else:
        save_rgb(from_game_asset(), args.out)
        print("ref-width = 540")


if __name__ == "__main__":
    main()
