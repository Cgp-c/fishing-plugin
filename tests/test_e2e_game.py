#!/usr/bin/env python
"""端到端回归：以纯黑盒方式外部驱动"简单钓鱼游戏验证"窗口跑完整插件链路。

与单元测试不同，这里走的是真实输入/输出路径：
  游戏窗口（独立进程）← 屏幕截屏（ImageGrab）+ 真实鼠标点击（PCWindowBridge）
插件侧覆盖游戏 31 项冒烟断言对应的回归点：
  1. HOME（无求福锚点）→ 连续缺失 → 自动暂停
  2. 进入钓鱼界面 → 恢复；READY 判定 → SAFE_CAST 随机抛竿 → WAITING
  3. 五色鱼获（--force-rarity 白绿蓝紫黄）：
     白/绿/蓝 → 自动点左下按钮；紫/黄 → 暂停 + 通知，人工处理（模拟点击）后自动继续
  4. 误触链路：点击订单面板（模拟越界偏高）→ 订单详情 → 锚点缺失暂停 → 关闭后恢复
  5. F9 热键暂停/恢复（合成按键）
  6. 审计核对：所有 cast 点击都在 SAFE_CAST 内，sell/submit 都在 btn_left 内

运行：python tests/test_e2e_game.py   （会弹出游戏窗口并移动鼠标，请勿操作电脑）
"""
from __future__ import annotations

import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME_DIR = ROOT.parent / "fishing-game-verify"
sys.path.insert(0, str(ROOT))

from fishing_plugin.audit import Audit                       # noqa: E402
from fishing_plugin.bot import BotStatus, FishingBot         # noqa: E402
from fishing_plugin.config import load_config                # noqa: E402
from fishing_plugin.detector import Detector                 # noqa: E402
from fishing_plugin.devices.pc_window import PCWindowBridge  # noqa: E402
from fishing_plugin.humanize import Humanizer                # noqa: E402
from fishing_plugin.notifier import Notifier                 # noqa: E402

WINDOW_TITLE = "简单钓鱼游戏验证"
FAST_DELAYS = {"cast": [80, 150], "catch_click": [60, 120],
               "poll": [60, 130], "after_action": [40, 90]}
RARITY_SEQ = ["白", "绿", "蓝", "紫", "黄"]


