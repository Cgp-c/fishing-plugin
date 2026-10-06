#!/usr/bin/env python
"""设备桥安全约束测试（不连接真实设备，全部 mock subprocess）。

覆盖：序列号校验（格式/不匹配→拒绝）、命令白名单（结构外命令一律拒绝并审计）、
点击像素换算、adb/hdc 工具路径防 PATH 劫持（A1）。
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

from fishing_plugin.audit import Audit                                # noqa: E402
from fishing_plugin.devices.adb import AdbBridge                      # noqa: E402
from fishing_plugin.devices.base import (DeviceError, resolve_tool,   # noqa: E402
                                         validate_serial)
from fishing_plugin.devices.hdc import HdcBridge                      # noqa: E402

FAKE_TOOL = "C:/PlatformTools/adb.exe"           # which() 的假解析结果（不会真执行）
FAKE_TOOL_HDC = "C:/Sdk/toolchains/hdc.exe"
ADB_OTHER = b"List of devices attached\nOTHER\tdevice\n"


def fake_run(stdout: bytes = b"", rc: int = 0):
    def _run(argv, capture_output=True, timeout=None):
        return CompletedProcess(argv, rc, stdout, b"")
    return _run


def fake_which(name, path=None):
    return FAKE_TOOL if name == "adb" else FAKE_TOOL_HDC


def make_adb(devices: str, serial: str = "ABC123") -> AdbBridge:
    out = ("List of devices attached\n" + devices).encode()
    with mock.patch("fishing_plugin.devices.adb.subprocess.run", side_effect=fake_run(out)), \
         mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
        return AdbBridge(serial=serial)


def make_hdc(targets: str, serial: str = "KEY1") -> HdcBridge:
    with mock.patch("fishing_plugin.devices.hdc.subprocess.run",
                    side_effect=fake_run(targets.encode())), \
         mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
        return HdcBridge(serial=serial)


class TestAdbSecurity(unittest.TestCase):
    def test_serial_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            with mock.patch("fishing_plugin.devices.adb.subprocess.run",
                            side_effect=fake_run(ADB_OTHER)), \
                 mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
                with self.assertRaises(DeviceError):
                    AdbBridge(serial="ABC123", audit=audit)
            refused = (Path(td) / "logs" / "audit.log").read_text(encoding="utf-8")
            self.assertIn("REFUSED", refused)

    def test_no_device(self):
        with mock.patch("fishing_plugin.devices.adb.subprocess.run",
                        side_effect=fake_run(b"List of devices attached\n")), \
             mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
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
        A = b.binary
        for ok in ([A, "devices"],
                   [A, "-s", b.serial, "exec-out", "screencap", "-p"],
                   [A, "-s", b.serial, "shell", "input", "tap", "100", "200"],
                   [A, "-s", b.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                   [A, "-s", b.serial, "shell", "wm", "size"],
                   [A, "kill-server"]):
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
                            side_effect=fake_run(b"OTHER\n")), \
                 mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
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
        T = b.remote_tmp                              # C1：每次运行随机的临时文件名
        H = b.binary
        for ok in ([H, "list", "targets"],
                   [H, "-t", b.serial, "shell", "snapshot_display", "-f", T],
                   [H, "-t", b.serial, "file", "recv", T, "C:/tmp/x.jpg"],
                   [H, "-t", b.serial, "shell", "rm", T],
                   [H, "-t", b.serial, "shell", "uitest", "uiInput", "click", "540", "1170"],
                   [H, "-t", b.serial, "shell", "power-shell", "wakeup"],
                   [H, "kill"]):
            b._checked(ok)

    def test_remote_tmp_random_and_pinned_to_instance(self):
        """C1：远端临时文件随机名；其他路径/其他实例的文件名一律拒绝。"""
        b1 = make_hdc("KEY1\n")
        b2 = make_hdc("KEY1\n")
        self.assertNotEqual(b1.remote_tmp, b2.remote_tmp)
        self.assertIn("__fishing_plugin_", b1.remote_tmp)
        for bad in (["hdc", "-t", b1.serial, "shell", "snapshot_display",
                     "-f", "/data/local/tmp/__fishing_plugin__.jpeg"],
                    ["hdc", "-t", b1.serial, "shell", "snapshot_display",
                     "-f", b2.remote_tmp],
                    ["hdc", "-t", b1.serial, "shell", "rm", "/etc/passwd"],
                    ["hdc", "-t", b1.serial, "file", "recv", b2.remote_tmp, "x"]):
            with self.assertRaises(DeviceError, msg=str(bad)):
                b1._checked(bad)

    def test_tap_pixel_math(self):
        b = make_hdc("KEY1\n")
        b._size = (1260, 2720)
        with mock.patch.object(b, "_run") as run:
            b.tap(0.25, 0.75)
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], ["314", "2039"])


class TestToolPathSecurity(unittest.TestCase):
    """A1：adb/hdc 工具路径防 PATH 劫持。"""

    def test_relative_path_rejected(self):
        with self.assertRaises(DeviceError):
            resolve_tool("adb", "adb.exe")            # 相对路径无法确认来源
        with self.assertRaises(DeviceError):
            resolve_tool("hdc", "tools/hdc")

    def test_nonexistent_path_rejected(self):
        with self.assertRaises(DeviceError):
            resolve_tool("adb", "C:/nonexistent/adb.exe")

    def test_configured_absolute_path_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "adb.exe"
            exe.write_bytes(b"")
            path, src = resolve_tool("adb", str(exe))
            self.assertEqual(path, str(exe))
            self.assertEqual(src, "配置")

    def test_which_resolution_absolute(self):
        with mock.patch("fishing_plugin.devices.base.shutil.which", return_value=FAKE_TOOL):
            path, src = resolve_tool("adb", "")
        self.assertEqual(path, str(Path(FAKE_TOOL).resolve()))
        self.assertEqual(src, "PATH")

    def test_bridge_uses_resolved_binary(self):
        b = make_adb("ABC123\tdevice\n")
        self.assertEqual(b.binary, str(Path(FAKE_TOOL).resolve()))
        self.assertEqual(b.binary_source, "PATH")

    def test_missing_tool_reports_clearly(self):
        with mock.patch("fishing_plugin.devices.base.shutil.which", return_value=None):
            with self.assertRaises(DeviceError):
                resolve_tool("adb", "")


class TestSerialValidation(unittest.TestCase):
    """A2：序列号格式校验（防参数注入面）。"""

    def test_bad_serials_rejected(self):
        for bad in ("shell rm -rf /", "a b", "a;b", "a|b", "x" * 65, "dev\nattack"):
            with self.assertRaises(DeviceError, msg=repr(bad)):
                validate_serial(bad)

    def test_good_serials_accepted(self):
        for ok in ("ABC123", "emulator-5554", "a1:b2:c3", "MY.device-01", ""):
            self.assertEqual(validate_serial(ok), ok.strip())

    def test_bridge_rejects_bad_serial(self):
        with mock.patch("fishing_plugin.devices.base.shutil.which", side_effect=fake_which):
            with self.assertRaises(DeviceError):
                AdbBridge(serial="shell rm -rf /")


if __name__ == "__main__":
    unittest.main(verbosity=2)
