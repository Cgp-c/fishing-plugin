"""OpenCV 检测核心：一帧 RGB 图像 → 结构化检测结果（纯函数式，无副作用）。

判定链（按优先级）：
  1. 求福主锚点模板匹配（TM_CCOEFF_NORMED ≥ 阈值）→ 是否在钓鱼界面
  2. 全帧平均亮度骤降 → CATCH（结算态全屏压暗遮罩，实测 66.8 vs 平时 ~196）
  3. 抛竿提示区对比度像素列覆盖率 → READY（深蓝字白描边 vs 青色水面，色距>60）
  4. 其余 → WAITING
分色与按钮区分只在 CATCH 内做；分色仅限名字条带（条带外金色装饰必误报）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Detection:
    qiufu_score: float = 0.0      # 求福模板匹配最高分
    in_fishing: bool = False      # 是否在钓鱼界面（主锚点命中）
    state: str = "UNKNOWN"        # READY / WAITING / CATCH / UNKNOWN
    rarity: str | None = None     # 白/绿/蓝/紫/黄/未知（仅 CATCH）
    button: str | None = None     # 出售/提交订单（仅 CATCH）
    mean_v: float = 255.0         # 全帧平均亮度
    notes: dict = field(default_factory=dict)   # 各中间计数（调试/测试用）


def _crop(frame: np.ndarray, zone: dict) -> np.ndarray:
    h, w = frame.shape[:2]
    return frame[int(zone["y0"] * h):int(zone["y1"] * h),
                  int(zone["x0"] * w):int(zone["x1"] * w)]


def load_template(path: str | Path) -> np.ndarray:
    """读模板为 RGB。中文路径下 cv2.imread 会失败，必须 imdecode+fromfile。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"模板读取失败: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


class Detector:
    def __init__(self, cfg: dict, template_path: str | Path, template_ref_width: int):
        self.cfg = cfg
        self.zones = cfg["zones"]
        self.th = cfg["thresholds"]
        self.bands = cfg["hsv_bands"]
        self.tpl = load_template(template_path)
        self.ref_w = int(template_ref_width)

    # ------------------------------------------------------------------ 主锚点
    def qiufu_score(self, frame: np.ndarray) -> float:
        tpl = self.tpl
        fw = frame.shape[1]
        if fw != self.ref_w:                       # 模板按帧宽缩放（跨分辨率）
            r = fw / self.ref_w
            tpl = cv2.resize(tpl, (max(8, round(tpl.shape[1] * r)),
                                   max(8, round(tpl.shape[0] * r))),
                             interpolation=cv2.INTER_AREA if r < 1 else cv2.INTER_LINEAR)
        crop = _crop(frame, self.zones["qiufu_search"])
        if crop.shape[0] < tpl.shape[0] or crop.shape[1] < tpl.shape[1]:
            return 0.0
        res = cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED)
        return float(res.max())

    # ------------------------------------------------------------------ READY
    def hint_contrast(self, frame: np.ndarray) -> tuple[float, float]:
        """抛竿提示区：与区域中位色的色距统计 → (对比像素面积比, 有字列覆盖率)。"""
        z = _crop(frame, self.zones["cast_hint"]).astype(np.float32)
        if z.size == 0:
            return 0.0, 0.0
        med = np.median(z.reshape(-1, 3), axis=0)
        dist = np.sqrt(((z - med) ** 2).sum(axis=2))
        m = dist > self.th["hint_color_dist"]
        zone_ratio = float(m.mean())
        col_ratio = float((m.sum(axis=0) >= 2).sum()) / m.shape[1]
        return zone_ratio, col_ratio

    # ------------------------------------------------------------------ 分色
    def classify_rarity(self, frame: np.ndarray) -> tuple[str, int]:
        """名字条带内 HSV 分色。返回 (稀有度, 选中像素数)。

        实测校准（真实结算截图）：条带金色底纹暗于文字（max 通道 90~120 vs
        文字 >120），因此亮度阈值取 120 以滤除底纹；分类用色相直方图峰值
        （10° 分箱 + 环形平滑），比均值抗干扰。float32 输入时 OpenCV 的 H
        已是 0~360 度，不要再乘 360。
        """
        band = _crop(frame, self.zones["name_band"])
        if band.size == 0:
            return "未知", 0
        b = band.reshape(-1, 3)
        mx, mn = b.max(1).astype(int), b.min(1).astype(int)
        v_min = self.th.get("nameband_v_min", 120)
        s_min = self.th.get("nameband_s_min", 60)
        sel = b[((mx - mn) > s_min) & (mx > v_min)]        # 彩色文字像素
        n = len(sel)
        if n < max(30, self.th["nameband_sel_ratio"] * b.shape[0]):
            return "白", n                                  # 无饱和彩色 → 白
        hsv = cv2.cvtColor(sel.astype(np.float32).reshape(-1, 1, 3) / 255.0,
                           cv2.COLOR_RGB2HSV)
        hue = hsv[:, 0, 0]                                  # 度，0~360
        hist, _ = np.histogram(hue, bins=36, range=(0, 360))
        ext = np.r_[hist[-2:], hist, hist[:2]]              # 环形平滑（±20°）
        sm = np.convolve(ext, [1, 2, 3, 2, 1], mode="same")[2:-2]
        peak = int(sm.argmax())
        hue_peak = (peak + 0.5) * 10.0
        cluster = hist[max(0, peak - 1):peak + 2].sum()     # 峰值 ±15° 聚类占比
        if cluster / n < self.th.get("nameband_share_min", 0.25):
            return "未知", n
        for bd in self.bands:
            if bd["lo"] <= hue_peak <= bd["hi"]:
                return bd["name"], n
        return "未知", n

    # ------------------------------------------------------------------ 按钮
    def button_kind(self, frame: np.ndarray) -> str | None:
        z = _crop(frame, self.zones["btn_left"]).reshape(-1, 3).astype(int)
        if z.size == 0:
            return None
        blue = int(((z[:, 2] > z[:, 0] + 40) & (z[:, 2] > z[:, 1] + 30) & (z[:, 2] > 140)).sum())
        orange = int(((z[:, 0] > 200) & (z[:, 1] > 130) & (z[:, 1] < 210) & (z[:, 2] < 120)).sum())
        n = z.shape[0]
        lo = self.th["btn_ratio_min"] * n
        if blue < lo and orange < lo:
            return None
        return "提交订单" if orange > blue else "出售"

    # ------------------------------------------------------------------ 总入口
    def analyze(self, frame: np.ndarray) -> Detection:
        frame = np.ascontiguousarray(frame, dtype=np.uint8)
        score = self.qiufu_score(frame)
        mean_v = float(frame.max(2).mean())
        det = Detection(qiufu_score=round(score, 3), mean_v=round(mean_v, 1))
        det.in_fishing = score >= self.th["qiufu_match"]
        det.notes["mean_v"] = det.mean_v

        if not det.in_fishing:
            det.state = "UNKNOWN"
            return det

        if mean_v < self.th["catch_mean_v_max"]:        # 全屏压暗 → 结算
            det.state = "CATCH"
            det.rarity, npx = self.classify_rarity(frame)
            det.button = self.button_kind(frame)
            det.notes["nameband_px"] = npx
            return det

        zone_ratio, col_ratio = self.hint_contrast(frame)
        det.notes["hint_zone_ratio"] = round(zone_ratio, 3)
        det.notes["hint_col_ratio"] = round(col_ratio, 3)
        if zone_ratio >= self.th["hint_zone_ratio"] and col_ratio >= self.th["hint_col_ratio"]:
            det.state = "READY"
        else:
            det.state = "WAITING"
        return det
