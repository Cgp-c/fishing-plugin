#!/usr/bin/env python
"""审计模块安全测试（A3 截图过期 / A4 日志脱敏 / C2 链式哈希）。"""
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


class TestChainHash(unittest.TestCase):
    """C2：日志链式哈希——改动任意行可被定位。"""

    def _log(self, td):
        return Path(td) / "logs" / "run.log"

    def test_chain_intact_across_instances(self):
        with tempfile.TemporaryDirectory() as td:
            a1 = Audit(Path(td))
            for i in range(3):
                a1.event("cast", f"({i})")
            ok, bad = Audit.verify_chain(self._log(td))
            self.assertTrue(ok)
            self.assertIsNone(bad)
            a2 = Audit(Path(td))                      # 新实例续链追加
            a2.event("resume", "续链")
            ok, bad = Audit.verify_chain(self._log(td))
            self.assertTrue(ok)

    def test_tampered_line_detected(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            for i in range(5):
                audit.event("cast", f"line{i}")
            log = self._log(td)
            lines = log.read_text(encoding="utf-8").splitlines()
            lines[2] = lines[2].replace("line2", "HACKED")   # 篡改第 3 行
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ok, bad = Audit.verify_chain(log)
            self.assertFalse(ok)
            self.assertEqual(bad, 3, "应定位到第一条被改的行")

    def test_removed_hash_detected(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Audit(Path(td))
            audit.event("cast", "(0)")
            log = self._log(td)
            lines = log.read_text(encoding="utf-8").splitlines()
            lines[0] = lines[0].split(" ##h=")[0]            # 剥离哈希
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ok, bad = Audit.verify_chain(log)
            self.assertFalse(ok)
            self.assertEqual(bad, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
