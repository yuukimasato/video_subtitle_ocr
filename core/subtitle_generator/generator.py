# core/subtitle_generator/generator.py
import logging
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional

from PySide6.QtCore import QCoreApplication

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
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating a rich set of default styles."))
        self.subtitle_polisher = subtitle_polisher

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
                entry = f"Dialogue: 0,{ev['start_time']},{ev['end_time']},{ev['style']},,0,0,0,,{text}"
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
