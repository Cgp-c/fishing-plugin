"""本地审计与运行日志（仅本地文件，不含账号/截图内容）+ 一键清理。

- logs/audit.log ：发往设备的每条命令（含被白名单拒绝的），可核对插件对手机做了什么
- logs/run.log   ：状态机事件流水（暂停/恢复/抛竿/出售/通知……）
- debug/         ：仅 --debug-save 时的截图，超过 48 小时自动过期删除（A3）

隐私处理（A4）：写入任何日志前把用户主目录替换为 ~，
traceback 中的 C:\\Users\\<用户名>\\... 不再泄露 Windows 用户名。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

DEBUG_TTL_S = 48 * 3600          # debug 截图保留时长


class Audit:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.logs_dir = self.root / "logs"
        self.debug_dir = self.root / "debug"
        self.logs_dir.mkdir(exist_ok=True)
        try:
            self._home = str(Path.home())
        except Exception:
            self._home = ""
        self._swept = False        # debug/ 过期清扫只做一次/实例

    def _sanitize(self, line: str) -> str:
        if self._home and len(self._home) > 3 and self._home in line:
            line = line.replace(self._home, "~")
        return line

    def _append(self, path: Path, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {self._sanitize(line)}\n")
        except OSError:
            pass                        # 日志失败不阻断主流程

    def cmd(self, device: str, argv: list[str], allowed: bool = True) -> None:
        flag = "OK " if allowed else "REFUSED"
        self._append(self.logs_dir / "audit.log", f"{flag} [{device}] {' '.join(map(str, argv))}")

    def event(self, name: str, detail: str = "") -> None:
        self._append(self.logs_dir / "run.log", f"{name} {detail}".rstrip())

    def debug_path(self, stem: str) -> Path:
        if not self._swept:
            self._sweep_debug()
            self._swept = True
        self.debug_dir.mkdir(exist_ok=True)
        return self.debug_dir / f"{time.strftime('%H%M%S')}_{stem}.png"

    def _sweep_debug(self) -> None:
        """删除超过 48h 的调试截图（截图含游戏昵称/头像，不留存过久）。"""
        if not self.debug_dir.is_dir():
            return
        now = time.time()
        try:
            for f in self.debug_dir.iterdir():
                if f.is_file() and now - f.stat().st_mtime > DEBUG_TTL_S:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    def purge(self) -> list[str]:
        """一键清理：删除 logs/ 与 debug/。返回被删除的目录列表。"""
        removed = []
        for d in (self.logs_dir, self.debug_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(str(d))
        return removed
