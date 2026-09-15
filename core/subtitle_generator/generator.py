# core/subtitle_generator/generator.py
import logging
import re
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication

from core.scene_text_policy import (
    POLICY_MODES,
    SceneTextPolicyConfig,
    apply_policy_static,
)
from core.subtitle_llm_polish import (
    SubtitlePolisherConfig,
    polish_subtitle_texts,
    deepseek_suggest_merge_params,
    params_close,
)

from .data_grouping import _DataGroupingMixin
from .event_merge import _EventMergeMixin
from .llm_merge import _LlmMergeMixin
from .roi_filters import _RoiFiltersMixin
from .source_classification import _SourceClassificationMixin
from .styling import _StylingMixin
from .timeline import _TimelineMixin

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate


def _sanitize_ass_body(body: str) -> str:
    """Make OCR text safe to embed as ASS Dialogue text.

    ASCII braces would be parsed as override blocks (the text between them
    disappears), and real CR/LF would corrupt the single-line Dialogue entry.
    Intentional ASS breaks (literal \\n / \\N sequences) are left untouched;
    braces are mapped to their fullwidth equivalents to keep them visible.
    """
    text = str(body or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return text.replace("{", "｛").replace("}", "｝")


_POS_TAG_RE = re.compile(r"\\pos\(([-\d.]+),\s*([-\d.]+)\)")


def _shift_pos_tag(tags: str, dx: float, dy: float) -> str:
    """把策略 spec 标签里的 \\pos(x,y) 平移 (dx, dy)(平面坐标 → 视频坐标)。

    策略 spec 的 mask/text/scene_ws 标签各含且仅含一个 \\pos;note 标签用
    视频坐标、由调用方跳过,不经此函数。平移后整数值省略小数位,与既有
    SCENE 行的 ``\\pos(160,100)`` 格式一致。
    """

    def _fmt(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:.1f}"

    return _POS_TAG_RE.sub(
        lambda m: "\\pos({},{})".format(
            _fmt(float(m.group(1)) + dx), _fmt(float(m.group(2)) + dy)),
        tags)


class OCRToASSOptimizer(
    _DataGroupingMixin,
    _TimelineMixin,
    _EventMergeMixin,
    _LlmMergeMixin,
    _RoiFiltersMixin,
    _SourceClassificationMixin,
    _StylingMixin,
):
    MIN_SCORE_THRESHOLD = 0.6
    MIN_DURATION_FRAMES = 3
    MERGE_TEXT_SIMILARITY = 0.85
    MERGE_POS_TOLERANCE = 20
    # 帧号不连续但间隔很小时，若总字数相同且文本/位置一致，仍合并为一条字幕。
    MERGE_BRIDGE_MAX_FRAMES = 8
    MERGE_BRIDGE_MAX_SEC = 0.45
    VIDEO_BOTTOM_AREA = 0.75
    VIDEO_TOP_AREA = 0.15
    # ROI 内字幕“主字号”学习与过滤（用于排除小场景字/杂项）。
    # 过滤只在“该 ROI 的主导位置类型为 BOTTOM/TOP”时更激进；
    # 若 ROI 主导为 SCENE，则保留场景文字逻辑。
    ROI_PROFILE_MIN_GROUPS = 3
    ROI_PROFILE_DOMINANT_RATIO = 0.55
    ROI_HEIGHT_FILTER_MIN_RATIO = 0.65
    ROI_HEIGHT_FILTER_MAX_RATIO = 1.65
    # ASS 条目级合并：压制同一 ROI 内因 OCR 抖动产生的时间轴碎片或多字错字副本。
    DIALOG_MERGE_MAX_GAP_SEC = 4.0
    DIALOG_MERGE_MIN_RATIO = 0.78
    DIALOG_MERGE_MIN_OVERLAP_LEN = 6
    # 子串包含合并只适用于几乎相接的事件（同一条字幕的闪烁/缺读）；
    # 间隔超过该值时即便文本是子串关系也视为两条不同字幕。
    DIALOG_MERGE_SUBSTRING_MAX_GAP_SEC = 0.6
    # Scene 噪声过滤与合并（常见：单字/纯数字/空括号/极短闪烁）
    SCENE_EVENT_MIN_DURATION_SEC = 0.35
    SCENE_EVENT_MIN_TEXT_LEN = 3
    SCENE_POS_TOLERANCE_PX = 24

    def __init__(
        self,
        video_path: str,
        output_path: str,
        fps: float,
        width: int,
        height: int,
        template_path: Optional[str] = None,
        subtitle_polisher: Optional[SubtitlePolisherConfig] = None,
        source_filter_config: Optional[Dict] = None,
        roi_pose_tags: Optional[Dict[str, Dict]] = None,
        roi_text_filter_policies: Optional[Dict[str, str]] = None,
        watermark_filter_config: Optional[Dict] = None,
        roi_scene_text_policies: Optional[Dict[str, str]] = None,
        roi_analysis_rects: Optional[Dict[str, Tuple]] = None,
    ):
        self.video_path = Path(video_path)
        self.output_path = Path(output_path)
        self.fps = fps
        self.width = width
        self.height = height
        self.template_path = Path(template_path) if template_path else None
        self.source_filter_config = source_filter_config
        # roi_id -> "keep_all"（手动 ROI，永不参与文字来源过滤）| "auto"
        # （自动字幕带 ROI，按场景预设过滤）。缺省视为 "keep_all"。
        self.roi_text_filter_policies = roi_text_filter_policies or {}
        # {"enabled": bool, "entries": [{"text", "bbox"}...]}，深度扫描产出的
        # 水印清单；在场景分类之前独立过滤。
        self.watermark_filter_config = watermark_filter_config
        # Optional per-ROI pose metadata (from polygon/rect ROIs with
        # write_pose_tags enabled): roi_id -> {"pos": [x, y], "frz": deg,
        # "frx": deg, "fry": deg}. When present, events from that ROI are
        # written with {\pos} / {\frz} / {\frx} / {\fry} placement tags so the
        # text lands where (and at the angle) the original picture text was.
        self.roi_pose_tags = roi_pose_tags or {}
        # roi_id -> 场景文字显示策略(overlap/mask/external/whitespace)。
        # 非 overlap 时该 ROI 的 SCENE 组事件被策略事件替换(场景文字重排,
        # 消除原字重影);缺省/overlap 保持原路径,输出逐事件一致。
        self.roi_scene_text_policies = roi_scene_text_policies or {}
        # roi_id -> ROI 外接矩形 (x1, y1, x2, y2)(视频坐标),策略分析图
        # (最大组中间帧)的裁剪窗口。
        self.roi_analysis_rects = roi_analysis_rects or {}
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating a rich set of default styles."))
        self.subtitle_polisher = subtitle_polisher

    # ------------------------------------------------------------------
    # 场景文字显示策略(静态 \pos 路径;overlap 缺省不改变任何行为)
    # ------------------------------------------------------------------

    def _group_is_scene(self, group) -> bool:
        """组中心是否落在场景区(与 styling 的 BOTTOM/TOP 判定同一阈值)。"""
        avg_box = group.get_avg_box()
        if not avg_box:
            return False
        y_center = (avg_box[1] + avg_box[3]) / 2
        return not (y_center > self.height * self.VIDEO_BOTTOM_AREA
                    or y_center < self.height * self.VIDEO_TOP_AREA)

    def _grab_frame_crop(self, frame_num: int, rect) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]]]:
        """读取单帧并按外接矩形裁剪,返回 (平面图, 平面原点(视频坐标))。

        每 ROI 策略仅取一帧(VideoCapture seek 一次);视频打不开/读帧失败/
        矩形完全出界时返回 (None, None),由调用方回退 overlap。
        """
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in rect)
        except (TypeError, ValueError):
            return None, None
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: cannot open video {} for analysis frame; keeping original placement."
                    ).format(self.video_path))
            return None, None
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_num))
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok or frame is None:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: failed to read frame {} for analysis; keeping original placement."
                    ).format(frame_num))
            return None, None
        fh, fw = frame.shape[:2]
        ox1, oy1 = max(0, x1), max(0, y1)
        ox2, oy2 = min(fw, x2), min(fh, y2)
        if ox2 - ox1 < 1 or oy2 - oy1 < 1:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: analysis rect {} outside video bounds; keeping original placement."
                    ).format(rect))
            return None, None
        return frame[oy1:oy2, ox1:ox2].copy(), (ox1, oy1)

    def _prepare_scene_policy_context(self, roi_id: str, groups) -> Optional[Dict]:
        """为该 ROI 构建策略上下文;不适用/不可用时返回 None(走原路径)。"""
        policy = str(self.roi_scene_text_policies.get(roi_id) or "overlap")
        if policy == "overlap":
            return None
        if policy not in POLICY_MODES:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: unknown mode {!r} for {}; keeping original placement."
                    ).format(policy, roi_id))
            return None
        scene_groups = [g for g in groups if g.lines and self._group_is_scene(g)]
        if not scene_groups:
            return None
        rect = self.roi_analysis_rects.get(roi_id)
        if rect is None:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: no analysis rect for {}; keeping original placement."
                    ).format(roi_id))
            return None
        # 分析图:最大 SCENE 组(帧跨度)的中间帧,每 ROI 仅 seek 一次。
        anchor = max(scene_groups,
                     key=lambda g: (g.duration_frames, len(g.frames)))
        mid_frame = (anchor.start_frame + anchor.end_frame) // 2
        plane_img, origin = self._grab_frame_crop(mid_frame, rect)
        if plane_img is None:
            return None
        return {
            "policy": policy,
            "plane": plane_img,
            "origin": origin,
            "rows": [],        # [(text, 视频坐标行框)],行序 = 收集序(组内自上而下)
            "row_times": [],   # 与 rows 对齐的 (start_time, end_time)
        }

    def _finish_scene_policy_events(self, ctx: Dict, roi_id: str) -> List[Dict]:
        """收集完成后生成策略事件,替换该 ROI 的原 SCENE styled 事件。"""
        rows_video = ctx["rows"]
        row_times = ctx["row_times"]
        if not rows_video:
            return []
        ox, oy = ctx["origin"]
        # 行框:视频坐标 − 外接框原点 → 平面坐标,与纯函数对接
        rows = [(text, (x1 - ox, y1 - oy, x2 - ox, y2 - oy))
                for text, (x1, y1, x2, y2) in rows_video]
        cfg = SceneTextPolicyConfig(mode=ctx["policy"])
        specs, applied, notes = apply_policy_static(
            rows, ctx["plane"], cfg, float(self.width), float(self.height),
            base_font_size=int(self.height * 0.04))
        for note in notes:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: {} (ROI {}).").format(note, roi_id))
        logger.info(
            _tr("OCRToASSOptimizer",
                "Scene text policy for {}: {} applied ({} spec(s)).").format(
                    roi_id, applied, len(specs)))

        def _ts(value: str) -> float:
            try:
                return self._parse_ass_time_to_seconds(value)
            except Exception:
                return 0.0

        # note/scene_ws 单条事件的时间 = 全部 SCENE 组跨度的并集
        union_start = min((s for s, _e in row_times), key=_ts)
        union_end = max((e for _s, e in row_times), key=_ts)
        events: List[Dict] = []
        for spec in specs:
            kind = spec["kind"]
            if kind == "mask":
                times = [row_times[i] for i in spec.get("rows", [])]
                start = min((s for s, _e in times), key=_ts) if times else union_start
                end = max((e for _s, e in times), key=_ts) if times else union_end
                tags = _shift_pos_tag(spec["tags"], float(ox), float(oy))
                events.append({
                    "roi": roi_id, "start_time": start, "end_time": end,
                    "style": "Scene", "tags": tags, "body": "",
                    "layer": int(spec.get("layer", 0)), "policy": True,
                })
            elif kind == "text":
                start, end = row_times[spec["row"]]
                tags = _shift_pos_tag(spec["tags"], float(ox), float(oy))
                events.append({
                    "roi": roi_id, "start_time": start, "end_time": end,
                    "style": "Scene", "tags": tags, "body": spec.get("body", ""),
                    "layer": int(spec.get("layer", 0)), "policy": True,
                })
            elif kind == "note":
                events.append({
                    "roi": roi_id, "start_time": union_start, "end_time": union_end,
                    "style": spec.get("style", "NoteBox"), "tags": spec["tags"],
                    "body": spec.get("body", ""), "policy": True,
                })
            elif kind == "scene_ws":
                events.append({
                    "roi": roi_id, "start_time": union_start, "end_time": union_end,
                    "style": spec.get("style", "Scene"),
                    "tags": _shift_pos_tag(spec["tags"], float(ox), float(oy)),
                    "body": spec.get("body", ""), "policy": True,
                })
        return events

    def convert_from_memory(
        self,
        restored_data_generator: Generator,
        *,
        polish_progress_callback: Optional[Callable[[int, str], None]] = None,
        polish_cancel_check: Optional[Callable[[], bool]] = None,
    ):
        logger.info(_tr("OCRToASSOptimizer", "--- Starting conversion from in-memory data to ASS subtitles ---"))
        try:
            organized_data = self._load_and_organize_ocr_data(restored_data_generator)

            # ── Watermark removal (independent of the source-filter switch) ──
            organized_data = self._apply_watermark_filter(organized_data)

            # ── Text source classification + filtering (per-ROI policy) ──
            if self.source_filter_config and self.source_filter_config.get("enabled"):
                organized_data = self._classify_and_filter_text_lines(organized_data)

            if not organized_data:
                logger.warning(_tr("OCRToASSOptimizer", "No valid OCR data found, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                    f.write(self._get_ass_header())
                return

            subtitle_events: List[Dict[str, str]] = []
            for roi_id, frame_list in organized_data.items():
                logger.info(_tr("OCRToASSOptimizer", "Processing ROI: {}, containing {} valid frames.").format(roi_id, len(frame_list)))
                groups = self._group_consecutive_frames(frame_list)
                for group in groups:
                    group.lines = self._select_representative_lines(group)
                groups = self._filter_groups_by_roi_profile(groups)
                logger.info(_tr("OCRToASSOptimizer", "ROI: {} generated {} subtitle groups.").format(roi_id, len(groups)))

                # 场景文字显示策略(仅非 overlap 生效;准备失败回退原路径)。
                policy_ctx = self._prepare_scene_policy_context(str(roi_id), groups)

                for group in groups:
                    # Prefer real timestamps when available. group.frames was
                    # collected during grouping — no O(N*G) rescan needed.
                    group_frames = group.frames or [
                        f for f in frame_list if group.start_frame <= f.frame_num <= group.end_frame
                    ]
                    start_sec = next((f.time_sec for f in group_frames if f.time_sec and f.time_sec > 0), 0.0)
                    if start_sec > 0:
                        end_sec = self._estimate_end_time(group_frames)
                        start_time = self._format_time_seconds(start_sec)
                        end_time = self._format_time_seconds(end_sec)
                    else:
                        start_time = self._format_time(group.start_frame)
                        end_time = self._format_time(group.end_frame + 1)
                    styled_lines = self._determine_style_and_position(group)
                    is_scene = bool(styled_lines) and all(
                        line["style"] == "Scene" for line in styled_lines)
                    if policy_ctx is not None and is_scene:
                        # 策略接管:收集行与时间,原 SCENE styled 事件被策略
                        # 事件替换(在 ROI 分组循环末尾统一生成)。
                        for line in sorted(group.lines, key=lambda ln: ln.box[1]):
                            policy_ctx["rows"].append(
                                (line.text, tuple(float(v) for v in line.box)))
                            policy_ctx["row_times"].append((start_time, end_time))
                        continue
                    pose = self.roi_pose_tags.get(str(roi_id))
                    if pose:
                        styled_lines = self._apply_roi_pose_tags(styled_lines, pose)
                    for line_info in styled_lines:
                        subtitle_events.append(
                            {
                                "roi": str(roi_id),
                                "start_time": start_time,
                                "end_time": end_time,
                                "style": line_info["style"],
                                "tags": line_info["tags"],
                                "body": line_info["text"],
                            }
                        )

                if policy_ctx is not None:
                    subtitle_events.extend(
                        self._finish_scene_policy_events(policy_ctx, str(roi_id)))

            subtitle_events_pre_merge = [dict(e) for e in subtitle_events]
            subtitle_events_pre_merge = self._filter_events(subtitle_events_pre_merge)
            subtitle_events = self._merge_temporal_near_duplicate_events(subtitle_events_pre_merge)

            if self.subtitle_polisher is not None and polish_progress_callback:
                polish_progress_callback(
                    91,
                    _tr(
                        "OCRToASSOptimizer",
                        "Step 4/4: Starting ASS generation and DeepSeek post-processing...",
                    ),
                )

            if subtitle_events and self.subtitle_polisher is not None and getattr(
                self.subtitle_polisher, "fragment_merge_enabled", False
            ):
                subtitle_events = self._llm_merge_fragmented_events(
                    subtitle_events,
                    self.subtitle_polisher,
                    cancel_check=polish_cancel_check if polish_cancel_check else (lambda: False),
                    polish_progress_callback=polish_progress_callback,
                )

            def _rerun_fragment_merge(events: List[Dict[str, str]]) -> List[Dict[str, str]]:
                # Re-applying merge parameters rebuilds events from the
                # pre-merge snapshot; without this the fragment-merge output
                # (and its API cost) would be silently discarded.
                if not (events and self.subtitle_polisher is not None and getattr(
                    self.subtitle_polisher, "fragment_merge_enabled", False
                )):
                    return events
                return self._llm_merge_fragmented_events(
                    events,
                    self.subtitle_polisher,
                    cancel_check=polish_cancel_check if polish_cancel_check else (lambda: False),
                    polish_progress_callback=polish_progress_callback,
                )

            if subtitle_events and self.subtitle_polisher is not None and getattr(
                self.subtitle_polisher, "strategy_review_enabled", False
            ):
                max_rounds = max(
                    1,
                    min(5, int(getattr(self.subtitle_polisher, "strategy_max_iterations", 3))),
                )
                current_params = self._merge_strategy_params_snapshot()

                for rnd in range(max_rounds):
                    if polish_cancel_check and polish_cancel_check():
                        break

                    if polish_progress_callback:
                        polish_progress_callback(
                            min(96, 93 + rnd),
                            _tr(
                                "OCRToASSOptimizer",
                                "Step 4/4: DeepSeek reviewing merge strategy (round {}/{})..."
                            ).format(rnd + 1, max_rounds),
                        )

                    sug = deepseek_suggest_merge_params(
                        self.subtitle_polisher,
                        subtitle_events_pre_merge,
                        subtitle_events,
                        current_params,
                        cancel_check=polish_cancel_check if polish_cancel_check else lambda: False,
                    )

                    if sug is None:
                        logger.info(
                            _tr("OCRToASSOptimizer", "DeepSeek strategy review skipped or failed; keeping merge parameters unchanged.")
                        )
                        break

                    patch, rationale = sug
                    if rationale:
                        logger.info(_tr("OCRToASSOptimizer", "DeepSeek strategy note: {}").format(rationale))

                    if not patch:
                        break

                    merged_params = dict(current_params)
                    merged_params.update(patch)
                    if "merge_min_overlap_len" in merged_params:
                        merged_params["merge_min_overlap_len"] = float(
                            int(merged_params["merge_min_overlap_len"])
                        )

                    if params_close(merged_params, current_params):
                        logger.info(_tr("OCRToASSOptimizer", "DeepSeek merge parameters converged."))
                        break

                    self._apply_merge_strategy_params(merged_params)
                    current_params = self._merge_strategy_params_snapshot()
                    subtitle_events = self._merge_temporal_near_duplicate_events(
                        [dict(e) for e in subtitle_events_pre_merge]
                    )
                    subtitle_events = _rerun_fragment_merge(subtitle_events)

                    logger.info(
                        _tr(
                            "OCRToASSOptimizer",
                            "Re-merged subtitles with tuned parameters (gap {:.2f}s ratio {:.3f} overlap {})."
                        ).format(
                            current_params["merge_max_gap_sec"],
                            current_params["merge_min_ratio"],
                            int(current_params["merge_min_overlap_len"]),
                        )
                    )

            if (
                subtitle_events
                and self.subtitle_polisher is not None
                and getattr(self.subtitle_polisher, "text_polish_enabled", True)
            ):
                bodies = [ev["body"] for ev in subtitle_events]

                def _on_batch(idx: int, total: int) -> None:
                    if polish_progress_callback:
                        pct = 96 + max(1, min(3, int(3 * idx / max(total, 1))))
                        polish_progress_callback(
                            min(99, pct),
                            _tr(
                                "OCRToASSOptimizer",
                                "Step 4/4: DeepSeek polishing subtitles ({}/{} batches)...",
                            ).format(idx, total),
                        )

                polished = polish_subtitle_texts(
                    bodies,
                    self.subtitle_polisher,
                    cancel_check=polish_cancel_check if polish_cancel_check else lambda: False,
                    on_batch_done=_on_batch,
                )
                if len(polished) != len(subtitle_events):
                    logger.warning(
                        _tr("OCRToASSOptimizer", "Polish output length mismatch, using original subtitles.")
                    )
                else:
                    for ev, nt in zip(subtitle_events, polished):
                        ev["body"] = nt
                    logger.info(_tr("OCRToASSOptimizer", "DeepSeek subtitle polishing applied."))

            all_dialogue_entries = []
            for ev in subtitle_events:
                text = ev["tags"] + _sanitize_ass_body(ev["body"])
                # 事件可选 layer(策略事件:遮罩 0、文本 1);缺省 0,与既有
                # 输出逐字节一致。
                layer = int(ev.get("layer", 0) or 0)
                entry = f"Dialogue: {layer},{ev['start_time']},{ev['end_time']},{ev['style']},,0,0,0,,{text}"
                all_dialogue_entries.append(entry)

            if not all_dialogue_entries:
                logger.warning(_tr("OCRToASSOptimizer", "No valid subtitle groups formed for any ROI, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                    f.write(self._get_ass_header())
                return

            # Sort by parsed start time — lexicographic order on "H:MM:SS.CC"
            # only coincides with chronological order below 10 hours.
            all_dialogue_entries.sort(
                key=lambda x: self._parse_ass_time_to_seconds(x.split(',')[1])
            )

            header_content = self._get_ass_header()
            final_content = header_content + "\n".join(all_dialogue_entries)
            
            with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                f.write(final_content)

            logger.info(_tr("OCRToASSOptimizer", "--- Conversion successful ---"))
            logger.info(_tr("OCRToASSOptimizer", "ASS subtitle file saved to: {}").format(self.output_path))

        except Exception as e:
            logger.error(_tr("OCRToASSOptimizer", "--- Conversion failed ---"))
            logger.error(_tr("OCRToASSOptimizer", "Error: {}").format(e), exc_info=True)
            raise
