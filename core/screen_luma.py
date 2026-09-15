# core/screen_luma.py
"""文字平面区域亮度曲线测量(屏幕亮度自适应字幕的感知层)。

对应《移动文字轨迹 → ASS 轨迹字幕》增量特性「屏幕亮度自适应」:
手机屏幕(文字平面)渐变变暗/变亮时,逐帧测量该区域的亮度相对基线的
变化,供 ``core.motion_ass.brightness_tag_chain`` 生成 ``\\1c``/``\\alpha``
忠实跟随标签。

- 只统计 ``status=="ok"`` 的帧(lost 帧无 quad,与 tracker/keyframe_selector
  同一哲学);
- 视频按顺序解码一遍(不随机 seek),解码序号与 ``TrackedQuad.frame_num``
  一一对应(轨迹起始帧号可以非 0);
- 亮度取 quad 外接矩形(裁到画面内)灰度的**中位值**:文字线条、反光高光
  等少数像素不影响中位值,比均值稳;
- baseline 取各帧中位值的 ``baseline_percentile`` 分位(默认 90:画面大部分
  时间的亮度水平,少量更亮帧不会抬高基线);ratio = luma / baseline 截到
  (0, 1](不为负、不超过 1;全黑帧取 1/255 的正下界);
- baseline <= 0(全程全黑退化)时全部返回 1.0(无亮度变化语义)。

本模块不 import PySide6,便于 CLI 脚本与离线单测复用。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core.scene_plane_tracker import TrackedQuad

__all__ = [
    "measure_luma_curve",
    "measure_luma_curve_with_baseline",
]

# luma=0、baseline>0 时的比值下界:落在 (0, 1] 内的最小可表示灰度级。
MIN_RATIO = 1.0 / 255.0


def _quad_bbox_in_frame(
    quad: Sequence[Sequence[float]], width: int, height: int,
) -> Optional[Tuple[int, int, int, int]]:
    """quad 外接矩形裁到画面内,返回半开区间 (x0, y0, x1, y1);空矩形返回 None。"""
    pts = np.asarray(quad, dtype=np.float64).reshape(-1, 2)
    x0 = max(0, int(math.floor(float(pts[:, 0].min()))))
    y0 = max(0, int(math.floor(float(pts[:, 1].min()))))
    x1 = min(int(width), int(math.ceil(float(pts[:, 0].max()))))
    y1 = min(int(height), int(math.ceil(float(pts[:, 1].max()))))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return x0, y0, x1, y1


def measure_luma_curve_with_baseline(
    video_path: str,
    tracks: List[TrackedQuad],
    *,
    baseline_percentile: float = 90.0,
) -> Tuple[List[Tuple[float, float]], float]:
    """同 :func:`measure_luma_curve`,另返回基线亮度。

    基线供调用方把亮度级容差(如 ``brightness_tol``)换算到比值空间
    (``core.motion_ass.simplify_luma_curve`` 的 ``baseline_luma``)。
    无 ok 帧 / 无有效样本时返回 ``([], 0.0)``。
    """
    ok_tracks = [t for t in tracks if t.status == "ok" and t.quad is not None]
    if not ok_tracks:
        return [], 0.0
    by_frame = {t.frame_num: t for t in ok_tracks}

    # 顺序解码一遍并逐 ok 帧测量(不随机 seek,保持与跟踪时一致的时间轴)。
    samples: List[Tuple[float, float]] = []  # (time_sec, 中位亮度)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        frame_idx = 0
        while True:
            ok_flag, frame = cap.read()
            if not ok_flag or frame is None:
                break
            track = by_frame.get(frame_idx)
            if track is not None:
                bbox = _quad_bbox_in_frame(
                    track.quad, frame.shape[1], frame.shape[0])
                if bbox is not None:
                    x0, y0, x1, y1 = bbox
                    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
                    samples.append((track.time_sec, float(np.median(gray))))
            frame_idx += 1
    finally:
        cap.release()

    if not samples:
        return [], 0.0

    lumas = np.array([luma for _t, luma in samples], dtype=np.float64)
    baseline = float(np.percentile(lumas, float(baseline_percentile)))
    if baseline <= 0.0:  # 全程全黑退化:无亮度变化语义,全部返回 1.0
        return [(t, 1.0) for t, _luma in samples], baseline
    curve: List[Tuple[float, float]] = []
    for t, luma in samples:
        ratio = luma / baseline
        if ratio > 1.0:
            ratio = 1.0
        elif ratio <= 0.0:
            ratio = MIN_RATIO  # 截到 (0, 1] 内的正下界
        curve.append((t, ratio))
    return curve, baseline


def measure_luma_curve(
    video_path: str,
    tracks: List[TrackedQuad],
    *,
    baseline_percentile: float = 90.0,
) -> List[Tuple[float, float]]:
    """逐 ok 帧测量文字平面区域亮度,返回 ``[(time_sec, ratio)]``(按帧号升序)。

    - 顺序解码一遍;对每个 ok 帧取 quad 外接矩形(裁到画面内)的灰度中位值;
    - baseline = 各帧中位值的 ``baseline_percentile`` 分位;
    - ratio = luma / baseline,截到 (0, 1];baseline <= 0(全黑退化)时全部
      返回 1.0;
    - 无 ok 帧返回 [];视频打不开抛 :class:`RuntimeError`(与 tracker 同风格)。
    """
    curve, _baseline = measure_luma_curve_with_baseline(
        video_path, tracks, baseline_percentile=baseline_percentile)
    return curve
