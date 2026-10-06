#!/usr/bin/env python
"""状态机硬停止语义单元测试（脚本化帧序列，不开游戏、不真点击）。

验证用户核心要求：
  1. 任何意外界面 → 立即 STOPPED，且**绝不自动恢复**
  2. 恢复只有一个人工入口（request_resume），且恢复前验证钓鱼界面
  3. 点击前复核：界面变化 → 放弃点击 + 停止（防购买护栏）
  4. READY 双帧确认；同一结算只点一次；异常/设备错误 → 停止
运行：python tests/test_bot_hardstop.py
"""
from __future__ import annotations

import random
import sys
import tempfile
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


def det(state="WAITING", in_fishing=True, rarity=None, button=None) -> Detection:
    return Detection(qiufu_score=0.95 if in_fishing else 0.1,
                     in_fishing=in_fishing, state=state, rarity=rarity, button=button,
                     mean_v=150.0)


class FakeBridge:
    """记录点击；截屏返回 1x1 假帧。"""

    name = "fake"

    def __init__(self):
        self.taps: list[tuple[float, float, str]] = []
        self._size = (100, 200)

    def screenshot(self):
        return np.zeros((200, 100, 3), np.uint8)

    def tap(self, x, y, tag=""):
        self.taps.append((x, y, tag))

    def screen_size(self):
        return self._size


class FakeDetector:
    """按脚本依次返回检测结果；耗尽后重复最后一个。"""

    def __init__(self, script: list[Detection]):
        self.script = list(script)
        self.calls = 0

    def analyze(self, frame) -> Detection:
        self.calls += 1
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]


def make_bot(script, cfg_over=None):
    cfg = {
        "zones": {
            "qiufu_search": {"x0": 0, "y0": 0.64, "x1": 0.18, "y1": 0.75},
            "cast_hint": {"x0": 0.32, "y0": 0.845, "x1": 0.68, "y1": 0.895},
            "name_band": {"x0": 0.40, "y0": 0.50, "x1": 0.60, "y1": 0.55},
            "btn_left": {"x0": 0.14, "y0": 0.79, "x1": 0.43, "y1": 0.86},
            "safe_cast": {"x0": 0.25, "y0": 0.78, "x1": 0.75, "y1": 0.92},
        },
        "thresholds": {"qiufu_match": 0.85, "anchor_miss_frames": 3, "unknown_frames": 3},
        "hsv_bands": [], "rare_rarities": ["紫", "黄"], "delays_ms": FAST,
        "action_cooldown_s": 0.0, "verify_before_tap": True, "ready_stable_frames": 2,
        "max_device_errors": 2, "hotkey_pause": "F9", "hotkey_resume": "F10",
    }
    cfg.update(cfg_over or {})
    tmp = Path(tempfile.mkdtemp())
    bridge = FakeBridge()
    fdet = FakeDetector(script)
    bot = FishingBot(bridge, fdet, Humanizer(random.Random(1), FAST),
                     Notifier(enabled=False), Audit(tmp), cfg, start_auto=True)
    return bot, bridge, fdet


