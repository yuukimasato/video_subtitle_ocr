# core/color_presence_gate.py
"""HSV-based heuristic: detect likely subtitle-colored pixels inside ROI (optional prefilter)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np


@dataclass
class ColorGatePreviewRow:
    frame_index: int
    passed: bool
    max_ratio: float
    thumb_bgr: np.ndarray


def _rect_crop_with_mask(
    frame_bgr: np.ndarray, x: int, y: int, w: int, h: int
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    h_img, w_img = frame_bgr.shape[:2]
    y1, y2 = max(0, y), min(h_img, y + h)
    x1, x2 = max(0, x), min(w_img, x + w)
    if y2 <= y1 or x2 <= x1:
        return None, None
    crop = frame_bgr[y1:y2, x1:x2]
    mk = np.full(crop.shape[:2], 255, dtype=np.uint8)
    return crop, mk


def _poly_crop_with_mask(
    frame_bgr: np.ndarray, points: List[List[int]],
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    try:
        h_img, w_img = frame_bgr.shape[:2]
        poly = np.array(points, dtype=np.int32)
        if poly.ndim != 2 or poly.shape[1] != 2 or poly.shape[0] < 3:
            return None, None
        poly[:, 0] = np.clip(poly[:, 0], 0, w_img - 1)
        poly[:, 1] = np.clip(poly[:, 1], 0, h_img - 1)
        x, y, w, h = cv2.boundingRect(poly)
        y1, y2 = max(0, y), min(h_img, y + h)
        x1, x2 = max(0, x), min(w_img, x + w)
        if y2 <= y1 or x2 <= x1:
            return None, None
        local = poly.copy()
        local[:, 0] -= x1
        local[:, 1] -= y1
        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        cv2.fillPoly(mask, [local], 255)
        crop = frame_bgr[y1:y2, x1:x2].copy()
        return crop, mask
    except Exception:
        return None, None


def get_roi_crop_and_mask(frame_bgr: np.ndarray, roi_entry: Dict) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    roi_type = roi_entry.get("type", "rect")
    pts = roi_entry.get("points")
    if roi_type == "rect":
        if not (isinstance(pts, list) and len(pts) == 4):
            return None, None
        x, y, w, h = [int(p) for p in pts]
        return _rect_crop_with_mask(frame_bgr, x, y, w, h)
    if roi_type == "poly":
        if not isinstance(pts, list) or len(pts) < 3:
            return None, None
        return _poly_crop_with_mask(frame_bgr, pts)
    return None, None


def _circular_hue_mean_deg(hues: np.ndarray) -> float:
    """OpenCV 色相（0-179，周期 180）的环形均值。

    色相是环形量：红色系样本横跨 0/179 边界时（如 [178,179,1,2]），
    算术均值会落到完全无关的色相区间，校准出的 inRange 范围随之失配。
    这里用倍角法（θ=2h，周期 360）求平均方向再折回 0-180。
    """
    theta = hues.astype(np.float64) * (2.0 * math.pi / 180.0)
    sin_mean = float(np.mean(np.sin(theta)))
    cos_mean = float(np.mean(np.cos(theta)))
    if abs(sin_mean) < 1e-12 and abs(cos_mean) < 1e-12:
        return float(np.mean(hues))
    return math.degrees(math.atan2(sin_mean, cos_mean)) / 2.0 % 180.0


def _hue_bounds_to_inrange_pairs(
    center_hue: float, margin: float,
) -> List[Tuple[float, float]]:
    """色相中心 ± margin → 一或两段 (lo, hi) inRange 区间。

    区间跨 0/179 边界时单段 [lo, hi]（lo>hi）无法表达环绕，拆成
    [0, hi] 与 [lo, 179] 两段；否则返回单段（钳制到 [0, 179]）。
    """
    lo = center_hue - margin
    hi = center_hue + margin
    if lo < 0.0 or hi > 179.0:
        wlo = lo % 180.0
        whi = hi % 180.0
        return [(0.0, whi), (wlo, 179.0)]
    return [(max(0.0, lo), min(179.0, hi))]


def calibrate_hsv_from_crop(
    bgr: np.ndarray,
    mask: Optional[np.ndarray] = None,
    margin: int = 15,
) -> Dict[str, np.ndarray]:
    """
    Derive HSV inRange bounds from one ROI crop (representative frame).
    Uses union of "white/light" and "saturated color" clusters inside mask.

    色相按环形量处理：红色/品红等横跨 0/179 边界的颜色也能得到正确
    范围（必要时输出 ``orange_lower2``/``orange_upper2`` 第二段区间，
    由 :func:`presence_ratio_masked` 取并集）。
    """
    if bgr is None or bgr.size == 0:
        raise ValueError("empty crop")
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    flat = hsv.reshape(-1, 3)
    if mask is not None and mask.size == bgr.shape[0] * bgr.shape[1]:
        m = (mask.reshape(-1) > 0)
        samples = flat[m]
    else:
        samples = flat
    if samples.shape[0] < 16:
        samples = flat

    h_ch, s_ch, v_ch = samples[:, 0].astype(np.float32), samples[:, 1].astype(np.float32), samples[:, 2].astype(np.float32)
    # 白色样本判定边界须与下方 white_lower/white_upper（inRange: s<=55, v>=170）
    # 保持一致，否则校准采样与实际判定口径不一致。
    white_mask = (s_ch <= 55.0) & (v_ch >= 170.0)
    colored_mask = (~white_mask) & (s_ch > 60.0) & (v_ch > 45.0)

    white_lower = np.array([0, 0, 170], dtype=np.uint8)
    white_upper = np.array([180, 55, 255], dtype=np.uint8)

    orange_lower = np.array([0, 90, 90], dtype=np.uint8)
    orange_upper = np.array([35, 255, 255], dtype=np.uint8)
    hue_pairs: List[Tuple[float, float]] = []
    if np.count_nonzero(colored_mask) >= 8:
        ch = h_ch[colored_mask]
        cs = s_ch[colored_mask]
        cvv = v_ch[colored_mask]
        mh = _circular_hue_mean_deg(ch)
        ms, mv = float(np.mean(cs)), float(np.mean(cvv))
        hue_pairs = _hue_bounds_to_inrange_pairs(mh, float(margin))
        lo = np.array(
            [hue_pairs[0][0], max(40.0, ms - margin), max(40.0, mv - margin)],
            dtype=np.float32,
        )
        hi = np.array(
            [hue_pairs[0][1], min(255.0, ms + margin), min(255.0, mv + margin)],
            dtype=np.float32,
        )
        orange_lower = np.clip(lo, [0, 0, 0], [179, 255, 255]).astype(np.uint8)
        orange_upper = np.clip(hi, [0, 0, 0], [179, 255, 255]).astype(np.uint8)

    out: Dict[str, np.ndarray] = {
        "orange_lower": orange_lower,
        "orange_upper": orange_upper,
        "white_lower": white_lower,
        "white_upper": white_upper,
    }
    if len(hue_pairs) > 1:
        # 环绕区间第二段（如红色 [0,5] ∪ [160,179]）；无第二段时缺省。
        lo2 = np.array(
            [hue_pairs[1][0], max(40.0, ms - margin), max(40.0, mv - margin)],
            dtype=np.float32,
        )
        hi2 = np.array(
            [hue_pairs[1][1], min(255.0, ms + margin), min(255.0, mv + margin)],
            dtype=np.float32,
        )
        out["orange_lower2"] = np.clip(lo2, [0, 0, 0], [179, 255, 255]).astype(np.uint8)
        out["orange_upper2"] = np.clip(hi2, [0, 0, 0], [179, 255, 255]).astype(np.uint8)
    return out


def presence_ratio_masked(bgr: np.ndarray, mask: Optional[np.ndarray], bounds: Dict[str, np.ndarray]) -> float:
    if bgr is None or bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mo = cv2.inRange(hsv, bounds["orange_lower"], bounds["orange_upper"])
    if bounds.get("orange_lower2") is not None and bounds.get("orange_upper2") is not None:
        # 环绕色相区间的第二段（见 calibrate_hsv_from_crop）。
        mo = cv2.bitwise_or(mo, cv2.inRange(hsv, bounds["orange_lower2"], bounds["orange_upper2"]))
    mw = cv2.inRange(hsv, bounds["white_lower"], bounds["white_upper"])
    uni = cv2.bitwise_or(mo, mw)
    if mask is not None and mask.shape[:2] == bgr.shape[:2]:
        uni = cv2.bitwise_and(uni, mask)
        denom = float(max(1, cv2.countNonZero(mask)))
    else:
        denom = float(max(1, bgr.shape[0] * bgr.shape[1]))
    return float(cv2.countNonZero(uni)) / denom


def single_roi_passes(
    frame_bgr: np.ndarray,
    roi_entry: Dict,
    bounds: Dict[str, np.ndarray],
    min_ratio: float,
) -> Tuple[bool, float]:
    crop, m = get_roi_crop_and_mask(frame_bgr, roi_entry)
    if crop is None:
        return False, 0.0
    r = presence_ratio_masked(crop, m, bounds)
    return (r >= min_ratio), float(r)


def frame_passes_for_active_rois(
    frame_bgr: np.ndarray,
    roi_data: List[Dict],
    active_roi_indices: Set[int],
    bounds: Dict[str, np.ndarray],
    min_ratio: float,
) -> Tuple[bool, float]:
    mx = 0.0
    ok = False
    for idx in sorted(active_roi_indices):
        if not (0 <= idx < len(roi_data)):
            continue
        p, r = single_roi_passes(frame_bgr, roi_data[idx], bounds, min_ratio)
        mx = max(mx, r)
        if p:
            ok = True
    return ok, mx


def roi_indices_active_at(frame_num: int, roi_data: List[Dict], fps: float) -> Set[int]:
    from core.roi_extractor import get_roi_frame_number

    active: Set[int] = set()
    for idx, roi_entry in enumerate(roi_data):
        s = get_roi_frame_number(roi_entry, fps, "start_time", "start_frame")
        e = get_roi_frame_number(roi_entry, fps, "end_time", "end_frame")
        s = max(0, int(s))
        e = max(0, int(e))
        if e < s:
            s, e = e, s
        if s <= frame_num <= e:
            active.add(idx)
    return active


def sample_probe_frames(roi_data: List[Dict], total_frames: int, fps: float, max_probes: int = 48) -> List[int]:
    from core.roi_extractor import get_roi_frame_number

    if not roi_data or total_frames <= 0:
        return []
    gmin, gmax = None, None
    for roi_entry in roi_data:
        s = get_roi_frame_number(roi_entry, fps, "start_time", "start_frame")
        e = get_roi_frame_number(roi_entry, fps, "end_time", "end_frame")
        s = max(0, min(int(s), total_frames - 1))
        e = max(0, min(int(e), total_frames - 1))
        if e < s:
            s, e = e, s
        gmin = s if gmin is None else min(gmin, s)
        gmax = e if gmax is None else max(gmax, e)
    if gmin is None or gmax is None:
        return []
    span = max(1, gmax - gmin + 1)
    n = int(np.clip(max_probes, 8, span))
    if span <= n:
        return list(range(gmin, gmax + 1))
    return list(np.linspace(gmin, gmax, num=n, dtype=int).tolist())


def pick_calibration_roi_index(frame_num: int, roi_data: List[Dict], fps: float) -> int:
    active = sorted(roi_indices_active_at(frame_num, roi_data, fps))
    if not active:
        return -1
    return int(active[0])


def build_gate_spec(
    bounds: Dict[str, np.ndarray],
    min_ratio: float,
    estimated_kept_roi_frames: int,
    preview_keep_ratio: float,
    calibration_frame: int,
) -> Dict:
    out = {
        "orange_lower": bounds["orange_lower"].copy(),
        "orange_upper": bounds["orange_upper"].copy(),
        "white_lower": bounds["white_lower"].copy(),
        "white_upper": bounds["white_upper"].copy(),
        "min_ratio": float(min_ratio),
        "estimated_kept_roi_frames": int(max(1, estimated_kept_roi_frames)),
        "preview_keep_ratio": float(preview_keep_ratio),
        "calibration_frame": int(calibration_frame),
    }
    if bounds.get("orange_lower2") is not None and bounds.get("orange_upper2") is not None:
        # 环绕色相区间的第二段；老 spec 无此键时保持原样。
        out["orange_lower2"] = bounds["orange_lower2"].copy()
        out["orange_upper2"] = bounds["orange_upper2"].copy()
    return out


def run_preview(
    video_path: str,
    roi_data: List[Dict],
    total_frames: int,
    fps: float,
    calibration_frame_num: int,
    min_ratio: float,
    max_probes: int = 40,
    thumb_max_side: int = 200,
) -> Dict[str, object]:
    """
    Returns dict: bounds, rows, kept_count, sampled_count, planned_roi_frames, calibration_frame, min_ratio
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("Could not open video for preview")

    # 打开 cap 后统一用 try/finally 释放，保证异常路径不泄漏 VideoCapture。
    try:
        cal_idx = pick_calibration_roi_index(calibration_frame_num, roi_data, fps)
        if cal_idx < 0:
            raise RuntimeError("No ROI covers the calibration frame")

        cap.set(cv2.CAP_PROP_POS_FRAMES, int(calibration_frame_num))
        ok, cal_frame = cap.read()
        if not ok or cal_frame is None:
            raise RuntimeError("Could not read calibration frame")

        crop, m = get_roi_crop_and_mask(cal_frame, roi_data[cal_idx])
        if crop is None:
            raise RuntimeError("Calibration ROI crop failed")
        bounds = calibrate_hsv_from_crop(crop, m)

        probes = sample_probe_frames(roi_data, total_frames, fps, max_probes=max_probes)
        rows: List[ColorGatePreviewRow] = []
        kept = 0
        for fnum in probes:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fnum))
            ret, fr = cap.read()
            if not ret or fr is None:
                continue
            active = roi_indices_active_at(int(fnum), roi_data, fps)
            if not active:
                continue
            passed, mx = frame_passes_for_active_rois(fr, roi_data, active, bounds, min_ratio)
            if passed:
                kept += 1
            # Thumbnail: first active ROI crop
            c0, _ = get_roi_crop_and_mask(fr, roi_data[sorted(active)[0]])
            thumb = c0 if c0 is not None else fr
            if thumb.size:
                hs = max(thumb.shape[0], thumb.shape[1])
                if hs > thumb_max_side:
                    sc = thumb_max_side / float(hs)
                    thumb = cv2.resize(thumb, (int(thumb.shape[1] * sc), int(thumb.shape[0] * sc)), interpolation=cv2.INTER_AREA)
            rows.append(ColorGatePreviewRow(frame_index=int(fnum), passed=passed, max_ratio=float(mx), thumb_bgr=thumb))

        sampled = len(rows)
        if sampled <= 0:
            raise RuntimeError("Preview produced no samples")

        from core.roi_extractor import get_roi_frame_number

        planned = 0
        for roi_entry in roi_data:
            s = get_roi_frame_number(roi_entry, fps, "start_time", "start_frame")
            e = get_roi_frame_number(roi_entry, fps, "end_time", "end_frame")
            s = max(0, min(int(s), total_frames - 1))
            e = max(0, min(int(e), total_frames - 1))
            if e < s:
                s, e = e, s
            planned += (e - s + 1)

        keep_ratio = float(kept) / float(max(1, sampled))

        return {
            "bounds": bounds,
            "rows": rows,
            "kept_count": int(kept),
            "sampled_count": int(sampled),
            "planned_roi_frames": int(planned),
            "preview_keep_ratio": float(keep_ratio),
            "calibration_frame": int(calibration_frame_num),
            "min_ratio": float(min_ratio),
        }
    finally:
        cap.release()


def recount_preview_rows(rows: List[ColorGatePreviewRow], min_ratio: float) -> int:
    return int(sum(1 for r in rows if r.max_ratio >= float(min_ratio)))
