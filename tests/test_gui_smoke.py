#!/usr/bin/env python
"""GUI 控制面板冒烟测试：构造窗口、模拟按钮操作，验证状态联动（不进 mainloop）。

运行：python tests/test_gui_smoke.py
"""
from __future__ import annotations

import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin.audit import Audit         # noqa: E402
from fishing_plugin.bot import BotStatus, FishingBot  # noqa: E402
from fishing_plugin.detector import Detection  # noqa: E402
from fishing_plugin.humanize import Humanizer  # noqa: E402
from fishing_plugin.notifier import Notifier   # noqa: E402

FAST = {"cast": [1, 2], "catch_click": [1, 2], "poll": [1, 2], "after_action": [1, 2]}


class FakeBridge:
    name = "fake"

    def screenshot(self):
        return np.zeros((200, 100, 3), np.uint8)

    def tap(self, x, y, tag=""):
        pass

    def screen_size(self):
        return (100, 200)


class FakeDetector:
    def analyze(self, frame):
        return Detection(qiufu_score=0.95, in_fishing=True, state="WAITING", mean_v=150.0)


CFG = {
    "zones": {"safe_cast": {"x0": 0.25, "y0": 0.78, "x1": 0.75, "y1": 0.92},
              "btn_left": {"x0": 0.14, "y0": 0.79, "x1": 0.43, "y1": 0.86}},
    "thresholds": {"qiufu_match": 0.85, "anchor_miss_frames": 3, "unknown_frames": 3},
    "hsv_bands": [], "rare_rarities": ["紫", "黄"], "delays_ms": FAST,
    "action_cooldown_s": 0.0, "verify_before_tap": False, "ready_stable_frames": 2,
    "max_device_errors": 2, "hotkey_pause": "F9", "hotkey_resume": "F10",
    "start_auto": False,
}


class GUISmoke(unittest.TestCase):
    def test_panel_flow(self):
        try:
            from fishing_plugin.app import ControlApp
        except ImportError as e:
            raise unittest.SkipTest(f"无 GUI 环境: {e}")
        bot = FishingBot(FakeBridge(), FakeDetector(), Humanizer(random.Random(1), FAST),
                         Notifier(enabled=False), Audit(Path(tempfile.mkdtemp())), dict(CFG))
        self.exited = False
        app = ControlApp(bot, on_exit=lambda: setattr(self, "exited", True))

        def _destroy():
            try:
                app.root.destroy()
            except Exception:
                pass
        self.addCleanup(_destroy)
        app.root.update()

        # 待开始：主按钮=开始
        self.assertEqual(app.status_lbl["text"], "待开始")
        self.assertEqual(app.main_btn["text"], "▶ 开始钓鱼")

        # 点「开始」→ 工作线程未启动，直接 step 排空恢复请求 → 运行中
        app._on_main()
        bot.step()
        app._tick()
        self.assertEqual(app.status_lbl["text"], "运行中")
        self.assertEqual(app.main_btn["text"], "● 运行中")

        # 暂停 → 已暂停；再点 → 恢复
        app._on_pause()
        app._tick()
        self.assertIs(bot.status, BotStatus.PAUSED_USER)
        self.assertEqual(app.status_lbl["text"], "已暂停")
        app._on_pause()                      # 暂停按钮在暂停态变「继续」
        bot.step()
        app._tick()
        self.assertIs(bot.status, BotStatus.RUNNING)

        # 停止态 → 主按钮变「继续」，原因显示
        bot.request_stop("意外界面：测试")
        app._tick()
        self.assertEqual(app.status_lbl["text"], "已停止")
        self.assertEqual(app.main_btn["text"], "▶ 继续")
        self.assertIn("意外界面", app.reason_lbl["text"])

        # 退出
        app._quit()
        time.sleep(0.2)
        try:
            app.root.update()
        except Exception:
            pass
        self.assertTrue(self.exited, "on_exit 回调应被调用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
