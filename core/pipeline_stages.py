# core/pipeline_stages.py
"""Stage functions for pipeline steps 1-3 (extraction, OCR, refine, restore).

Extracted verbatim from ``PipelineWorker.run`` so that the GUI thread worker
and (future) spawned chunk-worker processes drive identical stage logic.
Progress percentage boundaries are unchanged:

    1-10  extraction   10-65  OCR   65-80  boundary refinement   80-90  restore

Stage 4 (ASS generation + LLM polish) remains in ``PipelineWorker``/cli: it is
the only stage that talks to servers, and chunk workers must never call it.

``PipelineContext`` carries only what stages 1-3 need, holds no Qt objects and
stays picklable for ``multiprocessing`` spawn.
"""
import logging
import os
import threading
import time
import cv2
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QCoreApplication

from core import boundary_refine, coordinate_restorer, ocr_processor, roi_extractor
from core.ocr_optimizer import OcrOptimizer

logger = logging.getLogger(__name__)

# Absolute progress-percent boundaries, exported so a chunk coordinator can
# rescale worker progress onto the same 0-100 axis.
EXTRACT_START, EXTRACT_RANGE = 1, 9
OCR_START, OCR_RANGE = 10, 55
REFINE_START, REFINE_RANGE = 65, 15
RESTORE_START, RESTORE_RANGE = 80, 10

ProgressCb = Callable[[int, str], None]
CancelCheck = Callable[[], bool]


class PipelineCancelled(BaseException):
    """Raised by stage functions when ``cancel_check()`` turns true.

    Inherits BaseException so a generic ``except Exception`` in caller code
    cannot accidentally convert a user cancel into an error report.
    """


@dataclass
class PipelineContext:
    """Picklable parameter bundle for stage functions (steps 1-3 only)."""

    video_path: str
    roi_data: List[Dict]
    total_frames: int
    fps: float
    work_dir: str
    debug_mode: bool
    in_memory_ocr: bool = False
    visualize: bool = False
    time_slice_enabled: bool = False
    time_slice_seconds: float = 10.0
    merge_rois: bool = False
    save_intermediate_json: bool = False
    color_presence_gate_spec: Optional[Dict[str, Any]] = None
    ocr_engine_id: str = ""
    engine_options: Optional[Dict[str, Any]] = None
    # CLI runs stages 1-3 without boundary refinement (its single-process
    # path never had it); the chunk path must match, so the flag lives on
    # the context. The GUI pipeline keeps the default (True).
    enable_boundary_refine: bool = True
    # Boundary-refinement executor threads: 0 = auto (min(4, cores, RAM
    # budget); >=2 uses the parallel executor), 1 = legacy serial path.
    # Chunk workers pin this to 1: cross-window parallelism already
    # saturates the machine and per-thread engines would multiply memory.
    refine_workers: int = 0
    # Frame the sequential extractors start decoding at. Chunk workers set
    # this to their window's grab_start_frame (the plan's seek preroll) so a
    # late window stops re-decoding the video from frame 0; 0 = whole video.
    decode_start_frame: int = 0


