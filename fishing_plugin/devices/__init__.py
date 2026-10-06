"""设备桥工厂。"""
from __future__ import annotations

from ..audit import Audit
from .adb import AdbBridge
from .base import DeviceBridge, DeviceError
from .hdc import HdcBridge
from .pc_window import PCWindowBridge


def _tool_path(cfg: dict, name: str) -> str:
    return (cfg.get("tools") or {}).get(f"{name}_path", "") or ""


def create_bridge(cfg: dict, audit: Audit | None = None, *, device: str | None = None,
                  serial: str | None = None, title: str | None = None) -> DeviceBridge:
    dev = (device or cfg["device"]["type"]).lower()
    ser = serial if serial is not None else cfg["device"].get("serial", "")
    if dev == "pc":
        return PCWindowBridge(title or cfg["window_title"], audit=audit)
    if dev == "adb":
        return AdbBridge(serial=ser, audit=audit, binary=_tool_path(cfg, "adb"))
    if dev == "hdc":
        return HdcBridge(serial=ser, audit=audit, binary=_tool_path(cfg, "hdc"))
    raise DeviceError(f"未知设备类型: {dev}（可选 pc / adb / hdc）")
