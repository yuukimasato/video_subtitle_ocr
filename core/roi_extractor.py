import os
import cv2
import numpy as np
import logging
from typing import List, Dict, Generator, Tuple, Union, DefaultDict, Optional, Set
from collections import defaultdict
from PySide6.QtCore import QCoreApplication

from core import color_presence_gate as color_gate
from utils.time_utils import parse_time

logger = logging.getLogger(__name__)

_has_cuda_gpu = False
try:
    if cv2.cuda.getCudaEnabledDeviceCount() > 0:
        _has_cuda_gpu = True
        logger.info(QCoreApplication.translate("roi_extractor", "CUDA-enabled GPU detected and available for OpenCV."))
    else:
        logger.info(QCoreApplication.translate("roi_extractor", "No CUDA-enabled GPU detected or OpenCV not compiled with CUDA support."))
except AttributeError:
    logger.info(QCoreApplication.translate("roi_extractor", "OpenCV CUDA module not found. Likely OpenCV was not compiled with CUDA support."))
except Exception as e:
    logger.warning(QCoreApplication.translate("roi_extractor", f"Error checking for CUDA GPU: {e}"))


def _hsv_bounds_from_gate_spec(spec: Dict) -> Dict[str, np.ndarray]:
    bounds = {
        "orange_lower": spec["orange_lower"],
        "orange_upper": spec["orange_upper"],
        "white_lower": spec["white_lower"],
        "white_upper": spec["white_upper"],
    }
    # 环绕色相区间的可选第二段（老 gate spec 无此键，缺省不启用）。
    if spec.get("orange_lower2") is not None and spec.get("orange_upper2") is not None:
        bounds["orange_lower2"] = spec["orange_lower2"]
        bounds["orange_upper2"] = spec["orange_upper2"]
    return bounds


def _in_range_color_mask(bgr: np.ndarray, bgr_ref: List[int], tolerance: int) -> np.ndarray:
    b, g, r = int(bgr_ref[0]), int(bgr_ref[1]), int(bgr_ref[2])
    lo = np.array([max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)], dtype=np.uint8)
    hi = np.array([min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)], dtype=np.uint8)
    return cv2.inRange(bgr, lo, hi)


def apply_color_restrict_to_crop(bgr: np.ndarray, roi_entry: Dict) -> np.ndarray:
    """
    If roi_entry['color_restrict'] is enabled, keep pixels near the chosen text/outline BGR colors
    and set other pixels to white before OCR to reduce background false positives.
    """
    spec = roi_entry.get("color_restrict")
    if not isinstance(spec, dict) or not spec.get("enabled"):
        return bgr
    if bgr is None or bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] < 3:
        return bgr

    tol = int(np.clip(int(spec.get("tolerance", 40)), 1, 120))
    text_bgr = spec.get("text_bgr")
    outline_bgr = spec.get("outline_bgr")
    shadow_bgr = spec.get("shadow_bgr")
    morph_k = int(np.clip(int(spec.get("morph_kernel", 3)), 1, 15))
    if morph_k % 2 == 0:
        morph_k += 1

    mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
    if isinstance(text_bgr, (list, tuple)) and len(text_bgr) == 3:
        mask = cv2.bitwise_or(mask, _in_range_color_mask(bgr, list(text_bgr), tol))
    if isinstance(outline_bgr, (list, tuple)) and len(outline_bgr) == 3:
        mask = cv2.bitwise_or(mask, _in_range_color_mask(bgr, list(outline_bgr), tol))
    if isinstance(shadow_bgr, (list, tuple)) and len(shadow_bgr) == 3:
        mask = cv2.bitwise_or(mask, _in_range_color_mask(bgr, list(shadow_bgr), tol))

    if not cv2.countNonZero(mask):
        logger.warning(
            QCoreApplication.translate(
                "roi_extractor",
                "Color restriction produced an empty mask for ROI; skipping mask for this frame crop.",
            )
        )
        return bgr

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_k, morph_k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    out = bgr.copy()
    out[mask == 0] = (255, 255, 255)
    return out