def extract_and_ocr_stage(
    ctx: PipelineContext,
    *,
    progress_cb: ProgressCb,
    cancel_check: CancelCheck,
) -> Tuple[List[tuple], Dict[str, Any]]:
    """Steps 1+2 (progress 1-65): ROI frame extraction + intelligent OCR.

    Extraction and OCR are one stage because time-slice streaming interleaves
    them. Returns the raw per-frame OCR results and a stats dict.
    """
    stream_executor: Optional[ThreadPoolExecutor] = None
    optimizer = None
    frame_generator = None
    stop_event = threading.Event()
    external_cancel_check = cancel_check

    def cancel_check():
        return stop_event.is_set() or external_cancel_check()

    report_progress = progress_cb
    progress_lock = threading.Lock()
    last_progress = -1

    def progress_cb(pct, message):
        nonlocal last_progress
        with progress_lock:
            if pct > last_progress:
                last_progress = pct
                report_progress(pct, message)

    try:
        progress_cb(0, QCoreApplication.translate(
            "pipeline_worker", "Step 1/4: Calculating number of ROI frames to process..."))
        if cancel_check():
            raise PipelineCancelled()

        t0_roi = time.perf_counter()
        if ctx.merge_rois:
            total_roi_frames = roi_extractor.calculate_total_merged_frames(
                ctx.roi_data, ctx.total_frames, ctx.fps
            )
        else:
            total_roi_frames = roi_extractor.calculate_total_roi_frames(
                ctx.roi_data, ctx.total_frames, ctx.fps
            )

        if cancel_check():
            raise PipelineCancelled()

        if not total_roi_frames:
            raise RuntimeError(QCoreApplication.translate(
                "pipeline_worker",
                "ROI extraction step did not produce any data. Please check ROI time and region settings."))

        progress_cb(1, QCoreApplication.translate(
            "pipeline_worker",
            "Step 1/4: Calculation complete, total {} frames. Starting extraction..."
        ).format(total_roi_frames))

        roi_start_progress = EXTRACT_START
        roi_progress_range = EXTRACT_RANGE

        extraction_progress_total = total_roi_frames
        gate_spec = ctx.color_presence_gate_spec
        if gate_spec:
            est = gate_spec.get("estimated_kept_roi_frames")
            if isinstance(est, int) and est >= 1:
                extraction_progress_total = est

        if ctx.merge_rois:
            frame_generator = roi_extractor.extract_merged_roi_frames(
                ctx.video_path, ctx.roi_data, ctx.total_frames, ctx.fps, ctx.work_dir,
                save_to_disk=not ctx.in_memory_ocr,
                color_presence_gate=gate_spec,
                start_frame=ctx.decode_start_frame,
            )
        else:
            frame_generator = roi_extractor.extract_roi_frames(
                ctx.video_path, ctx.roi_data, ctx.total_frames, ctx.fps, ctx.work_dir,
                save_to_disk=not ctx.in_memory_ocr,
                color_presence_gate=gate_spec,
                start_frame=ctx.decode_start_frame,
            )

        extracted_count = 0

        # If time slicing is enabled, we can stream: extract frames, buffer per ROI+bucket,
        # and OCR previous bucket as soon as we detect a bucket switch for that ROI.
        stream_ocr = bool(ctx.time_slice_enabled and ctx.time_slice_seconds > 0)

        # Step 2 setup (may run during extraction in stream_ocr mode).
        progress_cb(10, QCoreApplication.translate(
            "pipeline_worker",
            "Step 2/4: Starting intelligent OCR recognition... (0/{})"
        ).format(total_roi_frames))

        t0_ocr = time.perf_counter()
        device_mode = ocr_processor.get_device_mode()
        if stream_ocr:
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Streaming mode enabled (time_slice={}s). OCR will run during extraction to reduce peak memory."
                ).format(ctx.time_slice_seconds)
            )

        optimizer = OcrOptimizer(
            work_dir=ctx.work_dir,
            visualize=ctx.visualize,
            in_memory_mode=ctx.in_memory_ocr,
            save_ocr_json=ctx.save_intermediate_json,
            ocr_engine_id=ctx.ocr_engine_id,
            engine_options=ctx.engine_options,
        )

        ocr_results: List[tuple] = []
        ocr_start_progress = OCR_START
        # 10-65% for OCR; 65-80% is reserved for the (much slower)
        # frame-by-frame boundary refinement that may follow.
        ocr_progress_range = OCR_RANGE
        processed_count = 0
        total_ocr_calls = 0
        total_frames_filled = 0

        # Streaming parallel flush (CPU only). In GPU mode, keep sequential to avoid VRAM contention.
        cpu_workers = (os.cpu_count() or 4)
        stream_parallel = bool(stream_ocr and device_mode == "cpu" and not ctx.visualize)
        pending_futures = set()
        max_stream_workers = max(1, min(4, cpu_workers))
        max_outstanding = max_stream_workers * 2

        def _process_bucket_task(frames: List[tuple]):
            if cancel_check():
                raise PipelineCancelled()
            local_opt = OcrOptimizer(
                work_dir=ctx.work_dir,
                visualize=ctx.visualize,
                in_memory_mode=ctx.in_memory_ocr,
                save_ocr_json=ctx.save_intermediate_json,
                ocr_engine_id=ctx.ocr_engine_id,
                engine_options=ctx.engine_options,
            )
            try:
                frames.sort(key=lambda x: x[2])
                res = local_opt.process_roi_group(
                    frames,
                    is_cancelled_func=cancel_check,
                    progress_callback=None
                )
                calls = int(getattr(local_opt, "ocr_calls", 0))
                filled = int(getattr(local_opt, "frames_filled", 0))
                return res, calls, filled, len(frames)
            finally:
                local_opt.cleanup()

        def _collect_one_completed():
            nonlocal processed_count, total_ocr_calls, total_frames_filled
            if not pending_futures:
                return
            completed = set()
            while not completed:
                if external_cancel_check():
                    raise PipelineCancelled()
                completed, _ = wait(pending_futures, timeout=0.1,
                                    return_when=FIRST_COMPLETED)
            for fut in completed:
                pending_futures.discard(fut)
                res, calls, filled, frame_cnt = fut.result()
                ocr_results.extend(res)
                processed_count += int(frame_cnt)
                total_ocr_calls += int(calls)
                total_frames_filled += int(filled)

                current_total_processed = min(processed_count, total_roi_frames)
                progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                progress_cb(
                    progress,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 2/4: OCR recognition in progress... ({}/{})"
                    ).format(current_total_processed, total_roi_frames)
                )
                break

        if stream_parallel:
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Streaming OCR will flush in parallel (cpu_workers={}, max_stream_workers={})."
                ).format(cpu_workers, max_stream_workers)
            )
            stream_executor = ThreadPoolExecutor(max_workers=max_stream_workers)

        # Buffer state for streaming by ROI.
        # roi_id -> current_bucket_idx, buffered_frames(list)
        bucket_idx_by_roi: Dict[str, int] = {}
        buffer_by_roi: Dict[str, List[tuple]] = {}

        def _compute_bucket(frame_data: tuple) -> int:
            # frame_data shape: (roi_entry, img_input, frame_num, roi_identifier, frame_time_sec)
            t = float(frame_data[4]) if len(frame_data) >= 5 and frame_data[4] is not None else 0.0
            if t <= 0 and ctx.fps and ctx.fps > 0:
                t = float(frame_data[2]) / float(ctx.fps)
            if t <= 0:
                return 0
            return int(t // ctx.time_slice_seconds)

        def _flush_one_roi_bucket(roi_id: str):
            nonlocal processed_count, total_ocr_calls, total_frames_filled
            frames = buffer_by_roi.get(roi_id) or []
            if not frames:
                return
            if stream_parallel and stream_executor is not None:
                fut = stream_executor.submit(_process_bucket_task, list(frames))
                pending_futures.add(fut)
                # Backpressure: don't let too many outstanding buckets build up.
                if len(pending_futures) >= max_outstanding:
                    _collect_one_completed()
            else:
                # Sequential flush using shared optimizer (keeps caches).
                frames.sort(key=lambda x: x[2])
                res = optimizer.process_roi_group(
                    frames,
                    is_cancelled_func=cancel_check,
                    progress_callback=None
                )
                ocr_results.extend(res)
                processed_count += len(frames)
                total_ocr_calls = int(getattr(optimizer, "ocr_calls", 0))
                total_frames_filled = int(getattr(optimizer, "frames_filled", 0))

                current_total_processed = min(processed_count, total_roi_frames)
                progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                progress_cb(
                    progress,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 2/4: OCR recognition in progress... ({}/{})"
                    ).format(current_total_processed, total_roi_frames)
                )
            # Clear buffer to release memory (important for in-memory mode).
            buffer_by_roi[roi_id] = []

        for i, frame_data in enumerate(frame_generator):
            if cancel_check():
                raise PipelineCancelled()

            extracted_count += 1
            # Step 1 progress (extraction)
            denom = max(1, extraction_progress_total)
            progress = roi_start_progress + int((extracted_count / denom) * roi_progress_range)
            progress = min(roi_start_progress + roi_progress_range, progress)
            progress_cb(
                progress,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 1/4: Extracting ROI frames... ({}/{})"
                ).format(extracted_count, denom)
            )

            if not stream_ocr:
                # Non-streaming path: store all then process (legacy behavior).
                # We'll build ROI groups after extraction to preserve current parallel logic.
                buffer_by_roi.setdefault("__ALL__", []).append(frame_data)
                continue

            roi_id = frame_data[3]
            bidx = _compute_bucket(frame_data)
            if roi_id not in bucket_idx_by_roi:
                bucket_idx_by_roi[roi_id] = bidx
                buffer_by_roi[roi_id] = [frame_data]
            else:
                current_b = bucket_idx_by_roi[roi_id]
                if bidx != current_b:
                    # Finalize previous bucket for this ROI, then start buffering the new one.
                    _flush_one_roi_bucket(roi_id)
                    bucket_idx_by_roi[roi_id] = bidx
                    buffer_by_roi[roi_id] = [frame_data]
                else:
                    buffer_by_roi[roi_id].append(frame_data)

        # End of extraction.流式模式下 OCR 进度(10-65)已在抽取期间推进,
        # 回发 10 会让进度条倒退;仅在 OCR 尚未推进过时补齐。
        if processed_count == 0:
            progress_cb(
                10,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 1/4: ROI frame extraction complete. Total {} ROI frames."
                ).format(extracted_count)
            )
        t1_roi = time.perf_counter()
        if extracted_count > 0:
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "ROI extraction done: {} ROI-frames in {:.2f}s ({:.1f} roi-frames/s)."
                ).format(extracted_count, (t1_roi - t0_roi), extracted_count / max(1e-6, (t1_roi - t0_roi)))
            )

        if extracted_count <= 0:
            raise RuntimeError(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "ROI extraction yielded no frames. If color presence filtering is enabled, try preview again with a higher ratio threshold or disable it.",
                )
            )

        total_roi_frames = max(1, extracted_count)

        if cancel_check():
            raise PipelineCancelled()

        if not stream_ocr:
            # Non-streaming legacy path: run OCR after extraction, keeping the previous parallel strategy.
            frames_to_process = buffer_by_roi.get("__ALL__", [])
            roi_groups = defaultdict(list)
            for frame_data in frames_to_process:
                roi_identifier = frame_data[3]
                roi_groups[roi_identifier].append(frame_data)

            # Parallelize by ROI group (safe boundary). Each thread gets its own optimizer (and thread-local OCR instance).
            max_workers = min(len(roi_groups), cpu_workers)
            if device_mode == "gpu":
                max_workers = 1
            use_parallel = max_workers > 1 and not ctx.visualize

            processed_lock = threading.Lock()
            stats_lock = threading.Lock()
            emit_lock = threading.Lock()
            last_emitted_progress = -1
            last_emit_ts = 0.0

            def _emit_ocr_progress(current_total_processed: int):
                nonlocal last_emitted_progress, last_emit_ts
                current_total_processed = int(max(0, min(current_total_processed, total_roi_frames)))
                progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                now = time.perf_counter()
                # Throttle UI updates to avoid spamming signals when callbacks are very frequent.
                # Emit whenever the integer progress increases, but also allow occasional updates
                # even if it stays the same (e.g. tiny groups) at ~5Hz max.
                with emit_lock:
                    if progress <= last_emitted_progress and (now - last_emit_ts) < 0.2:
                        return
                    last_emitted_progress = max(last_emitted_progress, progress)
                    last_emit_ts = now
                progress_cb(
                    progress,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 2/4: OCR recognition in progress... ({}/{})"
                    ).format(current_total_processed, total_roi_frames)
                )

            def process_one_group(roi_id: str, frames: List):
                if cancel_check():
                    return roi_id, [], 0, 0, 0, 0
                local_opt = OcrOptimizer(
                    work_dir=ctx.work_dir,
                    visualize=ctx.visualize,
                    in_memory_mode=ctx.in_memory_ocr,
                    save_ocr_json=ctx.save_intermediate_json,
                    ocr_engine_id=ctx.ocr_engine_id,
                    engine_options=ctx.engine_options,
                )
                try:
                    frames.sort(key=lambda x: x[2])

                    # In parallel mode, use the optimizer's internal progress callback to update
                    # global progress smoothly (instead of only when each ROI group finishes).
                    last_reported = 0

                    def progress_callback(group_processed_count: int):
                        nonlocal last_reported, processed_count
                        if cancel_check():
                            return
                        group_processed_count = int(max(0, min(group_processed_count, len(frames))))
                        delta = group_processed_count - last_reported
                        if delta <= 0:
                            return
                        last_reported = group_processed_count
                        with processed_lock:
                            processed_count += delta
                            current_total_processed = min(processed_count, total_roi_frames)
                        _emit_ocr_progress(current_total_processed)

                    res = local_opt.process_roi_group(
                        frames,
                        is_cancelled_func=cancel_check,
                        progress_callback=progress_callback if use_parallel else None
                    )
                    calls = int(getattr(local_opt, "ocr_calls", 0))
                    filled = int(getattr(local_opt, "frames_filled", 0))
                    return roi_id, res, calls, filled, len(frames), last_reported
                finally:
                    local_opt.cleanup()

            if use_parallel:
                logger.info(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Running OCR in parallel (device={}, groups={}, max_workers={}, save_json={})."
                    ).format(device_mode.upper(), len(roi_groups), max_workers, "ON" if ctx.save_intermediate_json else "OFF")
                )
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = []
                    for roi_id, frames in sorted(roi_groups.items()):
                        if cancel_check():
                            break
                        futures.append(ex.submit(process_one_group, roi_id, list(frames)))

                    for fut in as_completed(futures):
                        if cancel_check():
                            break
                        roi_id, roi_res, calls, filled, frame_cnt, group_reported = fut.result()
                        ocr_results.extend(roi_res)
                        with stats_lock:
                            total_ocr_calls += calls
                            total_frames_filled += filled
                        # Ensure the global counter accounts for any tail that wasn't reported
                        # (e.g. very small groups or early returns).
                        if frame_cnt and group_reported is not None:
                            remaining = int(frame_cnt) - int(group_reported)
                            if remaining > 0:
                                with processed_lock:
                                    processed_count += remaining
                                    current_total_processed = min(processed_count, total_roi_frames)
                                _emit_ocr_progress(current_total_processed)
            else:
                logger.info(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Running OCR sequentially (device={}, groups={}, save_json={})."
                    ).format(device_mode.upper(), len(roi_groups), "ON" if ctx.save_intermediate_json else "OFF")
                )
                for roi_id, frames in sorted(roi_groups.items()):
                    if cancel_check():
                        break
                    frames.sort(key=lambda x: x[2])

                    def progress_callback(group_processed_count: int):
                        current_total_processed = processed_count + group_processed_count
                        progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                        progress_cb(
                            progress,
                            QCoreApplication.translate(
                                "pipeline_worker",
                                "Step 2/4: OCR recognition in progress... ({}/{})"
                            ).format(current_total_processed, total_roi_frames)
                        )

                    optimized_group_results = optimizer.process_roi_group(
                        frames,
                        is_cancelled_func=cancel_check,
                        progress_callback=progress_callback
                    )
                    ocr_results.extend(optimized_group_results)
                    processed_count += len(frames)

                total_ocr_calls = int(getattr(optimizer, "ocr_calls", 0))
                total_frames_filled = int(getattr(optimizer, "frames_filled", 0))

        else:
            # Streaming path: flush remaining buffers at EOF, then cleanup.
            for roi_id in sorted(buffer_by_roi.keys()):
                if roi_id == "__ALL__":
                    continue
                if cancel_check():
                    raise PipelineCancelled()
                _flush_one_roi_bucket(roi_id)
            # Collect remaining parallel results (if any), then shutdown executor.
            while pending_futures:
                _collect_one_completed()
            if stream_executor is not None:
                stream_executor.shutdown(wait=True)
                stream_executor = None

        if cancel_check():
            raise PipelineCancelled()

        if not ocr_results:
            raise RuntimeError(QCoreApplication.translate(
                "pipeline_worker", "OCR recognition step did not produce any results."))

        t1_ocr = time.perf_counter()
        stats = {
            "extracted_count": extracted_count,
            "total_roi_frames": total_roi_frames,
            "total_ocr_calls": total_ocr_calls,
            "total_frames_filled": total_frames_filled,
            "ocr_elapsed": t1_ocr - t0_ocr,
        }
        return ocr_results, stats

    finally:
        # Signal running tasks before joining: the caller may remove work_dir
        # immediately after this function returns, so no task may outlive it.
        stop_event.set()
        try:
            if stream_executor is not None:
                stream_executor.shutdown(wait=True, cancel_futures=True)
        finally:
            try:
                if frame_generator is not None:
                    close = getattr(frame_generator, "close", None)
                    if close is not None:
                        close()
            finally:
                if optimizer is not None:
                    optimizer.cleanup()


