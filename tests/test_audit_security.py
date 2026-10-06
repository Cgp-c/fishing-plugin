#!/usr/bin/env python
"""审计模块隐私加固测试（A3 截图过期 / A4 日志脱敏）。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin.audit import DEBUG_TTL_S, Audit   # noqa: E402


class TestDebugExpiry(unittest.TestCase):
    def test_old_screenshots_purged_fresh_kept(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            debug = Path(td) / "debug"
            debug.mkdir()
            old = debug / "old.png"
            fresh = debug / "fresh.png"
            old.write_bytes(b"x")
            fresh.write_bytes(b"x")
            backdate = time.time() - DEBUG_TTL_S - 3600
            os.utime(old, (backdate, backdate))
            p_new = audit.debug_path("new")               # 触发清扫（只返回路径，不落盘）
            self.assertFalse(old.exists(), "超过 48h 的截图应被删除")
            self.assertTrue(fresh.exists(), "新截图应保留")
            self.assertTrue(p_new.name.endswith("_new.png"))

    def test_sweep_once_per_instance(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            p1 = audit.debug_path("a")
            backdated = p1.parent / "back.png"
            backdated.write_bytes(b"x")
            back = time.time() - DEBUG_TTL_S - 10
            os.utime(backdated, (back, back))
            audit.debug_path("b")                       # 第二次不再清扫
            self.assertTrue(backdated.exists())


class TestLogSanitization(unittest.TestCase):
    def test_home_path_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            home = str(Path.home())
            audit.event("traceback", f'File "{home}\\AppData\\x.py", line 1')
            content = (Path(td) / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertNotIn(home, content, "日志不得包含用户主目录")
            self.assertIn("~", content)

    def test_normal_text_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            audit.event("cast", "(0.500,0.850)")
            content = (Path(td) / "logs" / "run.log").read_text(encoding="utf-8")
            self.assertIn("(0.500,0.850)", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
