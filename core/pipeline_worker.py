# core/pipeline_worker.py
import os
import logging
import datetime
import shutil
import time
from dataclasses import replace
from PySide6.QtCore import QThread, Signal, QCoreApplication

from typing import TYPE_CHECKING, Callable, List, Dict, Optional, Any

from core import subtitle_generator
from core import chunk_planner, chunk_parallel_runner
from core import pipeline_stages
from core.pipeline_stages import PipelineContext, PipelineCancelled
from core.scene_text_policy import POLICY_MODES
from core.subtitle_llm_polish import SubtitlePolisherConfig
from utils.time_utils import parse_time

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # 仅类型标注：translation_config / font_identify_config 默认 None
    # （翻译与字体识别整体关闭），缺省路径不导入 font_intel 模块
    # （红线：翻译只挂主进程阶段 4；font_identify 是主进程后置阶段，
    # 字块图像不跨阶段携带、不进 chunk worker 参数包）。
    from font_intel.identify_stage import FontIdentifyConfig
    from font_intel.translation import TranslationConfig

# 轨迹接管覆盖门限:轨迹事件去重时长 / ROI 时长低于该值时不接管,该 ROI
# 整体回退静态路径(与跟踪失败同路径)。依据见 trajectory_takeover_ok。
TRAJECTORY_MIN_EVENT_COVERAGE = 0.5


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
    - 未知策略名(不在 POLICY_MODES)在此统一 ValueError——本函数是 CLI
      静态路径与 GUI 主流水线共用的收集入口,不静默透传;
    - ``merge_rois`` 时全部 ROI 合成同一张画布 ``roi_merged``:策略取第一
      个非 overlap,出现互不相同的策略时 logger.warning 并记录最终采用者
      (画布路径无法逐 ROI 拆分);
    - 返回 ``(policies, rects)`` 两个 dict,可能为空。
    """
    policies: Dict[str, str] = {}
    rects: Dict[str, tuple] = {}
    for idx, roi in enumerate(roi_data or []):
        if not isinstance(roi, dict):
            continue
        policy = str(roi.get("scene_text_policy") or "overlap")
        if policy != "overlap":
            if policy not in POLICY_MODES:
                raise ValueError(
                    f"unknown scene text policy {policy!r} for roi_{idx}; "
                    f"valid modes: {', '.join(POLICY_MODES)}")
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


def collect_roi_ocr_langs(roi_data: Optional[List[Dict]]) -> Dict[str, str]:
    """收集 roi_id → ocr_lang（GUI/CLI 通用的双语路由规则，T2.3）。

    翻译阶段按行所属 ROI 的识别语言路由源语言；未配置 ``ocr_lang`` 的
    ROI/非 dict 条目映射为空串 = 翻译侧回退自动检测。
    """
    langs: Dict[str, str] = {}
    for idx, roi in enumerate(roi_data or []):
        if not isinstance(roi, dict):
            continue
        langs[f"roi_{idx}"] = str(roi.get("ocr_lang") or "")
    return langs


def apply_cli_scene_text_policy(entries: Optional[List[Dict]],
                                cli_policy: Optional[str]) -> None:
    """CLI ``--scene-text-policy`` 覆盖 ROI 配置的固定优先级(就地修改)。

    - CLI 显式传入非 overlap 策略 → 覆盖全部 ROI 条目的策略(既有语义);
    - CLI 为缺省 overlap/空 → 不动条目,ROI JSON 内的逐 ROI 配置生效;
    - 合法性由 CLI argparse choices 校验,此处不重复校验。
    """
    if not cli_policy or cli_policy == "overlap":
        return
    for entry in entries or []:
        if isinstance(entry, dict):
            entry["scene_text_policy"] = cli_policy


def _normalize_plane_quad(points: List) -> Optional[List[List[float]]]:
    """把手绘多边形顶点归一化为文字平面四角（quad）。

    - 恰 4 点：原样返回（顶点顺序由下游自动纠正）；
    - 末点与首点几乎重合（手绘闭合点击——GUI 保存的多边形会把「回到起点」
      的那一击也存进去）时丢弃闭合点后再判；
    - 归一后仍多于 4 点：取最小外接矩形（``cv2.minAreaRect``）四角作近似
      ——文字平面（手机屏幕/信件/招牌）近似凸四边形，绕着屏幕点的杂点
      由外接矩形兜住；
    - 少于 4 点返回 None。
    """
    import math

    pts = [[float(p[0]), float(p[1])] for p in points]
    if len(pts) > 4:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        tol = max(4.0, 0.01 * diag)
        if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= tol:
            pts = pts[:-1]
    if len(pts) == 4:
        return pts
    if len(pts) > 4:
        import cv2
        import numpy as np

        box = cv2.boxPoints(cv2.minAreaRect(np.asarray(pts, dtype=np.float32)))
        return [[float(x), float(y)] for x, y in box]
    return None


def collect_motion_roi_specs(roi_data: Optional[List[Dict]]) -> List[Dict[str, Any]]:
    """收集走移动文字轨迹管线的 ROI 规格。

    绑定规则：「写入画面位置标签」勾选（write_pose_tags）且 ROI 形状是
    可跟踪的文字平面——四点多边形、带回闭点的手绘多边形（自动去重，见
    :func:`_normalize_plane_quad`）、一般多边形（最小外接矩形四角）或
    矩形（points=[x,y,w,h]，展开为四角）。静态 pose 标签跟不动运动画面，
    这类 ROI 交给轨迹管线合成 \\move 事件；其余（未勾选 pose、点数不足
    3、形状非法）保持静态路径。每个规格含 roi_id / quad / start_frame /
    end_frame / scene_text_policy / auto_brightness。
    """
    specs: List[Dict[str, Any]] = []
    for idx, roi in enumerate(roi_data or []):
        if not isinstance(roi, dict):
            continue
        if not roi.get("write_pose_tags"):
            continue
        rtype = str(roi.get("type", ""))
        points = roi.get("points")
        if rtype == "rect":
            if not (isinstance(points, (list, tuple)) and len(points) == 4):
                continue
            try:
                x, y, w, h = (float(v) for v in points)
            except (TypeError, ValueError):
                continue
            if w <= 0 or h <= 0:
                continue
            quad = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        elif rtype == "poly":
            if not (isinstance(points, list) and len(points) >= 4):
                continue
            try:
                quad = _normalize_plane_quad(points)
            except (TypeError, ValueError, IndexError):
                continue
            if quad is None:
                continue
        else:
            continue
        try:
            start_frame = int(roi.get("start_frame", 0) or 0)
            end_frame = roi.get("end_frame")
            end_frame = int(end_frame) if end_frame is not None else None
        except (TypeError, ValueError):
            continue
        specs.append({
            "roi_id": f"roi_{idx}",
            "quad": quad,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "scene_text_policy": str(roi.get("scene_text_policy") or "overlap"),
            "auto_brightness": bool(roi.get("motion_auto_brightness", False)),
            "occlusion_clip": bool(roi.get("motion_occlusion_clip", False)),
            "ocr_lang": str(roi.get("ocr_lang") or ""),
        })
    return specs


def trajectory_event_coverage_sec(
    events: List[Dict[str, Any]],
    fps: float,
) -> float:
    """轨迹事件覆盖的**去重**时间总长(秒)。

    事件起止是 ASS 时间串("0:00:20.02");相邻/重叠事件(同链分段)取并集
    而非简单求和,否则链条被打断成多段时覆盖会被高估。
    fps 无效或事件缺时间字段时按 0 处理(调用方据此判定回退)。
    """
    spans: List[tuple] = []
    for event in events or []:
        try:
            start = parse_time(str(event.get("start_time") or ""))
            end = parse_time(str(event.get("end_time") or ""))
        except (TypeError, ValueError):
            continue
        if end > start:
            spans.append((start, end))
    if not spans:
        return 0.0
    spans.sort()
    covered = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            covered += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    covered += cur_end - cur_start
    return covered


def trajectory_takeover_ok(
    events: List[Dict[str, Any]],
    roi_entry: Dict[str, Any],
    fps: float,
    *,
    min_coverage: float = TRAJECTORY_MIN_EVENT_COVERAGE,
) -> tuple:
    """判定轨迹接管是否足够完整,返回 (ok, 覆盖率)。

    覆盖率 = 事件去重时间总长 / ROI 时长。轨迹管线是单链设计,平面跟踪
    只在对比度足够的段落锁定——固定歌词带常只在头一两行锁住(实测
    「一周的朋友」OP 顶部繁中带 ok 率 14.8%,事件仅覆盖前 13.7s),低覆盖
    时静态路径(逐采样帧 OCR + 边界精修)的时间轴远比轨迹残段完整;而
    轨迹的真正目标——跟随画面运动的文字(滚动屏/移动物体上的字)——事件
    覆盖接近文字全程可见期,不受门限影响。
    """
    roi_start = roi_entry.get("start_frame")
    roi_end = roi_entry.get("end_frame")
    try:
        span_sec = (int(roi_end) - int(roi_start) + 1) / float(fps)
    except (TypeError, ValueError, ZeroDivisionError):
        return True, 1.0
    if span_sec <= 0:
        return True, 1.0
    coverage = trajectory_event_coverage_sec(events, fps) / span_sec
    return coverage >= min_coverage, coverage


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
                 translation_config: Optional["TranslationConfig"] = None,
                 font_identify_config: Optional["FontIdentifyConfig"] = None,
                 save_intermediate_json: Optional[bool] = None,
                 color_presence_gate_spec: Optional[Dict[str, Any]] = None,
                 ocr_engine_id: str = "",
                 source_filter_config: Optional[Dict[str, Any]] = None,
                 engine_options: Optional[Dict[str, Any]] = None,
                 watermark_filter_config: Optional[Dict[str, Any]] = None,
                 chunk_workers: int = 0,
                 motion_auto_detect: bool = False,
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
        # 翻译阶段配置（T2.3，主进程阶段 4：润色之后、ASS 写出之前）。
        # None = 完全关闭，零行为变化；绝不进入 _stage_ctx()（阶段 1-3
        # 的 picklable 参数包）——chunk worker 子进程禁止服务端调用。
        self.translation_config = translation_config
        # font_identify 后置阶段配置（T3.3，主进程：阶段 3 之后、构造
        # 优化器之前）。None = 完全关闭，零开销（不打开视频、不 import
        # torch 相关模块）；同样绝不进入 _stage_ctx()——字体识别是本地
        # 计算，只在主进程跑，字块图像不跨阶段携带（随机访问取帧重裁）。
        self.font_identify_config = font_identify_config
        # T3.3 产出：字体候选侧表（FontIdentifyResult），不改
        # restored_results 结构；T3.4（落名联动）在此之上消费。
        self.font_identifications: Optional[Any] = None
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
        # FR-1: 自动检测移动文字(无手动轨迹 ROI 时,按 ROI 范围采样检测)。
        self.motion_auto_detect = bool(motion_auto_detect)
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

    def _auto_detect_motion_specs(self) -> List[Dict[str, Any]]:
        """FR-1 自动检测(FR 表「移动文字检测触发」阶段三落地点):无手动
        轨迹 ROI 且启用「自动检测移动文字」时,在每个 ROI 外接矩形内做
        采样 OCR 行心位移检测(:mod:`core.motion_detector`),检出区域以
        该 ROI 的身份走轨迹管线(接管成功即抑制其静态碎片事件)。"""
        from core.motion_detector import detect_moving_text

        engine_id = str(self.ocr_engine_id) if self.ocr_engine_id else None
        specs: List[Dict[str, Any]] = []
        for idx, roi in enumerate(self.roi_data or []):
            if not isinstance(roi, dict):
                continue
            rect = _roi_analysis_rect(roi)
            if rect is None:
                continue
            try:
                start_frame = int(roi.get("start_frame", 0) or 0)
                end_frame = roi.get("end_frame")
                end_frame = int(end_frame) if end_frame is not None else None
            except (TypeError, ValueError):
                continue
            try:
                regions = detect_moving_text(
                    self.video_path, engine_id=engine_id,
                    start_frame=start_frame, end_frame=end_frame,
                    region=rect)
            except Exception as exc:
                logger.warning(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Auto motion detection on {0} failed ({1}); skipping it.",
                    ).format(f"roi_{idx}", exc),
                    exc_info=True,
                )
                continue
            for region in regions:
                specs.append({
                    "roi_id": f"roi_{idx}",
                    "quad": region.quad,
                    "start_frame": region.start_frame,
                    "end_frame": region.end_frame,
                    "scene_text_policy": str(
                        roi.get("scene_text_policy") or "overlap"),
                    "auto_brightness": bool(
                        roi.get("motion_auto_brightness", False)),
                    "occlusion_clip": bool(
                        roi.get("motion_occlusion_clip", False)),
                    "ocr_lang": str(roi.get("ocr_lang") or ""),
                })
        return specs

    def _roi_by_id(self, roi_id: str) -> Dict[str, Any]:
        """roi_id("roi_N") → ROI 条目;形状不合法时返回空 dict(门限判定按
        全通过处理,不误杀轨迹结果)。"""
        try:
            idx = int(str(roi_id).rsplit("_", 1)[1])
            roi = (self.roi_data or [])[idx]
            return roi if isinstance(roi, dict) else {}
        except (IndexError, ValueError, TypeError):
            return {}

    def _run_motion_stage(self) -> tuple:
        """移动文字轨迹阶段(pose 勾选 + 四点多边形 ROI;或自动检测命中)。

        对每个轨迹 ROI 调 scripts/motion_ass.build_motion_events(独立 OCR
        引擎实例,不占用主流水线进程级单例),返回 ``(events, roi_ids)``:
        events 合并进最终 .ass,roi_ids(轨迹接管成功的 ROI)在生成器里
        抑制对应静态事件,避免同区域双份文本。单个 ROI 失败仅告警并回退
        该 ROI 的静态 pose 路径,不中断任务链;轨迹事件覆盖 ROI 时间范围
        不足(:data:`TRAJECTORY_MIN_EVENT_COVERAGE`)同样整体回退——固定
        文字带的单链跟踪常只锁住头几行,残段远不如静态路径完整。
        """
        specs = collect_motion_roi_specs(self.roi_data)
        if not specs and self.motion_auto_detect:
            specs = self._auto_detect_motion_specs()
        if not specs:
            return [], set()
        from scripts.motion_ass import (
            MotionTrajectoryUnavailable,
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
                engine_options = dict(self.engine_options)
                if spec.get("ocr_lang"):
                    # 每 ROI 识别语言覆盖:轨迹管线的独立引擎按该语言初始化。
                    engine_options["lang"] = str(spec["ocr_lang"])
                events, summary = build_motion_events(
                    self.video_path, quad,
                    start_frame=spec["start_frame"],
                    end_frame=spec["end_frame"],
                    scene_text_policy=spec["scene_text_policy"],
                    auto_brightness=spec["auto_brightness"],
                    occlusion_clip=bool(spec.get("occlusion_clip", False)),
                    ocr_engine=engine_id,
                    engine_options=engine_options,
                    log=logger.info,
                )
                ok, coverage = trajectory_takeover_ok(
                    events, self._roi_by_id(roi_id), self.fps)
                if not ok:
                    # 覆盖门限:轨迹只锁住了 ROI 的一小段(固定文字带跟踪
                    # 常见),残段远不如静态路径完整——该 ROI 整体回退静态
                    # 路径,不留「轨迹覆盖前 15% + 静态覆盖全程」的残缺输出。
                    logger.warning(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Motion trajectory for {0} covers only {1:.0f}% of the ROI time range; falling back to static pose tags.",
                        ).format(roi_id, coverage * 100.0),
                    )
                    continue
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
            except MotionTrajectoryUnavailable as exc:
                # 预期降级(无 ok 帧/关键帧池无文字行等):回退该 ROI 的静态
                # pose 是设计内路径,一行告警即可,不挂完整 traceback——
                # 崩溃栈只留给真正的 bug(下方通用 except)。
                logger.warning(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Motion trajectory for {0} failed ({1}); falling back to static pose tags.",
                    ).format(roi_id, exc),
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

    def _identify_fonts(self, restored_results: List[tuple]) -> Optional[Any]:
        """font_identify 后置阶段（T3.3）：阶段 3 之后、构造优化器之前，
        本地主进程执行（无服务端调用；字块图像按 (roi, 稳定文本) 随机
        访问取帧重裁，不跨阶段携带）。

        配置 None/关闭 → 零开销直通返回 None；阶段任何异常降级为
        "无识别结果"（记 warning），绝不中断出片。结果存
        ``self.font_identifications``（侧表）并返回，供 T3.4 落名联动。
        """
        cfg = self.font_identify_config
        if cfg is None or not getattr(cfg, "enabled", False):
            return None
        try:
            # 延迟导入：关闭路径永不加载 font_intel.identify_stage（其
            # 自身零 torch 依赖，torch 相关模块由识别器链再延迟导入）。
            from font_intel.identify_stage import run_font_identify_stage

            result = run_font_identify_stage(
                list(restored_results), self.video_path, self.fps, cfg,
                roi_data=self.roi_data,
                video_width=self.video_width,
                video_height=self.video_height,
                cancel_check=lambda: self.is_cancelled,
            )
        except Exception as exc:
            logger.warning(
                "font_identify stage failed; degrading to no identifications: %s",
                exc, exc_info=True,
            )
            return None
        self.font_identifications = result
        return result

    def _build_font_compliance_config(self):
        """T3.4：识别开启且产出侧表时构造合规闸门配置（事件级 \fn 落名
        的决策入口）。识别关闭/无结果 → None（缺省路径零行为变化）；
        font_intel 缺失等异常降级为 None 并记 warning，绝不中断出片。"""
        if self.font_identifications is None:
            return None
        try:
            from font_intel.integration import FontComplianceConfig

            db_path = getattr(self.font_identify_config, "db_path", None)
            return FontComplianceConfig(
                enabled=True, db_path=db_path, interactive=False)
        except Exception as exc:
            logger.warning(
                "font compliance config unavailable; font closure disabled: %s",
                exc)
            return None

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

            # font_identify 后置阶段（T3.3）：本地计算、主进程执行——
            # 阶段 3 之后、构造优化器之前；关闭时零开销。产出字体候选
            # 侧表（不改 restored_results 结构），字体名落 ASS 由 T3.4 消费。
            if (
                self.font_identify_config is not None
                and getattr(self.font_identify_config, "enabled", False)
            ):
                restored_results = list(restored_results)
            self.font_identifications = self._identify_fonts(restored_results)
            if self.is_cancelled: return

            # T3.4 落名联动：识别开启且产出侧表时随附合规闸门配置（识别
            # 结果要过闸才写 \fn）；识别关闭/无结果 → None，生成器行为与
            # 旧版本逐字节一致。
            font_compliance_cfg = self._build_font_compliance_config()

            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 4/4: Starting ASS subtitle file generation..."))
            if self.is_cancelled: return

            t0_ass = time.perf_counter()
            polisher_cfg = self.subtitle_polisher
            if polisher_cfg is not None:
                polisher_cfg = replace(
                    polisher_cfg,
                    log_line=lambda s: self.llm_detail.emit(s),
                )
            # 翻译的逐批日志走同一 LLM 详情面板（与润色同侧的阶段 4 通道）。
            translation_cfg = self.translation_config
            if translation_cfg is not None:
                translation_cfg = replace(
                    translation_cfg,
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
                translation_config=translation_cfg,
                font_compliance=font_compliance_cfg,
                font_identifications=self.font_identifications,
                roi_ocr_langs=collect_roi_ocr_langs(self.roi_data) or None,
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
