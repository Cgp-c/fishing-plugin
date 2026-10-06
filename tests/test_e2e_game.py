#!/usr/bin/env python
"""端到端回归（硬停止语义版）：外部黑盒驱动"简单钓鱼游戏验证"窗口跑完整插件链路。

验证用户核心要求：
  1. 任何意外界面跳转（订单详情/活动公告/锚点缺失）→ 立即 STOPPED
  2. STOPPED 后绝不自动恢复——恢复只能人工（request_resume，对应 GUI「继续」/F10）
     且恢复前验证钓鱼界面
  3. 随时人工暂停（F9）/恢复（F10）
  4. 紫黄稀有鱼 → 停止+通知，人工处理后手动恢复
  5. 防购买：所有自动点击都在 SAFE_CAST / btn_left 内；点击前复核链路生效
  6. 五色鱼获分色序列正确、误触链路、审计核对

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
# force-rarity 列表耗尽后游戏回退随机出鱼（可能又出紫/黄），把后续也固定为白，
# 消除第 6 条及以后的不确定性（额外抛竿消耗的都是白鱼）
FORCE_SEQ = RARITY_SEQ + ["白"] * 5


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
             "--force-rarity=" + ",".join(FORCE_SEQ),
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
        cfg["action_cooldown_s"] = 3.0            # 保证误触落在冷却窗口内（确定性）
        cfg["start_auto"] = False                 # 等「人工」开始
        cfg["verify_before_tap"] = True           # 走真实防购买复核链路
        det = Detector(cfg, ROOT / "templates" / "qiufu_test.png", 540)
        notifier = Notifier(enabled=False)
        return FishingBot(cls.bridge, det, Humanizer(random.Random(42), FAST_DELAYS),
                          notifier, cls.audit, cfg)

    def human_fix_and_resume(self):
        """模拟人工处理意外（关活动公告）后点「继续」。"""
        self.bridge.tap(0.8775, 0.2975, tag="manual")     # ad_close_x 中心
        time.sleep(0.3)
        self.bot.request_resume()

    def drive(self, bot, until, timeout=20.0, note="", on_missing="default"):
        """反复 step 直到条件满足。on_missing：STOPPED 时的人工处理回调。"""
        cb = self.human_fix_and_resume if on_missing == "default" else on_missing
        t0 = time.monotonic()
        res = None
        while time.monotonic() - t0 < timeout:
            if cb and bot.status is BotStatus.STOPPED:
                cb()
            res = bot.step()
            if until(res, bot):
                return res
            time.sleep(0.05)
        self.fail(f"超时等待条件{note}：status={bot.status.name} reason={bot.stop_reason} "
                  f"last={res and (res.detection.state if res.detection else None)}")

    # ------------------------------------------------------------------ 用例
    def test_full_flow(self):
        bot = self.make_bot()
        self.bot = bot
        audit_dir = Path(self.tmp.name)

        # 1) 游戏在 HOME 时点「开始」→ 验证失败拒绝启动（小白引导 + 安全）
        bot.request_start()
        bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED)
        self.assertIn("不在钓鱼界面", bot.stop_reason)

        # 2) 模拟人手进入游戏 → 人工恢复 → 验证通过开始运行
        self.bridge.tap(0.5, 0.61, tag="manual")              # home_btn 中心
        bot.request_resume()
        bot.step()
        self.assertIs(bot.status, BotStatus.RUNNING)

        # 3) 五色鱼获（--force-rarity 白绿蓝紫黄，按 seen_catches 核对序列）
        commons = 0
        for k, expect in enumerate(RARITY_SEQ):
            self.drive(bot, lambda r, b, k=k: len(b.seen_catches) >= k + 1,
                       timeout=30, note=f"第{k + 1}条鱼({expect})")
            got = bot.seen_catches[k]
            self.assertEqual(got, expect, f"第{k + 1}条鱼分色: {bot.seen_catches}")

            if expect in ("紫", "黄"):
                # 稀有鱼 → 立即停止 + 通知；绝不自动点击
                self.drive(bot, lambda r, b: b.status is BotStatus.STOPPED,
                           timeout=8, note=f"{expect}停止", on_missing=None)
                self.assertIn("稀有鱼", bot.stop_reason)
                self.assertTrue(any(kk == "rare" for kk, _ in bot.notifier.messages))
                # 模拟人工处理鱼获，再人工恢复（若有活动公告，回调会关掉）
                self.bridge.tap(0.285, 0.825, tag="manual")
                bot.request_resume()
                self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING,
                           timeout=8, note=f"{expect}人工处理后恢复")
            else:
                commons += 1
                self.drive(bot, lambda r, b, c=commons: b.stats["sold"] + b.stats["submitted"] >= c,
                           timeout=15, note=f"{expect}自动出售")
        self.assertGreaterEqual(bot.stats["casts"], 5, "五条鱼应至少抛竿五次")

        # 4) 误触链路：出售后的冷却窗口内点订单面板（模拟点击越界偏高）→ 立即停止
        self.drive(bot, lambda r, b: b.stats["sold"] + b.stats["submitted"] >= commons + 1,
                   timeout=40, note="第6条鱼出售")
        self.bridge.tap(0.84, 0.4965, tag="manual")           # order_rows 中心
        for _ in range(4):
            bot.step()
        self.assertIs(bot.status, BotStatus.STOPPED, "订单详情遮挡锚点应立即停止")
        self.assertIn("意外界面", bot.stop_reason)
        # 停止后界面恢复（人工关闭弹窗）也**不会**自动继续——先证明这一点
        self.bridge.tap(0.5775, 0.3475, tag="manual")         # close_x 中心
        self.bridge.tap(0.8775, 0.2975, tag="manual")         # ad_close_x（若弹的是公告）
        for _ in range(6):
            bot.step()
            self.assertIs(bot.status, BotStatus.STOPPED, "绝不能自动恢复")
        # 人工恢复 → 验证通过 → 继续
        bot.request_resume()
        self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING,
                   timeout=8, note="人工恢复")

        # 5) F9 暂停 / F10 恢复（兜底热键；按住期间轮询捕捉上升沿）
        import ctypes
        u32 = ctypes.windll.user32
        u32.keybd_event(0x78, 0, 0, 0)                        # F9 down
        time.sleep(0.08)
        bot.poll_hotkeys()
        u32.keybd_event(0x78, 0, 2, 0)                        # F9 up
        self.assertIs(bot.status, BotStatus.PAUSED_USER)
        for _ in range(4):                                    # 暂停后不动作
            bot.step()
            self.assertIs(bot.status, BotStatus.PAUSED_USER)
        u32.keybd_event(0x79, 0, 0, 0)                        # F10 down
        time.sleep(0.08)
        bot.poll_hotkeys()
        u32.keybd_event(0x79, 0, 2, 0)                        # F10 up
        self.drive(bot, lambda r, b: b.status is BotStatus.RUNNING,
                   timeout=8, note="F10恢复")

        # 6) 审计核对：插件所有点击都落在允许区域（结构性防购买）
        log = (audit_dir / "logs" / "audit.log").read_text(encoding="utf-8")
        casts = re.findall(r"click\(([\d.]+),([\d.]+)\)@cast", log)
        btns = re.findall(r"click\(([\d.]+),([\d.]+)\)@(?:sell|submit)", log)
        self.assertGreaterEqual(len(casts), 5, f"cast 点击数: {len(casts)}")
        zones = load_config()["zones"]
        sc, bl = zones["safe_cast"], zones["btn_left"]
        for x, y in casts:
            self.assertTrue(sc["x0"] < float(x) < sc["x1"] and sc["y0"] < float(y) < sc["y1"],
                            f"cast 越界: ({x},{y})")
        for x, y in btns:
            self.assertTrue(bl["x0"] < float(x) < bl["x1"] and bl["y0"] < float(y) < bl["y1"],
                            f"按钮点击越界: ({x},{y})")
        bot.exit()
        print(f"\n    e2e stats={bot.stats} casts_log={len(casts)} btn_log={len(btns)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
