"""FishingBot：三态状态机主循环 + 安全暂停链路。

状态：
- RUNNING           正常运行（READY 抛竿 / CATCH 分色处理 / WAITING 静默）
- PAUSED_HOTKEY     F9 手动暂停（再按 F9 恢复）
- PAUSED_RARE       紫/黄鱼获 → 暂停+通知；人工处理完（不再是 CATCH）自动继续
- PAUSED_UNKNOWN    分色连续未知 → 暂停+通知；同上自动继续
- PAUSED_MISSING    求福锚点连续缺失（非钓鱼界面/未知弹窗）→ 暂停+通知；
                    锚点重新出现自动继续
- STOPPED           结束（热键不提供退出，Ctrl+C / 时长到 / request_stop）

安全设计：任何动作点击前有随机延迟 + 冷却期；点击只在 SAFE_CAST / btn_left 内；
紫黄鱼与未知情况一律保守暂停。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

from .audit import Audit
from .detector import Detection, Detector
from .devices.base import DeviceBridge
from .humanize import Humanizer
from .hotkey import HotkeyPoller
from .notifier import Notifier


class BotStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED_HOTKEY = "PAUSED_HOTKEY"
    PAUSED_RARE = "PAUSED_RARE"
    PAUSED_UNKNOWN = "PAUSED_UNKNOWN"
    PAUSED_MISSING = "PAUSED_MISSING"
    STOPPED = "STOPPED"


@dataclass
class StepResult:
    events: list[str] = field(default_factory=list)
    detection: Detection | None = None


class FishingBot:
    def __init__(self, bridge: DeviceBridge, detector: Detector, human: Humanizer,
                 notifier: Notifier, audit: Audit, cfg: dict, *,
                 dry_run: bool = False, debug_save: bool = False):
        self.bridge = bridge
        self.det = detector
        self.human = human
        self.notifier = notifier
        self.audit = audit
        self.cfg = cfg
        self.zones = cfg["zones"]
        self.th = cfg["thresholds"]
        self.rare = set(cfg["rare_rarities"])
        self.dry_run = dry_run
        self.debug_save = debug_save

        self.status = BotStatus.RUNNING
        self.hotkeys = HotkeyPoller((cfg.get("hotkey_pause") or "F9",))
        self.miss_count = 0
        self.unknown_count = 0
        self.last_tap = -1e9
        self.cooldown = float(cfg.get("action_cooldown_s", 2.0))
        self.stats = {"casts": 0, "sold": 0, "submitted": 0, "rare_paused": 0,
                      "missing_paused": 0, "unknown_paused": 0}
        self.seen_catches: list[str] = []      # 每次进入结算态记录一次分色结果
        self._last_state = "UNKNOWN"
        self._shot_i = 0

    # ------------------------------------------------------------------ 内部
    def _log(self, name: str, detail: str = "") -> None:
        self.audit.event(name, detail)
        print(f"    · {name} {detail}".rstrip(), flush=True)

    def _shoot(self) -> np.ndarray:
        frame = self.bridge.screenshot()
        if self.debug_save:
            self._shot_i += 1
            p = self.audit.debug_path(f"{self._shot_i:05d}")
            cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))[1].tofile(str(p))
        return frame

    def _tap(self, x: float, y: float, tag: str) -> None:
        if self.dry_run:
            self._log(f"tap(dry-run)@{tag}", f"({x:.3f},{y:.3f})")
            return
        self.bridge.tap(x, y, tag=tag)
        self.last_tap = time.monotonic()

    def _cooldown_ok(self) -> bool:
        return (time.monotonic() - self.last_tap) >= self.cooldown

    def _sleep(self, kind: str) -> None:
        time.sleep(self.human.interval(kind))

    # ------------------------------------------------------------------ 热键
    def poll_hotkeys(self) -> list[str]:
        fired = self.hotkeys.poll()
        for key in fired:
            if self.status is BotStatus.PAUSED_HOTKEY:
                self.status = BotStatus.RUNNING
                self._log("resume:hotkey", key)
                self.notifier.notify("info", f"{key} → 继续运行")
            else:
                self.status = BotStatus.PAUSED_HOTKEY
                self._log("pause:hotkey", key)
                self.notifier.notify("hotkey", f"{key} → 已暂停（再按继续）")
        return fired

    # ------------------------------------------------------------------ 主循环
    def step(self) -> StepResult:
        res = StepResult()
        det = self.det.analyze(self._shoot())
        res.detection = det
        ev = res.events
        ev.append(f"state={det.state}" if det.in_fishing else "anchor=miss")
        self.audit.event("detect",
                         f"qiufu={det.qiufu_score} state={det.state} "
                         f"rarity={det.rarity} btn={det.button} meanV={det.mean_v}")
        state_now = det.state if det.in_fishing else "UNKNOWN"
        if state_now == "CATCH" and self._last_state != "CATCH":
            self.seen_catches.append(det.rarity or "未知")
        self._last_state = state_now

        # ---- 主锚点缺失链路 ----
        if not det.in_fishing:
            self.miss_count += 1
            if self.miss_count >= self.th["anchor_miss_frames"] \
                    and self.status is BotStatus.RUNNING:
                self.status = BotStatus.PAUSED_MISSING
                self.stats["missing_paused"] += 1
                ev.append("pause:missing")
                self._log("pause:missing", f"连续{self.miss_count}帧无求福锚点")
                self.notifier.notify("missing", "不在钓鱼界面（弹窗/切换页面？），已暂停；"
                                     "处理完会自动继续")
            return res
        self.miss_count = 0

        # ---- 自动恢复 ----
        if self.status is BotStatus.PAUSED_MISSING:
            self.status = BotStatus.RUNNING
            ev.append("resume:missing")
            self._log("resume:missing", "锚点恢复 → 继续")
        if self.status in (BotStatus.PAUSED_RARE, BotStatus.PAUSED_UNKNOWN) \
                and det.state != "CATCH":
            why = "rare" if self.status is BotStatus.PAUSED_RARE else "unknown"
            self.status = BotStatus.RUNNING
            ev.append(f"resume:{why}")
            self._log(f"resume:{why}", "鱼获已人工处理 → 继续")
        if self.status is not BotStatus.RUNNING:      # 热键暂停等：只观察不动手
            return res

        # ---- 动作决策 ----
        if det.state == "READY":
            if self._cooldown_ok():
                x, y = self.human.point_in(self.zones["safe_cast"])
                ev.append(f"cast({x:.3f},{y:.3f})")
                self._sleep("cast")
                self._tap(x, y, "cast")
                self.stats["casts"] += 1
                self._log("cast", f"({x:.3f},{y:.3f})")
                self._sleep("after_action")
        elif det.state == "CATCH":
            rarity = det.rarity or "未知"
            if rarity in self.rare:
                self.status = BotStatus.PAUSED_RARE
                self.stats["rare_paused"] += 1
                ev.append(f"pause:rare:{rarity}")
                self._log("pause:rare", f"{rarity}色鱼获 → 等待人工处理")
                self.notifier.notify("rare", f"钓到 {rarity} 色鱼获！已暂停，"
                                     "请手动处理后自动继续")
            elif rarity == "未知":
                self.unknown_count += 1
                if self.unknown_count >= self.th["unknown_frames"]:
                    self.status = BotStatus.PAUSED_UNKNOWN
                    self.stats["unknown_paused"] += 1
                    ev.append("pause:unknown")
                    self._log("pause:unknown", f"连续{self.unknown_count}次无法分色")
                    self.notifier.notify("unknown", "鱼获颜色无法识别，已暂停等待人工")
                    self.unknown_count = 0
            else:
                self.unknown_count = 0
                if self._cooldown_ok():
                    x, y = self.human.point_in(self.zones["btn_left"])
                    kind = det.button or "出售"
                    ev.append(f"{kind}({x:.3f},{y:.3f})")
                    self._sleep("catch_click")
                    self._tap(x, y, "sell" if kind == "出售" else "submit")
                    self.stats["sold" if kind == "出售" else "submitted"] += 1
                    self._log(kind, f"{rarity}色 ({x:.3f},{y:.3f})")
                    self._sleep("after_action")
        else:                                          # WAITING：不操作
            self.unknown_count = 0
        return res

    def request_stop(self, reason: str = "") -> None:
        self.status = BotStatus.STOPPED
        self._log("stop", reason)

    def run(self, duration: float | None = None, max_steps: int | None = None) -> None:
        """主循环：默认一直跑到 Ctrl+C / 时长限制。"""
        t0 = time.monotonic()
        steps = 0
        print(f"▶ 开始运行（{self.bridge.name} 桥，dry_run={self.dry_run}）"
              f"  热键 {self.cfg.get('hotkey_pause')} 暂停/继续，Ctrl+C 退出", flush=True)
        try:
            while self.status is not BotStatus.STOPPED:
                self.poll_hotkeys()
                if self.status is BotStatus.STOPPED:
                    break
                self.step()
                steps += 1
                if duration is not None and time.monotonic() - t0 >= duration:
                    self.request_stop("时长到")
                    break
                if max_steps is not None and steps >= max_steps:
                    self.request_stop("步数到")
                    break
                time.sleep(self.human.interval("poll"))
        except KeyboardInterrupt:
            self.request_stop("Ctrl+C")
        print(f"■ 结束：{self.stats}", flush=True)
