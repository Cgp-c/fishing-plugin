"""设备桥抽象：检测核心与具体控制通道解耦。

实现：
- pc_window.PCWindowBridge ：电脑窗口截屏 + 真实鼠标点击（开发/回归测试用）
- adb.AdbBridge            ：安卓（USB）
- hdc.HdcBridge            ：鸿蒙（USB）

约定：
- frame 统一为 RGB uint8 ndarray，坐标统一用全屏百分比，由桥换算像素
- tap() 可带 tag 便于审计区分（cast/sell/manual……）
"""
from __future__ import annotations

import abc
import re
import shutil
from pathlib import Path
from typing import Optional

import numpy as np


class DeviceError(RuntimeError):
    """设备不可用 / 序列号不匹配 / 命令被白名单拒绝等。"""


# 序列号/connect key 合法字符（防参数注入：序列号会进入命令行参数）
_SERIAL_RE = re.compile(r"^[A-Za-z0-9:._\-]{1,64}$")


def validate_serial(serial: str) -> str:
    s = (serial or "").strip()
    if s and not _SERIAL_RE.match(s):
        raise DeviceError(f"设备序列号含非法字符: {serial!r}（只允许字母数字与 : . _ -，最长 64）")
    return s


def resolve_tool(tool_name: str, configured: str = "") -> tuple[str, str]:
    """解析 adb/hdc 可执行文件，防 PATH 劫持。

    返回 (绝对路径, 来源)。configured 非空时必须是**绝对路径**且存在；
    为空时用 shutil.which 解析成绝对路径（找不到则报错，绝不静默用裸名称）。
    启动时应把返回的绝对路径展示给用户核对。
    """
    configured = (configured or "").strip()
    if configured:
        p = Path(configured)
        if not p.is_absolute():
            raise DeviceError(f"{tool_name} 路径必须是绝对路径（防 PATH 劫持）: {configured}")
        if not p.is_file():
            raise DeviceError(f"{tool_name} 不存在: {configured}")
        return str(p), "配置"
    found = shutil.which(tool_name)
    if not found:
        raise DeviceError(f"在 PATH 中找不到 {tool_name}（可在 config 的 tools.{tool_name}_path "
                          "指定绝对路径）")
    return str(Path(found).resolve()), "PATH"


class DeviceBridge(abc.ABC):
    name = "abstract"

    def __init__(self, audit=None):
        self.audit = audit
        self._size: Optional[tuple[int, int]] = None   # (w, h)，来自最近一次截屏

    # ---- 子系统接口 -------------------------------------------------------
    @abc.abstractmethod
    def screenshot(self) -> np.ndarray:
        """抓一帧，RGB uint8 (H, W, 3)。"""

    @abc.abstractmethod
    def tap(self, x_pct: float, y_pct: float, tag: str = "") -> None:
        """在 (x_pct, y_pct) 百分比坐标处点击一次。"""

    def screen_size(self) -> tuple[int, int]:
        if self._size is None:
            frame = self.screenshot()
            self._size = (frame.shape[1], frame.shape[0])
        return self._size

    def wake(self) -> None:      # 亮屏（白名单内命令，默认不在主流程调用）
        pass

    def close(self, kill_server: bool = False) -> None:
        pass

    # ---- 审计辅助 ---------------------------------------------------------
    def _audit_cmd(self, argv: list[str], allowed: bool = True) -> None:
        if self.audit is not None:
            self.audit.cmd(self.name, [str(a) for a in argv], allowed=allowed)