def refine_stage(
    ctx: PipelineContext,
    ocr_results: List[tuple],
    *,
    ocr_stats: Dict[str, Any],
    progress_cb: ProgressCb,
    cancel_check: CancelCheck,
) -> List[tuple]:
    """Step 2.5 (progress 65-80): per-ROI boundary refinement.

    Default ON: an ROI without ``fade_in_refine_enabled`` is refined
    (frame-accurate head/tail re-check); only an explicit
    ``fade_in_refine_enabled=False`` opts out. Merged-ROI mode skips it.
    """
    if not ctx.merge_rois:
        any_refine = any(
            isinstance(r, dict) and bool(r.get("fade_in_refine_enabled", True))
            for r in (ctx.roi_data or [])
        )
    else:
        any_refine = False
    # Refinement owns 65-80%; emitting 80 here first would make the
    # progress bar jump backwards once refinement starts.
    if not any_refine:
        progress_cb(80, QCoreApplication.translate("pipeline_worker", "Step 2/4: OCR recognition complete."))
    est_skipped = max(0, ocr_stats["total_roi_frames"] - ocr_stats["total_ocr_calls"])
    logger.info(
        QCoreApplication.translate(
            "pipeline_worker",
            "OCR done: {} roi-frames filled, {} OCR calls (est. skipped {}), {:.2f}s."
        ).format(ocr_stats["total_frames_filled"], ocr_stats["total_ocr_calls"],
                 est_skipped, ocr_stats["ocr_elapsed"])
    )

    if cancel_check():
        raise PipelineCancelled()

    if not any_refine:
        return ocr_results

    # Refinement can take as long as the main OCR pass, so it
    # owns a visible progress range (65-80%) instead of sitting
    # at a single percentage point.
    refine_start_progress = REFINE_START
    refine_progress_range = REFINE_RANGE
    last_refine_progress = -1
    last_refine_emit_done = -1

    def _refine_progress(done: int, total: int) -> None:
        nonlocal last_refine_progress, last_refine_emit_done
        if total <= 0:
            return
        progress = refine_start_progress + int((done / total) * refine_progress_range)
        progress = min(refine_start_progress + refine_progress_range, progress)
        # Update on every percent step, and refresh the done/total
        # counter roughly every 1% of work so the label keeps
        # moving even between percent steps.
        if progress != last_refine_progress or (
            done - last_refine_emit_done >= max(1, total // 100)
        ):
            last_refine_progress = progress
            last_refine_emit_done = done
            progress_cb(
                progress,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 2/4: Refining subtitle boundaries frame-by-frame... ({}/{})"
                ).format(done, total),
            )

    progress_cb(
        refine_start_progress,
        QCoreApplication.translate(
            "pipeline_worker",
            "Step 2/4: Refining subtitle boundaries frame-by-frame...",
        ),
    )

    # Parallel executor (Phase 2): >=2 threads refine independent boundary
    # jobs concurrently on per-thread engines. Any failure falls back to the
    # legacy serial path, which stays the reference implementation.
    workers = int(ctx.refine_workers) if ctx.refine_workers > 0 else None
    if workers is None:
        from core.refine_executor import auto_refine_workers
        try:
            device = ocr_processor.get_device_mode()
        except Exception:
            device = "cpu"
        workers = auto_refine_workers(gpu_mode=device)
    if workers >= 2 and ctx.video_path and ocr_results:
        try:
            from core.refine_executor import refine_boundaries_parallel
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Boundary refinement running on {} threads..."
                ).format(workers)
            )
            return refine_boundaries_parallel(
                ocr_results,
                ctx.roi_data,
                video_path=ctx.video_path,
                fps=ctx.fps,
                engine_id=ctx.ocr_engine_id,
                engine_options=ctx.engine_options,
                workers=workers,
                max_backtrack_frames=max(3, int(ctx.fps * 1.0)) if ctx.fps and ctx.fps > 0 else 25,
                max_forward_frames=max(2, int(ctx.fps * 0.5)) if ctx.fps and ctx.fps > 0 else 12,
                edge_extend_frames=max(2, int(ctx.fps * 2.0)) if ctx.fps and ctx.fps > 0 else 50,
                progress_callback=_refine_progress,
                is_cancelled_func=cancel_check,
            )
        except Exception:
            logger.warning(
                "Parallel boundary refinement failed; falling back to serial.",
                exc_info=True,
            )

    return refine_fade_in_boundaries(
        ctx,
        ocr_results,
        max_backtrack_frames=max(3, int(ctx.fps * 1.0)) if ctx.fps and ctx.fps > 0 else 25,
        max_forward_frames=max(2, int(ctx.fps * 0.5)) if ctx.fps and ctx.fps > 0 else 12,
        edge_extend_frames=max(2, int(ctx.fps * 2.0)) if ctx.fps and ctx.fps > 0 else 50,
        progress_callback=_refine_progress,
        cancel_check=cancel_check,
    )