class E2E(unittest.TestCase):
    proc: subprocess.Popen
    bridge: PCWindowBridge

    @classmethod
    def setUpClass(cls):
        if not GAME_DIR.is_dir():
            raise unittest.SkipTest("找不到 fishing-game-verify，跳过 e2e")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.audit = Audit(Path(cls.tmp.name))
        cls.proc = subprocess.Popen(
            [sys.executable, "game/main.py",
             "--force-rarity=" + ",".join(RARITY_SEQ),
             "--wait-min=0.3", "--wait-max=0.8", "--seed=11"],
            cwd=GAME_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import ctypes
        hwnd = 0
        for _ in range(100):                      # 等游戏窗口出现（最长 10s）
            hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            cls.tearDownClass()
            raise RuntimeError("游戏窗口未出现")
        HWND_TOPMOST = ctypes.c_void_p(-1)
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 10, 10, 0, 0, 0x0001)
        time.sleep(0.6)
        cls.bridge = PCWindowBridge(WINDOW_TITLE, audit=cls.audit)
        cls.bridge.bring_to_front()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "proc", None):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    # ------------------------------------------------------------------ 辅助
    @classmethod
    def make_bot(cls) -> FishingBot:
        cfg = load_config()
        cfg["delays_ms"] = FAST_DELAYS
        cfg["action_cooldown_s"] = 0.6
        det = Detector(cfg, ROOT / "templates" / "qiufu_test.png", 540)
        notifier = Notifier(enabled=False)
        return FishingBot(cls.bridge, det, Humanizer(random.Random(42), FAST_DELAYS),
                          notifier, cls.audit, cfg)

    def drive(self, bot, until, timeout=20.0, note="", on_missing="default"):
        """反复 step 直到条件满足。on_missing：PAUSED_MISSING 时的回调（默认自动关弹窗）。"""
        cb = self.close_popup if on_missing == "default" else on_missing
        t0 = time.monotonic()
        res = None
        while time.monotonic() - t0 < timeout:
            if cb and bot.status is BotStatus.PAUSED_MISSING:
                cb()
            res = bot.step()
            if until(res, bot):
                return res
            time.sleep(0.05)
        self.fail(f"超时等待条件{note}：status={bot.status.name} "
                  f"last={res and (res.detection.state, res.detection.rarity, res.detection.notes)}")

    def close_popup(self):
        """卖鱼后 12% 概率弹活动公告 → 锚点缺失暂停。模拟人工关闭后自动继续。"""
        self.bridge.tap(0.8775, 0.2975, tag="manual")     # ad_close_x 中心
        time.sleep(0.3)

    # ------------------------------------------------------------------ 用例
    def test_full_flow(self):
        bot = self.make_bot()
        audit_dir = Path(self.tmp.name)

        # 1) HOME：无锚点 → 自动暂停 + 通知
        for _ in range(3):
            bot.step()   # 此处故意不关弹窗（HOME 本来就没弹窗，且要验证暂停本身）
        self.assertIs(bot.status, BotStatus.PAUSED_MISSING)
        self.assertTrue(any(k == "missing" for k, _ in bot.notifier.messages),
                        f"应发出缺失通知: {bot.notifier.messages}")

        # 2) 模拟人手进入游戏 → 锚点恢复 → 自动继续
        self.bridge.tap(0.5, 0.61, tag="manual")              # home_btn 中心
        self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING,
                   timeout=10, note="锚点恢复RUNNING")

        # 3) 五色鱼获（--force-rarity 白绿蓝紫黄，按 seen_catches 核对序列；
        #    结算在 step 内检测+动作一步完成，逐帧等 CATCH 会有竞态）
        commons = 0
        for k, expect in enumerate(RARITY_SEQ):
            self.drive(bot, lambda r, b, k=k: len(b.seen_catches) >= k + 1,
                       timeout=30, note=f"第{k + 1}条鱼({expect})")
            got = bot.seen_catches[k]
            self.assertEqual(got, expect, f"第{k + 1}条鱼分色: {bot.seen_catches}")

            if expect in ("紫", "黄"):
                self.drive(bot, lambda r, b: b.status is BotStatus.PAUSED_RARE,
                           timeout=8, note=f"{expect}暂停")
                self.assertTrue(any(kk == "rare" for kk, _ in bot.notifier.messages))
                # 模拟人工处理：手动点左下按钮卖掉/提交
                self.bridge.tap(0.285, 0.825, tag="manual")
                self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING,
                           timeout=8, note=f"{expect}人工处理后恢复")
            else:
                commons += 1
                self.drive(bot, lambda r, b, c=commons: b.stats["sold"] + b.stats["submitted"] >= c,
                           timeout=15, note=f"{expect}自动出售", on_missing=self.close_popup)
        self.assertGreaterEqual(bot.stats["casts"], 5, "五条鱼应至少抛竿五次")

        # 4) 误触链路：READY 时点订单面板（模拟插件点击越界偏高）
        self.drive(bot, lambda r, b: r.detection.state == "READY"
                   and b.status is BotStatus.RUNNING,
                   timeout=25, note="回到READY", on_missing=self.close_popup)
        self.bridge.tap(0.84, 0.4965, tag="manual")           # order_rows 中心
        for _ in range(4):
            bot.step()   # 不接 on_missing：验证订单详情确实触发暂停
        self.assertIs(bot.status, BotStatus.PAUSED_MISSING, "订单详情遮挡锚点应触发暂停")
        self.bridge.tap(0.5775, 0.3475, tag="manual")         # close_x 中心
        self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING
                   and r.detection.state == "READY", timeout=10, note="关闭订单详情恢复")

        # 5) F9 热键暂停/恢复（合成全局按键；按住期间轮询才能捕捉上升沿）
        import ctypes
        u32 = ctypes.windll.user32
        u32.keybd_event(0x78, 0, 0, 0)          # F9 down
        time.sleep(0.08)
        bot.poll_hotkeys()
        self.assertIs(bot.status, BotStatus.PAUSED_HOTKEY)
        u32.keybd_event(0x78, 0, 2, 0)          # F9 up
        time.sleep(0.3)
        bot.poll_hotkeys()                       # 松开不应误触发
        self.assertIs(bot.status, BotStatus.PAUSED_HOTKEY)
        u32.keybd_event(0x78, 0, 0, 0)
        time.sleep(0.08)
        bot.poll_hotkeys()
        self.assertIs(bot.status, BotStatus.RUNNING)
        u32.keybd_event(0x78, 0, 2, 0)

        # 6) 审计核对：插件所有点击都落在允许区域
        log = (audit_dir / "logs" / "audit.log").read_text(encoding="utf-8")
        casts = re.findall(r"click\(([\d.]+),([\d.]+)\)@cast", log)
        btns = re.findall(r"click\(([\d.]+),([\d.]+)\)@(?:sell|submit)", log)
        self.assertGreaterEqual(len(casts), 5, f"cast 点击数: {len(casts)}")
        sc = load_config()["zones"]["safe_cast"]
        for x, y in casts:
            self.assertTrue(sc["x0"] < float(x) < sc["x1"] and sc["y0"] < float(y) < sc["y1"],
                            f"cast 越界: ({x},{y})")
        bl = load_config()["zones"]["btn_left"]
        for x, y in btns:
            self.assertTrue(bl["x0"] < float(x) < bl["x1"] and bl["y0"] < float(y) < bl["y1"],
                            f"按钮点击越界: ({x},{y})")
        bot.request_stop("测试结束")
        print(f"\n    e2e stats={bot.stats} casts_log={len(casts)} btn_log={len(btns)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
