#!/usr/bin/env python
"""设备桥安全约束测试（不连接真实设备，全部 mock subprocess）。

覆盖：序列号校验（不匹配→拒绝）、命令白名单（结构外命令一律拒绝并审计）、
点击像素换算。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin.audit import Audit                      # noqa: E402
from fishing_plugin.devices.adb import AdbBridge            # noqa: E402
from fishing_plugin.devices.base import DeviceError         # noqa: E402
from fishing_plugin.devices.hdc import HdcBridge            # noqa: E402


def fake_run(stdout: bytes = b"", rc: int = 0):
    def _run(argv, capture_output=True, timeout=None):
        return CompletedProcess(argv, rc, stdout, b"")
    return _run


def make_adb(devices: str, serial: str = "ABC123") -> AdbBridge:
    out = ("List of devices attached\n" + devices).encode()
    with mock.patch("fishing_plugin.devices.adb.subprocess.run", side_effect=fake_run(out)):
        return AdbBridge(serial=serial)


def make_hdc(targets: str, serial: str = "KEY1") -> HdcBridge:
    with mock.patch("fishing_plugin.devices.hdc.subprocess.run", side_effect=fake_run(targets.encode())):
        return HdcBridge(serial=serial)


class TestAdbSecurity(unittest.TestCase):
    def test_serial_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            with mock.patch("fishing_plugin.devices.adb.subprocess.run",
                            side_effect=fake_run(b"List of devices attached\nOTHER\tdevice\n")):
                with self.assertRaises(DeviceError):
                    AdbBridge(serial="ABC123", audit=audit)
            refused = (Path(td) / "logs" / "audit.log").read_text(encoding="utf-8")
            self.assertIn("REFUSED", refused)

    def test_no_device(self):
        with mock.patch("fishing_plugin.devices.adb.subprocess.run",
                        side_effect=fake_run(b"List of devices attached\n")):
            with self.assertRaises(DeviceError):
                AdbBridge()

    def test_whitelist_rejects_dangerous(self):
        b = make_adb("ABC123\tdevice\n")
        for bad in (["adb", "-s", b.serial, "shell", "rm", "-rf", "/"],
                    ["adb", "-s", b.serial, "shell", "input", "text", "x"],
                    ["adb", "-s", b.serial, "shell", "dumpsys", "sms"],
                    ["adb", "install", "evil.apk"],
                    ["adb", "-s", b.serial, "tcpip", "5555"],
                    ["adb", "-s", b.serial, "shell", "input", "tap", "a", "b"],
                    ["adb", "-s", "OTHER", "shell", "wm", "size"]):
            with self.assertRaises(DeviceError, msg=str(bad)):
                b._checked(bad)

    def test_whitelist_allows_expected(self):
        b = make_adb("ABC123\tdevice\n")
        for ok in (["adb", "devices"],
                   ["adb", "-s", b.serial, "exec-out", "screencap", "-p"],
                   ["adb", "-s", b.serial, "shell", "input", "tap", "100", "200"],
                   ["adb", "-s", b.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                   ["adb", "-s", b.serial, "shell", "wm", "size"],
                   ["adb", "kill-server"]):
            b._checked(ok)     # 不抛即通过

    def test_tap_pixel_math(self):
        b = make_adb("ABC123\tdevice\n")
        b._size = (1080, 2340)
        with mock.patch.object(b, "_run") as run:
            b.tap(0.5, 0.5)
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], ["539", "1169"])


class TestHdcSecurity(unittest.TestCase):
    def test_serial_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            with mock.patch("fishing_plugin.devices.hdc.subprocess.run",
                            side_effect=fake_run(b"OTHER\n")):
                with self.assertRaises(DeviceError):
                    HdcBridge(serial="KEY1", audit=audit)
            refused = (Path(td) / "logs" / "audit.log").read_text(encoding="utf-8")
            self.assertIn("REFUSED", refused)

    def test_whitelist_rejects_dangerous(self):
        b = make_hdc("KEY1\n")
        for bad in (["hdc", "-t", b.serial, "shell", "rm", "-rf", "/"],
                    ["hdc", "-t", b.serial, "install", "x.hap"],
                    ["hdc", "file", "send", "evil", "/system/x"],
                    ["hdc", "-t", b.serial, "shell", "uitest", "uiInput", "click", "x", "1"],
                    ["hdc", "-t", "OTHER", "shell", "power-shell", "wakeup"]):
            with self.assertRaises(DeviceError, msg=str(bad)):
                b._checked(bad)

    def test_whitelist_allows_expected(self):
        b = make_hdc("KEY1\n")
        T = "/data/local/tmp/__fishing_plugin__.jpeg"
        for ok in (["hdc", "list", "targets"],
                   ["hdc", "-t", b.serial, "shell", "snapshot_display", "-f", T],
                   ["hdc", "-t", b.serial, "file", "recv", T, "C:/tmp/x.jpg"],
                   ["hdc", "-t", b.serial, "shell", "rm", T],
                   ["hdc", "-t", b.serial, "shell", "uitest", "uiInput", "click", "540", "1170"],
                   ["hdc", "-t", b.serial, "shell", "power-shell", "wakeup"],
                   ["hdc", "kill"]):
            b._checked(ok)

    def test_tap_pixel_math(self):
        b = make_hdc("KEY1\n")
        b._size = (1260, 2720)
        with mock.patch.object(b, "_run") as run:
            b.tap(0.25, 0.75)
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], ["314", "2039"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
