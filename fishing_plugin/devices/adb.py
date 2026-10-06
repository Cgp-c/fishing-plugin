"""安卓 adb 桥（USB 有线）。

安全约束：
- 命令白名单：devices / exec-out screencap / shell input tap / shell input keyevent
  (仅 KEYCODE_WAKEUP) / shell wm size / kill-server —— 其余一律拒绝并写审计
- 序列号校验：配置了 serial 时，仅当目标设备在线列表中才允许发命令
- 不开启 adb TCP 网络模式；screencap 用 exec-out 管道读取，截图不落盘
"""
from __future__ import annotations

import re
import subprocess

import cv2
import numpy as np

from .base import DeviceBridge, DeviceError, resolve_tool, validate_serial

_TIMEOUT = 20


class AdbBridge(DeviceBridge):
    name = "adb"

    def __init__(self, serial: str = "", audit=None, binary: str = ""):
        super().__init__(audit)
        self.serial = validate_serial(serial)
        self.binary, self.binary_source = resolve_tool("adb", binary)
        self._check_serial()

    # ------------------------------------------------------------------ 校验
    def list_devices(self) -> list[str]:
        out = self._run([self.binary, "devices"], binary=False)
        devs = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devs.append(parts[0])
        return devs

    def _check_serial(self) -> None:
        devs = self.list_devices()
        if not devs:
            raise DeviceError("无 adb 设备在线（检查 USB 连接与手机上的 USB 调试）")
        if self.serial and self.serial not in devs:
            self._audit_cmd([self.binary, "-s", self.serial, "..."], allowed=False)
            raise DeviceError(
                f"设备序列号不匹配：配置为 {self.serial}，在线设备 {devs}（拒绝向未登记设备发命令）")
        if not self.serial:
            self.serial = devs[0]

    # ------------------------------------------------------------------ 白名单
    def _checked(self, argv: list[str]) -> list[str]:
        """结构化白名单校验：本桥只会构造以下形状的命令。"""
        ok = False
        a = argv[:]
        if a[0] == self.binary:
            a = a[1:]
        if a and a[0] == "-s" and len(a) > 1:
            if a[1] != self.serial:
                raise DeviceError(f"目标序列号异常: {a[1]} != {self.serial}")
            a = a[2:]
        if a == ["devices"]:
            ok = True
        elif a[:3] == ["exec-out", "screencap", "-p"]:
            ok = True
        elif a[:3] == ["shell", "input", "tap"] and len(a) == 5:
            ok = all(re.fullmatch(r"\d+", s) for s in a[3:5])
        elif a[:3] == ["shell", "input", "keyevent"] and len(a) == 4 \
                and a[3] in ("KEYCODE_WAKEUP", "224"):
            ok = True
        elif a[:3] == ["shell", "wm", "size"]:
            ok = True
        elif a == ["kill-server"]:
            ok = True
        if not ok:
            self._audit_cmd(argv, allowed=False)
            raise DeviceError(f"命令不在白名单内，已拒绝: {argv}")
        self._audit_cmd(argv)
        return argv

    def _run(self, argv: list[str], binary: bool = False):
        argv = self._checked(argv)
        proc = subprocess.run(argv, capture_output=True, timeout=_TIMEOUT)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise DeviceError(f"命令失败({proc.returncode}): {argv}\n{err}")
        return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")

    def _dev(self) -> list[str]:
        return [self.binary, "-s", self.serial]

    # ------------------------------------------------------------------ 接口
    def screenshot(self) -> np.ndarray:
        data = self._run(self._dev() + ["exec-out", "screencap", "-p"], binary=True)
        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise DeviceError("screencap 解码失败")
        self._size = (bgr.shape[1], bgr.shape[0])
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def tap(self, x_pct: float, y_pct: float, tag: str = "") -> None:
        w, h = self.screen_size()
        x, y = int(x_pct * (w - 1)), int(y_pct * (h - 1))
        self._run(self._dev() + ["shell", "input", "tap", str(x), str(y)])

    def wake(self) -> None:
        self._run(self._dev() + ["shell", "input", "keyevent", "KEYCODE_WAKEUP"])

    def wm_size(self) -> tuple[int, int]:
        out = self._run(self._dev() + ["shell", "wm", "size"])
        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            raise DeviceError(f"wm size 解析失败: {out!r}")
        return int(m.group(1)), int(m.group(2))

    def close(self, kill_server: bool = False) -> None:
        if kill_server:
            self._run([self.binary, "kill-server"])
