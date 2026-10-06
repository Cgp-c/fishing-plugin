"""本地通知：控制台输出 + 蜂鸣（标准库 winsound，Windows）。零联网。"""
from __future__ import annotations

import sys
import time

try:
    import winsound
except ImportError:                       # 非 Windows 退化为控制台响铃
    winsound = None


class Notifier:
    """notify() 同时打印消息并蜂鸣；测试通过 hooks 注入回调而不发声。"""

    def __init__(self, enabled: bool = True, beep_times: int = 3, hooks=None):
        self.enabled = enabled
        self.beep_times = beep_times
        self.hooks = hooks or []          # list[callable[[str, str], None]]
        self.messages: list[tuple[str, str]] = []   # 供测试断言

    def notify(self, kind: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] [{kind}] {message}", flush=True)
        self.messages.append((kind, message))
        for cb in self.hooks:
            cb(kind, message)
        if not self.enabled:
            return
        times = self.beep_times if kind != "rare" else max(self.beep_times, 5)
        self._beep(times)

    def _beep(self, times: int) -> None:
        for _ in range(times):
            if winsound is not None:
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
            time.sleep(0.25)