def restore_stage(
    ctx: PipelineContext,
    ocr_results: List[tuple],
    *,
    progress_cb: ProgressCb,
    cancel_check: CancelCheck,
) -> List[tuple]:
    """Step 3 (progress 80-90): restore ROI-crop coordinates to full-frame space."""
    progress_cb(80, QCoreApplication.translate(
        "pipeline_worker", "Step 3/4: Starting coordinate restoration... (0/{})").format(len(ocr_results)))
    if cancel_check():
        raise PipelineCancelled()

    t0_restore = time.perf_counter()
    restored_results: List[tuple] = []
    restore_start_progress = RESTORE_START
    restore_progress_range = RESTORE_RANGE

    # Note: coordinate restorer JSON output is optional (default follows debug_mode).
    # Pass the same flag as OCR JSON output for consistency.
    restored_generator = coordinate_restorer.restore_coordinates(
        iter(ocr_results),
        ctx.work_dir,
        save_json=ctx.save_intermediate_json
    )

    for i, restored_result in enumerate(restored_generator):
        if cancel_check():
            raise PipelineCancelled()
        restored_results.append(restored_result)
        progress = restore_start_progress + int(((i + 1) / len(ocr_results)) * restore_progress_range)
        progress_cb(
            progress,
            QCoreApplication.translate(
                "pipeline_worker",
                "Step 3/4: Restoring coordinates... ({}/{})"
            ).format(i + 1, len(ocr_results))
        )

    if not restored_results:
        raise RuntimeError(QCoreApplication.translate(
            "pipeline_worker", "Coordinate restoration step did not produce any results."))
    progress_cb(90, QCoreApplication.translate("pipeline_worker", "Step 3/4: Coordinate restoration complete."))
    t1_restore = time.perf_counter()
    logger.info(
        QCoreApplication.translate(
            "pipeline_worker",
            "Coordinate restoration done: {} frames in {:.2f}s (save_json={})."
        ).format(len(restored_results), (t1_restore - t0_restore), "ON" if ctx.save_intermediate_json else "OFF")
    )
    return restored_results


