"""本地审计与运行日志（仅本地文件，不含账号/截图内容）+ 一键清理。

- logs/audit.log ：发往设备的每条命令（含被白名单拒绝的），可核对插件对手机做了什么
- logs/run.log   ：状态机事件流水（暂停/恢复/抛竿/出售/通知……）
- debug/         ：仅 --debug-save 时的截图
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path


class Audit:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.logs_dir = self.root / "logs"
        self.debug_dir = self.root / "debug"
        self.logs_dir.mkdir(exist_ok=True)

    def _append(self, path: Path, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {line}\n")
        except OSError:
            pass                        # 日志失败不阻断主流程

    def cmd(self, device: str, argv: list[str], allowed: bool = True) -> None:
        flag = "OK " if allowed else "REFUSED"
        self._append(self.logs_dir / "audit.log", f"{flag} [{device}] {' '.join(map(str, argv))}")

    def event(self, name: str, detail: str = "") -> None:
        self._append(self.logs_dir / "run.log", f"{name} {detail}".rstrip())

    def debug_path(self, stem: str) -> Path:
        self.debug_dir.mkdir(exist_ok=True)
        return self.debug_dir / f"{time.strftime('%H%M%S')}_{stem}.png"

    def purge(self) -> list[str]:
        """一键清理：删除 logs/ 与 debug/。返回被删除的目录列表。"""
        removed = []
        for d in (self.logs_dir, self.debug_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(str(d))
        return removed
