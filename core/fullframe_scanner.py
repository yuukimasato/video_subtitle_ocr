# core/fullframe_scanner.py
"""全片扫描普查：采样帧全画幅 OCR → 跨帧统计 → 字幕带 / 水印 / 场景文字分类。

“加载视频后快速自动 ROI”与“深度扫描复核”共用同一个扫描核心，仅采样数不同：

- 字幕带（band_rois）：文本随时间变化、位于顶部/底部安全区的行。同一区域
  （top/bottom）的所有时间段合并为一个跨度 [首命中帧, 末命中帧] 的带 ROI，
  并向外扩一个采样间隔——字幕头尾不再被采样粒度截断。
- 水印候选（watermarks）：归一化文本相同、坐标几乎恒定且出现率高的行
  （VSE 思路：恒定文本 + 恒定坐标）。水印命中不参与字幕带/场景文字聚类。
- 场景文字（scene_candidates）：其余零散文本，按空间网格聚类，可导出为
  ``text_filter_policy="keep_all"`` 的 ROI 候选（导入后用户可改画多边形精修）。

.. warning::
    调用 :func:`perform_scan` 会切换全局 OCR 引擎（内部通过
    ``core.ocr_engine_manager.set_engine`` 选择引擎，进程级单例会被替换/重建）。
    调用方若依赖先前的引擎状态，需要自行重新 ``set_engine``。
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2

from core import ocr_engine_manager
from core.subtitle_roi_suggester import (
    X_PAD_PX,
    Y_PAD_HEIGHT_RATIO,
    _cluster_hits_into_segments,
    _collect_frame_hits,
    _uniform_sample_indices,
    cluster_poly_points_if_tilted,
)
from utils.time_utils import format_time

logger = logging.getLogger(__name__)

ProgressCB = Callable[[int, int], None]

# 水印判定的空间网格与中心容差（像素）：中心量化到网格后同键聚合，
# 聚合后中心偏离均值超过容差的聚类不视为水印（坐标不恒定）。
WATERMARK_GRID_PX = 16
WATERMARK_CENTER_TOLERANCE_PX = 24.0

# 场景文字空间聚类的量化网格（像素）。
SCENE_GRID_PX = 64

# 场景候选合并：归一化文本一致且空间重叠（IoU 达标）或相邻（彼此外扩
# 长边的 SCENE_MERGE_EXPAND_RATIO 倍后相交）时视为同一实体文字。
# 手机/文档在相邻采样帧间位置略移，或同一文字跨 64px 网格被拆开时，
# 聚类会为同一封邮件产出多个候选；合并后用户精修的 ROI 列表不再重复。
SCENE_MERGE_IOU = 0.5
SCENE_MERGE_EXPAND_RATIO = 0.5

# 快照缩略图最大宽度（像素）。
SNAPSHOT_MAX_WIDTH = 320

Region = str  # "top" / "middle" / "bottom"


def normalize_text(text: str) -> str:
    """OCR 文本归一化：NFKC + 去空白 + 去常见标点 + 小写，用于跨帧文本比对。"""
    normalized = unicodedata.normalize("NFKC", str(text))
    stripped = "".join(
        ch for ch in normalized
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )
    return stripped.lower()


def _region_of(center_y: float, frame_height: float, top_ratio: float, bottom_ratio: float) -> Region:
    """按中心 y 把文本行划入顶部/中部/底部区域。"""
    if center_y < frame_height * top_ratio:
        return "top"
    if center_y > frame_height * (1.0 - bottom_ratio):
        return "bottom"
    return "middle"


def _quantized_center(bbox: Tuple[float, float, float, float], grid: int) -> Tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return (int((x1 + x2) / (2 * grid)), int((y1 + y2) / (2 * grid)))


@dataclass
class ScanReport:
    """一次全片扫描的普查结果。"""

    video_path: str
    fps: float
    frame_width: int
    frame_height: int
    total_frames: int
    sample_count: int
    sample_indices: List[int] = field(default_factory=list)
    # 富化 ROI dict（含 source/text_filter_policy/fade_in_refine_enabled）。
    band_rois: List[Dict[str, Any]] = field(default_factory=list)
    # {text, bbox, presence, first_frame, last_frame, snapshot_jpeg}
    watermarks: List[Dict[str, Any]] = field(default_factory=list)
    # {text, bbox, hit_count, first_frame, last_frame, snapshot_jpeg, roi_entry}
    scene_candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.band_rois or self.watermarks or self.scene_candidates)


def perform_scan(
    video_path: str,
    *,
    sample_count: int = 24,
    lang: str = "ch",
    model_tier: str = "auto",
    ocr_engine_id: str = "paddle",
    min_text_score: float = 0.6,
    top_ratio: float = 0.25,
    bottom_ratio: float = 0.45,
    watermark_presence_threshold: float = 0.6,
    band_range_pad_samples: int = 1,
    with_snapshots: bool = True,
    progress_cb: Optional[ProgressCB] = None,
) -> ScanReport:
    """对视频均匀采样做全画幅 OCR，输出字幕带 ROI、水印候选与场景文字候选。

    Args:
        video_path: 视频文件路径。
        sample_count: 均匀采样帧数（首尾都取）。快速自动 ROI 建议 12–16，
            深度扫描建议 24 以上。
        lang / model_tier / ocr_engine_id / min_text_score: 与
            ``suggest_subtitle_rois`` 含义一致。
        top_ratio / bottom_ratio: 顶部/底部安全区比例（中心 y 位于画面上部
            ``top_ratio`` 内划为顶部区，下部 ``bottom_ratio`` 内划为底部区，
            其余为中部场景文字区）。
        watermark_presence_threshold: 归一化文本出现率（占采样帧比例）达到该值
            且坐标恒定时判为水印候选。
        band_range_pad_samples: 带 ROI 时间范围向外扩的采样间隔数。
        with_snapshots: 是否为水印/场景文字候选截取快照（JPEG bytes）。
        progress_cb: 进度回调 ``progress_cb(done, total)``，逐采样帧回调。

    Returns:
        :class:`ScanReport`。视频无法打开/无有效帧时返回各列表为空的报告。
    """
    report = ScanReport(
        video_path=str(video_path), fps=0.0, frame_width=0, frame_height=0,
        total_frames=0, sample_count=int(max(1, sample_count)),
    )
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning("Fullframe scanner: cannot open video: %s", video_path)
        return report
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps <= 0 or total_frames <= 0 or frame_width <= 0 or frame_height <= 0:
            logger.warning(
                "Fullframe scanner: invalid video metadata (fps=%s, frames=%s, %sx%s).",
                fps, total_frames, frame_width, frame_height,
            )
            return report
        report.fps, report.total_frames = fps, total_frames
        report.frame_width, report.frame_height = frame_width, frame_height

        indices = _uniform_sample_indices(total_frames, report.sample_count)
        report.sample_indices = indices
        if not indices:
            return report

        # 全程只切换一次全局引擎（副作用见模块 docstring）。
        ocr_engine_manager.set_engine(ocr_engine_id, {"lang": lang, "model_tier": model_tier})
        engine = ocr_engine_manager.get_engine()

        hits: List[Dict[str, Any]] = []
        total = len(indices)
        for done, frame_idx in enumerate(indices, start=1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.debug("Fullframe scanner: cannot decode frame %s; skipped.", frame_idx)
            else:
                hits.extend(
                    _collect_frame_hits(
                        engine, frame, frame_idx, sample_pos=done - 1,
                        min_text_score=min_text_score,
                        bottom_start_y=None,
                    )
                )
            if progress_cb is not None:
                progress_cb(done, total)

        for hit in hits:
            hit["region"] = _region_of(hit["center_y"], frame_height, top_ratio, bottom_ratio)

        sample_interval = max(1, (indices[-1] - indices[0]) // max(1, total - 1)) if total > 1 else 1
        watermark_hits = _extract_watermark_hits(hits, total, watermark_presence_threshold)
        band_rois = _build_band_rois(
            hits, watermark_hits, fps, frame_width, frame_height,
            total_frames, sample_interval, band_range_pad_samples,
        )
        scene_candidates = _build_scene_candidates(
            hits, watermark_hits, fps, frame_width, frame_height,
        )

        report.band_rois = band_rois
        report.watermarks = [w for w, _ in watermark_hits]
        report.scene_candidates = scene_candidates

        if with_snapshots and (report.watermarks or report.scene_candidates):
            _attach_snapshots(cap, report)

        logger.info(
            "Fullframe scanner: %d samples -> %d band ROI(s), %d watermark(s), %d scene cluster(s).",
            total, len(band_rois), len(report.watermarks), len(scene_candidates),
        )
        return report
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# 水印判定：归一化文本 + 量化中心 跨帧聚合
# ---------------------------------------------------------------------------

def _extract_watermark_hits(
    hits: List[Dict[str, Any]], total_samples: int, presence_threshold: float,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """返回 [(水印候选 dict, 组成该候选的命中列表)]；命中从后续聚类中剔除。

    判定条件：归一化文本相同 + 量化中心同键的命中，出现率 ≥ 阈值，
    且中心偏离均值的最大距离在容差内（坐标恒定）。
    守卫：同一水平行（量化 y）内出现 ≥3 种不同文本时视为对话带——该行内
    的恒定文本不判为水印（避免把长台词误判成水印）。
    """
    groups: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = {}
    row_text_diversity: Dict[int, set] = {}
    for hit in hits:
        qx, qy = _quantized_center(hit["bbox"], WATERMARK_GRID_PX)
        text = normalize_text(hit.get("text", ""))
        if not text:
            continue
        groups.setdefault((text, qx, qy), []).append(hit)
        row_text_diversity.setdefault(qy, set()).add(text)

    results: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    for (text, _, qy), group in groups.items():
        if len(row_text_diversity.get(qy, ())) >= 3:
            continue  # 对话带：文本随时间变化，不做水印判定
        presence = len({h["sample_pos"] for h in group}) / max(1, total_samples)
        if presence < presence_threshold:
            continue
        xs = [(h["bbox"][0] + h["bbox"][2]) / 2 for h in group]
        ys = [(h["bbox"][1] + h["bbox"][3]) / 2 for h in group]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        spread = max(
            max(abs(x - mean_x) for x in xs), max(abs(y - mean_y) for y in ys)
        )
        if spread > WATERMARK_CENTER_TOLERANCE_PX:
            continue
        x1 = min(h["bbox"][0] for h in group)
        y1 = min(h["bbox"][1] for h in group)
        x2 = max(h["bbox"][2] for h in group)
        y2 = max(h["bbox"][3] for h in group)
        candidate = {
            "text": group[0].get("text", ""),
            "bbox": (x1, y1, x2, y2),
            "presence": presence,
            "first_frame": min(h["frame_idx"] for h in group),
            "last_frame": max(h["frame_idx"] for h in group),
            "snapshot_jpeg": b"",
        }
        results.append((candidate, group))

    results.sort(key=lambda item: item[0]["presence"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# 字幕带：按区域聚类时间段 → 合并 → 富化 ROI
# ---------------------------------------------------------------------------

def _build_band_rois(
    hits: List[Dict[str, Any]],
    watermark_hits: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]],
    fps: float,
    frame_width: int,
    frame_height: int,
    total_frames: int,
    sample_interval: int,
    range_pad_samples: int,
) -> List[Dict[str, Any]]:
    watermark_ids = {id(h) for _, group in watermark_hits for h in group}
    band_hits = [
        h for h in hits
        if h["region"] in ("top", "bottom") and id(h) not in watermark_ids
    ]
    rois: List[Dict[str, Any]] = []
    for region in ("bottom", "top"):
        region_hits = [h for h in band_hits if h["region"] == region]
        if not region_hits:
            continue
        segments = _cluster_hits_into_segments(region_hits)
        cluster_hits = [h for seg in segments for h in seg["hits"]]
        rois.append(_band_cluster_to_roi_entry(
            cluster_hits, region, fps, frame_width, frame_height,
            total_frames, sample_interval * max(0, int(range_pad_samples)),
        ))
    rois.sort(key=lambda r: r["start_frame"])
    return rois


def _band_cluster_to_roi_entry(
    cluster_hits: List[Dict[str, Any]],
    region: Region,
    fps: float,
    frame_width: int,
    frame_height: int,
    total_frames: int,
    range_pad_frames: int,
) -> Dict[str, Any]:
    """把同一区域的全部命中合并为一个（近似）全时长带 ROI。

    同区域不同时间的字幕（位置略有漂移）共用一个 union 区域——裁剪稍大但
    OCR 不受影响；时间范围取 [首命中, 末命中] 再向外扩 range_pad_frames，
    保证字幕头尾不被采样粒度截断。轴对齐带输出 rect（union bbox + 外扩）；
    明显倾斜的带经 :func:`cluster_poly_points_if_tilted` 输出 4 点 poly。
    """
    x1 = min(h["bbox"][0] for h in cluster_hits)
    y1 = min(h["bbox"][1] for h in cluster_hits)
    x2 = max(h["bbox"][2] for h in cluster_hits)
    y2 = max(h["bbox"][3] for h in cluster_hits)
    avg_height = sum(h["height"] for h in cluster_hits) / len(cluster_hits)

    y_pad = avg_height * Y_PAD_HEIGHT_RATIO
    roi_x1 = max(0, int(round(x1 - X_PAD_PX)))
    roi_y1 = max(0, int(round(y1 - y_pad)))
    roi_x2 = min(frame_width, int(round(x2 + X_PAD_PX)))
    roi_y2 = min(frame_height, int(round(y2 + y_pad)))

    start_frame = max(0, min(h["frame_idx"] for h in cluster_hits) - range_pad_frames)
    end_frame = min(
        total_frames - 1, max(h["frame_idx"] for h in cluster_hits) + range_pad_frames
    )

    quads = [q for h in cluster_hits for q in (h.get("quad") or [])]
    poly_points = cluster_poly_points_if_tilted(quads, frame_width, frame_height, avg_height)
    if poly_points is not None:
        roi_type, points = "poly", poly_points
    else:
        roi_type, points = "rect", [roi_x1, roi_y1, max(1, roi_x2 - roi_x1), max(1, roi_y2 - roi_y1)]

    return {
        "start_time": format_time(start_frame / fps),
        "end_time": format_time(end_frame / fps),
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "type": roi_type,
        "points": points,
        "source": "auto",
        "text_filter_policy": "auto",
        "fade_in_refine_enabled": True,
        "band_region": region,
    }


# ---------------------------------------------------------------------------
# 场景文字：中部区域命中按空间网格聚类
# ---------------------------------------------------------------------------

def _build_scene_candidates(
    hits: List[Dict[str, Any]],
    watermark_hits: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]],
    fps: float,
    frame_width: int,
    frame_height: int,
) -> List[Dict[str, Any]]:
    watermark_ids = {id(h) for _, group in watermark_hits for h in group}
    scene_hits = [
        h for h in hits
        if h["region"] == "middle" and id(h) not in watermark_ids
    ]
    groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for hit in scene_hits:
        groups.setdefault(_quantized_center(hit["bbox"], SCENE_GRID_PX), []).append(hit)

    candidates: List[Dict[str, Any]] = []
    for group in groups.values():
        x1 = min(h["bbox"][0] for h in group)
        y1 = min(h["bbox"][1] for h in group)
        x2 = max(h["bbox"][2] for h in group)
        y2 = max(h["bbox"][3] for h in group)
        first_frame = min(h["frame_idx"] for h in group)
        last_frame = max(h["frame_idx"] for h in group)
        avg_height = sum(h["height"] for h in group) / len(group)
        example_text = max(
            (h.get("text", "") for h in group), key=len, default=""
        )

        y_pad = avg_height * Y_PAD_HEIGHT_RATIO
        roi_x1 = max(0, int(round(x1 - X_PAD_PX)))
        roi_y1 = max(0, int(round(y1 - y_pad)))
        roi_x2 = min(frame_width, int(round(x2 + X_PAD_PX)))
        roi_y2 = min(frame_height, int(round(y2 + y_pad)))
        roi_entry = {
            "start_time": format_time(first_frame / fps),
            "end_time": format_time(last_frame / fps),
            "start_frame": int(first_frame),
            "end_frame": int(last_frame),
            "type": "rect",
            "points": [roi_x1, roi_y1, max(1, roi_x2 - roi_x1), max(1, roi_y2 - roi_y1)],
            "source": "auto",
            "text_filter_policy": "keep_all",
            "fade_in_refine_enabled": False,
            "band_region": "middle",
        }
        candidates.append({
            "text": example_text,
            "bbox": (x1, y1, x2, y2),
            "hit_count": len(group),
            "first_frame": first_frame,
            "last_frame": last_frame,
            "snapshot_jpeg": b"",
            "roi_entry": roi_entry,
        })
    candidates.sort(key=lambda c: c["hit_count"], reverse=True)
    candidates = _merge_scene_candidates(candidates, fps)
    return candidates


def _bbox_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bboxes_near(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
    iou_threshold: float,
    expand_ratio: float,
) -> bool:
    """两框重叠（IoU 达标），或彼此外扩 expand_ratio·自身长边后相交。"""
    if _bbox_iou(a, b) >= iou_threshold:
        return True

    def _expand(box: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        pad = max(box[2] - box[0], box[3] - box[1]) * expand_ratio
        return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)

    ea, eb = _expand(a), _expand(b)
    return ea[0] <= eb[2] and eb[0] <= ea[2] and ea[1] <= eb[3] and eb[1] <= ea[3]


def _merge_scene_candidates(
    candidates: List[Dict[str, Any]], fps: float
) -> List[Dict[str, Any]]:
    """合并文本一致且空间相邻/重叠的场景候选（按 hit_count 降序贪心）。"""
    merged: List[Dict[str, Any]] = []
    for cand in candidates:
        text = normalize_text(cand.get("text", ""))
        target: Optional[Dict[str, Any]] = None
        if text:
            for kept in merged:
                if normalize_text(kept.get("text", "")) != text:
                    continue
                if _bboxes_near(kept["bbox"], cand["bbox"], SCENE_MERGE_IOU, SCENE_MERGE_EXPAND_RATIO):
                    target = kept
                    break
        if target is None:
            merged.append(dict(cand))
            continue

        tx1, ty1, tx2, ty2 = target["bbox"]
        cx1, cy1, cx2, cy2 = cand["bbox"]
        target["bbox"] = (min(tx1, cx1), min(ty1, cy1), max(tx2, cx2), max(ty2, cy2))
        target["hit_count"] = int(target.get("hit_count", 0)) + int(cand.get("hit_count", 0))
        target["first_frame"] = min(int(target["first_frame"]), int(cand["first_frame"]))
        target["last_frame"] = max(int(target["last_frame"]), int(cand["last_frame"]))
        if len(str(cand.get("text", ""))) > len(str(target.get("text", ""))):
            target["text"] = cand.get("text", "")

        entry, other = target.get("roi_entry"), cand.get("roi_entry")
        if entry is not None and other is not None:
            ex, ey, ew, eh = (int(v) for v in entry["points"][:4])
            ox, oy, ow, oh = (int(v) for v in other["points"][:4])
            nx1, ny1 = min(ex, ox), min(ey, oy)
            nx2, ny2 = max(ex + ew, ox + ow), max(ey + eh, oy + oh)
            entry["points"] = [nx1, ny1, max(1, nx2 - nx1), max(1, ny2 - ny1)]
            start_frame = min(int(entry.get("start_frame", 0)), int(other.get("start_frame", 0)))
            end_frame = max(int(entry.get("end_frame", 0)), int(other.get("end_frame", 0)))
            entry["start_frame"], entry["end_frame"] = start_frame, end_frame
            if fps > 0:
                entry["start_time"] = format_time(start_frame / fps)
                entry["end_time"] = format_time(end_frame / fps)
        elif entry is None and other is not None:
            target["roi_entry"] = other
    merged.sort(key=lambda c: c["hit_count"], reverse=True)
    return merged


# ---------------------------------------------------------------------------
# 快照：为水印/场景候选从代表帧裁剪 JPEG 缩略图
# ---------------------------------------------------------------------------

def _attach_snapshots(cap: cv2.VideoCapture, report: ScanReport) -> None:
    """重新定位到各候选的代表帧，裁剪 bbox 区域并编码为 JPEG bytes。"""
    tasks: List[Tuple[Dict[str, Any], int]] = []
    for candidate in report.watermarks:
        tasks.append((candidate, candidate["first_frame"]))
    for candidate in report.scene_candidates:
        tasks.append((candidate, candidate["first_frame"]))
    if not tasks:
        return

    # 按 frame_idx 顺序 seek：快照直接写入各候选 dict（与遍历顺序无关），
    # 排序后顺序定位可大幅减少随机 seek 开销。
    for candidate, frame_idx in sorted(tasks, key=lambda t: int(t[1])):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        crop = _crop_bbox(frame, candidate["bbox"], pad=4)
        if crop is None:
            continue
        if crop.shape[1] > SNAPSHOT_MAX_WIDTH:
            scale = SNAPSHOT_MAX_WIDTH / crop.shape[1]
            crop = cv2.resize(
                crop, (SNAPSHOT_MAX_WIDTH, max(1, int(crop.shape[0] * scale)))
            )
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            candidate["snapshot_jpeg"] = buf.tobytes()


def _crop_bbox(
    frame: "cv2.Mat", bbox: Tuple[float, float, float, float], pad: int = 0,
) -> Optional["cv2.Mat"]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    xa = max(0, int(x1) - pad)
    ya = max(0, int(y1) - pad)
    xb = min(width, int(x2) + pad)
    yb = min(height, int(y2) + pad)
    if xb <= xa or yb <= ya:
        return None
    return frame[ya:yb, xa:xb]
