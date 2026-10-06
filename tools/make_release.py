#!/usr/bin/env python
"""发布打包：导出干净源码 zip + 双层哈希校验（B1）。

    python tools/make_release.py [--out dist]

产出（dist/ 下）：
  fishing-plugin-vX.Y.Z-source.zip   git archive 导出的干净源码
                                      （内嵌 MANIFEST.sha256：包内每个文件的哈希）
  SHA256SUMS.txt                     zip 本体的 SHA256

使用者校验方法（拿到发布物后）：
  # 校验 zip 未被替换
  certutil -hashfile fishing-plugin-vX.Y.Z-source.zip SHA256     # Windows
  sha256sum -c SHA256SUMS.txt                                    # Linux/macOS
  # 解压后可再核对包内任意单文件
  （解压目录内）sha256sum -c MANIFEST.sha256
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fishing_plugin import __version__   # noqa: E402


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(exist_ok=True)

    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("⚠ 工作区有未提交改动，发布物将不含它们。建议先提交再打包。")

    name = f"fishing-plugin-v{__version__}-source.zip"
    zip_path = out / name

    # 1) git archive 导出干净树（仅已提交内容，自动排除 logs/debug/__pycache__）
    with zip_path.open("wb") as f:
        r = subprocess.run(["git", "archive", "--format=zip", "HEAD"], cwd=ROOT, stdout=f)
    if r.returncode != 0:
        print("git archive 失败")
        return 1

    # 2) 生成包内逐文件 MANIFEST 并追加进 zip
    with zipfile.ZipFile(zip_path, "a") as zf:
        lines = []
        for info in zf.infolist():
            if info.is_dir() or info.filename == "MANIFEST.sha256":
                continue
            digest = hashlib.sha256(zf.read(info.filename)).hexdigest()
            lines.append(f"{digest}  {info.filename}")
        zf.writestr("MANIFEST.sha256", "\n".join(lines) + "\n")

    # 3) zip 本体哈希 → SHA256SUMS.txt
    sums = out / "SHA256SUMS.txt"
    zip_hash = sha256_file(zip_path)
    sums.write_text(f"{zip_hash}  {name}\n", encoding="utf-8")

    print(f"✔ {zip_path}")
    print(f"  SHA256 = {zip_hash}")
    print(f"✔ {sums}")
    print("校验：sha256sum -c SHA256SUMS.txt（或 certutil -hashfile <zip> SHA256）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
