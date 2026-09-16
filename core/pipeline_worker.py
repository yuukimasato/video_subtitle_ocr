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


def _roi_analysis_rect(roi: Dict[str, Any]) -> Optional[tuple]:
    """ROI 几何 → 视频坐标外接矩形 (x1, y1, x2, y2);无法解析时 None。

    rect 型 points=[x, y, w, h];poly 型取顶点 min/max。
    """
    rtype = roi.get("type", "rect")
    points = roi.get("points")
    try:
        if rtype == "rect":
            if not (isinstance(points, (list, tuple)) and len(points) == 4):
                return None
            x, y, w, h = (float(v) for v in points)
            return (int(round(x)), int(round(y)),
                    int(round(x + w)), int(round(y + h)))
        if rtype == "poly":
            if not (isinstance(points, (list, tuple)) and len(points) >= 3):
                return None
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            return (int(round(min(xs))), int(round(min(ys))),
                    int(round(max(xs))), int(round(max(ys))))
    except (TypeError, ValueError, IndexError):
        return None
    return None


def collect_roi_scene_text_options(
    roi_data: Optional[List[Dict]],
    merge_rois: bool = False,
) -> tuple:
    """收集每 ROI 场景文字显示策略与外接矩形(传给 OCRToASSOptimizer)。

    - 仅收集非 overlap 策略:缺省/overlap 不传,构造参数缺省 None、行为
      与旧版本完全一致;外接矩形对全部 ROI 收集(策略分析图裁剪窗口);
    - ``merge_rois`` 时全部 ROI 合成同一张画布 ``roi_merged``:策略取第一
      个非 overlap,出现互不相同的策略时 logger.warning;
    - 返回 ``(policies, rects)`` 两个 dict,可能为空。
    """
    policies: Dict[str, str] = {}
    rects: Dict[str, tuple] = {}
    for idx, roi in enumerate(roi_data or []):
        if not isinstance(roi, dict):
            continue
        policy = str(roi.get("scene_text_policy") or "overlap")
        if policy != "overlap":
            policies[f"roi_{idx}"] = policy
        rect = _roi_analysis_rect(roi)
        if rect is not None:
            rects[f"roi_{idx}"] = rect
    if not policies:
        return {}, {}
    if merge_rois:
        distinct: List[str] = []
        for policy in policies.values():
            if policy not in distinct:
                distinct.append(policy)
        if len(distinct) > 1:
            logger.warning(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "ROIs merged with differing scene text policies ({}); using the first one ({})."
                ).format(", ".join(distinct), distinct[0])
            )
        if rects:
            x1 = min(r[0] for r in rects.values())
            y1 = min(r[1] for r in rects.values())
            x2 = max(r[2] for r in rects.values())
            y2 = max(r[3] for r in rects.values())
            rects = {"roi_merged": (x1, y1, x2, y2)}
        else:
            rects = {}
        policies = {"roi_merged": distinct[0]}
    return policies, rects

