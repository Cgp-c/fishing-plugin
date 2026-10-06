"""拟人化：随机点击位置 + 随机延迟（降低行为特征，缓解封号风险——无法归零）。"""
from __future__ import annotations

import random
from typing import Mapping, Sequence


class Humanizer:
    def __init__(self, rng: random.Random, delays_ms: Mapping[str, Sequence[int]]):
        self.rng = rng
        self.delays = {k: (list(v) if isinstance(v, Sequence) else [v, v])
                       for k, v in delays_ms.items()}

    def point_in(self, zone: dict, margin: float = 0.12) -> tuple[float, float]:
        """区域内随机取点（避开边缘 margin 比例），返回全屏百分比坐标。"""
        x0, x1 = zone["x0"], zone["x1"]
        y0, y1 = zone["y0"], zone["y1"]
        mx = (x1 - x0) * margin
        my = (y1 - y0) * margin
        return (self.rng.uniform(x0 + mx, x1 - mx),
                self.rng.uniform(y0 + my, y1 - my))

    def interval(self, kind: str) -> float:
        """随机延迟（秒）。kind: cast / catch_click / poll / after_action。"""
        lo, hi = self.delays.get(kind, [500, 1000])
        return self.rng.uniform(lo, hi) / 1000.0