def make_boundary_probe_funcs(ctx: PipelineContext, cap, optimizer, cancel_check: CancelCheck):
    """Build OCR callbacks for boundary refinement.

    All random-access OCR shares one VideoCapture and one optimizer, and
    every result carries the frame's own POS_MSEC timestamp so refined
    boundaries stay consistent with the sequential extraction timeline.
    """
    min_score = 0.6  # matches OCRToASSOptimizer.MIN_SCORE_THRESHOLD

    def _ocr_at(roi_entry: Dict, roi_id: str, frame_num: int, upscale: bool) -> Optional[tuple]:
        if cancel_check():
            return None
        ct = roi_extractor.extract_single_roi_crop_with_time(
            ctx.video_path, roi_entry, int(frame_num), cap=cap, fps=ctx.fps
        )
        if ct is None:
            return None
        crop, time_sec = ct
        if upscale:
            h, w = crop.shape[:2]
            if w > 0 and h > 0 and max(w * 2, h * 2) <= 8192:
                crop = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        try:
            # use_cache=False: the same frame may be probed twice here with
            # different crops (normal + 2x upscale); the (roi, frame) cache
            # key cannot distinguish them.
            return optimizer._run_single_ocr(
                (roi_entry, crop, int(frame_num), roi_id, float(time_sec)),
                use_cache=False,
            )
        except Exception:
            logger.warning("Boundary refinement OCR failed at frame %s", frame_num, exc_info=True)
            return None

    def ocr_frame_func(roi_entry: Dict, roi_id: str, frame_num: int) -> Optional[tuple]:
        return _ocr_at(roi_entry, roi_id, frame_num, upscale=False)

    def probe_frame_func(roi_entry: Dict, roi_id: str, frame_num: int) -> Optional[tuple]:
        # Sensitive detector: a faint fade-in/out frame often only becomes
        # readable after a 2x upscale, which is exactly the 1-2 frame lag
        # this refinement exists to fix.
        r = _ocr_at(roi_entry, roi_id, frame_num, upscale=False)
        if r is not None and boundary_refine.ocr_text_present(r[1], min_score):
            return r
        return _ocr_at(roi_entry, roi_id, frame_num, upscale=True)

    return ocr_frame_func, probe_frame_func


