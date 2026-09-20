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
- ``ensure_coverage=True`` 时在清晰度 top-K 之后追加**时间覆盖**代表帧
  （见 :func:`select_keyframes`）：纯清晰度选取不保证覆盖 ok 帧的时间跨度
  ——清晰度集中在某一段（空白引导段/静态画面/虚焦段）时，池可能全部落在
  那一段里，后段明明有文字却一帧候选都没有。追加帧只扩池、不动前缀。

本模块不 import PySide6，便于 CLI 脚本与离线单测复用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.scene_plane_tracker import TrackedQuad, unwarp_canonical

logger = logging.getLogger(__name__)

MIN_PLANE_SIZE_PX = 8  # 推导平面尺寸的下限，防止退化 quad 产生 0×0 展开图

# 覆盖开启时池总长缺省上限 = COVERAGE_POOL_FACTOR * k（见 select_keyframes）。
# 有界的意义：池里每多一帧就多一次展开图 OCR 的代价（最坏多 decode 到该帧 +
# 1 次 OCR；实际到「某个含文字批」即停），长视频（ok 帧上万）不能无节制扩池。
COVERAGE_POOL_FACTOR = 2


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


def _bucket_index(time_sec: float, t_min: float, span: float, buckets: int) -> int:
    """时间 → 桶号 ``[0, buckets)``（时间跨度 ``span`` 等分 ``buckets`` 份）。

    ``span <= 0``（ok 帧全在同一时刻，如单帧轨迹）退化为桶 0；末帧
    ``time_sec == t_min + span`` 时浮点除法可能得到 ``buckets``，上界钳到
    ``buckets - 1``。任何输入都返回合法桶号：不抛错、不越界。
    """
    if span <= 0.0:
        return 0
    index = int((float(time_sec) - t_min) / span * buckets)
    if index < 0:
        return 0
    return buckets - 1 if index >= buckets else index


def _coverage_representatives(
    scored: List[Tuple[float, int]],
    time_of: Dict[int, float],
    picked: List[int],
    buckets: int,
    limit: int,
) -> Tuple[List[int], int]:
    """时间分桶补齐：为每个「尚无代表帧」的桶追加桶内最清晰帧。

    桶跨度 = ``time_of``（ok 帧）时间跨度 ``[t_min, t_max]`` 等分 ``buckets``
    份。已有代表帧的桶（``picked`` 里任一帧落在该桶）跳过；桶内无候选帧
    （该桶的 ok 帧都没解码成功）同样跳过——两类退化都只跳过，不抛错、
    不死循环。追加顺序 = 桶号升序（近似时间升序），最多 ``limit`` 帧
    （池总长上界，由调用方按剩余额度传入）。

    Returns:
        ``(追加帧号列表, 池中有代表帧的桶数)``。
    """
    if buckets <= 0 or limit <= 0 or not time_of:
        return [], 0
    t_min = min(time_of.values())
    t_max = max(time_of.values())
    span = t_max - t_min
    bucket_of = {
        frame_num: _bucket_index(time_sec, t_min, span, buckets)
        for frame_num, time_sec in time_of.items()
    }
    occupied = {bucket_of[frame_num] for frame_num in picked if frame_num in bucket_of}
    by_bucket: Dict[int, List[Tuple[float, int]]] = {}
    for score, frame_num in scored:
        by_bucket.setdefault(bucket_of[frame_num], []).append((score, frame_num))
    appended: List[int] = []
    for bucket in range(buckets):
        if len(appended) >= limit:  # 池总长上界：不再追加（前缀永不被截断）
            break
        if bucket in occupied or bucket not in by_bucket:
            continue
        # 桶内最清晰；并列取更早帧（与主排序同一口径）。
        _score, frame_num = min(by_bucket[bucket],
                                key=lambda item: (-item[0], item[1]))
        appended.append(frame_num)
        occupied.add(bucket)
    return appended, len(occupied)