def apply_roi_preprocess_to_crop(bgr: np.ndarray, roi_entry: Dict) -> np.ndarray:
    """
    Apply all per-ROI pre-OCR image preprocessing.

    Current steps:
    - optional color restriction mask (keep near text/outline colors)
    - optional light Gaussian blur (reduce noise/aliasing/compression artifacts)
    """
    if bgr is None or not isinstance(bgr, np.ndarray) or bgr.size == 0:
        return bgr
    out = apply_color_restrict_to_crop(bgr, roi_entry)
    if roi_entry.get("blur_enabled"):
        try:
            # Small kernel to avoid smearing thin strokes too much.
            out = cv2.GaussianBlur(out, (3, 3), 0)
        except Exception:
            return out
    return out


def extract_single_roi_crop_with_time(
    video_path: str,
    roi_entry: Dict,
    frame_num: int,
    cap: Optional[cv2.VideoCapture] = None,
    fps: float = 0.0,
) -> Optional[Tuple[np.ndarray, float]]:
    """
    Random-access extract a single ROI crop at a given frame index, together
    with the frame timestamp in seconds (CAP_PROP_POS_MSEC preferred, with
    frame_num/fps as fallback — matching the sequential extractor so refined
    boundaries don't shift timestamps).

    `cap` may be a reusable, already-opened VideoCapture to avoid reopening
    the video for every random access (boundary refinement issues many small
    seeks); when omitted, a temporary capture is opened and released here.
    """
    if not video_path:
        return None

    owned_cap = cap is None
    if owned_cap:
        cap = cv2.VideoCapture(video_path)
    if cap is None or not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_num))
        ret, frame = cap.read()
        if not ret or frame is None:
            return None

        try:
            ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
            frame_time_sec = ms / 1000.0 if ms > 0 else 0.0
        except Exception:
            frame_time_sec = 0.0
        if frame_time_sec <= 0 and fps and fps > 0:
            frame_time_sec = float(frame_num) / float(fps)

        h_img, w_img = frame.shape[:2]
        roi_type = roi_entry.get("type", "rect")
        points = roi_entry.get("points")

        crop: Optional[np.ndarray] = None
        if roi_type == "rect":
            if not (isinstance(points, list) and len(points) == 4):
                return None
            x, y, w, h = [int(p) for p in points]
            y1, y2 = max(0, y), min(h_img, y + h)
            x1, x2 = max(0, x), min(w_img, x + w)
            if y2 <= y1 or x2 <= x1:
                return None
            crop = frame[y1:y2, x1:x2].copy()
        elif roi_type == "poly":
            try:
                poly_points = np.array(points, dtype=np.int32)
                if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                    return None
                poly_points[:, 0] = np.clip(poly_points[:, 0], 0, w_img - 1)
                poly_points[:, 1] = np.clip(poly_points[:, 1], 0, h_img - 1)
                x, y, w, h = cv2.boundingRect(poly_points)
                y1, y2 = max(0, y), min(h_img, y + h)
                x1, x2 = max(0, x), min(w_img, x + w)
                if y2 <= y1 or x2 <= x1:
                    return None
                mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                local_pts = poly_points.copy()
                local_pts[:, 0] -= x1
                local_pts[:, 1] -= y1
                cv2.fillPoly(mask, [local_pts], 255)
                roi_area = frame[y1:y2, x1:x2].copy()
                # Keep outside polygon white to reduce background interference.
                roi_area[mask == 0] = (255, 255, 255)
                crop = roi_area
            except Exception:
                return None
        else:
            return None

        if crop is None or crop.size == 0:
            return None
        return apply_roi_preprocess_to_crop(crop, roi_entry), frame_time_sec
    finally:
        if owned_cap:
            cap.release()


def extract_single_roi_crop(
    video_path: str,
    roi_entry: Dict,
    frame_num: int,
) -> Optional[np.ndarray]:
    """
    Random-access extract a single ROI crop at a given frame index.
    Intended for boundary refinement (small number of seeks).
    """
    result = extract_single_roi_crop_with_time(video_path, roi_entry, frame_num)
    return result[0] if result is not None else None


