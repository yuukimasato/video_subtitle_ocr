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
    "measure_line_luma_curves",
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


# ---------------------------------------------------------------------------
# 逐行亮度(屏幕局部调暗的背景适配)
# ---------------------------------------------------------------------------

def _nearest_ok(
    by_frame: Dict[int, TrackedQuad], ref_frame: int,
) -> Optional[TrackedQuad]:
    """最近 ok 轨迹((距离, 帧号) 最小,并列取更早帧);无 ok 帧返回 None。"""
    best: Optional[Tuple[Tuple[int, int], TrackedQuad]] = None
    for f, tq in by_frame.items():
        key = (abs(f - ref_frame), f)
        if best is None or key < best[0]:
            best = (key, tq)
    return best[1] if best else None


def measure_line_luma_curves(
    video_path: str,
    tracks: List[TrackedQuad],
    line_boxes: Sequence[Sequence[float]],
    ref_frame: int,
    *,
    baseline_percentile: float = 90.0,
) -> Tuple[List[List[Tuple[float, float]]], List[float]]:
    """逐行亮度曲线:每行行框在逐 ok 帧的局部亮度 ``[(time_sec, ratio)]``。

    「屏幕局部调暗的背景适配」:整平面单条曲线会抹平局部明暗(如只有屏幕
    顶栏调暗、正文依旧全亮),本函数对每个行框独立测量——行框四角(参考帧
    平面坐标)经 ``H(ref→t) = H(init→t)·inv(H(init→ref))`` 逐帧映射为画面
    四边形,投影多边形(裁到画面内)内像素的灰度中位值为该行该帧亮度。
    参考帧 lost 时与 :func:`core.motion_ass.build_line_tracks` 同法重锚定到
    最近 ok 帧;baseline 取该行各帧中位值的 ``baseline_percentile`` 分位,
    ratio 截到 (0, 1],退化语义与 :func:`measure_luma_curve_with_baseline`
    一致(按行独立)。

    返回 ``(curves, baselines)``,与 ``line_boxes`` 等长;某行全程无有效样本
    (行框一直在画面外/无 ok 帧)时其曲线为 ``[]``、基线为 ``0.0``。
    """
    ok_tracks = [t for t in tracks if t.status == "ok" and t.homography is not None]
    if not ok_tracks or not line_boxes:
        return [[] for _ in line_boxes], [0.0] * len(line_boxes)
    by_frame = {t.frame_num: t for t in ok_tracks}
    ref = by_frame.get(int(ref_frame))
    if ref is None:
        ref_tq = _nearest_ok(by_frame, int(ref_frame))
        if ref_tq is None:
            return [[] for _ in line_boxes], [0.0] * len(line_boxes)
        ref = ref_tq
    h_ref = np.asarray(ref.homography, dtype=np.float64)

    corners_all = []
    for box in line_boxes:
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        corners_all.append(np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64))

    samples: List[List[Tuple[float, float]]] = [[] for _ in line_boxes]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        frame_idx = 0
        while True:
            ok_flag, frame = cap.read()
            if not ok_flag or frame is None:
                break
            tq = by_frame.get(frame_idx)
            if tq is not None and tq.homography is not None:
                h_t = np.asarray(tq.homography, dtype=np.float64)
                h_map = h_t @ np.linalg.inv(h_ref)
                fh, fw = frame.shape[:2]
                gray_full = None
                for li, corners in enumerate(corners_all):
                    proj = cv2.perspectiveTransform(
                        corners.reshape(-1, 1, 2).astype(np.float32),
                        h_map.astype(np.float32),
                    ).reshape(-1, 2).astype(np.float64)
                    x0 = max(0, int(math.floor(float(proj[:, 0].min()))))
                    y0 = max(0, int(math.floor(float(proj[:, 1].min()))))
                    x1 = min(fw, int(math.ceil(float(proj[:, 0].max()))))
                    y1 = min(fh, int(math.ceil(float(proj[:, 1].max()))))
                    if x1 - x0 < 1 or y1 - y0 < 1:
                        continue  # 行框投影整体在画面外:该帧无样本
                    if gray_full is None:
                        gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
                    cv2.fillPoly(mask, [(proj - [x0, y0]).astype(np.int32)], 255)
                    pixels = gray_full[y0:y1, x0:x1][mask > 0]
                    if pixels.size:
                        samples[li].append(
                            (tq.time_sec, float(np.median(pixels))))
            frame_idx += 1
    finally:
        cap.release()

    curves: List[List[Tuple[float, float]]] = []
    baselines: List[float] = []
    for line_samples in samples:
        if not line_samples:
            curves.append([])
            baselines.append(0.0)
            continue
        lumas = np.array([luma for _t, luma in line_samples], dtype=np.float64)
        baseline = float(np.percentile(lumas, float(baseline_percentile)))
        baselines.append(baseline)
        if baseline <= 0.0:
            curves.append([(t, 1.0) for t, _l in line_samples])
            continue
        curve: List[Tuple[float, float]] = []
        for t, luma in line_samples:
            ratio = luma / baseline
            if ratio > 1.0:
                ratio = 1.0
            elif ratio <= 0.0:
                ratio = MIN_RATIO
            curve.append((t, ratio))
        curves.append(curve)
    return curves, baselines
