# core/text_motion_evidence.py
"""字形支持的屏幕位置测量与文字运动判定(D1,V6)。

真实样本(11 号视频,静止聊天文字 + 横移风景)暴露:全灰度模板的匹配峰
被透明气泡外的背景纹理主导,围绕平面预测位置的搜索把背景运动当成了
文字运动(``0 static, 10 corrected``,并有 run drift 记录)。本模块把
「文字在哪」的证据收紧到**字形笔画**上:

- :func:`glyph_mask_from_reference` —— 只在 OCR 行框内取字形支持:局部
  亮度归一、适应暗字/亮字的笔画候选、连通组件去除贴边大边界与大片
  背景;有效像素 ≥32 且覆盖率在 1%–60%,否则 None(unknown);
- :func:`masked_text_match` —— 以字形掩膜加权的归一化相关匹配;最高峰
  与排除一个行宽范围后的次高峰之差 <0.05 视为歧义(返回 None);全灰度
  相关峰不能替代结果;
- :func:`classify_text_centers` —— 只消费**可信字形匹配**的中心:
  锚点 = 坐标中位数,全部点到锚点距离 ≤ 容差 → static;≥3 个可信点、
  两个连续间隔方向一致且位移都超容差 → moving;其余 unknown。纯中心
  判定只给出平移证据,角度/尺度不变需调用方另行提供字形/参考一致性
  证据;不接受直接从单应生成的中心冒充实测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "TextMotionEvidence",
    "classify_text_centers",
    "glyph_mask_from_reference",
    "masked_text_match",
]

_AMBIGUITY_MARGIN = 0.05     # 最高峰与次高峰之差低于此值 → 歧义
_MIN_GLYPH_PIXELS = 32
_MIN_GLYPH_COVERAGE = 0.01
_MAX_GLYPH_COVERAGE = 0.60
_STROKE_DELTA = 30.0


@dataclass(frozen=True)
class TextMotionEvidence:
    """可信中心的运动判定结果。``state`` ∈ static/moving/unknown。"""

    centers: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    scores: Dict[int, float] = field(default_factory=dict)
    state: str = "unknown"
    anchor: Optional[Tuple[float, float]] = None
    reason: str = ""


def classify_text_centers(
    centers: Dict[int, Tuple[float, float]],
    scores: Dict[int, float],
    *,
    tolerance_px: float = 2.5,
    min_score: float = 0.45,
    min_samples: int = 3,
) -> TextMotionEvidence:
    """按可信字形匹配中心判定静/动/未知(只给平移证据)。"""
    trusted: Dict[int, Tuple[float, float]] = {}
    for frame, center in (centers or {}).items():
        score = float((scores or {}).get(frame, -1.0))
        if score < float(min_score) or center is None:
            continue
        cx, cy = float(center[0]), float(center[1])
        if not (math.isfinite(cx) and math.isfinite(cy)):
            continue
        trusted[int(frame)] = (cx, cy)

    def _unknown(reason: str) -> TextMotionEvidence:
        return TextMotionEvidence(dict(trusted),
                                  {f: float((scores or {}).get(f, -1.0))
                                   for f in trusted},
                                  "unknown", None, reason)

    if len(trusted) < int(min_samples):
        return _unknown(
            f"trusted samples {len(trusted)} < min_samples {min_samples}")
    xs = sorted(c[0] for c in trusted.values())
    ys = sorted(c[1] for c in trusted.values())
    anchor = (xs[len(xs) // 2], ys[len(ys) // 2])
    max_dev = max(math.hypot(c[0] - anchor[0], c[1] - anchor[1])
                  for c in trusted.values())
    if max_dev <= float(tolerance_px):
        return TextMotionEvidence(dict(trusted),
                                  {f: float(scores[f]) for f in trusted},
                                  "static", anchor,
                                  f"max deviation {max_dev:.2f}px <= "
                                  f"tolerance {tolerance_px:g}px")
    # moving:≥3 个可信点、两个连续间隔方向一致且位移都超容差。
    frames = sorted(trusted)
    pts = [trusted[f] for f in frames]
    for i in range(len(pts) - 2):
        v1 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        v2 = (pts[i + 2][0] - pts[i + 1][0], pts[i + 2][1] - pts[i + 1][1])
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 > tolerance_px and n2 > tolerance_px and (v1[0] * v2[0]
                                                        + v1[1] * v2[1]) > 0:
            return TextMotionEvidence(
                dict(trusted), {f: float(scores[f]) for f in trusted},
                "moving", anchor,
                f"consistent displacement {n1:.1f}px/{n2:.1f}px")
    return _unknown(
        f"max deviation {max_dev:.2f}px > tolerance but no consistent "
        "displacement direction")


def glyph_mask_from_reference(
    reference_gray: "np.ndarray",
    box: Tuple[float, float, float, float],
    *,
    delta: float = _STROKE_DELTA,
    min_pixels: int = _MIN_GLYPH_PIXELS,
    min_coverage: float = _MIN_GLYPH_COVERAGE,
    max_coverage: float = _MAX_GLYPH_COVERAGE,
) -> Optional["np.ndarray"]:
    """OCR 行框内的字形支持掩膜(暗字/亮字自适应);不足返回 None。

    - 局部亮度归一:以行框中位灰为背景估计,双向阈值取笔画候选;
    - 取覆盖率更接近真实笔画(稀疏)的极性,覆盖率须在
      [``min_coverage``, ``max_coverage``];
    - 连通组件剔除贴边且占比过大的区域(气泡边界/大片背景)。
    返回与 ``reference_gray`` 同尺寸的 uint8 掩膜(1 = 字形支持)。
    """
    import cv2

    if reference_gray is None or box is None:
        return None
    h, w = reference_gray.shape[:2]
    x1, y1, x2, y2 = (int(round(float(v))) for v in box[:4])
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    crop = reference_gray[y1:y2, x1:x2].astype(np.float64)
    med = float(np.median(crop))
    dark = (crop < med - float(delta)).astype(np.uint8)
    bright = (crop > med + float(delta)).astype(np.uint8)
    area = float(crop.size)

    def _coverage(mask: "np.ndarray") -> float:
        return float(mask.sum()) / area if area else 0.0

    candidates: List["np.ndarray"] = []
    for mask in (dark, bright):
        cov = _coverage(mask)
        if min_coverage <= cov <= max_coverage:
            candidates.append(mask)
    if not candidates:
        return None
    # 笔画通常稀疏:取覆盖率较小的合格极性。
    mask = min(candidates, key=_coverage)
    # 剔除贴边大组件(气泡边界/背景块):触边且占框面积 >40% 的组件丢弃。
    n_comp, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    ch, cw = mask.shape
    for i in range(1, n_comp):
        x, y, bw, bh, comp_area = (int(stats[i, cv2.CC_STAT_LEFT]),
                                   int(stats[i, cv2.CC_STAT_TOP]),
                                   int(stats[i, cv2.CC_STAT_WIDTH]),
                                   int(stats[i, cv2.CC_STAT_HEIGHT]),
                                   int(stats[i, cv2.CC_STAT_AREA]))
        touches_border = x == 0 or y == 0 or x + bw >= cw or y + bh >= ch
        if touches_border and comp_area > 0.40 * area:
            mask[labels == i] = 0
    if int(mask.sum()) < int(min_pixels):
        return None
    full = np.zeros((h, w), np.uint8)
    full[y1:y2, x1:x2] = mask
    return full


def masked_text_match(
    frame_gray: "np.ndarray",
    reference_gray: "np.ndarray",
    glyph_mask: "np.ndarray",
    search_box: Tuple[int, int, int, int],
) -> Optional[Tuple[float, float, float]]:
    """以字形掩膜加权的归一化相关匹配,返回 (中心 x, y, 置信度)。

    ``search_box`` = (x1, y1, x2, y2) 限定在 ``frame_gray`` 内的搜索窗。
    模板 = ``reference_gray`` 中字形包围盒内的灰度,掩膜 = ``glyph_mask``
    对应区域;峰歧义(最高峰与排除一个模板尺寸范围后的次高峰之差
    <0.05)或无有效字形返回 None。
    """
    import cv2

    if (frame_gray is None or reference_gray is None
            or glyph_mask is None):
        return None
    ys, xs = np.nonzero(glyph_mask)
    if len(xs) == 0:
        return None
    gx1, gx2 = int(xs.min()), int(xs.max()) + 1
    gy1, gy2 = int(ys.min()), int(ys.max()) + 1
    template = reference_gray[gy1:gy2, gx1:gx2]
    mask = glyph_mask[gy1:gy2, gx1:gx2]
    ph, pw = template.shape[:2]
    fh, fw = frame_gray.shape[:2]
    x1, y1, x2, y2 = (int(round(float(v))) for v in search_box[:4])
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(fw, x2), min(fh, y2)
    if x2 - x1 < pw or y2 - y1 < ph:
        return None
    window = frame_gray[y1:y2, x1:x2]
    if window.shape[0] < ph or window.shape[1] < pw:
        return None
    res = cv2.matchTemplate(window, template, cv2.TM_CCORR_NORMED,
                            mask=mask.astype(np.float32))
    res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _min_s, max_s, _min_loc, max_loc = cv2.minMaxLoc(res)
    if max_s <= 0:
        return None
    # 次高峰:排除最高峰 ± 一个模板尺寸范围。
    suppressed = res.copy()
    rx0 = max(0, max_loc[0] - pw)
    rx1 = min(suppressed.shape[1], max_loc[0] + pw)
    ry0 = max(0, max_loc[1] - ph)
    ry1 = min(suppressed.shape[0], max_loc[1] + ph)
    suppressed[ry0:ry1, rx0:rx1] = -1.0
    _m2, second_s, _ml, _sl = cv2.minMaxLoc(suppressed)
    if (max_s - second_s) < _AMBIGUITY_MARGIN:
        return None
    cx = x1 + max_loc[0] + (pw - 1) / 2.0
    cy = y1 + max_loc[1] + (ph - 1) / 2.0
    return (float(cx), float(cy), float(max_s))