def select_keyframes(
    video_path: str,
    tracks: List[TrackedQuad],
    k: int = 3,
    min_gap_sec: float = 0.0,
    plane_size: Optional[Tuple[int, int]] = None,
    ensure_coverage: bool = False,
    coverage_buckets: Optional[int] = None,
    max_pool_size: Optional[int] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[int]:
    """从轨迹的 ok 帧中选出最清晰的 k 帧（可选追加时间覆盖代表帧）。

    Args:
        video_path: 视频路径；打不开时抛 :class:`RuntimeError`（与 tracker 一致）。
        tracks: :func:`scene_plane_tracker.track_plane` 产出的逐帧轨迹。
        k: 目标关键帧数；ok 帧不足时全部返回。
        min_gap_sec: 选中帧两两最小时间差（秒）；0 表示不做时间分散。
        plane_size: 展开平面尺寸 (w, h)；缺省由第一个 ok 帧的 quad 推导。
        ensure_coverage: 是否在清晰度 top-K 之后**补齐时间覆盖**：把 ok 帧的
            时间跨度等分成 ``coverage_buckets``（缺省 ``k``）个桶，为每个尚无
            代表帧的桶追加桶内最清晰帧。默认关闭——关闭时返回值与历史版本
            逐位相同。
        coverage_buckets: 时间桶数；``None`` = ``k``。``<= 0`` 视作不补齐
            （退化为只返回前缀）。帧数少于桶数时空桶直接跳过（确定性行为，
            不抛错）。
        max_pool_size: 池总长上限（仅 ``ensure_coverage`` 时生效）；``None`` =
            ``COVERAGE_POOL_FACTOR * k``。上限 ≤ 前缀长度时不追加任何帧——
            前缀永不被截断，覆盖也绝不减少既有候选。
        diagnostics: 可选 dict；提供时写入本次选取的实据（``scores`` 候选帧
            清晰度分数、``times`` 候选帧时间、``prefix_len`` / ``prefix``
            覆盖前的前缀、``pool`` 最终池、``coverage`` 覆盖情况），供调用方
            留痕排查。

    Returns:
        选中帧的 ``frame_num`` 列表。关闭覆盖时 = 清晰度分数降序的 top-K；
        开启覆盖时 = **同一前缀** + 覆盖代表帧（桶号升序，追加在末尾）；
        无 ok 帧返回空列表。

    覆盖的代价（有界）：选桶代表帧本身不需要额外解码（打分已顺序扫过全片），
    代价在下游——每次分批 OCR 每多一帧多一次展开图 OCR，最坏把池耗尽 =
    ``len(pool) - len(prefix)`` 次额外 OCR；实际一旦某批命中文字即停止，
    故覆盖帧只在「原池一帧文字都没有」时才被识别（这正是本能力的目标场景）。
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

    prefix_len = len(picked)  # 覆盖只追加、不改动前缀（分批 OCR 首个有文字批不变）
    buckets = 0
    limit = 0
    appended: List[int] = []
    covered_buckets = 0
    if ensure_coverage:
        buckets = k if coverage_buckets is None else int(coverage_buckets)
        limit = (COVERAGE_POOL_FACTOR * k if max_pool_size is None
                 else int(max_pool_size))
        appended, covered_buckets = _coverage_representatives(
            scores, time_of, picked, buckets, max(0, limit - prefix_len))
        picked = picked + appended

    if diagnostics is not None:
        times = list(time_of.values())
        diagnostics.update({
            "scores": {int(f): float(s) for s, f in scores},
            "times": {int(f): float(t) for f, t in time_of.items()},
            "prefix_len": prefix_len,
            "prefix": [int(f) for f in picked[:prefix_len]],
            "pool": [int(f) for f in picked],
            "coverage": {
                "enabled": bool(ensure_coverage),
                "buckets": buckets,
                "limit": limit,
                "added": [int(f) for f in appended],
                "covered_buckets": covered_buckets,
                "span_sec": ([min(times), max(times)] if times else None),
            },
        })
    return picked
