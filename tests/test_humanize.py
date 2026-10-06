#!/usr/bin/env python
"""拟人化随机点击/延迟的约束测试：随机点必须落在安全区内、延迟必须有界。"""
from __future__ import annotations

import random
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin.config import load_config      # noqa: E402
from fishing_plugin.humanize import Humanizer      # noqa: E402


class TestHumanize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        cls.zone = cls.cfg["zones"]["safe_cast"]

    def test_points_inside_safe_cast(self):
        h = Humanizer(random.Random(1), self.cfg["delays_ms"])
        z = self.zone
        for _ in range(2000):
            x, y = h.point_in(z)
            self.assertGreater(x, z["x0"])
            self.assertLess(x, z["x1"])
            self.assertGreater(y, z["y0"])
            self.assertLess(y, z["y1"])
            # 离界面边界（真实游戏订单面板在抛竿区上方 y<66%）保持安全距离
            self.assertGreater(y, 0.66, "点击越界偏高会误触订单面板")

    def test_points_spread(self):
        h = Humanizer(random.Random(2), self.cfg["delays_ms"])
        pts = [h.point_in(self.zone) for _ in range(500)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.assertGreater(statistics.pstdev(xs), 0.05, "x 分布应足够分散")
        self.assertGreater(statistics.pstdev(ys), 0.02, "y 分布应足够分散")

    def test_intervals_bounded(self):
        h = Humanizer(random.Random(3), self.cfg["delays_ms"])
        for kind, (lo, hi) in self.cfg["delays_ms"].items():
            for _ in range(200):
                t = h.interval(kind)
                self.assertGreaterEqual(t, lo / 1000.0 - 1e-9)
                self.assertLessEqual(t, hi / 1000.0 + 1e-9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