def collect_motion_roi_specs(roi_data: Optional[List[Dict]]) -> List[Dict[str, Any]]:
    """收集走移动文字轨迹管线的 ROI 规格。

    绑定规则:「写入画面位置标签」勾选(write_pose_tags)且 ROI 为四点
    多边形(type="poly"、points 恰 4 个顶点)——四点即文字平面 quad,静态
    pose 标签跟不动运动画面,这类 ROI 交给轨迹管线合成 \\move 事件;其余
    (矩形、非四点多边形、未勾选 pose)保持静态路径。每个规格含 roi_id /
    quad / start_frame / end_frame / scene_text_policy / auto_brightness。
    """
    specs: List[Dict[str, Any]] = []
    for idx, roi in enumerate(roi_data or []):
        if not isinstance(roi, dict):
            continue
        if not roi.get("write_pose_tags"):
            continue
        if str(roi.get("type", "")) != "poly":
            continue
        points = roi.get("points")
        if not (isinstance(points, list) and len(points) == 4):
            continue
        try:
            quad = [[float(p[0]), float(p[1])] for p in points]
            start_frame = int(roi.get("start_frame", 0) or 0)
            end_frame = roi.get("end_frame")
            end_frame = int(end_frame) if end_frame is not None else None
        except (TypeError, ValueError, IndexError):
            continue
        specs.append({
            "roi_id": f"roi_{idx}",
            "quad": quad,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "scene_text_policy": str(roi.get("scene_text_policy") or "overlap"),
            "auto_brightness": bool(roi.get("motion_auto_brightness", False)),
        })
    return specs


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

    def _run_motion_stage(self) -> tuple:
        """移动文字轨迹阶段(pose 勾选 + 四点多边形 ROI)。

        对每个轨迹 ROI 调 scripts/motion_ass.build_motion_events(独立 OCR
        引擎实例,不占用主流水线进程级单例),返回 ``(events, roi_ids)``:
        events 合并进最终 .ass,roi_ids(轨迹接管成功的 ROI)在生成器里
        抑制对应静态事件,避免同区域双份文本。单个 ROI 失败仅告警并回退
        该 ROI 的静态 pose 路径,不中断任务链。
        """
        specs = collect_motion_roi_specs(self.roi_data)
        if not specs:
            return [], set()
        from scripts.motion_ass import (
            build_motion_events,
            normalize_quad_winding,
            validate_quad,
        )

        self.progress_updated.emit(
            90,
            QCoreApplication.translate(
                "pipeline_worker",
                "Step 4/4: Tracking moving-text plane(s) for trajectory subtitles...",
            ),
        )
        engine_id = str(self.ocr_engine_id) if self.ocr_engine_id else None
        events_all: List[Dict[str, Any]] = []
        taken_over: set = set()
        for spec in specs:
            roi_id = spec["roi_id"]
            try:
                quad = validate_quad(normalize_quad_winding(spec["quad"]))
                events, summary = build_motion_events(
                    self.video_path, quad,
                    start_frame=spec["start_frame"],
                    end_frame=spec["end_frame"],
                    scene_text_policy=spec["scene_text_policy"],
                    auto_brightness=spec["auto_brightness"],
                    ocr_engine=engine_id,
                    log=logger.info,
                )
                events_all.extend(events)
                taken_over.add(roi_id)
                logger.info(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Motion trajectory for {0}: {1} event(s) ({2}/{3} frames ok, keyframes {4}, policy {5}).",
                    ).format(
                        roi_id, len(events), summary["ok_frames"],
                        summary["total_frames"], summary["keyframes"],
                        summary["policy"],
                    )
                )
            except Exception as exc:
                logger.warning(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Motion trajectory for {0} failed ({1}); falling back to static pose tags.",
                    ).format(roi_id, exc),
                    exc_info=True,
                )
        return events_all, taken_over

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
            # single-process, as does an explicit chunk_workers=1; disk-mode
            # extraction also stays single-process — overlapping windows write
            # the same per-frame files in the shared work_dir and race.
            plan = None
            if (
                self.chunk_workers != 1
                and not self.visualize
                and not self.save_intermediate_json
                and self.in_memory_ocr
            ):
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
            # 场景文字显示策略(仅非 overlap)与 ROI 外接矩形:collect 在
            # 无策略时返回空 dict → 传 None,优化器行为与旧版本一致。
            roi_scene_text_policies, roi_analysis_rects = (
                collect_roi_scene_text_options(self.roi_data, self.merge_rois))
            # 移动文字轨迹阶段(pose 勾选 + 四点多边形 ROI;无此类 ROI 时
            # 为空列表/空集,生成器行为与旧版本一致)。
            motion_events, motion_roi_ids = self._run_motion_stage()
            if self.is_cancelled:
                return
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
                roi_scene_text_policies=roi_scene_text_policies or None,
                roi_analysis_rects=roi_analysis_rects or None,
                motion_events=motion_events or None,
                motion_roi_ids=motion_roi_ids or None,
            )
            converter.convert_from_memory(
                iter(restored_results),
                polish_progress_callback=lambda p, msg: self.progress_updated.emit(p, msg),
                polish_cancel_check=lambda: self.is_cancelled,
            )
            if self.is_cancelled:
                # 阶段 4 的取消走 polish_cancel_check（内部 break 后仍会写出
                # 半成品 ASS 并正常返回）；与阶段 1-3 的取消语义保持一致，
                # 这里直接静默返回，不上报成功。
                return
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