def get_roi_frame_number(roi_entry: Dict, fps: float, time_key: str, frame_key: str) -> int:
    if frame_key in roi_entry and roi_entry[frame_key] is not None:
        return int(roi_entry[frame_key])
    time_val = roi_entry.get(time_key, 0)
    if isinstance(time_val, str):
        try:
            # Supports both "HH:MM:SS.mmm" and "MM:SS.mmm"; the fractional part is interpreted by digits (".5" = 500ms).
            return int(parse_time(time_val) * fps)
        except (ValueError, TypeError):
            logger.warning(
                QCoreApplication.translate(
                    "roi_extractor",
                    "Unrecognized time string {!r} for key '{}'; falling back to numeric seconds."
                ).format(time_val, time_key)
            )
    return int(float(time_val) * fps)

def calculate_total_roi_frames(
    roi_data: List[Dict],
    total_frames: int,
    fps: float
) -> int:
    if not roi_data:
        return 0

    # Count ROI-frames by summing interval lengths.
    # Note: overlapping ROIs are counted separately (one frame can produce multiple ROI crops).
    if total_frames <= 0:
        return 0

    total_roi_frames = 0
    for roi_entry in roi_data:
        start_frame = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        end_frame = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        start_frame = max(0, min(int(start_frame), total_frames - 1))
        end_frame = max(0, min(int(end_frame), total_frames - 1))
        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame
        total_roi_frames += (end_frame - start_frame + 1)
    logger.info(
        QCoreApplication.translate(
            "roi_extractor",
            "Pre-calculated total of {} ROI frames to process."
        ).format(total_roi_frames)
    )
    return total_roi_frames


def calculate_total_merged_frames(
    roi_data: List[Dict],
    total_frames: int,
    fps: float
) -> int:
    """
    Count unique frame indices where at least one ROI is active.
    This is the right denominator when we merge multiple ROI areas into one
    full-frame composite and only run OCR once per frame.
    """
    if not roi_data or total_frames <= 0:
        return 0

    start_events: DefaultDict[int, List[int]] = defaultdict(list)
    end_events: DefaultDict[int, List[int]] = defaultdict(list)
    for idx, roi_entry in enumerate(roi_data):
        start_frame = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        end_frame = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        start_frame = max(0, min(int(start_frame), total_frames - 1))
        end_frame = max(0, min(int(end_frame), total_frames - 1))
        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame
        start_events[start_frame].append(idx)
        if end_frame + 1 <= total_frames - 1:
            end_events[end_frame + 1].append(idx)

    active: Set[int] = set()
    cnt = 0
    for f in range(total_frames):
        if f in start_events:
            active.update(start_events[f])
        if f in end_events:
            active.difference_update(end_events[f])
        if active:
            cnt += 1

    logger.info(
        QCoreApplication.translate(
            "roi_extractor",
            "Pre-calculated total of {} merged frames to process (union of ROI intervals)."
        ).format(cnt)
    )
    return cnt


