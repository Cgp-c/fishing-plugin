"""鸿蒙 hdc 桥（USB 有线）。

⚠ 状态：命令按 HarmonyOS Device Connector 公开用法实现，尚未实机联调
（用户鸿蒙手机到位后用 --device hdc 验证，届时按实测微调命令与阈值）。

安全约束与 adb 桥一致：白名单 + 序列号校验 + 审计。
⚠ 已知偏差（如实说明）：hdc 的 screencap（snapshot_display）只能落文件再回传，
插件把截图写到 /data/local/tmp/__fishing_plugin__.jpeg 与本机临时目录，
读入内存后立即删除两端临时文件；除此之外截图同样不落盘。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .base import DeviceBridge, DeviceError, resolve_tool, validate_serial

_TIMEOUT = 25
_REMOTE_TMP = "/data/local/tmp/__fishing_plugin__.jpeg"


class HdcBridge(DeviceBridge):
    name = "hdc"

    def __init__(self, serial: str = "", audit=None, binary: str = ""):
        super().__init__(audit)
        self.serial = validate_serial(serial)      # hdc 称 connect key / target
        self.binary, self.binary_source = resolve_tool("hdc", binary)
        self._check_targets()

    # ------------------------------------------------------------------ 校验
    def list_targets(self) -> list[str]:
        out = self._run([self.binary, "list", "targets"], checked=False)
        return [ln.strip() for ln in out.splitlines()
                if ln.strip() and "empty" not in ln.lower()]

    def _check_targets(self) -> None:
        devs = self.list_targets()
        if not devs:
            raise DeviceError("无 hdc 设备在线（检查 USB 与开发者模式）")
        if self.serial and self.serial not in devs:
            self._audit_cmd([self.binary, "-t", self.serial, "..."], allowed=False)
            raise DeviceError(
                f"设备不匹配：配置为 {self.serial}，在线设备 {devs}（拒绝向未登记设备发命令）")
        if not self.serial:
            self.serial = devs[0]

    # ------------------------------------------------------------------ 白名单
    def _checked(self, argv: list[str]) -> None:
        a = argv[:]
        ok = False
        if a[0] == self.binary:
            a = a[1:]
        if a and a[0] == "-t" and len(a) > 1:
            if a[1] != self.serial:
                raise DeviceError(f"目标不匹配: {a[1]} != {self.serial}")
            a = a[2:]
        if a[:2] == ["list", "targets"]:
            ok = True
        elif a[:2] == ["shell", "snapshot_display"]:
            ok = True
        elif a[:2] == ["file", "recv"]:
            ok = len(a) >= 4 and a[2] == _REMOTE_TMP
        elif a[:2] == ["shell", "rm"] and len(a) == 3:
            ok = a[2] == _REMOTE_TMP
        elif a[:4] == ["shell", "uitest", "uiInput", "click"] and len(a) == 6:
            ok = all(re.fullmatch(r"\d+", s) for s in a[4:6])
        elif a[:2] == ["shell", "power-shell"] and len(a) == 3 and a[2] == "wakeup":
            ok = True
        elif a == ["kill"]:
            ok = True
        if not ok:
            self._audit_cmd(argv, allowed=False)
            raise DeviceError(f"命令不在白名单内，已拒绝: {argv}")
        self._audit_cmd(argv)

    def _run(self, argv: list[str], checked: bool = True, binary: bool = False):
        if checked:
            self._checked(argv)
        else:
            self._audit_cmd(argv)
        proc = subprocess.run(argv, capture_output=True, timeout=_TIMEOUT)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise DeviceError(f"命令失败({proc.returncode}): {argv}\n{err}")
        return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")

    def _dev(self) -> list[str]:
        return [self.binary, "-t", self.serial]

    # ------------------------------------------------------------------ 接口
    def screenshot(self) -> np.ndarray:
        self._run(self._dev() + ["shell", "snapshot_display", "-f", _REMOTE_TMP])
        with tempfile.TemporaryDirectory(prefix="fishing_plugin_") as td:
            local = Path(td) / "shot.jpeg"
            self._run(self._dev() + ["file", "recv", _REMOTE_TMP, str(local)])
            bgr = cv2.imdecode(np.fromfile(local, dtype=np.uint8), cv2.IMREAD_COLOR)
        self._run(self._dev() + ["shell", "rm", _REMOTE_TMP])   # 无论成败都尝试清理
        if bgr is None:
            raise DeviceError("snapshot_display 截图解码失败")
        self._size = (bgr.shape[1], bgr.shape[0])
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def tap(self, x_pct: float, y_pct: float, tag: str = "") -> None:
        w, h = self.screen_size()
        x, y = int(x_pct * (w - 1)), int(y_pct * (h - 1))
        self._run(self._dev() + ["shell", "uitest", "uiInput", "click", str(x), str(y)])

    def wake(self) -> None:
        self._run(self._dev() + ["shell", "power-shell", "wakeup"])

    def close(self, kill_server: bool = False) -> None:
        if kill_server:
            self._run([self.binary, "kill"])
