#!/usr/bin/env python
"""发布打包冒烟测试（B1）：zip 导出 + 双层哈希可校验。"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class TestRelease(unittest.TestCase):
    def test_make_release_produces_verifiable_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_release.py"),
                                "--out", td], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            zips = list(Path(td).glob("fishing-plugin-v*-source.zip"))
            self.assertEqual(len(zips), 1, "应产出一个源码 zip")
            z = zips[0]
            sums = Path(td) / "SHA256SUMS.txt"
            self.assertTrue(sums.exists(), "应产出 SHA256SUMS.txt")

            # zip 哈希与 SHA256SUMS 一致
            expected = f"{sha256_file(z)}  {z.name}"
            self.assertIn(expected, sums.read_text(encoding="utf-8").strip())

            # 包内 MANIFEST 覆盖所有文件且哈希正确
            with zipfile.ZipFile(z) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                self.assertIn("MANIFEST.sha256", names)
                manifest = zf.read("MANIFEST.sha256").decode("utf-8").splitlines()
                listed = {ln.split("  ", 1)[1] for ln in manifest}
                self.assertEqual(listed, {n for n in names if n != "MANIFEST.sha256"})
                for ln in manifest:
                    digest, fname = ln.split("  ", 1)
                    self.assertEqual(digest,
                                     hashlib.sha256(zf.read(fname)).hexdigest(),
                                     f"{fname} 哈希不符")
                # 关键内容确实在包里
                for must in ("fishing_plugin/bot.py", "fishing_plugin/devices/adb.py",
                             "templates/qiufu_real.png", "README.md"):
                    self.assertIn(must, names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
