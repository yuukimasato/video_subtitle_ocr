# core/subtitle_roi_suggester.py
"""自动字幕区域定位：采样帧全画幅 OCR → 底部文本行聚类 → 生成候选 ROI。

Backlog 项「自动字幕区域定位」的实现：对视频均匀采样若干帧做全画幅 OCR，
保留位于画面下部（``bottom_ratio``）且置信度达标的文本行，按“时间相邻 +
垂直带相近”聚类成段，每段输出一个与 ``main_window._create_roi_entry_from_ui``
结构完全一致的 rect ROI（可直接加入 ROI 列表供用户微调）。

.. warning::
    调用本函数会切换全局 OCR 引擎（内部通过
    ``core.ocr_engine_manager.set_engine(ocr_engine_id, {...})`` 选择引擎，
    进程级单例会被替换/重建）。调用方若依赖先前的引擎状态，需要自行重新
    ``set_engine``。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core import ocr_engine_manager
from utils.time_utils import format_time

logger = logging.getLogger(__name__)

# ROI bbox 外扩参数：x 方向固定像素；y 方向按段内平均行高的比例。
X_PAD_PX = 16
Y_PAD_HEIGHT_RATIO = 0.6

# 聚类参数：相邻采样点之间，两行文本的垂直带中心差 <= 行高/2 视为同一条字幕线。
BAND_CENTER_TOLERANCE_RATIO = 0.5

# 倾斜字幕判定：聚类带长边相对最近坐标轴的偏角超过该阈值时，自动 ROI 输出
# 4 点多边形（旋转矩形）而非轴对齐矩形。
POLY_TILT_THRESHOLD_DEG = 4.0

# 判定"倾斜带"所需的倾斜行占比下限（其余视为混入的非带内容）。
POLY_TILT_MAJORITY_RATIO = 0.6
# 倾斜行之间允许的最大方向差（度，含 180° 环绕）；超出视为方向不一致，
# 退回轴对齐矩形，避免单一旋转矩形无法覆盖整条带。
POLY_TILT_CONSISTENCY_TOL_DEG = 2.0 * POLY_TILT_THRESHOLD_DEG

ProgressCB = Callable[[int, int], None]


def _uniform_sample_indices(total_frames: int, sample_count: int) -> List[int]:
    """在整个时间范围内均匀采样（首尾都取），返回去重后的升序帧号列表。"""
    total_frames = int(total_frames)
    n = max(1, int(sample_count))
    if total_frames <= 0:
        return []
    if n == 1:
        return [0]
    if n >= total_frames:
        return list(range(total_frames))
    last = total_frames - 1
    indices: List[int] = []
    for i in range(n):
        idx = int(round(i * last / (n - 1)))
        if not indices or idx != indices[-1]:
            indices.append(idx)
    return indices


def _bbox_of_line(rec_box: Any, rec_poly: Any) -> Optional[Tuple[float, float, float, float]]:
    """从统一 OCR 结果的 rec_boxes/rec_polys 提取 (x1, y1, x2, y2)。"""
    try:
        if rec_box is not None:
            seq = [float(v) for v in list(rec_box)]
            if len(seq) == 4:
                x1, y1, x2, y2 = seq
                if x2 < x1:
                    x1, x2 = x2, x1
                if y2 < y1:
                    y1, y2 = y2, y1
                return (x1, y1, x2, y2)
        if rec_poly is not None:
            pts = [p for p in list(rec_poly) if p is not None]
            if len(pts) >= 2:
                xs: List[float] = []
                ys: List[float] = []
                for p in pts:
                    sub = [float(v) for v in list(p)]
                    if len(sub) >= 2:
                        xs.append(sub[0])
                        ys.append(sub[1])
                if xs and ys:
                    return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, ValueError):
        return None
    return None


def _quad_of_line(rec_poly: Any, bbox: Optional[Tuple[float, float, float, float]]) -> List[List[float]]:
    """取文本行的原始四边形顶点（rec_polys）；无多边形时退化为 bbox 四角。

    保留 quad 而不是只用 bbox，是倾斜字幕带能合成旋转 ROI 的前提。
    """
    try:
        if rec_poly is not None:
            pts: List[List[float]] = []
            for p in list(rec_poly):
                if p is None:
                    continue
                sub = [float(v) for v in list(p)]
                if len(sub) >= 2:
                    pts.append([sub[0], sub[1]])
            if len(pts) >= 3:
                return pts
    except (TypeError, ValueError):
        pass
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return []


def _collect_frame_hits(
    engine,
    frame,
    frame_idx: int,
    sample_pos: int,
    min_text_score: float,
    bottom_start_y: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """对单帧跑全画幅 OCR，返回通过过滤条件的文本行命中列表。

    ``bottom_start_y`` 为 ``None`` 时不过滤位置（全画幅命中）；
    传入数值时只保留中心 y 位于该值之下的行（旧行为）。
    任何 OCR 异常/空结果都按“该帧无字幕”处理（返回空列表），不中断整体流程。
    """
    try:
        raw = engine.predict(frame)
        result = engine.normalize_result(raw)
    except Exception:
        logger.debug("Subtitle suggestion: OCR failed on frame %s; treated as no subtitle.", frame_idx, exc_info=True)
        return []
    if not isinstance(result, dict):
        return []

    texts = result.get("rec_texts", []) or []
    scores = result.get("rec_scores", []) or []
    polys = result.get("rec_polys", []) or []
    boxes = result.get("rec_boxes", []) or []

    hits: List[Dict[str, Any]] = []
    for i, _text in enumerate(texts):
        try:
            score = float(scores[i]) if i < len(scores) else 0.0
        except (TypeError, ValueError):
            score = 0.0
        if score < min_text_score:
            continue
        bbox = _bbox_of_line(
            boxes[i] if i < len(boxes) else None,
            polys[i] if i < len(polys) else None,
        )
        if bbox is None:
            continue
        x1, y1, x2, y2 = bbox
        center_y = (y1 + y2) / 2.0
        # 只保留位于画面下部 bottom_ratio 区域的行（bottom_start_y 为 None 时不过滤）。
        if bottom_start_y is not None and center_y < bottom_start_y:
            continue
        hits.append(
            {
                "sample_pos": sample_pos,
                "frame_idx": int(frame_idx),
                "bbox": (x1, y1, x2, y2),
                "quad": _quad_of_line(
                    polys[i] if i < len(polys) else None,
                    (x1, y1, x2, y2),
                ),
                "height": max(1.0, y2 - y1),
                "center_y": center_y,
                "text": str(texts[i]) if i < len(texts) else "",
            }
        )
    return hits


def _cluster_hits_into_segments(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把命中行按“时间相邻 + 垂直带相近”聚类成段。

    - 时间相邻：只有与前一个采样点（sample_pos 严格 +1）衔接的命中才能并入
      现有段；相邻采样点之间的间隙不插值（该采样点无命中即断段）。
    - 垂直带相近：带中心差 <= 行高/2（行高取段尾命中与新命中的平均高度）。
    - 每段每个采样点最多吸收一条命中（多条命中行各自成段）。
    """
    segments: List[Dict[str, Any]] = []
    open_segments: List[Dict[str, Any]] = []

    for hit in sorted(hits, key=lambda h: (h["sample_pos"], h["center_y"])):
        pos = hit["sample_pos"]
        # 可续接性由 last_sample_pos == pos-1 精确约束：采样点序列中一旦
        # 出现无命中的间隙，旧段自然失效（间隙不插值），循环结束后统一闭合。

        matched = None
        best_distance = float("inf")
        for seg in open_segments:
            if seg["last_sample_pos"] != pos - 1:
                continue
            ref_height = (seg["last_height"] + hit["height"]) / 2.0
            if abs(hit["center_y"] - seg["last_center_y"]) <= ref_height * BAND_CENTER_TOLERANCE_RATIO:
                distance = abs(hit["center_y"] - seg["last_center_y"])
                if distance < best_distance:
                    matched = seg
                    best_distance = distance

        if matched is None:
            open_segments.append(
                {
                    "hits": [hit],
                    "last_sample_pos": pos,
                    "last_center_y": hit["center_y"],
                    "last_height": hit["height"],
                }
            )
        else:
            matched["hits"].append(hit)
            matched["last_sample_pos"] = pos
            matched["last_center_y"] = hit["center_y"]
            matched["last_height"] = hit["height"]

    segments.extend(open_segments)
    return segments