class TestHardStop(unittest.TestCase):

    def test_unexpected_ui_stops_and_never_auto_resumes(self):
        """锚点缺失 3 帧 → STOPPED；再喂 10 帧也不会自动恢复、不点击。"""
        script = [det("READY"), det("READY"), det("READY")] + \
                 [det("UNKNOWN", in_fishing=False)] * 13
        bot, bridge, _ = make_bot(script)
        for _ in range(16):
            bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("意外界面", bot.stop_reason)
        self.assertLessEqual(len(bridge.taps), 1, "停止后绝不再点击")

    def test_resume_requires_human_and_verifies(self):
        """人工恢复：界面回来才恢复；界面不在则保持 STOPPED。"""
        script = [det("UNKNOWN", in_fishing=False)] * 4
        bot, bridge, _ = make_bot(script)
        for _ in range(4):
            bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        # 人工恢复请求，但界面仍未回来（脚本最后一个元素=无锚点）→ 保持停止
        bot.request_resume()
        bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("不在钓鱼界面", bot.stop_reason)
        # 人工恢复请求 + 界面回来了 → 恢复
        bot.det = FakeDetector([det("WAITING")])   # 测试注入：界面已回来
        bot.request_resume()
        bot.step()
        self.assertIs(bot.status, BotStatus.RUNNING)

    def test_pre_tap_verification_aborts_on_change(self):
        """点击前复核发现界面变化 → 不点击 + 停止（防购买护栏核心）。"""
        script = [det("READY"), det("READY"),
                  det("UNKNOWN", in_fishing=False)]      # 复核帧：界面变了
        bot, bridge, _ = make_bot(script)
        bot.step()                                        # 第 1 帧 READY（双帧确认未满足）
        bot.step()                                        # 第 2 帧 READY → 延迟后复核 → 变了
        self.assertEqual(bridge.taps, [], "界面变化必须放弃点击")
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("点击前界面已变化", bot.stop_reason)

    def test_ready_needs_two_frames(self):
        """单帧 READY 不抛竿；连续两帧才抛（复核仍一致时）。"""
        script = [det("READY"), det("WAITING"), det("READY"), det("READY"), det("READY")]
        bot, bridge, _ = make_bot(script)
        bot.step()                                        # READY 第 1 帧：不动作
        bot.step()                                        # WAITING：计数清零
        self.assertEqual(bridge.taps, [])
        bot.step()                                        # READY 第 1 帧
        bot.step()                                        # READY 第 2 帧 → 复核(READY) → 点击
        self.assertEqual(len(bridge.taps), 1)
        self.assertEqual(bridge.taps[0][2], "cast")

    def test_rare_fish_stops_and_manual_flow(self):
        """紫/黄 → STOPPED + 通知；人工处理（脚本切回 READY）+ resume 恢复。"""
        script = [det("CATCH", rarity="紫", button=None), det("CATCH", rarity="紫")]
        bot, bridge, _ = make_bot(script)
        bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("稀有鱼", bot.stop_reason)
        self.assertEqual(bot.stats["rare_stops"], 1)
        self.assertEqual(bridge.taps, [], "稀有鱼绝不自动点击")
        self.assertTrue(any(k == "rare" for k, _ in bot.notifier.messages))
        # 人工处理完 → 手动恢复
        bot.det = FakeDetector([det("READY")])
        bot.request_resume()
        bot.step()
        self.assertIs(bot.status, BotStatus.RUNNING)

    def test_unknown_rarity_stops(self):
        script = [det("CATCH", rarity="未知", button="出售")] * 3
        bot, bridge, _ = make_bot(script)
        for _ in range(3):
            bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("识别异常", bot.stop_reason)
        self.assertEqual(bridge.taps, [])

    def test_catch_single_tap_guard(self):
        """同一结算只点一次；结算迟迟不消失 → 停止（不二次点击）。"""
        script = [det("READY"), det("READY"), det("READY"),
                  det("CATCH", rarity="白", button="出售"),
                  det("CATCH", rarity="白", button="出售")] + \
                 [det("CATCH", rarity="白", button="出售")] * 12
        bot, bridge, _ = make_bot(script)
        bot.step(); bot.step()
        self.assertEqual(len(bridge.taps), 1)             # 抛竿
        bot.step()                                        # 进入结算并点击出售
        sells = [t for t in bridge.taps if t[2] == "sell"]
        self.assertEqual(len(sells), 1)
        for _ in range(12):                               # 结算不消失
            bot.step()
        self.assertEqual([t for t in bridge.taps if t[2] == "sell"], sells,
                         "同一结算绝不二次点击")
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("结算界面未消失", bot.stop_reason)

    def test_device_error_stops_after_limit(self):
        """设备错误连续 2 次 → STOPPED。"""
        from fishing_plugin.devices.base import DeviceError
        class BoomBridge(FakeBridge):
            def screenshot(self):
                raise DeviceError("USB 断开")
        tmp = Path(tempfile.mkdtemp())
        cfg = make_bot([det("WAITING")])[0].cfg
        bridge = BoomBridge()
        fdet = FakeDetector([det("WAITING")])
        bot = FishingBot(bridge, fdet, Humanizer(random.Random(1), FAST),
                         Notifier(enabled=False), Audit(tmp), cfg, start_auto=True)
        bot.step()
        self.assertIs(bot.status, BotStatus.RUNNING, "第 1 次设备错误应先容忍")
        bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("设备错误", bot.stop_reason)

    def test_internal_exception_stops(self):
        """任何未捕获异常 → STOPPED，绝不带病运行。"""
        class BoomDet(FakeDetector):
            def analyze(self, frame):
                raise ValueError("detector boom")
        tmp = Path(tempfile.mkdtemp())
        cfg = make_bot([det("WAITING")])[0].cfg
        bot = FishingBot(FakeBridge(), BoomDet([]), Humanizer(random.Random(1), FAST),
                         Notifier(enabled=False), Audit(tmp), cfg, start_auto=True)
        bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("程序异常", bot.stop_reason)

    def test_user_pause_never_auto_resumes(self):
        """手动暂停后喂多少帧都不动；resume 才恢复。"""
        bot, bridge, _ = make_bot([det("READY")] * 10)
        bot.request_pause()
        for _ in range(10):
            bot.step()
        self.assertIs(bot.status, BotStatus.PAUSED_USER)
        self.assertEqual(bridge.taps, [])
        bot.request_resume()
        bot.step()
        self.assertIs(bot.status, BotStatus.RUNNING)

    def test_purchase_zone_guards(self):
        """所有自动点击都严格落在白名单区（SAFE_CAST / btn_left）——结构性防购买。"""
        script = [det("READY")] * 30
        bot, bridge, _ = make_bot(script)
        for _ in range(10):
            bot.step()
        z = bot.zones
        for x, y, tag in bridge.taps:
            zone = z["safe_cast"] if tag == "cast" else z["btn_left"]
            self.assertTrue(zone["x0"] < x < zone["x1"] and zone["y0"] < y < zone["y1"],
                            f"{tag} 点击越界: ({x:.3f},{y:.3f})")

    def test_dry_run_never_taps(self):
        script = [det("READY")] * 6
        bot, bridge, _ = make_bot(script, cfg_over={"verify_before_tap": False})
        bot.dry_run = True
        for _ in range(4):
            bot.step()
        self.assertEqual(bridge.taps, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
