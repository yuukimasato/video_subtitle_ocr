# core/chunk_worker.py
"""Body of one spawned worker process for chunk-parallel OCR.

A worker runs pipeline steps 1-3 (extraction, OCR + boundary refinement,
coordinate restore) over its window and ships restored frame records back
to the coordinator. Stage 4 (ASS generation + LLM polish) is NOT run here:
LLM calls must stay single-point in the coordinator.

Message protocol (dicts on a ``multiprocessing.Queue``):

``progress``  ``{"type", "window", "pct", "msg"}``   pct is the stage-relative
              absolute axis (0-90) as emitted by the stage functions; the
              coordinator rescales it onto the global progress bar.
``records``   ``{"type", "window", "records"}``      batches of restored tuples.
``done``      ``{"type", "window", "stats"}``        worker finished cleanly.
``cancelled`` ``{"type", "window"}``
``error``     ``{"type", "window", "error"}``        worker failed; the
              coordinator retries once, then falls back to sequential.
``heartbeat`` ``{"type", "window"}``                 liveness signal.

Only ``chunk_worker_main`` and the clip helper run inside the child; both are
module-level so ``spawn`` can pickle them by reference.
"""
import logging
import threading
from dataclasses import replace as dc_replace
from typing import Any, Dict, List

from PySide6.QtCore import QCoreApplication

from core import roi_extractor
from core.chunk_planner import ChunkWindow
from core.pipeline_stages import (
    PipelineCancelled,
    PipelineContext,
    extract_and_ocr_stage,
    refine_stage,
    restore_stage,
)

logger = logging.getLogger(__name__)

RECORDS_BATCH_SIZE = 500
HEARTBEAT_INTERVAL_S = 15.0


def clip_roi_data_to_window(roi_data: List[Dict], window: ChunkWindow, fps: float) -> List[Dict]:
    """Clip every ROI's active range to the window's work range.

    Frame numbers stay global (the seam merger depends on that). Entries
    outside the window are dropped; entries spanning it are trimmed. The
    frame-based keys (``start_frame``/``end_frame``) are written back and
    take precedence over ``start_time``/``end_time`` in the extractor.
    """
    work_start, work_end = window.work_start_frame, window.work_end_frame
    clipped: List[Dict] = []
    for roi in roi_data or []:
        if not isinstance(roi, dict):
            continue
        r = dict(roi)
        start_f = roi_extractor.get_roi_frame_number(r, fps, "start_time", "start_frame")
        # The extractor treats end_frame inclusively (end event at end+1).
        end_f = roi_extractor.get_roi_frame_number(r, fps, "end_time", "end_frame")
        new_start = max(start_f, work_start)
        new_end = min(end_f, work_end - 1)
        if new_end < new_start:
            continue
        r["start_frame"] = new_start
        r["end_frame"] = new_end
        clipped.append(r)
    return clipped


def run_window_stages(ctx: PipelineContext, window: ChunkWindow, *, progress_cb, cancel_check) -> List[tuple]:
    """Run steps 1-3 over ``window`` and return restored frame records.

    Used both by :func:`chunk_worker_main` (inside the spawned process) and
    by the coordinator's sequential fallback for failed windows, so a
    fallback window is processed by exactly the same code as a worker.
    Returns ``[]`` when no ROI intersects the window.
    """
    clipped_rois = clip_roi_data_to_window(ctx.roi_data, window, ctx.fps)
    if not clipped_rois:
        return []
    wctx = dc_replace(ctx, roi_data=clipped_rois)
    ocr_results, stats = extract_and_ocr_stage(
        wctx, progress_cb=progress_cb, cancel_check=cancel_check)
    refined = refine_stage(
        wctx, ocr_results, ocr_stats=stats,
        progress_cb=progress_cb, cancel_check=cancel_check)
    return restore_stage(
        wctx, refined, progress_cb=progress_cb, cancel_check=cancel_check)


def chunk_worker_main(payload: Dict[str, Any]) -> None:
    """Entry point of one worker process. Results go out via ``payload["queue"]``."""
    ctx: PipelineContext = payload["ctx"]
    window: ChunkWindow = payload["window"]
    queue = payload["queue"]
    cancel_event = payload["cancel_event"]
    window_id = window.index

    # Stage functions translate user-facing strings; follow cli.py's headless
    # pattern so translate() always has an application instance.
    if QCoreApplication.instance() is None:
        QCoreApplication([])

    def cancel_check() -> bool:
        return cancel_event.is_set()

    def progress_cb(pct: int, msg: str) -> None:
        try:
            queue.put({"type": "progress", "window": window_id, "pct": int(pct), "msg": str(msg)})
        except Exception:
            pass

    stop_heartbeat = threading.Event()

    def _beat():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_S):
            try:
                queue.put({"type": "heartbeat", "window": window_id})
            except Exception:
                pass

    heartbeat = threading.Thread(target=_beat, daemon=True)
    heartbeat.start()

    try:
        restored = run_window_stages(ctx, window, progress_cb=progress_cb, cancel_check=cancel_check)
        if not restored:
            # No ROI intersects this window: nothing to do (not an error).
            queue.put({"type": "done", "window": window_id, "stats": {"records": 0}})
            return
        for i in range(0, len(restored), RECORDS_BATCH_SIZE):
            if cancel_check():
                raise PipelineCancelled()
            queue.put({"type": "records", "window": window_id,
                       "records": restored[i:i + RECORDS_BATCH_SIZE]})
        queue.put({"type": "done", "window": window_id,
                   "stats": {"records": len(restored)}})
    except PipelineCancelled:
        queue.put({"type": "cancelled", "window": window_id})
    except Exception as e:
        logger.error("chunk worker %d failed: %s", window_id, e, exc_info=True)
        queue.put({"type": "error", "window": window_id, "error": f"{type(e).__name__}: {e}"})
    finally:
        stop_heartbeat.set()
