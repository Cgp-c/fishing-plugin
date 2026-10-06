"""PC 窗口桥：按标题找窗口、屏幕截屏、真实鼠标点击（SendInput 级）。

用途：开发与回归测试——对"简单钓鱼游戏验证"窗口跑完整插件链路。
与手机桥同样只做「截屏 + 点击」两件事。
"""
from __future__ import annotations

import sys
import time
from typing import Optional

import numpy as np
from PIL import ImageGrab

from .base import DeviceBridge, DeviceError

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    def _dpi_aware() -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    _dpi_aware()


class PCWindowBridge(DeviceBridge):
    name = "pc"

    def __init__(self, window_title: str, audit=None):
        super().__init__(audit)
        self.title = window_title
        if sys.platform != "win32":
            raise DeviceError("PCWindowBridge 仅支持 Windows")
        self.user32 = ctypes.windll.user32
        self.hwnd = self.user32.FindWindowW(None, window_title)
        if not self.hwnd:
            raise DeviceError(f"找不到窗口: {window_title}（游戏开了吗？）")

    # ------------------------------------------------------------------ 内部
    def _client_rect(self) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        pt = wintypes.POINT(0, 0)
        if not self.user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise DeviceError("GetClientRect 失败")
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(pt)):
            raise DeviceError("ClientToScreen 失败")
        return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top

    def _pct_to_px(self, x_pct: float, y_pct: float) -> tuple[int, int]:
        ox, oy, w, h = self._client_rect()
        self._size = (w, h)
        return ox + int(x_pct * (w - 1)), oy + int(y_pct * (h - 1))

    # ------------------------------------------------------------------ 接口
    def bring_to_front(self, topmost: bool = True) -> None:
        self.user32.ShowWindow(self.hwnd, 9)          # SW_RESTORE
        self.user32.SetForegroundWindow(self.hwnd)
        if topmost:
            HWND_TOPMOST = ctypes.c_void_p(-1)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            self.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                     SWP_NOSIZE | SWP_NOMOVE)
        time.sleep(0.08)

    def screenshot(self) -> np.ndarray:
        ox, oy, w, h = self._client_rect()
        if w <= 0 or h <= 0:
            raise DeviceError("窗口不可见（最小化？）")
        img = ImageGrab.grab(bbox=(ox, oy, ox + w, oy + h))
        self._size = (w, h)
        return np.asarray(img.convert("RGB"), dtype=np.uint8)

    def tap(self, x_pct: float, y_pct: float, tag: str = "") -> None:
        sx, sy = self._pct_to_px(x_pct, y_pct)
        self._audit_cmd([f"click({x_pct:.3f},{y_pct:.3f}){('@' + tag) if tag else ''}"])
        self.bring_to_front()
        self.user32.SetCursorPos(sx, sy)
        time.sleep(0.03)
        MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
        self.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.04)
        self.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.05)
