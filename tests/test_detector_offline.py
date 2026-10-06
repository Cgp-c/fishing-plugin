#!/usr/bin/env python
"""离线检测测试：用三张真实游戏截图验证检测核心（无需开游戏）。

素材来源：../fishing-plugin-analysis/{ready,waiting,catch}.jpg（929x2000 真实截图副本）
模板：templates/qiufu_real.png（同批截图提取，ref_width=929）

运行：python tests/test_detector_offline.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin.config import load_config          # noqa: E402
from fishing_plugin.detector import Detector, load_template  # noqa: E402

SHOTS = ROOT.parent / "fishing-plugin-analysis"
TPL_REAL = ROOT / "templates" / "qiufu_real.png"
REF_W_REAL = 929


def frame(name: str) -> np.ndarray:
    return np.asarray(Image.open(SHOTS / name).convert("RGB"), dtype=np.uint8)


class TestDetectorOffline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = load_config()
        cls.cfg = cfg
        cls.det = Detector(cfg, TPL_REAL, REF_W_REAL)

    def test_ready_state(self):
        d = self.det.analyze(frame("ready.jpg"))
        self.assertTrue(d.in_fishing, f"qiufu={d.qiufu_score}")
        self.assertGreaterEqual(d.qiufu_score, 0.85)
        self.assertEqual(d.state, "READY",
                         f"notes={d.notes}")
        self.assertGreaterEqual(d.notes["hint_col_ratio"], 0.30)

    def test_waiting_state(self):
        d = self.det.analyze(frame("waiting.jpg"))
        self.assertTrue(d.in_fishing, f"qiufu={d.qiufu_score}")
        self.assertEqual(d.state, "WAITING", f"notes={d.notes}")
        self.assertLess(d.notes["hint_col_ratio"], 0.30)

    def test_catch_state_green(self):
        d = self.det.analyze(frame("catch.jpg"))
        self.assertTrue(d.in_fishing, f"qiufu={d.qiufu_score}")
        self.assertEqual(d.state, "CATCH")
        self.assertLess(d.mean_v, self.cfg["thresholds"]["catch_mean_v_max"])
        self.assertEqual(d.rarity, "绿", f"notes={d.notes}")
        self.assertEqual(d.button, "出售", f"notes={d.notes}")

    def test_template_scale_invariance(self):
        """手机分辨率与模板来源不同时，模板自动缩放仍应命中。"""
        img = frame("ready.jpg")
        small = np.asarray(Image.fromarray(img).resize(
            (650, round(650 * img.shape[0] / img.shape[1])), Image.LANCZOS))
        d = self.det.analyze(small)
        self.assertTrue(d.in_fishing, f"qiufu={d.qiufu_score}")
        self.assertEqual(d.state, "READY", f"notes={d.notes}")

    def test_missing_template_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_template(ROOT / "templates" / "not_exist.png")


if __name__ == "__main__":
    unittest.main(verbosity=2)
