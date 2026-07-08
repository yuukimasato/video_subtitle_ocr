# core/pipeline_worker.py
import os
import logging
import datetime
import shutil
import time
from dataclasses import replace
from PySide6.QtCore import QThread, Signal, QCoreApplication
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from typing import List, Dict, Optional, Any
from collections import defaultdict
import re

from core import roi_extractor, ocr_processor, coordinate_restorer, subtitle_generator
from core.ocr_optimizer import OcrOptimizer
from core.subtitle_llm_polish import SubtitlePolisherConfig

logger = logging.getLogger(__name__)

class PipelineWorker(QThread):
    progress_updated = Signal(int, str)
    llm_detail = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, video_path: str, roi_data: List[Dict], total_frames: int, fps: float,
                 video_width: int, video_height: int, output_ass_path: str,
                 debug_mode: bool, template_path: Optional[str],
                 in_memory_ocr: bool = False, visualize: bool = False,
                 time_slice_enabled: bool = False, time_slice_seconds: float = 10.0,
                 merge_rois: bool = False,
                 subtitle_polisher: Optional[SubtitlePolisherConfig] = None,
                 save_intermediate_json: Optional[bool] = None,
                 color_presence_gate_spec: Optional[Dict[str, Any]] = None,
                 ocr_engine_id: str = "",
                 source_filter_config: Optional[Dict[str, Any]] = None,
                 parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.roi_data = roi_data
        self.total_frames = total_frames
        self.fps = fps
        self.video_width = video_width
        self.video_height = video_height
        self.output_ass_path = output_ass_path
        self.debug_mode = debug_mode
        self.template_path = template_path
        self.in_memory_ocr = in_memory_ocr
        self.visualize = visualize
        self.time_slice_enabled = time_slice_enabled
        self.time_slice_seconds = max(0.1, float(time_slice_seconds or 10.0))
        self.merge_rois = bool(merge_rois)
        self.subtitle_polisher = subtitle_polisher
        # Default behavior: keep intermediate JSON only when debugging.
        self.save_intermediate_json = bool(debug_mode) if save_intermediate_json is None else bool(save_intermediate_json)
        self.color_presence_gate_spec = color_presence_gate_spec
        self.ocr_engine_id = ocr_engine_id
        self.source_filter_config = source_filter_config
        self.is_cancelled = False
        self.work_dir: Optional[str] = None

    def run(self):
        try:
            t0_total = time.perf_counter()
            video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            self.work_dir = os.path.join(os.path.dirname(self.output_ass_path), f"{video_name}_{timestamp}_ocr_temp")
            os.makedirs(self.work_dir, exist_ok=True)
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Intermediate files will be saved to: {}"
                ).format(self.work_dir)
            )
            
            log_mode_in_memory = QCoreApplication.translate("pipeline_worker", "in-memory data stream")
            log_mode_disk_file = QCoreApplication.translate("pipeline_worker", "disk file stream")
            log_mode = log_mode_in_memory if self.in_memory_ocr else log_mode_disk_file
            
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "OCR pipeline will run in {} mode."
                ).format(log_mode)
            )

            self.progress_updated.emit(0, QCoreApplication.translate("pipeline_worker", "Step 1/4: Calculating number of ROI frames to process..."))
            if self.is_cancelled: return

            try:
                t0_roi = time.perf_counter()
                if self.merge_rois:
                    total_roi_frames = roi_extractor.calculate_total_merged_frames(
                        self.roi_data, self.total_frames, self.fps
                    )
                else:
                    total_roi_frames = roi_extractor.calculate_total_roi_frames(
                        self.roi_data, self.total_frames, self.fps
                    )

                if self.is_cancelled: return

                if not total_roi_frames:
                    raise RuntimeError(QCoreApplication.translate("pipeline_worker", "ROI extraction step did not produce any data. Please check ROI time and region settings."))

                self.progress_updated.emit(
                    1,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 1/4: Calculation complete, total {} frames. Starting extraction..."
                    ).format(total_roi_frames)
                )

                roi_start_progress = 1
                roi_progress_range = 9

                extraction_progress_total = total_roi_frames
                gate_spec = self.color_presence_gate_spec
                if gate_spec:
                    est = gate_spec.get("estimated_kept_roi_frames")
                    if isinstance(est, int) and est >= 1:
                        extraction_progress_total = est

                frame_generator = roi_extractor.extract_roi_frames(
                    self.video_path, self.roi_data, self.total_frames, self.fps, self.work_dir,
                    save_to_disk=not self.in_memory_ocr,
                    color_presence_gate=gate_spec,
                )
                if self.merge_rois:
                    frame_generator = roi_extractor.extract_merged_roi_frames(
                        self.video_path, self.roi_data, self.total_frames, self.fps, self.work_dir,
                        save_to_disk=not self.in_memory_ocr,
                        color_presence_gate=gate_spec,
                    )

                extracted_count = 0

                # If time slicing is enabled, we can stream: extract frames, buffer per ROI+bucket,
                # and OCR previous bucket as soon as we detect a bucket switch for that ROI.
                stream_ocr = bool(self.time_slice_enabled and self.time_slice_seconds > 0)

                # Step 2 setup (may run during extraction in stream_ocr mode).
                self.progress_updated.emit(
                    10,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 2/4: Starting intelligent OCR recognition... (0/{})"
                    ).format(total_roi_frames)
                )

                t0_ocr = time.perf_counter()
                device_mode = ocr_processor.get_device_mode()
                if stream_ocr:
                    logger.info(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Streaming mode enabled (time_slice={}s). OCR will run during extraction to reduce peak memory."
                        ).format(self.time_slice_seconds)
                    )

                optimizer = OcrOptimizer(
                    work_dir=self.work_dir,
                    visualize=self.visualize,
                    in_memory_mode=self.in_memory_ocr,
                    save_ocr_json=self.save_intermediate_json,
                    ocr_engine_id=self.ocr_engine_id,
                )

                ocr_results = []
                ocr_start_progress = 10
                ocr_progress_range = 70
                processed_count = 0
                total_ocr_calls = 0
                total_frames_filled = 0

                # Streaming parallel flush (CPU only). In GPU mode, keep sequential to avoid VRAM contention.
                cpu_workers = (os.cpu_count() or 4)
                stream_parallel = bool(stream_ocr and device_mode == "cpu" and not self.visualize)
                stream_executor: Optional[ThreadPoolExecutor] = None
                pending_futures = set()
                future_frame_counts: Dict[object, int] = {}
                max_stream_workers = max(1, min(4, cpu_workers))
                max_outstanding = max_stream_workers * 2

                def _process_bucket_task(frames: List[tuple]):
                    local_opt = OcrOptimizer(
                        work_dir=self.work_dir,
                        visualize=self.visualize,
                        in_memory_mode=self.in_memory_ocr,
                        save_ocr_json=self.save_intermediate_json,
                        ocr_engine_id=self.ocr_engine_id,
                    )
                    frames.sort(key=lambda x: x[2])
                    res = local_opt.process_roi_group(
                        frames,
                        is_cancelled_func=lambda: self.is_cancelled,
                        progress_callback=None
                    )
                    calls = int(getattr(local_opt, "ocr_calls", 0))
                    filled = int(getattr(local_opt, "frames_filled", 0))
                    local_opt.cleanup()
                    return res, calls, filled, len(frames)

                def _collect_one_completed(block: bool):
                    nonlocal processed_count, total_ocr_calls, total_frames_filled
                    if not pending_futures:
                        return
                    it = as_completed(list(pending_futures))
                    for fut in it:
                        pending_futures.discard(fut)
                        try:
                            res, calls, filled, frame_cnt = fut.result()
                        except Exception as e:
                            raise
                        ocr_results.extend(res)
                        processed_count += int(frame_cnt)
                        total_ocr_calls += int(calls)
                        total_frames_filled += int(filled)

                        current_total_processed = min(processed_count, total_roi_frames)
                        progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                        self.progress_updated.emit(
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
                    if t <= 0 and self.fps and self.fps > 0:
                        t = float(frame_data[2]) / float(self.fps)
                    if t <= 0:
                        return 0
                    return int(t // self.time_slice_seconds)

                def _flush_one_roi_bucket(roi_id: str):
                    frames = buffer_by_roi.get(roi_id) or []
                    if not frames:
                        return
                    if stream_parallel and stream_executor is not None:
                        fut = stream_executor.submit(_process_bucket_task, list(frames))
                        pending_futures.add(fut)
                        future_frame_counts[fut] = len(frames)
                        # Backpressure: don't let too many outstanding buckets build up.
                        if len(pending_futures) >= max_outstanding:
                            _collect_one_completed(block=True)
                    else:
                        # Sequential flush using shared optimizer (keeps caches).
                        frames.sort(key=lambda x: x[2])
                        res = optimizer.process_roi_group(
                            frames,
                            is_cancelled_func=lambda: self.is_cancelled,
                            progress_callback=None
                        )
                        ocr_results.extend(res)
                        processed_count += len(frames)
                        total_ocr_calls = int(getattr(optimizer, "ocr_calls", 0))
                        total_frames_filled = int(getattr(optimizer, "frames_filled", 0))

                        current_total_processed = min(processed_count, total_roi_frames)
                        progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                        self.progress_updated.emit(
                            progress,
                            QCoreApplication.translate(
                                "pipeline_worker",
                                "Step 2/4: OCR recognition in progress... ({}/{})"
                            ).format(current_total_processed, total_roi_frames)
                        )
                    # Clear buffer to release memory (important for in-memory mode).
                    buffer_by_roi[roi_id] = []

                for i, frame_data in enumerate(frame_generator):
                    if self.is_cancelled: return

                    extracted_count += 1
                    # Step 1 progress (extraction)
                    denom = max(1, extraction_progress_total)
                    progress = roi_start_progress + int((extracted_count / denom) * roi_progress_range)
                    progress = min(roi_start_progress + roi_progress_range, progress)
                    self.progress_updated.emit(
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

                # End of extraction
                self.progress_updated.emit(
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

            except Exception as e:
                logger.error(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Error during ROI extraction: {}"
                    ).format(e)
                )
                raise

            if self.is_cancelled: return

            if not stream_ocr:
                # Non-streaming legacy path: run OCR after extraction, keeping the previous parallel strategy.
                frames_to_process = buffer_by_roi.get("__ALL__", [])
                roi_groups = defaultdict(list)
                for frame_data in frames_to_process:
                    roi_identifier = frame_data[3]
                    roi_groups[roi_identifier].append(frame_data)

                # Parallelize by ROI group (safe boundary). Each thread gets its own optimizer (and thread-local OCR instance).
                cpu_workers = (os.cpu_count() or 4)
                max_workers = min(len(roi_groups), cpu_workers)
                if device_mode == "gpu":
                    max_workers = 1
                use_parallel = max_workers > 1 and not self.visualize

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
                    self.progress_updated.emit(
                        progress,
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Step 2/4: OCR recognition in progress... ({}/{})"
                        ).format(current_total_processed, total_roi_frames)
                    )

                def process_one_group(roi_id: str, frames: List):
                    if self.is_cancelled:
                        return roi_id, [], 0, 0, 0, 0
                    local_opt = OcrOptimizer(
                        work_dir=self.work_dir,
                        visualize=self.visualize,
                        in_memory_mode=self.in_memory_ocr,
                        save_ocr_json=self.save_intermediate_json,
                        ocr_engine_id=self.ocr_engine_id,
                    )
                    frames.sort(key=lambda x: x[2])

                    # In parallel mode, use the optimizer's internal progress callback to update
                    # global progress smoothly (instead of only when each ROI group finishes).
                    last_reported = 0
                    def progress_callback(group_processed_count: int):
                        nonlocal last_reported, processed_count
                        if self.is_cancelled:
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
                        is_cancelled_func=lambda: self.is_cancelled,
                        progress_callback=progress_callback if use_parallel else None
                    )
                    calls = int(getattr(local_opt, "ocr_calls", 0))
                    filled = int(getattr(local_opt, "frames_filled", 0))
                    local_opt.cleanup()
                    return roi_id, res, calls, filled, len(frames), last_reported

                if use_parallel:
                    logger.info(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Running OCR in parallel (device={}, groups={}, max_workers={}, save_json={})."
                        ).format(device_mode.upper(), len(roi_groups), max_workers, "ON" if self.save_intermediate_json else "OFF")
                    )
                    with ThreadPoolExecutor(max_workers=max_workers) as ex:
                        futures = []
                        for roi_id, frames in sorted(roi_groups.items()):
                            if self.is_cancelled:
                                break
                            futures.append(ex.submit(process_one_group, roi_id, list(frames)))

                        for fut in as_completed(futures):
                            if self.is_cancelled:
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
                        ).format(device_mode.upper(), len(roi_groups), "ON" if self.save_intermediate_json else "OFF")
                    )
                    for roi_id, frames in sorted(roi_groups.items()):
                        if self.is_cancelled:
                            break
                        frames.sort(key=lambda x: x[2])

                        def progress_callback(group_processed_count: int):
                            current_total_processed = processed_count + group_processed_count
                            progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                            self.progress_updated.emit(
                                progress,
                                QCoreApplication.translate(
                                    "pipeline_worker",
                                    "Step 2/4: OCR recognition in progress... ({}/{})"
                                ).format(current_total_processed, total_roi_frames)
                            )

                        optimized_group_results = optimizer.process_roi_group(
                            frames,
                            is_cancelled_func=lambda: self.is_cancelled,
                            progress_callback=progress_callback
                        )
                        ocr_results.extend(optimized_group_results)
                        processed_count += len(frames)

                    total_ocr_calls = int(getattr(optimizer, "ocr_calls", 0))
                    total_frames_filled = int(getattr(optimizer, "frames_filled", 0))

                optimizer.cleanup()
            else:
                # Streaming path: flush remaining buffers at EOF, then cleanup.
                for roi_id in sorted(buffer_by_roi.keys()):
                    if roi_id == "__ALL__":
                        continue
                    if self.is_cancelled:
                        return
                    _flush_one_roi_bucket(roi_id)
                # Collect remaining parallel results (if any), then shutdown executor.
                while pending_futures:
                    _collect_one_completed(block=True)
                if stream_executor is not None:
                    stream_executor.shutdown(wait=True)
                optimizer.cleanup()

            if self.is_cancelled: return

            if not ocr_results:
                raise RuntimeError(QCoreApplication.translate("pipeline_worker", "OCR recognition step did not produce any results."))
            self.progress_updated.emit(80, QCoreApplication.translate("pipeline_worker", "Step 2/4: OCR recognition complete."))
            t1_ocr = time.perf_counter()
            est_skipped = max(0, total_roi_frames - total_ocr_calls)
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "OCR done: {} roi-frames filled, {} OCR calls (est. skipped {}), {:.2f}s."
                ).format(total_frames_filled, total_ocr_calls, est_skipped, (t1_ocr - t0_ocr))
            )

            if self.is_cancelled: return

            # Optional per-ROI boundary refinement: only run frame-by-frame OCR around
            # text appearance/disappearance edges to improve timing accuracy.
            # This is designed to correct cases where the optimizer fills/propagates text
            # across visually-similar frames during subtitle fade-in/out.
            if not self.merge_rois:
                try:
                    any_refine = any(bool(r.get("fade_in_refine_enabled")) for r in (self.roi_data or []))
                except Exception:
                    any_refine = False
                if any_refine:
                    self.progress_updated.emit(
                        80,
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Step 2/4: Refining fade-in timing (frame-by-frame near edges)...",
                        ),
                    )
                    ocr_results = self._refine_fade_in_boundaries(
                        ocr_results,
                        max_backtrack_frames=max(3, int(self.fps * 1.0)) if self.fps and self.fps > 0 else 25,
                        max_forward_frames=max(2, int(self.fps * 0.5)) if self.fps and self.fps > 0 else 12,
                    )

            self.progress_updated.emit(
                80,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 3/4: Starting coordinate restoration... (0/{})"
                ).format(len(ocr_results))
            )
            if self.is_cancelled: return

            t0_restore = time.perf_counter()
            restored_results = []
            restore_start_progress = 80
            restore_progress_range = 10

            # Note: coordinate restorer JSON output is optional (default follows debug_mode).
            # Pass the same flag as OCR JSON output for consistency.
            restored_generator = coordinate_restorer.restore_coordinates(
                iter(ocr_results),
                self.work_dir,
                save_json=self.save_intermediate_json
            )

            for i, restored_result in enumerate(restored_generator):
                if self.is_cancelled: return
                restored_results.append(restored_result)
                progress = restore_start_progress + int(((i + 1) / len(ocr_results)) * restore_progress_range)
                self.progress_updated.emit(
                    progress,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 3/4: Restoring coordinates... ({}/{})"
                    ).format(i + 1, len(ocr_results))
                )

            if not restored_results:
                raise RuntimeError(QCoreApplication.translate("pipeline_worker", "Coordinate restoration step did not produce any results."))
            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 3/4: Coordinate restoration complete."))
            t1_restore = time.perf_counter()
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Coordinate restoration done: {} frames in {:.2f}s (save_json={})."
                ).format(len(restored_results), (t1_restore - t0_restore), "ON" if self.save_intermediate_json else "OFF")
            )

            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 4/4: Starting ASS subtitle file generation..."))
            if self.is_cancelled: return

            t0_ass = time.perf_counter()
            polisher_cfg = self.subtitle_polisher
            if polisher_cfg is not None:
                polisher_cfg = replace(
                    polisher_cfg,
                    log_line=lambda s: self.llm_detail.emit(s),
                )
            converter = subtitle_generator.OCRToASSOptimizer(
                video_path=self.video_path,
                output_path=self.output_ass_path,
                fps=self.fps,
                width=self.video_width,
                height=self.video_height,
                template_path=self.template_path,
                subtitle_polisher=polisher_cfg,
                source_filter_config=self.source_filter_config,
            )
            converter.convert_from_memory(
                iter(restored_results),
                polish_progress_callback=lambda p, msg: self.progress_updated.emit(p, msg),
                polish_cancel_check=lambda: self.is_cancelled,
            )
            self.progress_updated.emit(100, QCoreApplication.translate("pipeline_worker", "Step 4/4: ASS subtitle generation complete."))
            t1_ass = time.perf_counter()
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "ASS generation done in {:.2f}s."
                ).format((t1_ass - t0_ass))
            )
            t1_total = time.perf_counter()
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Pipeline total time: {:.2f}s."
                ).format((t1_total - t0_total))
            )

            self.finished.emit(self.output_ass_path)

        except Exception as e:
            logger.error(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Pipeline processing failed: {}"
                ).format(e),
                exc_info=True
            )
            self.error.emit(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "An error occurred during processing: {}"
                ).format(e)
            )
        finally:
            if self.work_dir and not self.debug_mode:
                try:
                    shutil.rmtree(self.work_dir)
                    logger.info(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Temporary working directory deleted: {}"
                        ).format(self.work_dir)
                    )
                except Exception as e:
                    logger.warning(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Could not delete temporary working directory {}: {}"
                        ).format(self.work_dir, e)
                    )

    def cancel(self):
        self.is_cancelled = True
        logger.info(QCoreApplication.translate("pipeline_worker", "Task cancellation request sent."))
        
    def terminate(self):
        if self.isRunning():
            logger.warning(QCoreApplication.translate("pipeline_worker", "Forcibly terminating thread..."))
            super().terminate()

    @staticmethod
    def _ocr_text_present(ocr_data: object) -> bool:
        if not isinstance(ocr_data, dict):
            return False
        texts = ocr_data.get("rec_texts", [])
        if not isinstance(texts, list):
            return False
        return any(bool(str(t).strip()) for t in texts)

    def _roi_index_from_identifier(self, roi_identifier: str) -> Optional[int]:
        # roi_identifier is "roi_{idx}" in non-merged mode.
        if not roi_identifier:
            return None
        m = re.fullmatch(r"roi_(\d+)", str(roi_identifier).strip())
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _run_single_frame_ocr(self, roi_entry: Dict, frame_num: int, roi_identifier: str) -> Optional[tuple]:
        """
        Random access: extract one crop and OCR it. Returns a tuple shaped like ocr_results items.
        """
        if self.is_cancelled:
            return None
        try:
            crop = roi_extractor.extract_single_roi_crop(self.video_path, roi_entry, int(frame_num))
        except Exception:
            crop = None
        if crop is None:
            return None

        # Best-effort timestamp from fps (fine for local edge refinement).
        frame_time_sec = (float(frame_num) / float(self.fps)) if self.fps and self.fps > 0 else 0.0
        frame_data = (roi_entry, crop, int(frame_num), str(roi_identifier), frame_time_sec)
        try:
            opt = OcrOptimizer(
                work_dir=self.work_dir or "",
                visualize=self.visualize,
                in_memory_mode=True,
                save_ocr_json=self.save_intermediate_json,
                ocr_engine_id=self.ocr_engine_id,
            )
            res = opt._run_single_ocr(frame_data)
            opt.cleanup()
            return res
        except Exception:
            return None

    def _refine_fade_in_boundaries(
        self,
        ocr_results: List[tuple],
        *,
        max_backtrack_frames: int,
        max_forward_frames: int,
    ) -> List[tuple]:
        """
        For ROIs that enabled fade refinement, re-run OCR frame-by-frame around
        text appearance/disappearance edges to improve timing accuracy.

        - Fade-in (empty -> non-empty): fixes premature start caused by fill/skip.
        - Fade-out (non-empty -> empty): fixes late end caused by fill/skip.
        """
        if not ocr_results:
            return ocr_results

        # Index results by (roi_id, frame_num) to allow in-place replacement.
        index_map: Dict[tuple, int] = {}
        for idx, item in enumerate(ocr_results):
            try:
                frame_num = int(item[2])
                roi_id = str(item[3])
                index_map[(roi_id, frame_num)] = idx
            except Exception:
                continue

        # Scan per ROI sequentially by frame_num.
        by_roi: Dict[str, List[tuple]] = defaultdict(list)
        for item in ocr_results:
            try:
                by_roi[str(item[3])].append(item)
            except Exception:
                continue
        for roi_id in by_roi:
            by_roi[roi_id].sort(key=lambda x: int(x[2]))

        for roi_id, items in sorted(by_roi.items(), key=lambda kv: kv[0]):
            if self.is_cancelled:
                return ocr_results
            roi_idx = self._roi_index_from_identifier(roi_id)
            if roi_idx is None or not (0 <= roi_idx < len(self.roi_data)):
                continue
            roi_entry_cfg = self.roi_data[roi_idx]
            if not bool(roi_entry_cfg.get("fade_in_refine_enabled")):
                continue

            # Walk frames to find transitions: empty <-> non-empty.
            prev_has = False
            prev_frame_num: Optional[int] = None
            for it in items:
                if self.is_cancelled:
                    return ocr_results
                frame_num = int(it[2])
                has = self._ocr_text_present(it[1])
                if has and not prev_has:
                    # Fade-in edge at this frame. Refine around appearance.
                    start_frame = int(roi_entry_cfg.get("start_frame", frame_num))
                    end_frame = int(roi_entry_cfg.get("end_frame", frame_num))
                    # Backtrack window
                    back_start = max(start_frame, frame_num - int(max_backtrack_frames))
                    # Forward window (optional) to stabilize around fade-in.
                    fwd_end = min(end_frame, frame_num + int(max_forward_frames))

                    # Re-OCR a small window frame-by-frame and overwrite results.
                    # This ensures earlier frames aren't wrongly filled with later text.
                    for f in range(back_start, fwd_end + 1):
                        if self.is_cancelled:
                            return ocr_results
                        r = self._run_single_frame_ocr(roi_entry_cfg, f, roi_id)
                        if r is None:
                            continue
                        pos = index_map.get((roi_id, int(f)))
                        if pos is not None:
                            ocr_results[pos] = r
                    # After overwriting, stop further backtracking for nearby frames by
                    # re-evaluating prev_has from the just-processed edge frame.
                    pos_edge = index_map.get((roi_id, frame_num))
                    if pos_edge is not None:
                        prev_has = self._ocr_text_present(ocr_results[pos_edge][1])
                    else:
                        prev_has = True
                    prev_frame_num = frame_num
                    continue

                if (not has) and prev_has:
                    # Fade-out edge at this frame (current is empty, previous had text).
                    # Refine around disappearance to avoid overly-long end time.
                    # Use previous frame as the "last known text" anchor when available.
                    anchor = prev_frame_num if prev_frame_num is not None else (frame_num - 1)
                    start_frame = int(roi_entry_cfg.get("start_frame", anchor))
                    end_frame = int(roi_entry_cfg.get("end_frame", anchor))
                    back_start = max(start_frame, int(anchor) - int(max_backtrack_frames))
                    fwd_end = min(end_frame, frame_num + int(max_forward_frames))

                    for f in range(back_start, fwd_end + 1):
                        if self.is_cancelled:
                            return ocr_results
                        r = self._run_single_frame_ocr(roi_entry_cfg, f, roi_id)
                        if r is None:
                            continue
                        pos = index_map.get((roi_id, int(f)))
                        if pos is not None:
                            ocr_results[pos] = r

                    pos_now = index_map.get((roi_id, frame_num))
                    if pos_now is not None:
                        prev_has = self._ocr_text_present(ocr_results[pos_now][1])
                    else:
                        prev_has = False
                    prev_frame_num = frame_num
                    continue

                prev_has = has
                prev_frame_num = frame_num

        return ocr_results

