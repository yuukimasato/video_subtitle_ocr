# core/motion_detector.py
"""移动文字自动检测(FR-1):采样 OCR 行心位移超阈值 → 触发轨迹管线。

需求口径(《移动文字轨迹 → ASS 轨迹字幕》FR-1):**采样 OCR 行心位移超
阈值触发密集(轨迹)模式**;检测器只负责产出「quad + 时间范围」,消费方
(项目 CLI ``--motion-auto`` / 主流水线 worker)拿它走既有的
``scripts.motion_ass.build_motion_events`` 轨迹链路,与手动 quad 完全同路。

算法(:func:`detect_moving_text`):

1. 以 ``sample_stride_sec`` 为步长在 ``[start_frame, end_frame]`` 内采样帧,
   顺序解码(不随机 seek),``ocr_fn`` 识别采样帧(可裁剪到 ``region``)
   得行框与文本;
2. 同文本(去空白后全等)的行心按时间串成轨迹,行心跳变超过
   ``match_px``(缺省 ``max(48, 2.5×行高)``)处断链——同文本异位(两块
   招牌同词)不会串;
3. 链内样本数 ≥ ``min_samples`` 且**最大行心位移** > ``move_thresh_px``
   (与主流水线移动门限同源,默认 24px)、时长 ≥ ``min_duration_sec`` →
   判为移动文字;
4. quad = 该链全部行框角点的最小外接矩形(``cv2.minAreaRect``)外扩
   0.5×行高后的四角(任意旋向,由调用方 ``normalize_quad_winding`` 归一);
   时间范围 = 链首末采样帧各外扩 ``pad_sec``;
5. 相邻重叠区域(同屏多行同动)合并为一个块级区域,供跟踪获得更多特征。

检测 OCR 的成本与采样密度成正比(默认 0.5s 一帧全区域识别);长视频建议
用 ``--roi``/GUI ROI 圈定检测范围。本模块不依赖 PySide6。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MotionRegion",
    "detect_moving_text",
]

OcrFn = Callable[[object], Dict]


@dataclass
class MotionRegion:
    """检测出的移动文字区域(消费方输入,quad 旋向未归一)。"""

    quad: List[List[float]]           # 画面坐标四角(minAreaRect 外扩)
    start_frame: int
    end_frame: int
    max_displacement_px: float
    sample_count: int
    texts: List[str] = field(default_factory=list)

    def bbox(self) -> Tuple[float, float, float, float]:
        xs = [p[0] for p in self.quad]
        ys = [p[1] for p in self.quad]
        return min(xs), min(ys), max(xs), max(ys)


def _rows_from_ocr(ocr_data: Dict) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """统一 OCR dict → [(text, (x1, y1, x2, y2))](优先 rec_polys 外接框)。"""
    texts = ocr_data.get("rec_texts") or []
    polys = ocr_data.get("rec_polys") or []
    rec_boxes = ocr_data.get("rec_boxes") or []
    rows: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for i, text in enumerate(texts):
        box: Optional[Tuple[float, float, float, float]] = None
        if i < len(polys):
            try:
                pts = [(float(p[0]), float(p[1])) for p in polys[i]]
            except (TypeError, ValueError, IndexError):
                pts = []
            if len(pts) >= 3:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                box = (min(xs), min(ys), max(xs), max(ys))
        if box is None and i < len(rec_boxes):
            rb = rec_boxes[i]
            try:
                if rb is not None and len(rb) >= 4:
                    box = (float(rb[0]), float(rb[1]), float(rb[2]), float(rb[3]))
            except (TypeError, ValueError):
                box = None
        if box is None:
            continue
        rows.append((str(text), box))
    return rows


def _norm_text(text: str) -> str:
    return "".join(str(text).split())


def _union_bbox(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bbox_overlap(a: Tuple[float, float, float, float],
                  b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def detect_moving_text(
    video_path: str,
    *,
    ocr_fn: Optional[OcrFn] = None,
    engine_id: Optional[str] = None,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    sample_stride_sec: float = 0.5,
    move_thresh_px: float = 24.0,
    min_samples: int = 3,
    min_duration_sec: float = 0.0,
    pad_sec: float = 0.25,
    region: Optional[Tuple[int, int, int, int]] = None,
    match_px: Optional[float] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[MotionRegion]:
    """采样 OCR 行心位移检测移动文字,返回按起始帧排序的区域列表。

    ``region`` 为画面坐标裁剪窗 (x1, y1, x2, y2)(检测范围,通常是一个
    用户 ROI 的外接矩形);``ocr_fn`` 缺省用 ``engine_id`` 构造独立引擎。
    无样本/无移动链返回空列表;视频打不开抛 :class:`RuntimeError`。
    """
    import cv2

    if log is None:
        def log(message: str) -> None:
            logger.info("motion-detector: %s", message)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    owned_engine = None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        stride = max(1, int(round(max(0.01, float(sample_stride_sec)) * fps)))
        start = max(0, int(start_frame))
        last_frame: Optional[int] = None
        if end_frame is not None:
            last_frame = int(end_frame)
        elif cap.get(cv2.CAP_PROP_FRAME_COUNT) > 0:
            last_frame = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) - 1
        if last_frame is not None and last_frame < start:
            return []

        if ocr_fn is None:
            from core.ocr_engine_manager import build_standalone_engine

            owned_engine = build_standalone_engine(engine_id)

            def ocr_fn(img, _engine=owned_engine):
                return _engine.normalize_result(_engine.predict(img))

        x0 = y0 = 0
        if region is not None:
            # 裁剪起点钳在画面内(max(0, x1));偏移量必须用同一个钳后值,
            # 否则 region 越过画面左/上边缘时所有 OCR 框会被整体平移。
            x0, y0 = max(0, int(region[0])), max(0, int(region[1]))

        # 1) 采样 OCR:frame → [(text, box, center, height)](画面坐标)
        samples: List[Tuple[int, float, list]] = []
        frame_idx = 0
        while True:
            ok_flag, frame = cap.read()
            if not ok_flag or frame is None:
                break
            due = frame_idx >= start and frame_idx % stride == 0 and (
                last_frame is None or frame_idx <= last_frame)
            reached_end = last_frame is not None and frame_idx >= last_frame
            if due:
                h_lim, w_lim = frame.shape[:2]
                if region is not None:
                    x1, y1, x2, y2 = (int(v) for v in region)
                    crop = frame[max(0, y1):min(h_lim, y2),
                                 max(0, x1):min(w_lim, x2)]
                else:
                    crop = frame
                if crop.size:
                    data = ocr_fn(crop)
                    if isinstance(data, dict):
                        rows = _rows_from_ocr(data)
                        t_sec = frame_idx / fps
                        rows_out = []
                        for text, (bx1, by1, bx2, by2) in rows:
                            box = (bx1 + x0, by1 + y0, bx2 + x0, by2 + y0)
                            center = ((box[0] + box[2]) / 2.0,
                                      (box[1] + box[3]) / 2.0)
                            rows_out.append(
                                (text, box, center, max(1.0, box[3] - box[1])))
                        if rows_out:
                            samples.append((frame_idx, t_sec, rows_out))
            if reached_end:
                break
            frame_idx += 1
    finally:
        cap.release()
        if owned_engine is not None:
            try:
                owned_engine.cleanup()
            except Exception:
                pass

    if len(samples) < 2:
        return []

    # 2) 同文本串链(行心跳变超 match_px 断链)
    by_text: Dict[str, list] = {}
    for frame_num, t_sec, rows in samples:
        for text, box, center, height in rows:
            by_text.setdefault(_norm_text(text), []).append(
                (frame_num, t_sec, box, center, height, text))

    def match_for(group: Sequence[tuple]) -> float:
        if match_px is not None:
            return float(match_px)
        heights = sorted(g[4] for g in group)
        med_h = heights[len(heights) // 2]
        return max(48.0, 2.5 * med_h)

    runs: List[dict] = []
    for key, group in by_text.items():
        if not key:
            continue
        group.sort(key=lambda g: g[0])
        m = match_for(group)
        run: List[tuple] = [group[0]]
        for item in group[1:]:
            prev = run[-1]
            dist = math.hypot(item[3][0] - prev[3][0],
                              item[3][1] - prev[3][1])
            if dist > m:
                runs.append(_run_dict(key, run))
                run = [item]
            else:
                run.append(item)
        runs.append(_run_dict(key, run))

    # 3) 位移/时长门限 → 区域
    regions: List[MotionRegion] = []
    for run in runs:
        items = run["items"]
        if len(items) < max(2, int(min_samples)):
            continue
        centers = [it[3] for it in items]
        disp = max(
            math.hypot(a[0] - b[0], a[1] - b[1])
            for a in centers for b in centers)
        if disp <= float(move_thresh_px):
            continue
        duration = items[-1][1] - items[0][1]
        if duration < float(min_duration_sec):
            continue
        points: List[List[float]] = []
        for _f, _t, box, _c, _h, _text in items:
            points.extend([[box[0], box[1]], [box[2], box[1]],
                           [box[2], box[3]], [box[0], box[3]]])
        heights = sorted(it[4] for it in items)
        med_h = heights[len(heights) // 2]
        pad = 0.5 * med_h
        quad = _padded_min_area_rect(points, pad)
        if quad is None:
            continue
        pad_frames = int(round(float(pad_sec) * fps))
        end_cap = (last_frame if last_frame is not None
                   else items[-1][0] + pad_frames)
        regions.append(MotionRegion(
            quad=quad,
            start_frame=max(start, items[0][0] - pad_frames),
            end_frame=min(items[-1][0] + pad_frames, end_cap),
            max_displacement_px=disp,
            sample_count=len(items),
            texts=[it[5] for it in items],
        ))

    if not regions:
        return []
    regions.sort(key=lambda r: r.start_frame)

    # 4) 重叠区域合并(同屏多行同动 → 一个块级平面)
    merged: List[MotionRegion] = []
    for r in regions:
        placed = False
        for m in merged:
            grow = 0.2 * (r.bbox()[3] - r.bbox()[1]
                          + m.bbox()[3] - m.bbox()[1]) / 2.0
            if _bbox_overlap(
                    _expand(r.bbox(), grow), _expand(m.bbox(), grow)):
                m.quad = _padded_min_area_rect(
                    [p for q in (r.quad, m.quad) for p in q], 0.0) or m.quad
                m.start_frame = min(m.start_frame, r.start_frame)
                m.end_frame = max(m.end_frame, r.end_frame)
                m.max_displacement_px = max(m.max_displacement_px,
                                            r.max_displacement_px)
                m.sample_count += r.sample_count
                for t in r.texts:
                    if t not in m.texts:
                        m.texts.append(t)
                placed = True
                break
        if not placed:
            merged.append(r)

    if log is not None:
        log(
            f"detected {len(merged)} moving region(s): "
            + "; ".join(
                f"frames [{r.start_frame}, {r.end_frame}] "
                f"disp {r.max_displacement_px:.0f}px ({', '.join(r.texts[:3])}"
                f"{'…' if len(r.texts) > 3 else ''})"
                for r in merged))
    return merged


def _run_dict(key: str, items: Sequence[tuple]) -> dict:
    return {"key": key, "items": list(items)}


def _expand(
    box: Tuple[float, float, float, float], pad: float,
) -> Tuple[float, float, float, float]:
    return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)


def _padded_min_area_rect(
    points: Sequence[Sequence[float]], pad: float,
) -> Optional[List[List[float]]]:
    """点集最小外接矩形四角(外扩 pad px);退化返回 None。"""
    import cv2

    import numpy as np

    arr = np.asarray(points, dtype=np.float32)
    if len(arr) < 3:
        return None
    rect = cv2.minAreaRect(arr)
    (cx, cy), (w, h), _angle = rect
    w = max(1.0, float(w) + 2.0 * float(pad))
    h = max(1.0, float(h) + 2.0 * float(pad))
    box = cv2.boxPoints(((float(cx), float(cy)), (w, h), _angle))
    return [[float(x), float(y)] for x, y in box]
