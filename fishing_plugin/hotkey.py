"""全局热键（如 F9 急停）：轮询 GetAsyncKeyState，无需管理员权限、无钩子注入。

仅 Windows 支持实时轮询；其他平台 check() 恒返回空（仍可 Ctrl+C 退出）。
"""
from __future__ import annotations

import sys
from typing import Sequence

if sys.platform == "win32":
    import ctypes

_VK_CODES = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "SPACE": 0x20, "PAUSE": 0x13,
}


class HotkeyPoller:
    def __init__(self, keys: Sequence[str] | None = None):
        keys = keys or ("F9",)
        self.vks: list[tuple[str, int]] = []
        for k in keys:
            vk = _VK_CODES.get(k.upper())
            if vk is None:
                raise ValueError(f"不支持的热键: {k}（可选: {sorted(_VK_CODES)}）")
            self.vks.append((k.upper(), vk))
        self._prev: dict[str, bool] = {name: False for name, _ in self.vks}

    def poll(self) -> list[str]:
        """返回本次刚被按下的键（上升沿），用于切换暂停/恢复。"""
        if sys.platform != "win32":
            return []
        pressed_now: dict[str, bool] = {}
        for name, vk in self.vks:
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            pressed_now[name] = bool(state & 0x8000)
        fired = [k for k, v in pressed_now.items() if v and not self._prev[k]]
        self._prev = pressed_now
        return fired
