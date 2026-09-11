# core/boundary_refine.py
"""Frame-accurate boundary refinement for OCR subtitle timelines.

The skip/fill OCR optimizer determines subtitle presence coarsely: during
fade-in/fade-out the text can be unreadable for one or two frames, and the
motion sentinel may reuse a neighbouring result while the ROI barely changes,
so the detected first/last text frames can lag behind the frames where text
is actually visible in the video.

For every empty<->text transition this module:

1. Re-grounds a SMALL window around the edge with real per-frame OCR, so
   results that were filled/skipped rather than truly recognized are
   corrected.
2. Walks outward from the re-grounded edge one frame at a time with an
   enhanced probe (the caller may retry with a 2x upscale), extending the
   subtitle to the first/last frame where text is actually readable.

Safety rules:
- Extended frames reuse the anchor (fully readable) frame's OCR content;
  only the boundary and per-frame timestamp change. This keeps grouping
  stable and prevents faint/partial readings from injecting junk events.
- The walk stops at any frame whose probe text is dissimilar to the anchor
  text, so it can never cross into a neighbouring subtitle or scene text.
- Frame timestamps always come from the probed frame itself (POS_MSEC
  based), so refinement never shifts the timeline.

All video/OCR access is injected via callbacks, keeping the logic
unit-testable.
"""

import logging
import re
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import Levenshtein
except ImportError:  # pragma: no cover - optional dependency
    Levenshtein = None

# How many frames around a detected edge are re-OCRed to correct filled or
# skipped results. Small on purpose: the probe walk handles the boundary
# search, this only fixes stale fill immediately around the edge.
GROUND_WINDOW_FRAMES = 3

# Marker key set on walk-extended results (see set_extended_item).
_EXTENDED_MARKER = "_boundary_extended"


