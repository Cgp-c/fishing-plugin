"""命令行入口。

    python -m fishing_plugin [选项]

默认启动**图形控制面板**（小白友好：大按钮开始/暂停/退出，任何意外自动停止、
绝不自动恢复，恢复只能点「继续」或按 F10）。加 --no-gui 走控制台模式（测试/高级）。

常用：
  --device pc|adb|hdc     设备桥（默认 pc：对"简单钓鱼游戏验证"窗口跑）
  --serial XXX            手机序列号（不填则要求恰好一台设备在线）
  --template PATH         求福模板（真实手机: templates/qiufu_real.png）
  --ref-width N           模板来源截图宽度（qiufu_real.png 为 929）
  --dry-run               只检测不点击（首次上真机建议先跑这个）
  --debug-save            截图落盘到 debug/（默认不落盘）
  --no-gui                控制台模式（配合 --auto-start 无人值守；Ctrl+C 退出）
  --auto-start            （--no-gui）启动后立即开始，不等待人工
  --list-devices          列出 adb/hdc 在线设备
  --purge                 一键清理 logs/ 与 debug/
  --duration 秒           运行时长上限（到点自动停止）
  --kill-server           退出时 adb kill-server / hdc kill
  --seed N                固定随机种子（测试复现用）
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from . import __version__
from .audit import Audit
from .bot import BotStatus, FishingBot
from .config import PLUGIN_ROOT, load_config
from .detector import Detector
from .devices import create_bridge
from .devices.adb import AdbBridge
from .devices.base import DeviceError
from .devices.hdc import HdcBridge
from .humanize import Humanizer
from .notifier import Notifier


def _win_dpi_aware() -> None:
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 截屏物理像素一致的前提
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fishing_plugin",
                                 description="自动钓鱼插件（本地检测，零联网，任何意外即停）")
    ap.add_argument("--device", choices=["pc", "adb", "hdc"], default=None)
    ap.add_argument("--serial", default=None, help="手机序列号（序列号校验用）")
    ap.add_argument("--title", default=None, help="pc 桥窗口标题覆盖")
    ap.add_argument("--config", default=None, help="配置文件路径（默认 config.json）")
    ap.add_argument("--template", default=None, help="求福模板图片路径")
    ap.add_argument("--ref-width", type=int, default=None, help="模板来源截图宽度(px)")
    ap.add_argument("--dry-run", action="store_true", help="只检测不点击")
    ap.add_argument("--debug-save", action="store_true", help="截图保存到 debug/")
    ap.add_argument("--no-gui", action="store_true", help="控制台模式（默认开图形面板）")
    ap.add_argument("--auto-start", action="store_true",
                    help="（控制台模式）启动后立即开始，不等待人工")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--purge", action="store_true", help="清理 logs/ 与 debug/")
    ap.add_argument("--duration", type=float, default=None, help="运行秒数上限")
    ap.add_argument("--kill-server", action="store_true",
                    help="退出时 adb kill-server / hdc kill")
    ap.add_argument("--no-beep", action="store_true")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（测试用）")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)

    audit = Audit(PLUGIN_ROOT)
    if args.purge:
        removed = audit.purge()
        print("已清理：" + ("、".join(removed) if removed else "（无日志/调试文件）"))
        return 0

    if args.list_devices:
        for cls, label in ((AdbBridge, "adb"), (HdcBridge, "hdc")):
            try:
                bridge = cls(audit=audit, binary=(cfg.get("tools") or {}).get(f"{label}_path", ""))
                names = bridge.list_devices() if hasattr(bridge, "list_devices") else bridge.list_targets()
                print(f"{label}: {names}")
                print(f"  工具路径: {bridge.binary}（来源：{bridge.binary_source}）")
            except (DeviceError, FileNotFoundError) as e:
                print(f"{label}: 不可用（{e}）")
        return 0

    _win_dpi_aware()

    tpl = args.template or (PLUGIN_ROOT / cfg["template"])
    ref_w = args.ref_width or cfg["template_ref_width"]
    try:
        detector = Detector(cfg, tpl, ref_w)
    except FileNotFoundError as e:
        print(f"模板加载失败：{e}")
        return 2

    rng = random.Random(args.seed)
    notifier = Notifier(enabled=cfg["notify"]["beep"] and not args.no_beep,
                        beep_times=cfg["notify"]["beep_times"])
    try:
        bridge = create_bridge(cfg, audit, device=args.device,
                               serial=args.serial, title=args.title)
    except DeviceError as e:
        print(f"设备不可用：{e}")
        return 3

    human = Humanizer(rng, cfg["delays_ms"])
    bot = FishingBot(bridge, detector, human, notifier, audit, cfg,
                     dry_run=args.dry_run, debug_save=args.debug_save,
                     start_auto=args.auto_start)
    is_phone = (args.device in ("adb", "hdc")
                or (args.device is None and cfg["device"]["type"] in ("adb", "hdc")))
    tool_info = ""
    if getattr(bridge, "binary", None):
        tool_info = f"{bridge.name} = {bridge.binary}（来源：{bridge.binary_source}）"
        print(f"工具路径核对: {tool_info}   ← 请确认是官方 adb/hdc，防 PATH 劫持")
        audit.event("tool_path", tool_info)
    print(f"fishing-plugin v{__version__}  零联网·本地检测·任何意外即停  "
          f"模板={Path(tpl).name}({ref_w}px)  桥={bridge.name}")

    def _cleanup() -> None:
        bridge.close(kill_server=args.kill_server)
        if is_phone:
            print("提示：用完记得关闭手机上的 USB 调试（开发者选项），拔线即可。")

    rc = 0
    try:
        if args.no_gui:
            bot.run(duration=args.duration)
            if bot.status is not BotStatus.STOPPED:
                rc = 4
        else:
            from .app import ControlApp
            suffix = "（试运行·不点击）" if args.dry_run else ""
            app = ControlApp(bot, on_exit=_cleanup, title_suffix=suffix,
                             tool_info=tool_info)
            app.run()
    except DeviceError as e:
        print(f"运行中设备错误：{e}")
        rc = 4
    finally:
        bot.exit()
        if args.no_gui:
            _cleanup()
    return rc


if __name__ == "__main__":
    sys.exit(main())
