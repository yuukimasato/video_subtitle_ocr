# core/line_restoration.py
"""静态路径场景行的逐行还原估计(纯函数,不依赖 PySide6)。

「写入画面位置标签」(write_pose_tags)开启时,每个场景行不再统一继承
ROI 级姿态(手绘 quad 的整体倾角),而是各自从识别多边形/画面像素估计:

- :func:`line_frz_deg` —— 行方向角(ASS ``\\frz``,视觉逆时针为正)。
  OCR 多边形是屏幕坐标(还原只做常量平移),其长边方向就是该行文字的
  真实倾角;对 12.mp4 这类「ROI 整体倾斜但各行倾角各异」的面板,
  ROI 级 frz 与逐行真值可差出数度(实测 8.0° vs 1.9°)。
- :func:`sample_line_style` —— 从该行所在帧采样文字颜色与描边宽度:
  多边形内部像素的中位色作背景,远离背景的像素聚簇为墨迹,取离背景
  最远的四分位中位色作文字色(避开抗锯齿过渡像素);墨迹掩码的距离
  变换中位值 ×2 估计原字笔画宽,扣除替换字体的固有笔画后给出 ``\\bord``。
  墨迹占比/对比度不在可信区间时返回 None(宁可不写,不猜颜色)。

标签串只含本平台渲染可精确复现的标签(``\\1c``/``\\3c``/``\\bord``);
``\\frx``/``\\fry`` 的伪 3D 在 libass 里按字形独立错切、整行呈扇形,
无法从几何可靠反演,故不自动生成(mask_only 策略留给手工排版补写)。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "line_frz_deg",
    "sample_line_style",
    "format_style_tags",
]

# 取色的可信区间:墨迹像素占多边形内部像素的比例上下限,以及墨迹到
# 背景的最小色距(低于它说明行内对比不足,采样不可信)。
INK_FRAC_MIN = 0.03
INK_FRAC_MAX = 0.60
MIN_CONTRAST = 60.0


def _poly_edges(polygon: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    return [
        (float(polygon[(i + 1) % len(polygon)][0]) - float(polygon[i][0]),
         float(polygon[(i + 1) % len(polygon)][1]) - float(polygon[i][1]))
        for i in range(len(polygon))
    ]


def line_frz_deg(polygon: Sequence[Sequence[float]]) -> float:
    """识别多边形 → 行方向角(ASS ``\\frz``,度,视觉逆时针为正)。

    取四边形两组对边中较长的一组作行方向(短边组 = 行高方向),两条长边
    反向对齐后取平均方向,并规范化为自左向右;图像坐标 y 向下,基线向右
    下沉(顺时针倾斜)的行 frz 为负。长边对近竖直(竖排文字/退化框)时
    ``\\frz±90`` 对横排替换文本无意义,返回 0.0。
    """
    pts = [(float(p[0]), float(p[1])) for p in polygon]
    if len(pts) < 4:
        return 0.0
    edges = _poly_edges(pts)
    lens = [math_hypot(e) for e in edges]
    # 对边对:(e0, e2) 与 (e1, e3)
    pair_a = (lens[0] + lens[2]) / 2.0
    pair_b = (lens[1] + lens[3]) / 2.0
    if max(pair_a, pair_b) < 1e-6:
        return 0.0
    idxs = (0, 2) if pair_a >= pair_b else (1, 3)
    vecs = [edges[i] for i in idxs]
    # 第二条边反向对齐(绕四边形一周,对边方向相反)
    v0, v1 = vecs
    if v0[0] * v1[0] + v0[1] * v1[1] < 0:
        v1 = (-v1[0], -v1[1])
    mx = (v0[0] + v1[0]) / 2.0
    my = (v0[1] + v1[1]) / 2.0
    if math_hypot((mx, my)) < 1e-6:
        return 0.0
    # 方向规范化为自左向右(几何上行方向 ±180° 等价,但 frz 符号不是):
    # 文字从左往右排,基线 x 分量为负时取反向。
    if mx < 0:
        mx, my = -mx, -my
    # 屏幕顺时针倾斜(y 沿行方向增大)→ ASS frz 为负(+0.0 归一化 -0.0)
    frz = -float(np.degrees(np.arctan2(my, mx))) + 0.0
    if abs(frz) > 45.0:
        # 长边对近竖直(竖排文字/退化框):\frz±90 对横排替换文本无意义,
        # 不输出旋转标签。
        return 0.0
    return frz


def math_hypot(v: Tuple[float, float]) -> float:
    return float(np.hypot(v[0], v[1]))


def sample_line_style(
    frame_bgr: np.ndarray,
    polygon: Sequence[Sequence[float]],
    fs_px: float,
) -> Optional[Dict[str, object]]:
    """从帧图像采样行的文字颜色与描边宽度;不可信时返回 None。

    返回 ``{"primary": (b, g, r), "outline": (b, g, r), "bord": float}``。
    ``fs_px`` 为替换文字的有效字号(描边估计的参照),≤0 时跳过描边估计
    (只取色)。
    """
    import cv2

    h, w = frame_bgr.shape[:2]
    pts = np.array(
        [[(float(p[0]), float(p[1])) for p in polygon]],
        dtype=np.float32).astype(np.int32)
    pts[:, :, 0] = np.clip(pts[:, :, 0], 0, w - 1)
    pts[:, :, 1] = np.clip(pts[:, :, 1], 0, h - 1)
    if pts.min() < 0 or pts.shape[1] < 3:
        return None
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, pts, 255)
    interior = mask > 0
    n_inside = int(interior.sum())
    if n_inside < 48:  # 行太小,采样无统计意义
        return None
    pixels = frame_bgr.reshape(-1, 3).astype(np.float32)
    inside_flat = interior.reshape(-1)
    inside_px = pixels[inside_flat]
    bg = np.median(inside_px, axis=0)

    dist = np.abs(inside_px - bg).sum(axis=1)
    ink = dist > MIN_CONTRAST
    frac = float(ink.mean())
    if frac < INK_FRAC_MIN or frac > INK_FRAC_MAX:
        return None
    ink_px = inside_px[ink]
    ink_dist = dist[ink]
    # 离背景最远的 25% = 笔画核心(避开抗锯齿过渡像素)
    k = max(1, int(round(len(ink_px) * 0.25)))
    core_idx = np.argsort(ink_dist)[-k:]
    text_bgr = np.median(ink_px[core_idx], axis=0)
    color = tuple(int(round(c)) for c in text_bgr)

    bord = None
    fs_px = float(fs_px or 0.0)
    if fs_px > 0:
        ys, xs = np.nonzero(interior)
        ys, xs = ys[ink], xs[ink]
        if len(xs) >= 16:
            m2 = np.zeros((h, w), np.uint8)
            m2[ys, xs] = 255
            m2 = cv2.morphologyEx(m2, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            dt = cv2.distanceTransform(m2, cv2.DIST_L2, 3)
            vals = dt[m2 > 0]
            stroke = float(np.median(vals)) * 2.0
            # 替换字体固有笔画 ≈ 0.06×字号;补差的一半为描边宽度
            bord = max(0.2, min(3.0, 0.5 * (stroke - 0.06 * fs_px)))
            bord = float(round(bord, 1))

    return {"primary": color, "outline": color, "bord": bord}


def format_style_tags(style: Dict[str, object]) -> str:
    """采样结果 → ASS 标签串(``\\1c``/``\\3c``/``\\bord``;BGR 十六进制)。"""
    b, g, r = (int(c) for c in style["primary"])  # type: ignore[misc]
    ob, og, orr = (int(c) for c in style["outline"])  # type: ignore[misc]
    tags = f"\\1c&H{b:02X}{g:02X}{r:02X}&\\3c&H{ob:02X}{og:02X}{orr:02X}&"
    bord = style.get("bord")
    if bord:
        tags += f"\\bord{float(bord):.1f}"
    return tags
