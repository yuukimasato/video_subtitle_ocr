# core/keyframe_selector.py
"""清晰关键帧选取：从平面跟踪轨迹中挑出最清晰的 top-K 帧。

对应《移动文字轨迹 → ASS 轨迹字幕》设计 §4：OCR 只在清晰关键帧上做，
运动模糊帧的碎片读法因此自然消失，位置则由逐帧单应跟踪提供。

- 清晰度分数：把该帧按轨迹保存的逆单应展开回关键帧平面
  （:func:`scene_plane_tracker.unwarp_canonical`，统一坐标、跨帧可比），
  灰度后取 Laplacian 方差。
- 只考虑 ``status=="ok"`` 的帧：lost 帧无单应、无证据，永不入选
  （与 tracker「失败不外推」同一哲学）。
- 视频按顺序解码一遍（不随机 seek），帧号与 ``TrackedQuad.frame_num``
  逐一对应（轨迹起始帧号可以非 0）。
- ``min_gap_sec > 0`` 时贪心时间分散：按分数降序遍历，与已选帧时间过近
  的跳过；分数并列取更早帧；ok 帧不足 k 时全部返回；没有 ok 帧返回空列表。

本模块不 import PySide6，便于 CLI 脚本与离线单测复用。
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

from core.scene_plane_tracker import TrackedQuad, unwarp_canonical

logger = logging.getLogger(__name__)

MIN_PLANE_SIZE_PX = 8  # 推导平面尺寸的下限，防止退化 quad 产生 0×0 展开图


def _plane_size_from_quad(quad: List[List[float]]) -> Tuple[int, int]:
    """由 quad 推导展开平面尺寸：w=上下两边均值、h=左右两边均值。

    四舍五入取整，每维最小 :data:`MIN_PLANE_SIZE_PX`。
    """
    pts = np.asarray(quad, dtype=np.float64).reshape(-1, 2)
    top = float(np.hypot(*(pts[1] - pts[0])))
    bottom = float(np.hypot(*(pts[2] - pts[3])))
    left = float(np.hypot(*(pts[3] - pts[0])))
    right = float(np.hypot(*(pts[2] - pts[1])))
    w = max(MIN_PLANE_SIZE_PX, int(round((top + bottom) / 2.0)))
    h = max(MIN_PLANE_SIZE_PX, int(round((left + right) / 2.0)))
    return w, h


def _sharpness_score(unwarped_bgr: np.ndarray) -> float:
    """清晰度分数：灰度图 Laplacian 方差（聚焦/模糊的经典无参考度量）。"""
    gray = cv2.cvtColor(unwarped_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def select_keyframes(
    video_path: str,
    tracks: List[TrackedQuad],
    k: int = 3,
    min_gap_sec: float = 0.0,
    plane_size: Optional[Tuple[int, int]] = None,
) -> List[int]:
    """从轨迹的 ok 帧中选出最清晰的 k 帧。

    Args:
        video_path: 视频路径；打不开时抛 :class:`RuntimeError`（与 tracker 一致）。
        tracks: :func:`scene_plane_tracker.track_plane` 产出的逐帧轨迹。
        k: 目标关键帧数；ok 帧不足时全部返回。
        min_gap_sec: 选中帧两两最小时间差（秒）；0 表示不做时间分散。
        plane_size: 展开平面尺寸 (w, h)；缺省由第一个 ok 帧的 quad 推导。

    Returns:
        选中帧的 ``frame_num`` 列表，按清晰度分数降序；无 ok 帧返回空列表。
    """
    if k <= 0:
        return []
    ok_tracks = [t for t in tracks if t.status == "ok"]
    if not ok_tracks:
        return []
    if plane_size is None:
        plane_size = _plane_size_from_quad(ok_tracks[0].quad)
    size = (int(plane_size[0]), int(plane_size[1]))

    by_frame = {t.frame_num: t for t in ok_tracks}
    time_of = {t.frame_num: t.time_sec for t in ok_tracks}

    # 顺序解码一遍并逐 ok 帧评分（不随机 seek，保持与跟踪时一致的时间轴）。
    scores: List[Tuple[float, int]] = []  # (清晰度分数, frame_num)
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
            if track is not None and track.homography_inv is not None:
                unwarped = unwarp_canonical(frame, track.homography_inv, size)
                scores.append((_sharpness_score(unwarped), track.frame_num))
            frame_idx += 1
    finally:
        cap.release()

    if not scores:
        return []

    # 分数降序；并列取更早帧（frame_num 升序）。
    ranked = sorted(scores, key=lambda item: (-item[0], item[1]))

    # top-k 选取；min_gap_sec>0 时贪心跳过与已选帧时间过近的候选。
    picked: List[int] = []
    for _score, frame_num in ranked:
        if len(picked) >= k:
            break
        if min_gap_sec > 0:
            too_close = any(
                abs(time_of[frame_num] - time_of[chosen]) < min_gap_sec
                for chosen in picked
            )
            if too_close:
                continue
        picked.append(frame_num)
    return picked