def refine_fade_in_boundaries(
    ctx: PipelineContext,
    ocr_results: List[tuple],
    *,
    max_backtrack_frames: int,
    max_forward_frames: int,
    edge_extend_frames: int = 0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_check: CancelCheck,
) -> List[tuple]:
    """
    For ROIs that enabled fade refinement, re-run OCR frame-by-frame around
    text appearance/disappearance edges and walk to the true first/last
    visible-text frame, so subtitle timing matches the picture instead of
    the first frame OCR happened to read.

    ``edge_extend_frames`` lets the walk cross the ROI's own start/end
    (random-access probing), recovering subtitles clipped by the ROI
    time range.
    """
    if not ocr_results:
        return ocr_results
    if not ctx.video_path:
        return ocr_results

    cap = cv2.VideoCapture(ctx.video_path)
    if not cap.isOpened():
        logger.warning("Boundary refinement skipped: could not reopen video.")
        return ocr_results
    optimizer = OcrOptimizer(
        work_dir=ctx.work_dir or "",
        visualize=ctx.visualize,
        in_memory_mode=True,
        save_ocr_json=ctx.save_intermediate_json,
        ocr_engine_id=ctx.ocr_engine_id,
        engine_options=ctx.engine_options,
    )
    try:
        ocr_frame_func, probe_frame_func = make_boundary_probe_funcs(ctx, cap, optimizer, cancel_check)
        refined = boundary_refine.refine_boundaries(
            ocr_results,
            ctx.roi_data,
            ocr_frame_func=ocr_frame_func,
            probe_frame_func=probe_frame_func,
            is_cancelled_func=cancel_check,
            max_backtrack_frames=max_backtrack_frames,
            max_forward_frames=max_forward_frames,
            edge_extend_frames=edge_extend_frames,
            progress_callback=progress_callback,
        )
        extra_calls = int(getattr(optimizer, "ocr_calls", 0))
        if extra_calls:
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Boundary refinement used {} extra single-frame OCR calls."
                ).format(extra_calls)
            )
        return refined
    except Exception:
        logger.warning("Boundary refinement failed; keeping original timing.", exc_info=True)
        return ocr_results
    finally:
        optimizer.cleanup()
        cap.release()
