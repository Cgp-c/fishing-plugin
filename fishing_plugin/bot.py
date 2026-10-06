"""FishingBot：三态状态机 + 硬停止语义（可靠性收口版）。

状态只有 4 个：
- WAITING_START ：程序启动后等待用户点「开始」（GUI 大按钮）
- RUNNING       ：正常运行（READY 抛竿 / CATCH 分色处理 / WAITING 静默）
- PAUSED_USER   ：人工暂停（F9 / GUI 按钮）
- STOPPED       ：**任何**意外（界面跳转/锚点缺失、稀有鱼、识别异常、设备错误、
                  程序异常、点击前复核不符、人工停止、时长到）

铁律（对应用户要求）：
1. 任何意外界面跳转 → 立即 STOPPED + 响铃 + 原因写日志
2. STOPPED / PAUSED_USER **绝不自动恢复**——唯一恢复入口是人工操作
   （GUI「继续」按钮或 F10），恢复前重新截屏验证确在钓鱼界面
3. 永不点击 SAFE_CAST / btn_left 之外的区域；点击前二次复核，界面变了就放弃点击并停止
4. 绝不代用户关闭任何弹窗、绝不触碰游戏内任何购买/充值入口

线程模型：GUI/热键线程只调用 request_*()（仅设标志）；设备访问（截屏/点击）
全部发生在 step()/run() 所在的工作线程，无锁竞争。
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

from .audit import Audit
from .detector import Detection, Detector
from .devices.base import DeviceBridge, DeviceError
from .humanize import Humanizer
from .hotkey import HotkeyPoller
from .notifier import Notifier


class BotStatus(str, Enum):
    WAITING_START = "WAITING_START"
    RUNNING = "RUNNING"
    PAUSED_USER = "PAUSED_USER"
    STOPPED = "STOPPED"


# 停止原因 → 统计键
_REASON_STATS = {
    "意外界面": "missing_stops",
    "稀有鱼": "rare_stops",
    "识别异常": "unknown_stops",
    "设备错误": "device_stops",
    "程序异常": "error_stops",
    "手动停止": "user_stops",
}


@dataclass
class StepResult:
    events: list[str] = field(default_factory=list)
    detection: Detection | None = None


class FishingBot:
    def __init__(self, bridge: DeviceBridge, detector: Detector, human: Humanizer,
                 notifier: Notifier, audit: Audit, cfg: dict, *,
                 dry_run: bool = False, debug_save: bool = False,
                 start_auto: bool = False):
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

        start_auto = start_auto or bool(cfg.get("start_auto", False))
        self.status = BotStatus.RUNNING if start_auto else BotStatus.WAITING_START
        self.stop_reason: str | None = None if start_auto else "等待开始"

        self.hotkeys = HotkeyPoller((cfg.get("hotkey_pause") or "F9",
                                     cfg.get("hotkey_resume") or "F10"))
        self.miss_count = 0
        self.unknown_count = 0
        self.device_errors = 0
        self.ready_count = 0
        self.last_tap = -1e9
        self.cooldown = float(cfg.get("action_cooldown_s", 2.0))
        self.verify_before_tap = bool(cfg.get("verify_before_tap", True))
        self.ready_stable_frames = int(cfg.get("ready_stable_frames", 2))
        self.max_device_errors = int(cfg.get("max_device_errors", 2))
        self._catch_tapped = False            # 同一结算只允许一次按钮点击
        self._catch_frames_after_tap = 0
        self._last_state = "UNKNOWN"

        self.stats = {"casts": 0, "sold": 0, "submitted": 0, "missing_stops": 0,
                      "rare_stops": 0, "unknown_stops": 0, "device_stops": 0,
                      "error_stops": 0, "user_stops": 0}
        self.seen_catches: list[str] = []     # 每次进入结算态记录一次分色结果
        self._shot_i = 0

        # 线程协作：GUI/热键线程设标志，工作线程执行
        self._pending_manual = False          # 有开始/恢复请求待处理
        self._exit = False
        self._wake = threading.Event()

    # ================================================================ 人工入口
    # 以下方法线程安全：只设置标志/状态，绝不直接碰设备
    def request_start(self) -> None:
        """请求开始（等待验证后转 RUNNING）。"""
        self._pending_manual = True
        self._wake.set()

    request_resume = request_start           # 语义相同：人工恢复

    def request_pause(self) -> None:
        if self.status is BotStatus.RUNNING:
            self.status = BotStatus.PAUSED_USER
            self.stop_reason = "手动暂停"
            self._log("pause:user")
            self.notifier.notify("info", "已暂停（点「继续」或按 F10 恢复）")

    def request_stop(self, reason: str = "手动停止") -> None:
        if self.status is not BotStatus.STOPPED:
            self._hard_stop(reason)
        self._wake.set()

    def exit(self) -> None:
        """完全退出工作线程。"""
        self._exit = True
        if self.status is not BotStatus.STOPPED:
            self.status = BotStatus.STOPPED
            self.stop_reason = "退出"
        self._wake.set()

    def poll_hotkeys(self) -> list[str]:
        """热键兜底（GUI 定时器或控制台循环调用）。F9 暂停/恢复，F10 恢复。"""
        fired = self.hotkeys.poll()
        for key in fired:
            if key == (self.cfg.get("hotkey_pause") or "F9").upper():
                if self.status is BotStatus.RUNNING:
                    self.request_pause()
                else:
                    self.request_resume()
            else:                              # F10 等恢复键
                self.request_resume()
        return fired

    # ================================================================ 内部工具
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

    def _observe(self) -> Detection:
        return self.det.analyze(self._shoot())

    def _tap(self, x: float, y: float, tag: str) -> None:
        if self.dry_run:
            self._log(f"tap(dry-run)@{tag}", f"({x:.3f},{y:.3f})")
            return
        self.bridge.tap(x, y, tag=tag)
        self.last_tap = time.monotonic()

    def _cooldown_ok(self) -> bool:
        return (time.monotonic() - self.last_tap) >= self.cooldown

    def _hard_stop(self, reason: str, detail: str = "", events: list[str] | None = None) -> None:
        """任何意外 → 硬停止。绝不自动恢复。"""
        if self.status is BotStatus.STOPPED:
            return
        self.status = BotStatus.STOPPED
        self.stop_reason = reason + (f"：{detail}" if detail else "")
        self.stats[_REASON_STATS.get(reason, "error_stops")] += 1
        if events is not None:
            events.append(f"stop:{reason}")
        self._log("stop", self.stop_reason)
        kind = "rare" if reason == "稀有鱼" else reason
        self.notifier.notify(kind, f"已停止——{self.stop_reason}。"
                              "处理完后点「继续」或按 F10 恢复（不会自动恢复）")

    # ================================================================ 恢复执行
    def _drain_pending(self) -> None:
        """执行挂起的人工开始/恢复请求（工作线程内，独占设备）。"""
        self._pending_manual = False
        if self._exit or self.status is BotStatus.RUNNING:
            return
        try:
            det = self._observe()
        except DeviceError as e:
            self._hard_stop("设备错误", str(e))
            return
        except Exception as e:                                  # noqa: BLE001
            self._hard_stop("程序异常", repr(e))
            return
        if det.in_fishing:
            prev = self.status
            self.status = BotStatus.RUNNING
            self.stop_reason = None
            self.miss_count = self.unknown_count = self.device_errors = 0
            self.ready_count = 0
            self._catch_tapped = False
            self._catch_frames_after_tap = 0
            self._wake.clear()
            self._log("resume", f"from {prev.value}（人工恢复，锚点验证通过）")
            self.notifier.notify("info", "已恢复运行")
        else:
            self.status = BotStatus.STOPPED
            self.stop_reason = "无法开始/恢复：当前不在钓鱼界面（请先进入游戏钓鱼页面）"
            self._log("resume_failed", self.stop_reason)
            self.notifier.notify("missing", self.stop_reason)

    # ================================================================ 单步
    def step(self) -> StepResult:
        res = StepResult()
        if self._pending_manual:
            self._drain_pending()
        if self.status is not BotStatus.RUNNING or self._exit:
            res.events.append("idle")
            return res
        try:
            self._step_inner(res)
        except DeviceError as e:
            self.device_errors += 1
            self._log("device_error", f"{self.device_errors}/{self.max_device_errors} {e}")
            if self.device_errors >= self.max_device_errors:
                self._hard_stop("设备错误", str(e), res.events)
        except Exception as e:                                  # noqa: BLE001
            self.audit.event("traceback", traceback.format_exc())
            self._hard_stop("程序异常", repr(e), res.events)
        return res

    def _step_inner(self, res: StepResult) -> None:
        det = self._observe()
        res.detection = det
        self.audit.event("detect",
                         f"qiufu={det.qiufu_score} state={det.state} "
                         f"rarity={det.rarity} btn={det.button} meanV={det.mean_v}")
        state_now = det.state if det.in_fishing else "UNKNOWN"
        if state_now == "CATCH" and self._last_state != "CATCH":
            self.seen_catches.append(det.rarity or "未知")
            self._catch_tapped = False
            self._catch_frames_after_tap = 0
        if state_now != "CATCH":
            self._catch_tapped = False
        self._last_state = state_now

        # ---- 主锚点缺失：界面跳转/弹窗 → 立即硬停止 ----
        if not det.in_fishing:
            self.miss_count += 1
            if self.miss_count >= self.th["anchor_miss_frames"]:
                self._hard_stop("意外界面",
                                f"连续{self.miss_count}帧未见钓鱼界面锚点（界面跳转/弹窗？）",
                                res.events)
            return
        self.miss_count = 0
        self.device_errors = 0

        if det.state == "READY":
            self.ready_count += 1
            if self.ready_count >= self.ready_stable_frames and self._cooldown_ok():
                x, y = self.human.point_in(self.zones["safe_cast"])
                res.events.append(f"cast({x:.3f},{y:.3f})")
                time.sleep(self.human.interval("cast"))
                if self._verify_before_tap("READY") is not None:
                    self._tap(x, y, "cast")
                    self.stats["casts"] += 1
                    self._log("cast", f"({x:.3f},{y:.3f})")
                time.sleep(self.human.interval("after_action"))
        elif det.state == "CATCH":
            self.ready_count = 0
            rarity = det.rarity or "未知"
            if rarity in self.rare:
                self._hard_stop("稀有鱼", f"钓到 {rarity} 色鱼获，等待人工处理", res.events)
                return
            if rarity == "未知" or det.button not in ("出售", "提交订单"):
                self.unknown_count += 1
                if self.unknown_count >= self.th["unknown_frames"]:
                    self._hard_stop("识别异常",
                                    f"连续{self.unknown_count}次无法识别鱼色/按钮"
                                    f"（rarity={rarity} btn={det.button}）", res.events)
                return
            self.unknown_count = 0
            if self._catch_tapped:
                # 同一结算已点过一次：等待界面离开结算，迟迟不离开 → 停止（不二次点击）
                self._catch_frames_after_tap += 1
                if self._catch_frames_after_tap > 10:
                    self._hard_stop("意外界面", "结算界面未消失（点击可能未生效）", res.events)
                return
            if self._cooldown_ok():
                kind = det.button
                x, y = self.human.point_in(self.zones["btn_left"])
                res.events.append(f"{kind}({x:.3f},{y:.3f})")
                time.sleep(self.human.interval("catch_click"))
                if self._verify_before_tap("CATCH", want_btn=kind) is not None:
                    self._tap(x, y, "sell" if kind == "出售" else "submit")
                    self._catch_tapped = True
                    self.stats["sold" if kind == "出售" else "submitted"] += 1
                    self._log(kind, f"{rarity}色 ({x:.3f},{y:.3f})")
                time.sleep(self.human.interval("after_action"))
        else:                                                    # WAITING：不操作
            self.ready_count = 0

    # ================================================================ 点击前复核
    def _verify_before_tap(self, want_state: str, want_btn: str | None = None) -> bool | None:
        """点击（含随机延迟）之后、真正 tap 之前二次复核。

        返回 True=可以点击；False=期间用户暂停/停止（静默放弃，不点击）；
        None=界面与决策时不一致 → 放弃点击并硬停止（防误点购买等意外界面的关键护栏）。
        """
        if self.status is not BotStatus.RUNNING:
            return False
        if not self.verify_before_tap:
            return True
        det = self._observe()
        ok = det.in_fishing and det.state == want_state
        if ok and want_state == "CATCH":
            ok = det.button == want_btn
        if self.status is not BotStatus.RUNNING:                # 复核期间用户操作
            return False
        if not ok:
            self._hard_stop("意外界面",
                            f"点击前界面已变化（期望{want_state}，实际"
                            f"{'无锚点' if not det.in_fishing else det.state}），已放弃点击")
            return None
        return True

    # ================================================================ 主循环
    def run(self, duration: float | None = None, max_steps: int | None = None) -> None:
        """工作线程主循环：未运行时阻塞等待人工操作，绝不自动恢复。"""
        t0 = time.monotonic()
        steps = 0
        print(f"▶ 就绪（{self.bridge.name} 桥，dry_run={self.dry_run}）。"
              f"任何意外都会立即停止，需人工点「继续」或按 F10 恢复", flush=True)
        try:
            while not self._exit:
                if self.status is BotStatus.RUNNING:
                    self.step()
                    steps += 1
                    if duration is not None and time.monotonic() - t0 >= duration:
                        self._hard_stop("手动停止", "运行时长已到")
                        break
                    if max_steps is not None and steps >= max_steps:
                        self._hard_stop("手动停止", "步数已到")
                        break
                    time.sleep(self.human.interval("poll"))
                else:
                    self._wake.wait(0.2)                        # 等待人工操作（GUI/热键）
                    self._wake.clear()
                    if self._pending_manual:
                        self.step()                             # 借 step 排空恢复请求
        except KeyboardInterrupt:                               # 仅控制台模式
            self._hard_stop("手动停止", "Ctrl+C")
        print(f"■ 循环结束：status={self.status.value} reason={self.stop_reason} {self.stats}",
              flush=True)
