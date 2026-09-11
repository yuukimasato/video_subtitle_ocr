# core/pipeline_worker.py
import os
import logging
import datetime
import shutil
import time
from dataclasses import replace
from PySide6.QtCore import QThread, Signal, QCoreApplication

from typing import Callable, List, Dict, Optional, Any

from core import subtitle_generator
from core import chunk_planner, chunk_parallel_runner
from core import pipeline_stages
from core.pipeline_stages import PipelineContext, PipelineCancelled
from core.subtitle_llm_polish import SubtitlePolisherConfig

logger = logging.getLogger(__name__)

class PipelineWorker(QThread):
    progress_updated = Signal(int, str)
    llm_detail = Signal(str)
    # NOTE: deliberately NOT named `finished` — that would shadow
    # QThread.finished and break the built-in thread-completion signal.
    pipeline_finished = Signal(str)
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
                 engine_options: Optional[Dict[str, Any]] = None,
                 watermark_filter_config: Optional[Dict[str, Any]] = None,
                 chunk_workers: int = 0,
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
        # 深度扫描产出的水印清单（{"enabled": bool, "entries": [...]}），
        # 在 ASS 生成阶段独立于场景过滤执行。
        self.watermark_filter_config = watermark_filter_config
        # Engine initialization options (lang, model_tier, ...) forwarded to
        # OcrOptimizer → ocr_engine_manager.set_engine(engine_id, options).
        self.engine_options = dict(engine_options or {})
        # Chunk-parallel OCR (long-video speedup): 0 = auto (decide by
        # duration/cores/RAM/GPU), 1 = force single-process path, N>1 =
        # cap the auto worker count at N.
        self.chunk_workers = int(chunk_workers or 0)
        self.is_cancelled = False
        self.work_dir: Optional[str] = None

    def _stage_ctx(self) -> PipelineContext:
        """Picklable parameter bundle for stage functions (steps 1-3)."""
        return PipelineContext(
            video_path=self.video_path,
            roi_data=self.roi_data,
            total_frames=self.total_frames,
            fps=self.fps,
            work_dir=self.work_dir or "",
            debug_mode=self.debug_mode,
            in_memory_ocr=self.in_memory_ocr,
            visualize=self.visualize,
            time_slice_enabled=self.time_slice_enabled,
            time_slice_seconds=self.time_slice_seconds,
            merge_rois=self.merge_rois,
            save_intermediate_json=self.save_intermediate_json,
            color_presence_gate_spec=self.color_presence_gate_spec,
            ocr_engine_id=self.ocr_engine_id,
            engine_options=self.engine_options,
        )

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

            ctx = self._stage_ctx()

            def _progress(pct: int, message: str) -> None:
                if not message:
                    # Chunk-parallel workers report bare percentages; give
                    # the progress dialog a translated umbrella label.
                    message = QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 1-3/4: Processing chunks in parallel...")
                self.progress_updated.emit(pct, message)

            cancel_check: Callable[[], bool] = lambda: self.is_cancelled

            # Long-video speedup: split into per-worker windows when the
            # planner says it pays off. visualize/debug per-frame dumps stay
            # single-process, as does an explicit chunk_workers=1.
            plan = None
            if self.chunk_workers != 1 and not self.visualize:
                plan = chunk_planner.plan_chunks(
                    self.total_frames, self.fps,
                    cpu_count=os.cpu_count() or 1,
                    available_ram_mb=chunk_parallel_runner.probe_available_ram_mb(),
                    gpu_mode=chunk_parallel_runner.get_device_mode_light(),
                    max_workers=max(0, self.chunk_workers),
                )

            if plan is not None:
                logger.info(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Chunk-parallel OCR: {} workers, {} windows."
                    ).format(plan.workers, len(plan.windows))
                )
                restored_results = chunk_parallel_runner.run_chunk_parallel(
                    ctx, plan, progress_cb=_progress, cancel_check=cancel_check)
            else:
                # Steps 1+2 (progress 0-65): extraction + intelligent OCR.
                ocr_results, ocr_stats = pipeline_stages.extract_and_ocr_stage(
                    ctx, progress_cb=_progress, cancel_check=cancel_check)

                # Step 2.5 (progress 65-80): per-ROI boundary refinement.
                ocr_results = pipeline_stages.refine_stage(
                    ctx, ocr_results, ocr_stats=ocr_stats,
                    progress_cb=_progress, cancel_check=cancel_check)

                if self.is_cancelled: return

                # Step 3 (progress 80-90): coordinate restoration.
                restored_results = pipeline_stages.restore_stage(
                    ctx, ocr_results, progress_cb=_progress, cancel_check=cancel_check)

            if self.is_cancelled: return

            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 4/4: Starting ASS subtitle file generation..."))
            if self.is_cancelled: return

            t0_ass = time.perf_counter()
            polisher_cfg = self.subtitle_polisher
            if polisher_cfg is not None:
                polisher_cfg = replace(
                    polisher_cfg,
                    log_line=lambda s: self.llm_detail.emit(s),
                )
            # Per-ROI placement metadata for ROIs with "write pose tags" on.
            roi_pose_tags: Dict[str, Dict[str, Any]] = {}
            # Per-ROI text-filter policy ("keep_all" for manual ROIs — scene
            # text must survive; "auto" for detected main-subtitle bands).
            roi_text_filter_policies: Dict[str, str] = {}
            for idx, roi in enumerate(self.roi_data or []):
                if not isinstance(roi, dict):
                    continue
                pose = roi.get("pose")
                if roi.get("write_pose_tags") and isinstance(pose, dict):
                    roi_pose_tags[f"roi_{idx}"] = pose
                roi_text_filter_policies[f"roi_{idx}"] = str(
                    roi.get("text_filter_policy") or "keep_all"
                )
            if self.merge_rois:
                # 所有 ROI 合成同一张画布：任一 keep_all 即整体不过滤。
                merged_policy = (
                    "keep_all"
                    if "keep_all" in set(roi_text_filter_policies.values())
                    else "auto"
                )
                roi_text_filter_policies = {"roi_merged": merged_policy}
            converter = subtitle_generator.OCRToASSOptimizer(
                video_path=self.video_path,
                output_path=self.output_ass_path,
                fps=self.fps,
                width=self.video_width,
                height=self.video_height,
                template_path=self.template_path,
                subtitle_polisher=polisher_cfg,
                source_filter_config=self.source_filter_config,
                roi_pose_tags=roi_pose_tags or None,
                roi_text_filter_policies=roi_text_filter_policies or None,
                watermark_filter_config=self.watermark_filter_config,
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

            self.pipeline_finished.emit(self.output_ass_path)

        except PipelineCancelled:
            return
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

    def _refine_fade_in_boundaries(
        self,
        ocr_results: List[tuple],
        *,
        max_backtrack_frames: int,
        max_forward_frames: int,
        edge_extend_frames: int = 0,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[tuple]:
        """Thin delegate to the extracted stage function (kept for the
        benchmark script and tests that drive refinement directly)."""
        return pipeline_stages.refine_fade_in_boundaries(
            self._stage_ctx(),
            ocr_results,
            max_backtrack_frames=max_backtrack_frames,
            max_forward_frames=max_forward_frames,
            edge_extend_frames=edge_extend_frames,
            progress_callback=progress_callback,
            cancel_check=lambda: self.is_cancelled,
        )