def _levenshtein_ratio(a: str, b: str) -> float:
    """Normalized Levenshtein similarity, with a small pure-python fallback."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if Levenshtein is not None:
        return float(Levenshtein.ratio(a, b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


def joined_text(ocr_data: object) -> str:
    if not isinstance(ocr_data, dict):
        return ""
    texts = ocr_data.get("rec_texts", [])
    if not isinstance(texts, list):
        return ""
    return "".join(str(t) for t in texts if str(t).strip())


def ocr_text_present(ocr_data: object, min_score: float = 0.0) -> bool:
    """True when the OCR result contains at least one non-empty line whose
    score reaches ``min_score`` (mirrors the ASS generator's filter)."""
    if not isinstance(ocr_data, dict):
        return False
    texts = ocr_data.get("rec_texts", [])
    scores = ocr_data.get("rec_scores", [])
    if not isinstance(texts, list):
        return False
    for i, text in enumerate(texts):
        if not str(text).strip():
            continue
        if min_score > 0 and i < len(scores):
            try:
                if float(scores[i]) < min_score:
                    continue
            except (TypeError, ValueError):
                pass
        return True
    return False


def parse_roi_index(roi_identifier: object) -> Optional[int]:
    """roi_identifier is "roi_{idx}" in non-merged mode."""
    if not roi_identifier:
        return None
    m = re.fullmatch(r"roi_(\d+)", str(roi_identifier).strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def refine_boundaries(
    ocr_results: List[tuple],
    roi_data: List[Dict],
    *,
    ocr_frame_func: Callable[[Dict, str, int], Optional[tuple]],
    probe_frame_func: Callable[[Dict, str, int], Optional[tuple]],
    is_cancelled_func: Optional[Callable[[], bool]] = None,
    max_backtrack_frames: int = 25,
    max_forward_frames: int = 12,
    min_score: float = 0.6,
    content_similarity_threshold: float = 0.5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    edge_extend_frames: int = 0,
) -> List[tuple]:
    """Refine fade-in/fade-out boundaries for ROIs that enabled refinement.

    ``ocr_frame_func(roi_entry, roi_id, frame_num)`` must run a normal OCR on
    an arbitrarily accessed frame and return a result tuple shaped like
    ``(roi_entry, ocr_data, frame_num, roi_id, time_sec)`` with the frame's
    own timestamp, or None when extraction failed.

    ``probe_frame_func`` has the same contract but should be more sensitive
    (e.g. retry with an upscaled crop) because it decides where the boundary
    actually is. A returned tuple means "text visible on this frame"; None
    means "no text detected".

    ``edge_extend_frames``: how far the outward walk may cross the ROI's own
    start/end frame. When the ROI time range clips a subtitle (its head/tail
    lies outside the extraction range), the walk can still recover the true
    boundary because probing uses random access. 0 keeps the walk inside the
    ROI range (legacy behaviour).

    ``progress_callback(done, total)`` is invoked once per scanned frame of
    the refine-enabled ROIs so callers can drive a progress bar.
    """
    if not ocr_results:
        return ocr_results
    if is_cancelled_func is None:
        is_cancelled_func = lambda: False

    index_map: Dict[Tuple[str, int], int] = {}
    by_roi: Dict[str, List[tuple]] = defaultdict(list)
    for idx, item in enumerate(ocr_results):
        try:
            frame_num = int(item[2])
            roi_id = str(item[3])
        except (TypeError, ValueError, IndexError):
            continue
        index_map[(roi_id, frame_num)] = idx
        by_roi[roi_id].append(item)
    for roi_id in by_roi:
        by_roi[roi_id].sort(key=lambda x: int(x[2]))

    results = ocr_results

    def get_item(roi_id: str, frame_num: int) -> Optional[tuple]:
        pos = index_map.get((roi_id, int(frame_num)))
        return results[pos] if pos is not None else None

    def set_item(roi_id: str, item: tuple) -> None:
        nonlocal results
        key = (roi_id, int(item[2]))
        pos = index_map.get(key)
        if pos is not None:
            results[pos] = item
        else:
            results.append(item)
            index_map[key] = len(results) - 1

    def set_extended_item(roi_id: str, anchor_item: tuple, frame_num: int, time_sec: float) -> None:
        """Store a walk-extended frame: anchor content, own timestamp, marked.

        The marker lets later ground windows recognize boundary frames and
        refuse to downgrade them with a faint empty reading (a neighbouring
        subtitle's edge window can reach into this subtitle's faint tail).
        """
        content = anchor_item[1]
        if isinstance(content, dict):
            content = dict(content)
            content[_EXTENDED_MARKER] = True
        set_item(roi_id, (anchor_item[0], content, int(frame_num), roi_id, float(time_sec or 0.0)))

    def ground_window(roi_id: str, roi_entry: Dict, back: int, fwd: int) -> None:
        """Re-OCR a small window around the edge with real per-frame OCR."""
        for f in range(back, fwd + 1):
            if is_cancelled_func():
                return
            r = ocr_frame_func(roi_entry, roi_id, f)
            if r is None:
                continue
            existing = get_item(roi_id, f)
            if (
                existing is not None
                and isinstance(existing[1], dict)
                and existing[1].get(_EXTENDED_MARKER)
                and not ocr_text_present(r[1], min_score)
            ):
                continue  # never downgrade a walk-extended boundary frame
            set_item(roi_id, r)

    def presence(roi_id: str, frame_num: int) -> bool:
        item = get_item(roi_id, frame_num)
        return item is not None and ocr_text_present(item[1], min_score)

    def _roi_refine_enabled(roi_id: str) -> bool:
        roi_idx = parse_roi_index(roi_id)
        if roi_idx is None or not (0 <= roi_idx < len(roi_data)):
            return False
        # 边界精修（首/末帧逐帧复核）默认开启：缺省视为启用，只有 ROI 显式
        # 置 fade_in_refine_enabled=False 才跳过。旧配置/CLI/建议 ROI 未写
        # 该字段时也享受帧级对齐。
        return bool(roi_data[roi_idx].get("fade_in_refine_enabled", True))

    # Progress denominator: frames of all refine-enabled ROIs, so the caller
    # can drive a progress bar with a stable total.
    refine_total_frames = sum(
        len(by_roi[roi_id]) for roi_id in by_roi if _roi_refine_enabled(roi_id)
    )
    refine_done_frames = 0

    for roi_id in sorted(by_roi.keys()):
        if is_cancelled_func():
            return results
        if not _roi_refine_enabled(roi_id):
            continue
        roi_entry = roi_data[parse_roi_index(roi_id)]
        roi_start = int(roi_entry.get("start_frame", 0))
        roi_end = int(roi_entry.get("end_frame", 0))
        # The frame list is captured once; refinement only overwrites these
        # frames or appends contiguous extensions, so edge scanning stays valid.
        frames = [int(it[2]) for it in by_roi[roi_id]]
        if frames and not roi_end:
            # ROI entries without an explicit frame range (e.g. built from
            # time-only specs): fall back to the observed frame span so the
            # fade-out ground windows and tail handling still work.
            roi_start = roi_start if roi_start else min(frames)
            roi_end = max(frames)

        for scan_pos, frame_num in enumerate(frames):
            if is_cancelled_func():
                return results
            refine_done_frames += 1
            if progress_callback is not None:
                progress_callback(refine_done_frames, refine_total_frames)

            prev_frame = frames[scan_pos - 1] if scan_pos > 0 else None
            has = presence(roi_id, frame_num)
            prev_has = presence(roi_id, prev_frame) if prev_frame is not None else False

            if has and not prev_has:
                _refine_fade_in_edge(
                    roi_id, roi_entry, roi_start, roi_end, frame_num,
                    ground_window, probe_frame_func, get_item, presence,
                    set_extended_item, is_cancelled_func,
                    max_backtrack_frames, max_forward_frames,
                    min_score, content_similarity_threshold,
                    edge_extend_frames,
                )
            elif (not has) and prev_has:
                _refine_fade_out_edge(
                    roi_id, roi_entry, roi_start, roi_end, frame_num,
                    ground_window, probe_frame_func, get_item, presence,
                    set_extended_item, is_cancelled_func,
                    max_backtrack_frames, max_forward_frames,
                    min_score, content_similarity_threshold,
                    edge_extend_frames,
                )

        # The ROI time range may clip the subtitle tail: when the last
        # extracted frame still shows text, no empty frame follows inside the
        # range, so the scan loop never saw a fade-out edge. SSIM fill may
        # also have propagated text over faint/empty fade-out frames up to
        # the ROI edge, so walk BACKWARD re-grounding each frame with real
        # per-frame OCR and trim the wrongly-filled ones; then, if text truly
        # runs to the edge, probe forward (bounded by edge_extend_frames) to
        # recover the clipped tail beyond the ROI range.
        if frames and presence(roi_id, frames[-1]):
            f = frames[-1]
            limit = max(roi_start, f - int(max_backtrack_frames))
            while f >= limit:
                if is_cancelled_func():
                    break
                r = ocr_frame_func(roi_entry, roi_id, f)
                if r is None:
                    break
                if not ocr_text_present(r[1], min_score):
                    set_item(roi_id, r)  # correct a filled frame to truly-empty
                    f -= 1
                    continue
                break  # real text reached: keep the voted content, tail verified
            if presence(roi_id, frames[-1]):
                _refine_fade_out_edge(
                    roi_id, roi_entry, roi_start, roi_end, frames[-1] + 1,
                    ground_window, probe_frame_func, get_item, presence,
                    set_extended_item, is_cancelled_func,
                    max_backtrack_frames, max_forward_frames,
                    min_score, content_similarity_threshold,
                    edge_extend_frames,
                )

    return results


def _refine_fade_in_edge(
    roi_id: str,
    roi_entry: Dict,
    roi_start: int,
    roi_end: int,
    edge_frame: int,
    ground_window,
    probe_frame_func,
    get_item,
    presence,
    set_extended_item,
    is_cancelled_func,
    max_backtrack_frames: int,
    max_forward_frames: int,
    min_score: float,
    content_similarity_threshold: float,
    edge_extend_frames: int = 0,
) -> None:
    # 1) Re-ground the window so filled/skipped frames reflect real per-frame OCR.
    back = max(roi_start, edge_frame - GROUND_WINDOW_FRAMES)
    fwd = min(roi_end, edge_frame + GROUND_WINDOW_FRAMES)
    ground_window(roi_id, roi_entry, back, fwd)

    # 2) Anchor on the last frame at/before the edge that truly shows text.
    # Scan backwards from the edge so a previous subtitle still lingering
    # inside the ground window cannot hijack the anchor (mirrors fade-out).
    anchor_frame = edge_frame
    for f in range(edge_frame, back - 1, -1):
        if presence(roi_id, f):
            anchor_frame = f
            break
    anchor_item = get_item(roi_id, anchor_frame)
    if anchor_item is None:
        return
    anchor_text = joined_text(anchor_item[1])

    # 3) Walk backwards to the true first visible-text frame. The walk may
    # cross the ROI's start frame by edge_extend_frames (probing uses random
    # access), recovering subtitles whose head lies outside the ROI range.
    limit = max(
        roi_start - int(edge_extend_frames),
        anchor_frame - int(max_backtrack_frames),
    )
    f = anchor_frame - 1
    while f >= limit:
        if is_cancelled_func():
            return
        r = probe_frame_func(roi_entry, roi_id, f)
        if r is None or not ocr_text_present(r[1], min_score):
            break
        if _levenshtein_ratio(joined_text(r[1]), anchor_text) < content_similarity_threshold:
            break  # different text (another subtitle/scene text): never cross it
        set_extended_item(roi_id, anchor_item, f, float(r[4]) if len(r) > 4 and r[4] is not None else 0.0)
        f -= 1


def _refine_fade_out_edge(
    roi_id: str,
    roi_entry: Dict,
    roi_start: int,
    roi_end: int,
    edge_frame: int,
    ground_window,
    probe_frame_func,
    get_item,
    presence,
    set_extended_item,
    is_cancelled_func,
    max_backtrack_frames: int,
    max_forward_frames: int,
    min_score: float,
    content_similarity_threshold: float,
    edge_extend_frames: int = 0,
) -> None:
    # 1) Re-ground the window so filled/skipped frames reflect real per-frame OCR.
    back = max(roi_start, edge_frame - GROUND_WINDOW_FRAMES)
    fwd = min(roi_end, edge_frame + GROUND_WINDOW_FRAMES)
    ground_window(roi_id, roi_entry, back, fwd)

    # 2) Anchor on the last frame before the edge that still shows text
    # (the window re-grounding may have moved the edge).
    anchor_frame = edge_frame - 1
    for f in range(edge_frame - 1, back - 1, -1):
        if presence(roi_id, f):
            anchor_frame = f
            break
    anchor_item = get_item(roi_id, anchor_frame)
    if anchor_item is None:
        return
    anchor_text = joined_text(anchor_item[1])

    # 3) Walk forwards to the true last visible-text frame. May cross the
    # ROI's end frame by edge_extend_frames (random-access probing).
    limit = min(
        roi_end + int(edge_extend_frames),
        anchor_frame + int(max_forward_frames),
    )
    f = anchor_frame + 1
    while f <= limit:
        if is_cancelled_func():
            return
        r = probe_frame_func(roi_entry, roi_id, f)
        if r is None or not ocr_text_present(r[1], min_score):
            break
        if _levenshtein_ratio(joined_text(r[1]), anchor_text) < content_similarity_threshold:
            break  # different text (another subtitle/scene text): never cross it
        set_extended_item(roi_id, anchor_item, f, float(r[4]) if len(r) > 4 and r[4] is not None else 0.0)
        f += 1
