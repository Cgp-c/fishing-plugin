"""图形控制面板（小白友好）：状态灯 + 大按钮 + 实时统计。

设计要点：
- tkinter 标准库，零新增依赖
- 主线程跑 GUI，FishingBot 跑工作线程；GUI 只调用 bot 的 request_*()（线程安全）
- 停止/暂停后**只能**通过「继续」按钮（或 F10 兜底热键）恢复——绝不自动恢复
- 每 200ms 刷新一次显示并兜底轮询热键
"""
from __future__ import annotations

import threading
import tkinter as tk

from .bot import BotStatus, FishingBot

DOT_COLOR = {
    BotStatus.WAITING_START: "#9e9e9e",   # 灰：待开始
    BotStatus.RUNNING: "#2ecc40",         # 绿：运行中
    BotStatus.PAUSED_USER: "#f1c40f",     # 黄：手动暂停
    BotStatus.STOPPED: "#ff4136",         # 红：已停止
}
STATUS_TEXT = {
    BotStatus.WAITING_START: "待开始",
    BotStatus.RUNNING: "运行中",
    BotStatus.PAUSED_USER: "已暂停",
    BotStatus.STOPPED: "已停止",
}


class ControlApp:
    def __init__(self, bot: FishingBot, *, on_exit=None, title_suffix: str = ""):
        self.bot = bot
        self.on_exit = on_exit

        self.root = tk.Tk()
        self.root.title("自动钓鱼插件" + title_suffix)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        big = ("Microsoft YaHei UI", 13, "bold")
        mid = ("Microsoft YaHei UI", 11)
        small = ("Microsoft YaHei UI", 9)

        # ---- 状态行：圆点 + 状态 + 原因 ----
        row = tk.Frame(self.root, padx=14, pady=10)
        row.pack(fill="x")
        self.dot = tk.Canvas(row, width=26, height=26, highlightthickness=0)
        self.dot.pack(side="left")
        self._dot_id = self.dot.create_oval(4, 4, 22, 22, fill="#9e9e9e", outline="")
        self.status_lbl = tk.Label(row, text="待开始", font=big, anchor="w")
        self.status_lbl.pack(side="left", padx=(8, 0))
        self.reason_lbl = tk.Label(self.root, text="点击下方按钮开始", font=mid,
                                   fg="#555555", wraplength=300, justify="left")
        self.reason_lbl.pack(fill="x", padx=14)

        # ---- 统计行 ----
        self.stats_lbl = tk.Label(self.root, text="", font=mid, fg="#333333")
        self.stats_lbl.pack(fill="x", padx=14, pady=(6, 0))

        # ---- 大按钮 ----
        btns = tk.Frame(self.root, padx=14, pady=10)
        btns.pack(fill="x")
        self.main_btn = tk.Button(btns, text="▶ 开始钓鱼", font=big, width=12,
                                  height=1, command=self._on_main, bg="#e8f5e9")
        self.main_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.pause_btn = tk.Button(btns, text="⏸ 暂停", font=mid, width=7,
                                   command=self._on_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=(0, 6))
        tk.Button(btns, text="✕ 退出", font=mid, width=6,
                  command=self._quit).pack(side="left")

        # ---- 底部提醒 ----
        tk.Label(self.root, text="任何意外都会自动停止，不会自动恢复；处理完点「继续」\n"
                                 "兜底热键：F9 暂停/继续 · F10 继续 ｜ 用完记得关闭手机 USB 调试",
                 font=small, fg="#888888", justify="left").pack(fill="x", padx=14, pady=(0, 8))

        self._tick_job = None
        self._worker = threading.Thread(target=bot.run, daemon=True)
        self._tick_job = self.root.after(200, self._tick)

    # ------------------------------------------------------------------ 回调
    def _on_main(self) -> None:
        self.bot.request_start()             # 开始/继续（恢复前会验证钓鱼界面）

    def _on_pause(self) -> None:
        if self.bot.status is BotStatus.RUNNING:
            self.bot.request_pause()
        else:
            self.bot.request_resume()

    def _quit(self) -> None:
        self.bot.exit()
        self.root.after(120, self._really_quit)

    def _really_quit(self) -> None:
        try:
            if self._tick_job is not None:
                self.root.after_cancel(self._tick_job)
        except Exception:
            pass
        try:
            self.root.destroy()
        finally:
            if self.on_exit:
                self.on_exit()

    # ------------------------------------------------------------------ 刷新
    def _tick(self) -> None:
        try:
            self.bot.poll_hotkeys()          # 兜底热键（GUI 优先）
            st = self.bot.status
            self.dot.itemconfig(self._dot_id, fill=DOT_COLOR[st])
            self.status_lbl.config(text=STATUS_TEXT[st],
                                   fg=DOT_COLOR[st] if st is BotStatus.STOPPED else "#222222")
            reason = self.bot.stop_reason or ("一切正常" if st is BotStatus.RUNNING else "点击下方按钮开始")
            self.reason_lbl.config(text=reason)
            s = self.bot.stats
            self.stats_lbl.config(
                text=f"抛竿 {s['casts']} ｜ 出售 {s['sold']} ｜ 提交订单 {s['submitted']}"
                     f" ｜ 稀有鱼停下 {s['rare_stops']} ｜ 意外停下 {s['missing_stops']}")
            if st is BotStatus.WAITING_START:
                self.main_btn.config(text="▶ 开始钓鱼", state="normal", bg="#e8f5e9")
                self.pause_btn.config(text="⏸ 暂停", state="disabled")
            elif st is BotStatus.RUNNING:
                self.main_btn.config(text="● 运行中", state="disabled", bg="#f0f0f0")
                self.pause_btn.config(text="⏸ 暂停", state="normal")
            elif st is BotStatus.PAUSED_USER:
                self.main_btn.config(text="▶ 继续", state="normal", bg="#fff8e1")
                self.pause_btn.config(text="▶ 继续", state="normal")
            else:                            # STOPPED：唯一恢复入口=人工
                self.main_btn.config(text="▶ 继续", state="normal", bg="#fdecea")
                self.pause_btn.config(text="▶ 继续", state="normal")
        except tk.TclError:
            return                           # 窗口已销毁
        self._tick_job = self.root.after(200, self._tick)

    # ------------------------------------------------------------------ 启动
    def run(self) -> None:
        self._worker.start()
        self.root.mainloop()