def _composite_rois_on_full_frame(
    frame_bgr: np.ndarray,
    roi_data: List[Dict],
    active_rois: Set[int],
    background_bgr: Tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    """
    Create a full-size composite frame where only ROI pixels are kept and all
    other pixels are set to background_bgr. Coordinates remain in original
    video space so downstream does not need offset restoration.
    """
    h_img, w_img = frame_bgr.shape[:2]
    canvas = np.full((h_img, w_img, 3), background_bgr, dtype=np.uint8)

    for roi_idx in sorted(active_rois):
        roi_entry = roi_data[roi_idx]
        roi_type = roi_entry.get('type', 'rect')
        points = roi_entry.get('points')

        if roi_type == 'rect':
            if not (isinstance(points, list) and len(points) == 4):
                continue
            x, y, w, h = [int(p) for p in points]
            y1, y2 = max(0, y), min(h_img, y + h)
            x1, x2 = max(0, x), min(w_img, x + w)
            if y2 <= y1 or x2 <= x1:
                continue
            crop = frame_bgr[y1:y2, x1:x2].copy()
            crop = apply_roi_preprocess_to_crop(crop, roi_entry)
            canvas[y1:y2, x1:x2] = crop
            continue

        if roi_type == 'poly':
            try:
                poly_points = np.array(points, dtype=np.int32)
                if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                    continue
                poly_points[:, 0] = np.clip(poly_points[:, 0], 0, w_img - 1)
                poly_points[:, 1] = np.clip(poly_points[:, 1], 0, h_img - 1)

                x, y, w, h = cv2.boundingRect(poly_points)
                y1, y2 = max(0, y), min(h_img, y + h)
                x1, x2 = max(0, x), min(w_img, x + w)
                if y2 <= y1 or x2 <= x1:
                    continue

                # Local mask in bounding rect to avoid touching full-frame memory.
                local_pts = poly_points.copy()
                local_pts[:, 0] -= x1
                local_pts[:, 1] -= y1
                local_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                cv2.fillPoly(local_mask, [local_pts], 255)

                crop = frame_bgr[y1:y2, x1:x2].copy()
                crop = apply_roi_preprocess_to_crop(crop, roi_entry)

                # Paste only polygon pixels onto canvas.
                canvas_roi = canvas[y1:y2, x1:x2]
                canvas_roi[local_mask == 255] = crop[local_mask == 255]
                canvas[y1:y2, x1:x2] = canvas_roi
            except Exception:
                continue

    return canvas


def _seek_decode_start(
    cap: cv2.VideoCapture,
    start_frame: int,
    total_frames: int,
    start_events: DefaultDict[int, List[int]],
    end_events: DefaultDict[int, List[int]],
) -> Tuple[int, Set[int]]:
    """Seek a sequential extractor to ``start_frame``; return the frame number
    decoding actually starts at and the ROI set active at that point.

    Chunk-parallel workers pass their window's ``grab_start_frame`` so a late
    window no longer re-decodes everything before it; the planner's preroll
    before the work window absorbs CAP_PROP_POS_FRAMES seek imprecision.
    Interval events that fired before the seek point are pre-applied so
    ``active_rois`` reflects mid-video state. A backend that cannot seek
    falls back to full sequential decode from frame 0.
    """
    target = max(0, min(int(start_frame), total_frames - 1))
    if target <= 0:
        return 0, set()
    try:
        ok = cap.set(cv2.CAP_PROP_POS_FRAMES, target)
    except Exception:
        ok = False
    if not ok:
        logger.warning(
            "Video seek to frame %d failed; falling back to sequential decode from frame 0.",
            target,
        )
        return 0, set()
    active: Set[int] = set()
    for f in sorted(f for f in set(start_events) | set(end_events) if f < target):
        if f in start_events:
            active.update(start_events[f])
        if f in end_events:
            active.difference_update(end_events[f])
    return target, active


def extract_merged_roi_frames(
    video_path: str,
    roi_data: List[Dict],
    total_frames: int,
    fps: float,
    work_dir: str,
    save_to_disk: bool = True,
    background_bgr: Tuple[int, int, int] = (255, 255, 255),
    color_presence_gate: Optional[Dict] = None,
    start_frame: int = 0,
) -> Generator[Tuple[Dict, Union[str, np.ndarray], int, str, float], None, None]:
    """
    Decode sequentially, and for frames where any ROI is active, output ONE
    full-size composite image that keeps only ROI pixels.

    ``start_frame`` seeks to a mid-video position first (chunk-parallel
    workers pass ``grab_start_frame``); 0 decodes the whole video.
    """
    if not video_path or not roi_data:
        logger.warning(QCoreApplication.translate("roi_extractor", "Extraction cannot start: video path or ROI data not provided."))
        return
    if save_to_disk and not work_dir:
        logger.error(QCoreApplication.translate("roi_extractor", "In save_to_disk mode, a valid working directory `work_dir` must be provided."))
        return
    if total_frames <= 0:
        logger.warning(QCoreApplication.translate("roi_extractor", "Extraction aborted: invalid total_frames."))
        return

    video_name = os.path.splitext(os.path.basename(video_path))[0]

    # Interval events. Loop locals are ev_start/ev_end: the function's
    # start_frame parameter must stay unshadowed for the seek below.
    start_events: DefaultDict[int, List[int]] = defaultdict(list)
    end_events: DefaultDict[int, List[int]] = defaultdict(list)
    for idx, roi_entry in enumerate(roi_data):
        ev_start = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        ev_end = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        ev_start = max(0, min(int(ev_start), total_frames - 1))
        ev_end = max(0, min(int(ev_end), total_frames - 1))
        if ev_end < ev_start:
            ev_start, ev_end = ev_end, ev_start
        start_events[ev_start].append(idx)
        if ev_end + 1 <= total_frames - 1:
            end_events[ev_end + 1].append(idx)

    output_dir = None
    if save_to_disk:
        output_dir = os.path.join(work_dir, "1_roi_images", f"{video_name}_roi_merged")
        os.makedirs(output_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(
            QCoreApplication.translate(
                "roi_extractor",
                "Could not open video file for extraction: {}"
            ).format(video_path)
        )
        return

    def _get_frame_time_sec(cap_obj: cv2.VideoCapture) -> float:
        try:
            ms = float(cap_obj.get(cv2.CAP_PROP_POS_MSEC))
            if ms > 0:
                return ms / 1000.0
        except Exception:
            pass
        return 0.0

    merged_gate_bounds = None
    merged_gate_min_ratio = 0.01
    if color_presence_gate is not None:
        merged_gate_bounds = _hsv_bounds_from_gate_spec(color_presence_gate)
        merged_gate_min_ratio = float(color_presence_gate.get("min_ratio", 0.01))

    try:
        current_frame_num, active_rois = _seek_decode_start(
            cap, start_frame, total_frames, start_events, end_events)
        roi_identifier = "roi_merged"
        roi_entry_merged = {"type": "full", "points": [0, 0, 0, 0], "full_frame": True}

        while True:
            if current_frame_num > total_frames - 1:
                break

            if current_frame_num in start_events:
                active_rois.update(start_events[current_frame_num])
            if current_frame_num in end_events:
                active_rois.difference_update(end_events[current_frame_num])

            ok = cap.grab()
            if not ok:
                break
            if not active_rois:
                current_frame_num += 1
                continue

            ret, frame = cap.retrieve()
            if not ret or frame is None:
                current_frame_num += 1
                continue

            frame_num = current_frame_num
            frame_time_sec = _get_frame_time_sec(cap)
            if frame_time_sec <= 0 and fps and fps > 0:
                frame_time_sec = frame_num / fps

            if color_presence_gate is not None:
                ok, _ = color_gate.frame_passes_for_active_rois(frame, roi_data, active_rois, merged_gate_bounds, merged_gate_min_ratio)
                if not ok:
                    current_frame_num += 1
                    continue

            merged = _composite_rois_on_full_frame(frame, roi_data, active_rois, background_bgr=background_bgr)
            if save_to_disk and output_dir:
                filename = f"frame_{frame_num:06d}.jpg"
                frame_path = os.path.join(output_dir, filename)
                cv2.imwrite(frame_path, merged)
                yield roi_entry_merged, frame_path, frame_num, roi_identifier, frame_time_sec
            else:
                yield roi_entry_merged, merged, frame_num, roi_identifier, frame_time_sec

            current_frame_num += 1

    finally:
        cap.release()

def extract_roi_frames(
    video_path: str,
    roi_data: List[Dict],
    total_frames: int,
    fps: float,
    work_dir: str,
    save_to_disk: bool = True,
    color_presence_gate: Optional[Dict] = None,
    start_frame: int = 0,
) -> Generator[Tuple[Dict, Union[str, np.ndarray], int, str, float], None, None]:
    """Sequentially decode ROI crops; ``start_frame`` seeks to a mid-video
    position first (chunk-parallel workers pass ``grab_start_frame``)."""
    if not video_path or not roi_data:
        logger.warning(QCoreApplication.translate("roi_extractor", "Extraction cannot start: video path or ROI data not provided."))
        return

    if save_to_disk and not work_dir:
        logger.error(QCoreApplication.translate("roi_extractor", "In save_to_disk mode, a valid working directory `work_dir` must be provided."))
        return

    video_name = os.path.splitext(os.path.basename(video_path))[0]

    # Build interval events: at start_frame add ROI, at (end_frame + 1) remove ROI.
    # Loop locals are ev_start/ev_end: the function's start_frame parameter
    # must stay unshadowed for the seek below.
    if total_frames <= 0:
        logger.warning(QCoreApplication.translate("roi_extractor", "Extraction aborted: invalid total_frames."))
        return

    start_events: DefaultDict[int, List[int]] = defaultdict(list)
    end_events: DefaultDict[int, List[int]] = defaultdict(list)
    for idx, roi_entry in enumerate(roi_data):
        ev_start = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        ev_end = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        ev_start = max(0, min(int(ev_start), total_frames - 1))
        ev_end = max(0, min(int(ev_end), total_frames - 1))
        if ev_end < ev_start:
            ev_start, ev_end = ev_end, ev_start
        start_events[ev_start].append(idx)
        # Removal at end+1 (if within bounds).
        if ev_end + 1 <= total_frames - 1:
            end_events[ev_end + 1].append(idx)
        else:
            # End at last frame: no explicit removal needed inside loop.
            pass
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(
            QCoreApplication.translate(
                "roi_extractor",
                "Could not open video file for extraction: {}"
            ).format(video_path)
        )
        return
    
    roi_dirs = {}
    if save_to_disk:
        for idx in range(len(roi_data)):
            roi_identifier = f"roi_{idx}"
            output_dir = os.path.join(work_dir, "1_roi_images", f"{video_name}_{roi_identifier}")
            os.makedirs(output_dir, exist_ok=True)
            roi_dirs[idx] = output_dir
    
    use_gpu_for_processing = _has_cuda_gpu
    if use_gpu_for_processing:
        logger.info(QCoreApplication.translate("roi_extractor", "Attempting to use GPU for ROI extraction."))
    else:
        logger.info(QCoreApplication.translate("roi_extractor", "Using CPU for ROI extraction (GPU not available or OpenCV not compiled with CUDA)."))

    # Color gate bounds are constant for the whole run; build them once here
    # instead of per ROI per frame.
    gate_bounds = None
    gate_min_ratio = 0.01
    if color_presence_gate is not None:
        gate_bounds = _hsv_bounds_from_gate_spec(color_presence_gate)
        gate_min_ratio = float(color_presence_gate.get("min_ratio", 0.01))

    def _get_frame_time_sec(cap_obj: cv2.VideoCapture) -> float:
        # CAP_PROP_POS_MSEC is often more accurate than frame_num/fps for VFR inputs.
        # It is still OpenCV-provided, but avoids systematic drift when FPS metadata is off.
        try:
            ms = float(cap_obj.get(cv2.CAP_PROP_POS_MSEC))
            if ms > 0:
                return ms / 1000.0
        except Exception:
            pass
        # Fallback: approximate by frame index / fps when POS_MSEC not available.
        return 0.0

    try:
        # Sequential decode is significantly faster than frequent random seeks.
        current_frame_num, active_rois = _seek_decode_start(
            cap, start_frame, total_frames, start_events, end_events)
        while True:
            if current_frame_num > total_frames - 1:
                break
            
            # Apply interval events at this frame index.
            if current_frame_num in start_events:
                active_rois.update(start_events[current_frame_num])
            if current_frame_num in end_events:
                active_rois.difference_update(end_events[current_frame_num])

            ok = cap.grab()
            if not ok:
                break
            if not active_rois:
                # No ROI active on this frame; skip retrieve to save decode cost.
                current_frame_num += 1
                continue

            ret, frame = cap.retrieve()
            if not ret or frame is None:
                logger.warning(
                    QCoreApplication.translate(
                        "roi_extractor",
                        "Could not retrieve video frame {}."
                    ).format(current_frame_num)
                )
                current_frame_num += 1
                continue

            frame_num = current_frame_num
            frame_time_sec = _get_frame_time_sec(cap)
            if frame_time_sec <= 0 and fps and fps > 0:
                frame_time_sec = frame_num / fps
            
            gpu_frame = None
            if use_gpu_for_processing:
                try:
                    gpu_frame = cv2.cuda_GpuMat()
                    gpu_frame.upload(frame)
                except Exception as e:
                    logger.warning(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "Failed to upload frame to GPU for frame {}. Falling back to CPU for this frame. Error: {}"
                        ).format(frame_num, e)
                    )
                    gpu_frame = None
            current_processing_frame = gpu_frame if gpu_frame is not None else frame
            
            if gpu_frame is not None:
                h_img, w_img = gpu_frame.rows, gpu_frame.cols
            else:
                h_img, w_img = frame.shape[:2]

            for roi_idx in sorted(active_rois):
                roi_entry = roi_data[roi_idx]
                # Chunk-parallel windows pass clipped ROI lists tagged with
                # their index in the coordinator's full list; identifiers
                # must stay global so stage 4 per-ROI metadata matches.
                roi_identifier = f"roi_{roi_entry.get('_global_roi_index', roi_idx)}"
                roi_type = roi_entry.get('type', 'rect')
                points = roi_entry.get('points')
                if points is None:
                    logger.error(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "ROI {} has no 'points' field; skipped."
                        ).format(roi_identifier)
                    )
                    continue
                
                roi_frame_result = None
                
                if roi_type == 'rect':
                    if isinstance(points, list) and len(points) == 4:
                        x, y, w, h = [int(p) for p in points]
                        
                        y1, y2 = max(0, y), min(h_img, y + h)
                        x1, x2 = max(0, x), min(w_img, x + w)
                        
                        if y2 > y1 and x2 > x1:
                            if gpu_frame is not None:
                                gpu_roi_frame = current_processing_frame[y1:y2, x1:x2]
                                roi_frame_result = gpu_roi_frame.download()
                            else:
                                roi_frame_result = current_processing_frame[y1:y2, x1:x2]
                    else:
                        logger.error(
                            QCoreApplication.translate(
                                "roi_extractor",
                                "Incorrect points format for rectangular ROI: {}"
                            ).format(points)
                        )
                        continue
                elif roi_type == 'poly':
                    try:
                        poly_points = np.array(points, dtype=np.int32)
                        if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                            logger.error(
                                QCoreApplication.translate(
                                    "roi_extractor",
                                    "Incorrect points format for polygonal ROI: {}"
                                ).format(points)
                            )
                            continue

                        poly_points[:, 0] = np.clip(poly_points[:, 0], 0, w_img - 1)
                        poly_points[:, 1] = np.clip(poly_points[:, 1], 0, h_img - 1)

                        x, y, w, h = cv2.boundingRect(poly_points)
                        y1, y2 = max(0, y), min(h_img, y + h)
                        x1, x2 = max(0, x), min(w_img, x + w)
                        if y2 > y1 and x2 > x1:
                            # Local bounding-rect mask: faster than a full-frame
                            # mask (the old GPU path uploaded one every frame),
                            # and the outside-polygon background is white —
                            # consistent with the random-access extractor and
                            # the merged-composite path.
                            local_pts = poly_points.copy()
                            local_pts[:, 0] -= x1
                            local_pts[:, 1] -= y1
                            local_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                            cv2.fillPoly(local_mask, [local_pts], 255)
                            crop = frame[y1:y2, x1:x2].copy()
                            crop[local_mask == 0] = (255, 255, 255)
                            roi_frame_result = crop
                    except Exception as e:
                        logger.error(
                            QCoreApplication.translate(
                                "roi_extractor",
                                "Error processing polygonal ROI: {}"
                            ).format(e)
                        )
                        continue
                else:
                    logger.error(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "Unknown ROI type: {}"
                        ).format(roi_type)
                    )
                    continue

                if roi_frame_result is not None and roi_frame_result.size > 0 and color_presence_gate is not None:
                    passed, _ = color_gate.single_roi_passes(frame, roi_entry, gate_bounds, gate_min_ratio)
                    if not passed:
                        continue

                if roi_frame_result is not None and roi_frame_result.size > 0:
                    roi_frame_result = apply_roi_preprocess_to_crop(roi_frame_result, roi_entry)
                    if save_to_disk:
                        filename = f"frame_{frame_num:06d}.jpg"
                        frame_path = os.path.join(roi_dirs[roi_idx], filename)
                        cv2.imwrite(frame_path, roi_frame_result)
                        yield roi_entry, frame_path, frame_num, roi_identifier, frame_time_sec
                    else:
                        yield roi_entry, roi_frame_result.copy(), frame_num, roi_identifier, frame_time_sec
                else:
                    logger.warning(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "ROI extraction resulted in empty image, frame {}, ROI {}"
                        ).format(frame_num, roi_identifier)
                    )

            current_frame_num += 1

    finally:
        cap.release()