def _long_edge_tilt_deg(quad: Sequence[Sequence[float]]) -> Optional[float]:
    """单个文本行四边形的长边相对最近坐标轴的偏角（度，(-90, 90]）。

    与 OpenCV 旋转矩形的角度约定无关；无法计算时返回 None。
    """
    pts = np.asarray(quad, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        return None
    e1 = pts[1] - pts[0]
    e2 = pts[2] - pts[1]
    edge = e1 if (e1[0] ** 2 + e1[1] ** 2) >= (e2[0] ** 2 + e2[1] ** 2) else e2
    if edge[0] == 0 and edge[1] == 0:
        return None
    tilt = math.degrees(math.atan2(edge[1], edge[0]))
    if tilt > 90.0:
        tilt -= 180.0
    elif tilt <= -90.0:
        tilt += 180.0
    return tilt


def _axis_deviation_deg(tilt_deg: float) -> float:
    """偏角相对最近坐标轴（水平或垂直）的偏离量，[0, 45]。"""
    return min(abs(tilt_deg), 90.0 - abs(tilt_deg))


def _angle_spread_deg(a: float, b: float) -> float:
    """两个方向的差值（0~90]，含 180° 环绕（89° 与 -89° 实为同一垂直朝向）。"""
    d = abs(a - b)
    return min(d, 180.0 - d)


def cluster_poly_points_if_tilted(
    quad_points: Sequence[Sequence[float]],
    frame_width: int,
    frame_height: int,
    avg_height: float,
) -> Optional[List[List[int]]]:
    """把聚类内全部文本行 quad 点合并成倾斜带；仅当明显倾斜时返回多边形。

    "倾斜带"按**逐文本行**判定：对每个 quad 求其自身长边相对最近坐标轴的
    偏角，当超过 ``POLY_TILT_THRESHOLD_DEG`` 的行占比达
    ``POLY_TILT_MAJORITY_RATIO`` 且这些行的倾斜方向一致
    （最大两两差 ≤ ``POLY_TILT_CONSISTENCY_TOL_DEG``，含 180° 环绕）时，
    以这些倾斜行的点求最小外接旋转矩形，沿长边外扩 ``X_PAD_PX``、短边外扩
    ``y_pad`` 后返回 4 个顶点（逐点钳制到画面内）；否则返回 ``None``
    （调用方维持原轴对齐 rect 路径）。

    .. note::
        不能用全部点的最小外接矩形朝向判定倾斜：底部字幕带内各句文本的
        位置/宽度随时间变化，点云整体可能呈对角分布，令水平字幕带被误判为
        倾斜带并输出对角多边形，OCR 只能覆盖字幕的局部（截断文本）。
    """
    try:
        pts = np.array(quad_points, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
            return None
        # 每 4 个点为一条文本行 quad（_quad_of_line 恒返回 4 点）。
        line_quads = [pts[i:i + 4] for i in range(0, pts.shape[0] - 3, 4)]
        if not line_quads:
            return None
        quad_tilts = [(q, _long_edge_tilt_deg(q)) for q in line_quads]
        measured = [(q, t) for q, t in quad_tilts if t is not None]
        if not measured:
            return None
        tilted_pairs = [(q, t) for q, t in measured
                        if _axis_deviation_deg(t) > POLY_TILT_THRESHOLD_DEG]
        if len(tilted_pairs) < max(1, math.ceil(
                POLY_TILT_MAJORITY_RATIO * len(measured))):
            return None
        base_tilt = tilted_pairs[0][1]
        if any(_angle_spread_deg(t, base_tilt) > POLY_TILT_CONSISTENCY_TOL_DEG
               for _, t in tilted_pairs[1:]):
            return None

        # 仅用倾斜行求外接矩形：混入的非倾斜行不属于该带，纳入会把矩形
        # 拉偏、重新引入截断风险。
        tilted_points = np.concatenate([q for q, _ in tilted_pairs])
        rect = cv2.minAreaRect(tilted_points)
        (cx, cy), (rw, rh), angle = rect
        if rw <= 0 or rh <= 0:
            return None
        box = cv2.boxPoints(rect)
        e1 = box[1] - box[0]
        e2 = box[2] - box[1]
        edge = e1 if (e1[0] ** 2 + e1[1] ** 2) >= (e2[0] ** 2 + e2[1] ** 2) else e2
        tilt = math.degrees(math.atan2(edge[1], edge[0]))
        if tilt > 90.0:
            tilt -= 180.0
        elif tilt <= -90.0:
            tilt += 180.0
        deviation = min(abs(tilt), 90.0 - abs(tilt))
        if deviation <= POLY_TILT_THRESHOLD_DEG:
            return None
        y_pad = avg_height * Y_PAD_HEIGHT_RATIO
        long_side = max(rw, rh) + 2.0 * X_PAD_PX
        short_side = min(rw, rh) + 2.0 * y_pad
        padded = ((cx, cy), (long_side, short_side), angle)
        poly = cv2.boxPoints(padded)
        return [
            [
                int(round(min(max(float(px), 0.0), float(frame_width) - 1.0))),
                int(round(min(max(float(py), 0.0), float(frame_height) - 1.0))),
            ]
            for px, py in poly
        ]
    except Exception:
        logger.debug("cluster tilt merge failed; falling back to rect", exc_info=True)
        return None


def _segment_to_roi_entry(
    segment: Dict[str, Any],
    fps: float,
    frame_width: int,
    frame_height: int,
) -> Dict[str, Any]:
    """把段转换为与 main_window._create_roi_entry_from_ui 一致的 ROI dict。

    轴对齐字幕带输出 ``type='rect'``（points=[x, y, w, h]，与历史行为一致）；
    明显倾斜的带输出 ``type='poly'``（4 点旋转矩形，points=[[x, y], ...]）。
    """
    hits = segment["hits"]
    x1 = min(h["bbox"][0] for h in hits)
    y1 = min(h["bbox"][1] for h in hits)
    x2 = max(h["bbox"][2] for h in hits)
    y2 = max(h["bbox"][3] for h in hits)
    avg_height = sum(h["height"] for h in hits) / len(hits)

    y_pad = avg_height * Y_PAD_HEIGHT_RATIO
    roi_x1 = max(0, int(round(x1 - X_PAD_PX)))
    roi_y1 = max(0, int(round(y1 - y_pad)))
    roi_x2 = min(frame_width, int(round(x2 + X_PAD_PX)))
    roi_y2 = min(frame_height, int(round(y2 + y_pad)))
    roi_w = max(1, roi_x2 - roi_x1)
    roi_h = max(1, roi_y2 - roi_y1)

    start_frame = min(h["frame_idx"] for h in hits)
    end_frame = max(h["frame_idx"] for h in hits)

    quads = [q for h in hits for q in (h.get("quad") or [])]
    poly_points = cluster_poly_points_if_tilted(quads, frame_width, frame_height, avg_height)
    if poly_points is not None:
        roi_type, points = "poly", poly_points
    else:
        roi_type, points = "rect", [roi_x1, roi_y1, roi_w, roi_h]

    return {
        "start_time": format_time(start_frame / fps),
        "end_time": format_time(end_frame / fps),
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "type": roi_type,
        "points": points,
        # 自动建议的字幕带默认做帧级边界精修（淡入淡出首尾逐帧复核）。
        "fade_in_refine_enabled": True,
    }


def suggest_subtitle_rois(
    video_path: str,
    *,
    sample_count: int = 12,
    lang: str = "ch",
    model_tier: str = "auto",
    ocr_engine_id: str = "paddle",
    min_text_score: float = 0.6,
    bottom_ratio: float = 0.45,
    progress_cb: Optional[ProgressCB] = None,
) -> List[Dict[str, Any]]:
    """对视频采样做全画幅 OCR，自动建议底部字幕区域的 ROI 列表。

    .. note::
        调用本函数会切换全局 OCR 引擎（``core.ocr_engine_manager.set_engine``）。

    Args:
        video_path: 视频文件路径。
        sample_count: 在整个时间范围内均匀采样的帧数（首尾都取）。
        lang: 传给 OCR 引擎的语言选项（如 "ch" / "en" / "japan"）。
        model_tier: 传给 OCR 引擎的模型档位（"auto" / "tiny" / "small" / "medium"）。
        ocr_engine_id: OCR 引擎 ID（如 "paddle" / "rapid"）。
        min_text_score: 文本行保留的最小识别置信度。
        bottom_ratio: 只保留中心 y 位于画面下部该比例区域内的行（0.45 = 下部 45%）。
        progress_cb: 进度回调 ``progress_cb(done, total)``，逐采样帧回调。

    Returns:
        ROI dict 列表，结构与 ``main_window._create_roi_entry_from_ui`` 一致：
        ``{'start_time', 'end_time', 'start_frame', 'end_frame', 'type', 'points'}``。
        轴对齐字幕带 ``type='rect'``、``points=[x, y, w, h]``；倾斜字幕带
        ``type='poly'``、``points=[[x, y], ...]``（4 点旋转矩形）。
        视频无法打开/无有效帧时返回空列表。
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning("Subtitle suggestion: cannot open video: %s", video_path)
        return []

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or total_frames <= 0 or frame_width <= 0 or frame_height <= 0:
            logger.warning(
                "Subtitle suggestion: invalid video metadata (fps=%s, frames=%s, %sx%s).",
                fps, total_frames, frame_width, frame_height,
            )
            return []

        indices = _uniform_sample_indices(total_frames, sample_count)
        if not indices:
            return []

        # 全程只切换一次全局引擎（副作用见 docstring）。
        ocr_engine_manager.set_engine(ocr_engine_id, {"lang": lang, "model_tier": model_tier})
        engine = ocr_engine_manager.get_engine()

        bottom_start_y = frame_height * (1.0 - min(1.0, max(0.0, bottom_ratio)))
        hits: List[Dict[str, Any]] = []
        total = len(indices)
        for done, frame_idx in enumerate(indices, start=1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.debug("Subtitle suggestion: cannot decode frame %s; skipped.", frame_idx)
            else:
                hits.extend(
                    _collect_frame_hits(
                        engine,
                        frame,
                        frame_idx,
                        sample_pos=done - 1,
                        min_text_score=min_text_score,
                        bottom_start_y=bottom_start_y,
                    )
                )
            if progress_cb is not None:
                progress_cb(done, total)

        segments = _cluster_hits_into_segments(hits)
        return [
            _segment_to_roi_entry(seg, fps, frame_width, frame_height)
            for seg in sorted(segments, key=lambda s: min(h["frame_idx"] for h in s["hits"]))
        ]
    finally:
        cap.release()
