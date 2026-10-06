"""配置加载：默认值内置，config.json 可覆盖（本地文件，无任何账号字段）。

检测参数来源：fishing-game-verify/docs/layout.md + docs/colors.md，
其中 READY/CATCH 判别阈值已用三张真实游戏截图（929x2000）像素级校准。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    # PC 窗口桥使用的游戏窗口标题（测试游戏）
    "window_title": "简单钓鱼游戏验证",
    # 求福模板（相对插件根目录；真实手机用 templates/qiufu_real.png + ref_width 929）
    "template": "templates/qiufu_test.png",
    "template_ref_width": 540,

    # 区域坐标（全屏百分比，与 fishing-game-verify/docs/layout.md 对齐）
    "zones": {
        # 求福图标搜索区（图标圆心部分 y66.6%~72.1%，放宽上下沿）
        "qiufu_search": {"x0": 0.000, "y0": 0.640, "x1": 0.180, "y1": 0.750},
        # 抛竿提示文字区（真实实测 x32%~68%, y84.5%~89.5%）
        "cast_hint":    {"x0": 0.320, "y0": 0.845, "x1": 0.680, "y1": 0.895},
        # 鱼获名字检测区（排除左右金色纹样；colors.md：只允许在此分色）
        "name_band":    {"x0": 0.400, "y0": 0.500, "x1": 0.600, "y1": 0.550},
        # 左下按钮区（出售蓝 / 提交订单橙金）
        "btn_left":     {"x0": 0.140, "y0": 0.790, "x1": 0.430, "y1": 0.860},
        # 安全抛竿点击区（layout.md 建议：抛竿区中部偏下，远离订单面板）
        "safe_cast":    {"x0": 0.250, "y0": 0.780, "x1": 0.750, "y1": 0.920},
    },

    "thresholds": {
        "qiufu_match": 0.85,        # 模板匹配判定在钓鱼界面（实测命中≈0.98+，遮挡≈0.2）
        "anchor_miss_frames": 3,    # 求福连续缺失 N 帧 → 非钓鱼界面 → 自动暂停
        # 结算判别：全屏压暗遮罩使全帧平均亮度骤降（实测 66.8 vs 平时 ~196）
        "catch_mean_v_max": 95.0,
        # READY 判别：提示文字与水面底色的色距（实测水面 RGB(29,174,213)，
        # 深蓝字+白描边 → 色距>60 像素列覆盖率 ready=76%、waiting=0%）
        "hint_color_dist": 60.0,
        "hint_zone_ratio": 0.08,    # 对比度像素占提示区面积比下限
        "hint_col_ratio": 0.30,     # 有字列覆盖率下限（鱼漂等窄目标 <15%）
        # 名字条带分色（colors.md 检测色带，色相单位：度）
        "nameband_sel_ratio": 0.008, # 饱和像素低于此比例 → 白
        "nameband_v_min": 120,      # 文字亮度下限（滤除暗于文字的金色底纹）
        "nameband_s_min": 60,       # 饱和度下限（max-min 通道差）
        "nameband_share_min": 0.25, # 峰值±15°聚类占比下限，低于判未知
        "unknown_frames": 3,        # 分色连续未知 N 次 → 暂停待人工
        # 按钮区分（仅结算态内使用）：蓝/橙像素占按钮区比例
        "btn_ratio_min": 0.02,
    },

    # HSV 色带（度）：绿/蓝/紫/黄，白由饱和像素不足判定
    "hsv_bands": [
        {"name": "绿", "lo": 90.0, "hi": 160.0},
        {"name": "蓝", "lo": 195.0, "hi": 235.0},
        {"name": "紫", "lo": 255.0, "hi": 330.0},
        {"name": "黄", "lo": 42.0,  "hi": 65.0},
    ],
    "rare_rarities": ["紫", "黄"],   # 稀有鱼 → 暂停 + 通知，人工处理完自动继续

    # 随机延迟（毫秒，区间内均匀）：模拟人手
    "delays_ms": {
        "cast":         [700, 1600],   # READY → 抛竿前
        "catch_click":  [500, 1200],   # 结算 → 点按钮前
        "poll":         [400, 800],    # 截屏轮询间隔
        "after_action": [600, 1200],   # 动作后额外等待
    },
    "action_cooldown_s": 2.0,          # 任意两次点击最小间隔（防连点/防重复抛竿）
    "verify_before_tap": True,         # 点击前二次复核界面（防购买护栏的关键开关）
    "ready_stable_frames": 2,          # READY 连续 N 帧才抛竿（防单帧误判）
    "max_device_errors": 2,            # 截屏/设备连续失败 N 次 → 硬停止
    "start_auto": False,               # 启动后是否自动开始（False=等用户点「开始」）

    # 热键为兜底，主入口是 GUI 大按钮（小白友好）
    # 手机工具绝对路径（防 PATH 劫持；留空=按 PATH 查找并在启动时显示解析结果供核对）
    "tools": {"adb_path": "", "hdc_path": ""},
    "hotkey_pause": "F9",              # 暂停/恢复切换
    "hotkey_resume": "F10",            # 恢复（停止/暂停后人工恢复的唯一快捷途径）
    "device": {"type": "pc", "serial": ""},   # pc | adb | hdc
    "notify": {"beep": True, "beep_times": 3},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    """加载配置：DEFAULTS ← config.json（可选再 ← config.local.json）。

    config.local.json 供存放个人设备序列号等，已被 .gitignore 排除。
    """
    cfg = copy.deepcopy(DEFAULTS)
    root = PLUGIN_ROOT
    for name in ("config.json", "config.local.json"):
        p = Path(path) if (path and name == "config.json") else root / name
        if p and p.is_file():
            cfg = _deep_merge(cfg, json.loads(p.read_text(encoding="utf-8")))
    return cfg
