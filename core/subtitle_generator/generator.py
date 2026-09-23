# core/subtitle_generator/generator.py
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication

from utils.atomic_write import atomic_write_text

from core.line_restoration import merge_restoration_tags
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

if TYPE_CHECKING:
    # 仅类型标注（惰性导入见 _apply_font_compliance_to_header 与
    # _translate_subtitle_events）：font_intel 只依赖标准库，配置为 None
    # 的缺省路径不触任何 font_intel 导入与行为。
    from font_intel.integration import FontComplianceConfig
    from font_intel.translation import TranslationConfig


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


# 场景策略分析图的取帧模式:单帧(旧规则,缺省) | 时间邻域多帧中位合成。
ANALYSIS_FRAME_MODES = ("single", "median")
# median 模式下每个候选帧的邻域半径(取 ±r 共 2r+1 帧做中位合成)。
ANALYSIS_MEDIAN_RADIUS = 2

# PP-OCR 语言 id（GUI/CLI 的 ocr_lang 取值）→ 翻译提示词用源语言名
# （T2.3 双语路由）。未列出的 id（或 ROI 未配置 ocr_lang）回退自动
# 检测：TranslationConfig.source_language 留空即既有 prompt，行为不变。
OCR_LANG_SOURCE_NAMES = {
    "ch": "简体中文",
    "chinese_cht": "繁体中文",
    "en": "英语",
    "japan": "日语",
    "korean": "韩语",
    "russian": "俄语",
    "french": "法语",
    "german": "德语",
    "it": "意大利语",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "arabic": "阿拉伯语",
}


class FrameReader:
    """可缓存的视频分析帧读取器:单个 VideoCapture + 按帧号缓存。

    多个 ROI 共享同一 reader:无论 ROI 数与探测帧数多少,视频只打开一次
    (``opens`` 计数);同一帧号的重复探测直接命中缓存,不再重复解码
    (``seeks`` 计数)。``read`` 返回该帧(未失败)或 None(打不开/读帧
    失败/越界);``close`` 释放句柄。读取内容与逐次独立打开 VideoCapture
    + seek 逐字节一致。
    """

    def __init__(self, video_path):
        self._path = str(video_path)
        self._cap = None
        self._frames: Dict[int, Optional[np.ndarray]] = {}
        self.opens = 0
        self.seeks = 0
        self.open_failed = False

    def _ensure_cap(self):
        if self._cap is None:
            cap = cv2.VideoCapture(self._path)
            self.opens += 1
            if not cap.isOpened():
                cap.release()
                self.open_failed = True
                return None
            self._cap = cap
        return self._cap

    def read(self, frame_num) -> Optional[np.ndarray]:
        num = int(frame_num)
        if num in self._frames:
            return self._frames[num]
        cap = self._ensure_cap()
        if cap is None:
            self._frames[num] = None
            return None
        self.seeks += 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, num)
        ok, frame = cap.read()
        self._frames[num] = frame if (ok and frame is not None) else None
        return self._frames[num]

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


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
        motion_events: Optional[List[Dict[str, str]]] = None,
        motion_roi_ids: Optional[set] = None,
        motion_evidence: Optional[Dict[str, List[Dict]]] = None,
        roi_auto_brightness: Optional[Dict[str, bool]] = None,
        roi_occlusion_clip: Optional[Dict[str, bool]] = None,
        analysis_frame_mode: str = "single",
        font_compliance: Optional["FontComplianceConfig"] = None,
        font_identifications: Optional[Any] = None,
        translation_config: Optional["TranslationConfig"] = None,
        roi_ocr_langs: Optional[Dict[str, str]] = None,
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
        # roi_id -> 场景文字显示策略(overlap/mask/mask_only/external/whitespace)。
        # 非 overlap 时该 ROI 的 SCENE 组事件被策略事件替换(场景文字重排,
        # 消除原字重影);mask_only 仅出遮罩、识别文本写成 Comment 行,供
        # 用户在遮罩上自行排版覆写;缺省/overlap 保持原路径,输出逐事件一致。
        self.roi_scene_text_policies = roi_scene_text_policies or {}
        # roi_id -> ROI 外接矩形 (x1, y1, x2, y2)(视频坐标),策略分析图
        # (最大组中间帧)的裁剪窗口。
        self.roi_analysis_rects = roi_analysis_rects or {}
        # 移动文字轨迹事件(scripts/motion_ass.build_motion_events 产出,
        # Name=motion):在过滤/合并/润色之后原样并入事件表,不参与任何
        # 重写;事件带可选 layer/Name 列。
        self.motion_events = [dict(e) for e in (motion_events or [])]
        # 轨迹接管成功的 ROI:该 ROI 的静态 OCR 事件整体跳过(避免同区域
        # 双份文本);轨迹失败回退时为空,静态路径照常。
        self.motion_roi_ids = set(motion_roi_ids or ())
        # A3:roi_id → 轨迹管线策略扩增前的行级可见区间(build_motion_events
        # summary["pre_policy_events"]),接管前与静态 OCR 证据做文本保真
        # 对照;缺省(旧调用方未传)时接管一律按 unverified 拒绝,保留静态。
        self.motion_evidence = dict(motion_evidence or {})
        # B3:roi_id → 自动亮度开关;开启时该 ROI 的静态策略事件(遮罩与
        # 文字)按帧采样屏幕背景亮度并叠加 \1c/\alpha 曲线标签。
        self.roi_auto_brightness = dict(roi_auto_brightness or {})
        # B4:roi_id → 前景轮廓遮挡裁剪开关;开启时该 ROI 的静态策略事件
        # (文字与同物体有色遮罩)按帧计算屏幕前景轮廓并写时间局部 iclip。
        self.roi_occlusion_clip = dict(roi_occlusion_clip or {})
        # A3:保真门控拒绝接管的 ROI——其轨迹候选事件在写出前整体移除,
        # 不只清 motion_roi_ids(否则候选会作为附加输出漏进最终 .ass)。
        self._rejected_motion_rois: set = set()
        # 策略分析图取帧模式:single=单帧最高亮度(旧规则,缺省,行为
        # 不变);median=每个候选帧取时间邻域多帧中位合成(动态背景更稳)。
        if analysis_frame_mode not in ANALYSIS_FRAME_MODES:
            raise ValueError(
                f"unknown analysis frame mode: {analysis_frame_mode!r}; "
                f"valid modes: {', '.join(ANALYSIS_FRAME_MODES)}")
        self.analysis_frame_mode = analysis_frame_mode
        # 策略分析帧共享读取器(懒创建,convert_from_memory 结束时释放):
        # 多 ROI 共用单个 VideoCapture,按帧号缓存,读取次数不随 ROI 数
        # 线性增长。
        self._analysis_reader: Optional[FrameReader] = None
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating a rich set of default styles."))
        self.subtitle_polisher = subtitle_polisher
        # 字体合规闸门（T1.7/T1.8）：None 或 enabled=False 时全部路径与
        # 旧版本逐字节一致（不开字体库、不决策、不产任何合规报告文件）。
        self.font_compliance = font_compliance
        self._compliance_decisions: List[dict] = []
        self._compliance_report_written = False
        # 字体识别侧表（T3.3 产出，T3.4 事件级落名消费）：None = 完全
        # 关闭，捕获/闭环/建议包路径全部零操作。挂到事件上的识别快照
        # 走私有键，绝不进入 ASS 输出。
        self.font_identifications = font_identifications
        self._font_update_suggestions: List[dict] = []
        self._font_suggestions_written = False
        # 翻译阶段（T2.3，主进程阶段 4：润色之后、事件写出之前）。
        # None = 完全关闭：不调用 _translate_subtitle_events，从而不导入
        # font_intel.translation，所有既有路径与输出逐字节不变。
        # roi_ocr_langs: roi_id → PP-OCR 语言 id（GUI/CLI 的 ocr_lang），
        # 双语场景按行所属 ROI 路由源语言；缺键（如 merge_rois 画布的
        # "roi_merged"）回退自动检测。
        self.translation_config = translation_config
        self.roi_ocr_langs = dict(roi_ocr_langs or {})

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

    def _get_analysis_reader(self) -> FrameReader:
        """懒创建跨 ROI 共享的分析帧读取器(单 VideoCapture + 帧缓存)。"""
        if self._analysis_reader is None:
            self._analysis_reader = FrameReader(self.video_path)
        return self._analysis_reader

    def _close_analysis_reader(self) -> None:
        if self._analysis_reader is not None:
            self._analysis_reader.close()

    def _roi_policy_mode(self, roi_id: str) -> Optional[str]:
        """该 ROI 生效的非 overlap 策略名;overlap/未知策略名返回 None。

        未知策略名不在此处报错(由收集入口统一 ValueError),仅视作未
        启用策略,保持其余行为与旧版本一致。
        """
        policy = str(self.roi_scene_text_policies.get(roi_id) or "overlap")
        if policy == "overlap" or policy not in POLICY_MODES:
            return None
        return policy

    def _grab_frame_crop(self, frame_num: int, rect) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int]]]:
        """读取单帧并按外接矩形裁剪,返回 (平面图, 平面原点(视频坐标))。

        帧读取走跨 ROI 共享的 :class:`FrameReader`(单 VideoCapture、按
        帧号缓存):多 ROI/多探测帧不重复打开或解码同一视频。视频打不开/
        读帧失败/矩形完全出界时返回 (None, None),由调用方回退 overlap。
        """
        try:
            x1, y1, x2, y2 = (int(round(float(v))) for v in rect)
        except (TypeError, ValueError):
            return None, None
        reader = self._get_analysis_reader()
        frame = reader.read(frame_num)
        if frame is None:
            if reader.open_failed:
                logger.warning(
                    _tr("OCRToASSOptimizer",
                        "Scene text policy: cannot open video {} for analysis frame; keeping original placement."
                        ).format(self.video_path))
            else:
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

    def _static_evidence_spans(self, groups, frame_list) -> List[Any]:
        """静态分组 → 文本保真证据(A3,只读现有分组,不新增 OCR)。

        每组按帧时间推导组窗口(与静态事件的时间推导同一规则),组内行依
        屏幕 y/x 确定稳定 ``row_order``。
        """
        from core.trajectory_fidelity import TextSpan

        fps = float(self.fps) if self.fps else 25.0
        spans: List[TextSpan] = []
        for group in groups:
            group_frames = group.frames or [
                f for f in frame_list
                if group.start_frame <= f.frame_num <= group.end_frame]
            start_sec = next(
                (f.time_sec for f in group_frames
                 if f.time_sec and f.time_sec > 0), 0.0)
            if start_sec > 0:
                end_sec = self._estimate_end_time(group_frames)
            else:
                start_sec = group.start_frame / fps
                end_sec = (group.end_frame + 1) / fps
            ordered = sorted(group.lines, key=lambda ln: (ln.box[1], ln.box[0]))
            for order, line in enumerate(ordered):
                spans.append(TextSpan(
                    int(round(start_sec * 100.0)),
                    int(round(end_sec * 100.0)),
                    line.text, order))
        return spans

    def _trajectory_takeover_fidelity(self, roi_id: str, groups,
                                      frame_list) -> Any:
        """A3 门控:轨迹证据与静态 OCR 文本对照;coverage 只是前置必要条件。

        无轨迹证据(旧调用方未传)→ unverified,保留静态;对照失败 → 拒绝。
        """
        from core.trajectory_fidelity import TextSpan, check_text_fidelity

        evidence = self.motion_evidence.get(str(roi_id)) or []
        static_spans = self._static_evidence_spans(groups, frame_list)
        motion_spans = [
            TextSpan(int(e.get("start_cs", 0)), int(e.get("end_cs", 0)),
                     str(e.get("text", "")), int(e.get("row_order", 0)))
            for e in evidence
        ]
        return check_text_fidelity(static_spans, motion_spans)

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
        # 分析图:跨全部 SCENE 组的时间范围等距取候选帧,取灰度中位亮度
        # 最高者。不能只看最大组——最大组往往是"手机静止"的暗屏段(亮屏段
        # 因移动被位置容差切碎),而取色要的是"亮屏底色";背景色与文本内容
        # 无关,任一充分照亮帧都具代表性(与 keyframe_selector 的选帧哲学
        # 一致,这里以亮度为准)。
        lo = min(g.start_frame for g in scene_groups)
        hi = max(g.end_frame for g in scene_groups)
        span = max(0, hi - lo)
        candidates = sorted({
            lo + int(round(span * f))
            for f in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0)
        })
        # 取帧模式:single=每个候选帧单张(旧规则,缺省);median=候选帧
        # 的时间邻域(±ANALYSIS_MEDIAN_RADIUS)多帧中位合成,动态背景/噪声
        # 下取色更稳定。默认 single,输出与旧版本逐字节一致。
        median_mode = self.analysis_frame_mode == "median"
        plane_img = origin = None
        best_luma = -1.0
        probes: List[Tuple[int, float]] = []
        for frame_num in candidates:
            samples: List[np.ndarray] = []
            sample_origin: Optional[Tuple[int, int]] = None
            if median_mode:
                for offset in range(-ANALYSIS_MEDIAN_RADIUS,
                                    ANALYSIS_MEDIAN_RADIUS + 1):
                    if frame_num + offset < 0:
                        continue
                    img, org = self._grab_frame_crop(frame_num + offset, rect)
                    if img is None:
                        continue
                    if sample_origin is None:
                        sample_origin = org
                    samples.append(img)
            else:
                img, org = self._grab_frame_crop(frame_num, rect)
                if img is not None:
                    sample_origin = org
                    samples.append(img)
            if not samples:
                continue
            plane = (np.median(np.stack(samples), axis=0).astype(np.uint8)
                     if median_mode else samples[0])
            luma = float(np.median(cv2.cvtColor(plane, cv2.COLOR_BGR2GRAY)))
            probes.append((frame_num, round(luma, 1)))
            if luma > best_luma:
                plane_img, origin, best_luma = plane, sample_origin, luma
        if plane_img is None:
            return None
        logger.info(
            _tr("OCRToASSOptimizer",
                "Scene text policy: analysis frame probes (frame, luma) = {} for {}; picked luma {:.1f}."
                ).format(probes, roi_id, best_luma))
        return {
            "policy": policy,
            "plane": plane_img,
            "origin": origin,
            "rows": [],        # [(text, 视频坐标行框)],行序 = 收集序(组内自上而下)
            "rows_motion": [],  # [(text, 视频坐标行框)] 逐帧行,仅供移动门限
            "row_times": [],   # 与 rows 对齐的 (start_time, end_time)
            # 与 rows 对齐的逐行还原素材(策略模块不读此键):该行多边形
            # (视频坐标)、行高、所在组的代表帧号——供 _finish_scene_policy_events
            # 在 pose 开启时追加逐行 \frz 与取色标签。
            "row_meta": [],
        }

    def _split_policy_scene_lines(self, group, styled_lines) -> tuple:
        """把组的 styled 行拆成 (进策略的 OCR 行, 保持原路径的 styled 行)。

        SCENE 组的 styled 行与按 y 排序的 OCR 行一一对应;同一组里混有
        非 Scene 样式(对白/顶部)时只让 Scene 行进策略,其余保持原样,
        两类都不丢失。无 Scene 行时返回 ([], 全部 styled 行)。
        """
        scene_flags = [info["style"] == "Scene" for info in styled_lines]
        other_lines = [info for info in styled_lines
                       if info["style"] != "Scene"]
        if not any(scene_flags):
            return [], list(styled_lines)
        sorted_lines = sorted(group.lines, key=lambda ln: ln.box[1])
        if len(sorted_lines) == len(scene_flags):
            scene_lines = [ln for ln, flag in zip(sorted_lines, scene_flags)
                           if flag]
        else:
            # 样式行与 OCR 行数不一致时保守回退:全部行进策略(与旧
            # is_scene 整组接管行为一致;SCENE 组逐行产样式,正常不可达)。
            scene_lines = list(sorted_lines)
        return scene_lines, other_lines

    def _finish_scene_policy_events(self, ctx: Dict, roi_id: str) -> List[Dict]:
        """收集完成后生成策略事件,替换该 ROI 的原 SCENE styled 事件。

        A2 时间契约:不再对整个 ROI 做一次布局、也不再对 note/scene_ws 取
        全 ROI 时间并集——先由 :func:`core.scene_timeline.active_slices`
        把逐行可见区间切成「活跃行集合恒定」的半开时间片,每片只对当时
        有效的行调用 :func:`apply_policy_static`,产生的 text/mask/note/
        scene_ws 事件时间都夹在本片内;结束后仅合并所有显示属性完全相同
        的相邻事件(同一行跨片延续时保持连续,不重新取并集)。

        pose 开启(``roi_pose_tags[roi_id]`` 为真)时,``kind == "text"``
        的策略文本事件按 ctx["row_meta"] 的逐行素材追加还原标签:逐行
        ``\\frz``(该行多边形的长边方向角,视频坐标)+ 帧像素采样得到的
        ``\\1c/\\3c/\\bord``(取帧/采样失败只跳过取色)。Scene 行在策略
        路径下已被移出 styled_lines,不再经过 ``_apply_roi_pose_tags``,
        没有这份追加就既无逐行旋转也无 ROI 级姿态标签。

        ``kind == "note"``(external 的整块 NoteBox)与 ``scene_ws``
        (whitespace 的合成行)是整块/合成放置,没有逐行几何可还原
        (无多边形、无行高),不在逐行还原范围。
        """
        rows_video = ctx["rows"]
        row_times = ctx["row_times"]
        if not rows_video:
            return []
        ox, oy = ctx["origin"]
        # 移动门限:同一文本的行框中心跨组位移超过 Scene 位置容差,说明场景
        # 文字在移动——静态遮罩/空白区会与原字错位,自动降级 external(逐帧
        # 跟随属于轨迹管线的能力)。
        # 注意用逐帧行(rows_motion)而非代表行(rows):分组/代表行投票会把
        # 移动文本坍缩到单一位置,移动信息在那里已经被抹掉。
        centers: Dict[str, List[Tuple[float, float]]] = {}
        for text, (x1, y1, x2, y2) in ctx.get("rows_motion", []):
            centers.setdefault(text, []).append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
        tol = float(self.SCENE_POS_TOLERANCE_PX)
        moved = any(
            np.hypot(cx - px, cy - py) > tol
            for pts in centers.values() if len(pts) > 1
            for (cx, cy) in pts for (px, py) in pts
        )
        policy = str(ctx["policy"])
        if moved and policy in ("mask", "mask_only", "whitespace"):
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: text moves more than {:.0f}px in {}; "
                    "static {} would misalign, falling back to external."
                    ).format(tol, roi_id, policy))
            policy = "external"
        # 行框:视频坐标 − 外接框原点 → 平面坐标,与纯函数对接
        rows = [(text, (x1 - ox, y1 - oy, x2 - ox, y2 - oy))
                for text, (x1, y1, x2, y2) in rows_video]

        def _ts(value: str) -> float:
            try:
                return self._parse_ass_time_to_seconds(value)
            except Exception:
                return 0.0

        # A2:逐行可见区间 → 连续实例 → 活动集合时间片。非正时长(空组等
        # 退化输入)不进切片并留痕;实例 = 该行重叠跨度合并后的连续段。
        from core.scene_timeline import TimedRow, active_slices

        row_spans: Dict[int, List[Tuple[float, float]]] = {}
        invalid_rows = 0
        for i, (s_str, e_str) in enumerate(row_times):
            start_sec, end_sec = _ts(s_str), _ts(e_str)
            if end_sec <= start_sec:
                invalid_rows += 1
                continue
            row_spans.setdefault(i, []).append((start_sec, end_sec))
        if invalid_rows:
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Scene text policy: {} row(s) with non-positive duration "
                    "excluded from the active-set timeline (ROI {}).").format(
                        invalid_rows, roi_id))
        timed: List[TimedRow] = []
        for i, spans in row_spans.items():
            merged: List[List[float]] = []
            for start, end in sorted(spans):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            for start, end in merged:
                timed.append(TimedRow(i, int(round(start * 100.0)),
                                      int(round(end * 100.0))))
        slices = active_slices(timed)

        # 逐行还原(仅 pose 开启的 ROI):策略行的多边形/行高/代表帧由
        # convert_from_memory 按行记录在 row_meta(与 rows 同序)。
        pose = self.roi_pose_tags.get(str(roi_id))
        row_meta = ctx.get("row_meta") or []
        frx = float(pose.get("frx", 0.0) or 0.0) if pose else 0.0
        fry = float(pose.get("fry", 0.0) or 0.0) if pose else 0.0
        frame_cache: Dict[int, Optional[np.ndarray]] = {}

        def _row_frame(meta: Dict) -> Optional[np.ndarray]:
            """该行所在帧(按帧号缓存);取帧失败返回 None(只跳过取色)。"""
            frame_num = meta.get("frame")
            if frame_num is None:
                return None
            num = int(frame_num)
            if num not in frame_cache:
                try:
                    frame_cache[num] = self._get_analysis_reader().read(num)
                except Exception:  # 取帧失败只跳过取色,不影响几何标签
                    frame_cache[num] = None
            return frame_cache[num]

        cfg = SceneTextPolicyConfig(mode=policy)
        base_fs = int(self.height * 0.04)
        # B3:自动亮度开启时,静态遮罩/文字按帧采样屏幕背景亮度(遮罩的
        # base_color 与亮度曲线按通道缩放、保持不透明)。
        auto_brightness = bool(self.roi_auto_brightness.get(str(roi_id)))
        luma_cache: Dict[int, Optional[np.ndarray]] = {}

        def _read_frame(num: int) -> Optional[np.ndarray]:
            if num not in luma_cache:
                try:
                    luma_cache[num] = self._get_analysis_reader().read(num)
                except Exception:  # 取帧失败只跳过该采样点
                    luma_cache[num] = None
            return luma_cache[num]

        events: List[Dict] = []
        for sl in slices:
            slice_rows = [rows[i] for i in sl.row_ids]
            slice_meta = [row_meta[i] if i < len(row_meta) else {}
                          for i in sl.row_ids]
            specs, applied, notes = apply_policy_static(
                slice_rows, ctx["plane"], cfg, float(self.width),
                float(self.height), base_font_size=base_fs)
            for note in notes:
                logger.warning(
                    _tr("OCRToASSOptimizer",
                        "Scene text policy: {} (ROI {}, slice {}).").format(
                            note, roi_id,
                            (sl.start_cs / 100.0, sl.end_cs / 100.0)))
            logger.info(
                _tr("OCRToASSOptimizer",
                    "Scene text policy for {}: {} applied on slice "
                    "[{}, {}) with {} row(s), {} spec(s).").format(
                        roi_id, applied, sl.start_cs / 100.0,
                        sl.end_cs / 100.0, len(sl.row_ids), len(specs)))
            start_time = self._format_time_seconds(sl.start_cs / 100.0)
            end_time = self._format_time_seconds(sl.end_cs / 100.0)
            slice_events: List[Dict] = []
            for spec in specs:
                kind = spec["kind"]
                if kind == "mask":
                    tags = _shift_pos_tag(spec["tags"], float(ox), float(oy))
                    box = spec.get("box") or []
                    slice_events.append({
                        "roi": roi_id,
                        "start_time": start_time, "end_time": end_time,
                        "style": "Scene", "tags": tags, "body": "",
                        "layer": int(spec.get("layer", 0)), "policy": True,
                        "base_color": spec.get("base_color"),
                        "_mask_box": box,
                        # B4:屏幕坐标几何框(平面坐标 + 分析窗原点)。
                        "_screen_box": (float(box[0]) + ox, float(box[1]) + oy,
                                        float(box[2]) + ox,
                                        float(box[3]) + oy)
                        if len(box) == 4 else None,
                    })
                elif kind == "text":
                    tags = _shift_pos_tag(spec["tags"], float(ox), float(oy))
                    if pose:
                        # Scene 行进策略路径后不再经过 styling._apply_roi_pose_tags,
                        # 逐行还原标签(行 frz + 取色)在此追加;几何标签必须并入
                        # 既有 override 块内部(见 merge_restoration_tags)。
                        meta = (slice_meta[spec["row"]]
                                if spec["row"] < len(slice_meta) else {})
                        tags = merge_restoration_tags(
                            tags, meta.get("poly") or [], _row_frame(meta),
                            float(meta.get("height") or 0.0), frx, fry)
                    event = {
                        "roi": roi_id,
                        "start_time": start_time, "end_time": end_time,
                        "style": "Scene", "tags": tags,
                        "body": spec.get("body", ""),
                        "layer": int(spec.get("layer", 0)), "policy": True,
                    }
                    if spec.get("comment"):  # mask_only:排版参考行 → Comment 行
                        event["comment"] = True
                    row_box = (slice_rows[spec["row"]][1]
                               if spec["row"] < len(slice_rows) else None)
                    if row_box is not None:
                        event["_screen_box"] = (
                            float(row_box[0]) + ox, float(row_box[1]) + oy,
                            float(row_box[2]) + ox, float(row_box[3]) + oy)
                    slice_events.append(event)
                elif kind == "note":
                    slice_events.append({
                        "roi": roi_id,
                        "start_time": start_time, "end_time": end_time,
                        "style": spec.get("style", "NoteBox"),
                        "tags": spec["tags"],
                        "body": spec.get("body", ""), "policy": True,
                    })
                elif kind == "scene_ws":
                    slice_events.append({
                        "roi": roi_id,
                        "start_time": start_time, "end_time": end_time,
                        "style": spec.get("style", "Scene"),
                        "tags": _shift_pos_tag(spec["tags"], float(ox),
                                               float(oy)),
                        "body": spec.get("body", ""), "policy": True,
                    })
            fps = float(self.fps) if self.fps else 25.0
            f0 = max(0, int(round(sl.start_cs / 100.0 * fps)))
            f1 = int(round(sl.end_cs / 100.0 * fps))
            frame_list = sorted({f for f in range(f0, f1 + 1, 3)}
                                | {f0, f1}) if f1 >= f0 else []
            if auto_brightness and slice_events:
                from core.scene_brightness import (
                    apply_scene_brightness,
                    sample_static_mask_luma_curve,
                )

                t0, t1 = sl.start_cs / 100.0, sl.end_cs / 100.0
                mask_events = [e for e in slice_events if e.get("_mask_box")]
                text_events = [e for e in slice_events
                               if not e.get("_mask_box")]
                mask_curves = {}
                for e in mask_events:
                    box = e.get("_mask_box")
                    key = tuple(round(v, 1) for v in box)
                    if key not in mask_curves:
                        rect = (box[0] + ox, box[1] + oy,
                                box[2] + ox, box[3] + oy)
                        mask_curves[key] = sample_static_mask_luma_curve(
                            _read_frame, rect, t0, t1, fps=fps)
                curved: List[Dict] = []
                for e in mask_events:
                    key = tuple(round(v, 1) for v in e.get("_mask_box"))
                    curved.extend(apply_scene_brightness(
                        [e], mask_curves.get(key) or []))
                # 文字与遮罩共用采样时间;文字取全部遮罩框的并集为采样窗。
                if text_events and mask_events:
                    ux1 = min(e["_mask_box"][0] for e in mask_events)
                    uy1 = min(e["_mask_box"][1] for e in mask_events)
                    ux2 = max(e["_mask_box"][2] for e in mask_events)
                    uy2 = max(e["_mask_box"][3] for e in mask_events)
                    union_curve = sample_static_mask_luma_curve(
                        _read_frame, (ux1 + ox, uy1 + oy, ux2 + ox, uy2 + oy),
                        t0, t1, fps=fps)
                    curved.extend(apply_scene_brightness(
                        text_events, union_curve))
                else:
                    curved.extend(text_events)
                slice_events = curved
            # B4:前景轮廓遮挡裁剪——同一物体的文字与有色遮罩同受
            # 时间局部 iclip;clear 段不裁,unknown 不复用过期轮廓。
            if self.roi_occlusion_clip.get(str(roi_id)) and slice_events:
                import cv2 as _cv2

                from core.occluder_contours import (
                    ContourResult,
                    apply_screen_occlusion,
                    build_seeds,
                    refine_occluder_contours,
                )

                boxes = [e["_screen_box"] for e in slice_events
                         if e.get("_screen_box")]
                if boxes and f1 >= f0:
                    plane_h, plane_w = ctx["plane"].shape[:2]
                    margin = 24.0
                    ux1 = max(0, int(min(b[0] for b in boxes) - ox - margin))
                    uy1 = max(0, int(min(b[1] for b in boxes) - oy - margin))
                    ux2 = min(float(plane_w), max(b[2] for b in boxes) - ox
                              + margin)
                    uy2 = min(float(plane_h), max(b[3] for b in boxes) - oy
                              + margin)
                    if ux2 > ux1 and uy2 > uy1:
                        domain = np.zeros((int(plane_h), int(plane_w)),
                                          np.uint8)
                        domain[uy1:int(uy2), ux1:int(ux2)] = 1
                        plane_gray = _cv2.cvtColor(ctx["plane"],
                                                   _cv2.COLOR_BGR2GRAY)

                        def _crop(f: int) -> Optional[np.ndarray]:
                            img = _read_frame(f)
                            if img is None:
                                return None
                            return img[oy:oy + int(plane_h),
                                       ox:ox + int(plane_w)]

                        screen_occlusions: Dict[int, Any] = {}

                        def _contours_for(f: int) -> ContourResult:
                            crop = _crop(f)
                            if crop is None:
                                return ContourResult([], "unknown",
                                                     "frame unavailable")
                            gray = _cv2.cvtColor(crop, _cv2.COLOR_BGR2GRAY)
                            fg, bg, status, reason = build_seeds(
                                gray, plane_gray, domain)
                            if status != "valid":
                                return ContourResult([], status, reason)
                            result = refine_occluder_contours(
                                crop, fg, bg, domain,
                                resolution_height=float(self.height or 1080))
                            if result.valid:
                                # 裁剪坐标 → 视频屏幕坐标(到 PlayRes 恰好
                                # 一次;apply_screen_occlusion 直接消费)。
                                from core.occluder_contours import ContourRing

                                result.rings = [
                                    ContourRing(
                                        r.points + np.array([ox, oy]),
                                        r.is_hole, r.parent)
                                    for r in result.rings]
                            return result

                        for f in frame_list:
                            screen_occlusions[f] = _contours_for(f)
                        # 相邻有效轮廓 IoU < 0.9 时补中间帧(单轮)。
                        sampled = sorted(screen_occlusions)

                        def _raster(result: ContourResult) -> np.ndarray:
                            m = np.zeros((int(plane_h), int(plane_w)),
                                         np.uint8)
                            outer = [r.points.astype(np.int32)
                                     for r in result.rings if not r.is_hole]
                            inner = [r.points.astype(np.int32)
                                     for r in result.rings if r.is_hole]
                            if outer:
                                _cv2.fillPoly(m, outer, 1)
                            if inner:
                                _cv2.fillPoly(m, inner, 0)
                            return m

                        extra: Dict[int, Any] = {}
                        for a, b in zip(sampled, sampled[1:]):
                            ra, rb = screen_occlusions[a], screen_occlusions[b]
                            if not (ra.valid and rb.valid):
                                continue
                            ma, mb = _raster(ra), _raster(rb)
                            inter = int(((ma > 0) & (mb > 0)).sum())
                            union = int(((ma > 0) | (mb > 0)).sum())
                            if union and inter / union >= 0.9:
                                continue
                            mid = (a + b) // 2
                            if mid not in screen_occlusions:
                                extra[mid] = _contours_for(mid)
                        screen_occlusions.update(extra)
                        event_geometry = {}
                        for e in slice_events:
                            if e.get("_screen_box") is not None:
                                box = e["_screen_box"]

                                class _Geom:
                                    def __init__(self, box, frames):
                                        self._box = box
                                        self._frames = frames

                                    def frames(self):
                                        return [(f, self._box)
                                                for f in self._frames]

                                event_geometry[str(id(e))] = _Geom(
                                    box, sorted(screen_occlusions))
                        slice_events = apply_screen_occlusion(
                            slice_events, screen_occlusions,
                            fps=float(self.fps) if self.fps else 25.0,
                            event_geometry=event_geometry)
            for e in slice_events:
                e.pop("_mask_box", None)
                e.pop("_screen_box", None)
            events.extend(slice_events)
        # 仅合并「显示属性完全相同」的相邻事件(同行跨片延续);不做任何
        # 时间并集/最小最大回填。
        def _disp_key(ev: Dict) -> tuple:
            return (ev.get("roi"), ev.get("style"), ev.get("tags"),
                    ev.get("body"), int(ev.get("layer", 0)),
                    bool(ev.get("comment")))

        merged_events: List[Dict] = []
        for ev in sorted(events, key=lambda e: (_ts(e["start_time"]),
                                                _ts(e["end_time"]))):
            if (merged_events
                    and _disp_key(merged_events[-1]) == _disp_key(ev)
                    and abs(_ts(merged_events[-1]["end_time"])
                            - _ts(ev["start_time"])) < 0.005):
                merged_events[-1]["end_time"] = ev["end_time"]
            else:
                merged_events.append(ev)
        return merged_events

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
                if self.motion_events:
                    logger.warning(_tr("OCRToASSOptimizer", "No valid OCR data found; writing motion-trajectory events only."))
                    # 纯轨迹输出同样过翻译拆分（原文 Comment / 译文 Dialogue）。
                    if self.translation_config is not None:
                        self._translate_subtitle_events(
                            [],
                            progress_callback=polish_progress_callback,
                            cancel_check=polish_cancel_check,
                        )
                    self._write_final_file([])
                else:
                    logger.warning(_tr("OCRToASSOptimizer", "No valid OCR data found, an empty ASS file will be generated."))
                    atomic_write_text(self.output_path, self._get_ass_header(),
                                      encoding='utf-8-sig')
                return

            subtitle_events: List[Dict[str, str]] = []
            for roi_id, frame_list in organized_data.items():
                logger.info(_tr("OCRToASSOptimizer", "Processing ROI: {}, containing {} valid frames.").format(roi_id, len(frame_list)))
                groups = self._group_consecutive_frames(frame_list)
                for group in groups:
                    group.lines = self._select_representative_lines(group)
                if self._roi_policy_mode(str(roi_id)) is None:
                    # 未启用场景策略的 ROI 保持 ROI 画像过滤(默认行为不变);
                    # 策略 ROI 里场景行与对白行都必须进入各自路径,不按画像
                    # 静默丢弃任一类。
                    groups = self._filter_groups_by_roi_profile(groups)
                logger.info(_tr("OCRToASSOptimizer", "ROI: {} generated {} subtitle groups.").format(roi_id, len(groups)))
                if str(roi_id) in self.motion_roi_ids:
                    # A3:coverage 只是前置必要条件——接管前先用轨迹预策略
                    # 证据对照静态 OCR 文本;缺证据(unverified)或对照失败
                    # (缺字/出现超前文本)都保留静态结果,并把该 ROI 的轨迹
                    # 候选从最终输出移除(不只清 motion_roi_ids)。
                    verdict = self._trajectory_takeover_fidelity(
                        str(roi_id), groups, frame_list)
                    if verdict.ok:
                        # 该 ROI 已由移动文字轨迹管线接管(轨迹事件在写文件时
                        # 并入),跳过静态事件生成避免同区域双份文本;轨迹失败
                        # 回退时该 ROI 不在 motion_roi_ids,静态路径照常。
                        logger.info(_tr("OCRToASSOptimizer", "ROI {} handled by motion-trajectory pipeline (text fidelity: {}); static events skipped.").format(roi_id, verdict.reason))
                        continue
                    self.motion_roi_ids.discard(str(roi_id))
                    self._rejected_motion_rois.add(str(roi_id))
                    logger.warning(_tr("OCRToASSOptimizer", "ROI {} trajectory takeover rejected by text fidelity ({}); static events restored and trajectory candidates removed.").format(roi_id, verdict.reason))

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
                    # 组代表帧(组内中间帧):逐行取色(既有 pose 路径)与策略行
                    # 的逐行还原标签(_finish_scene_policy_events)共用同一帧,
                    # FrameReader 按帧号缓存,同一帧只解码一次。
                    mid_frame = None
                    if group_frames:
                        mid_frame = group_frames[len(group_frames) // 2].frame_num
                    if policy_ctx is not None and styled_lines:
                        # 逐行拆分:Scene 行进策略,对白/顶部行保持原路径,
                        # 同一批行里两类都不丢失。
                        scene_lines, other_lines = self._split_policy_scene_lines(
                            group, styled_lines)
                        for line in scene_lines:
                            policy_ctx["rows"].append(
                                (line.text, tuple(float(v) for v in line.box)))
                            # 策略模块只消费 rows(text, box);还原素材走并行
                            # 的新键 row_meta(策略模块忽略未知键),不改变
                            # 既有键的形状。
                            policy_ctx["row_meta"].append({
                                "poly": [tuple(p) for p in line.polygon],
                                "height": line.bounding_height,
                                "frame": mid_frame,
                            })
                            policy_ctx["row_times"].append((start_time, end_time))
                        if scene_lines:
                            for fr in group_frames:
                                for line in fr.lines:
                                    policy_ctx["rows_motion"].append(
                                        (line.text,
                                         tuple(float(v) for v in line.box)))
                        if not other_lines:
                            continue
                        styled_lines = other_lines
                    pose = self.roi_pose_tags.get(str(roi_id))
                    if pose and not self.template_path:
                        # C2:上下字幕带(多行合并事件)在有 pose 且无模板时
                        # 保留分行几何——事件字号取各行 fit 值的最小值,
                        # 避免组宽超过 ROI。
                        from core.line_geometry import fit_line_size

                        for info in styled_lines:
                            line_boxes = info.get("line_boxes")
                            if not line_boxes or "\\fs" in info["tags"]:
                                continue
                            fits = [fit_line_size(tuple(float(v) for v in b),
                                                  None)
                                    for b in line_boxes]
                            fits = [f for f in fits if f is not None]
                            if not fits:
                                continue
                            fs_value = min(fits)
                            fs_text = (str(int(fs_value))
                                       if float(fs_value).is_integer()
                                       else f"{fs_value:.2f}")
                            info["tags"] = (info["tags"][:-1]
                                            + f"\\fs{fs_text}" + "}")
                    if pose:
                        # 取组内中间帧供逐行取色(打不开/失败时自动跳过取色);
                        # FrameReader 按帧号缓存,多组共享同一句柄。
                        styled_lines = self._apply_roi_pose_tags(
                            styled_lines, pose, frame_num=mid_frame)
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

            # A3:构造唯一的 selected_motion 列表——通过的 ROI 事件保留,
            # 拒绝/unverified ROI 的候选整体移除,无 roi 的显式 quad(手工
            # --motion-quad)作为附加输出保留。字体/翻译/写出只消费这里的
            # 结果,禁止把已丢弃的候选混进最终输出。
            if self._rejected_motion_rois:
                selected = [
                    ev for ev in self.motion_events
                    if str(ev.get("roi")) not in self._rejected_motion_rois]
                removed = len(self.motion_events) - len(selected)
                logger.warning(_tr(
                    "OCRToASSOptimizer",
                    "Removed {} trajectory candidate event(s) from rejected ROI(s): {}.").format(
                        removed, ", ".join(sorted(self._rejected_motion_rois))))
                self.motion_events[:] = selected

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

            # T3.4：润色/翻译改写 body 之前，按 OCR 原文把识别侧表挂到
            # 事件（合规关闭时只是轻量匹配、不改任何事件字段）。轨迹事件
            # 同样捕获（识别侧表无对应条目时零操作）。
            self._capture_event_font_idents(subtitle_events)
            if self.motion_events:
                self._capture_event_font_idents(self.motion_events)

            if (
                subtitle_events
                and self.subtitle_polisher is not None
                and getattr(self.subtitle_polisher, "text_polish_enabled", True)
            ):
                # 只润色普通字幕文本;策略事件(空 body 的遮罩、mask_only
                # 排版参考行)保持原样送写,不给模型"补全"空行的机会。
                polish_idx = [
                    k for k, ev in enumerate(subtitle_events)
                    if not ev.get("policy") and str(ev.get("body", ""))
                ]
                bodies = [subtitle_events[k]["body"] for k in polish_idx]

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
                if len(polished) != len(polish_idx):
                    logger.warning(
                        _tr("OCRToASSOptimizer", "Polish output length mismatch, using original subtitles.")
                    )
                else:
                    for k, nt in zip(polish_idx, polished):
                        subtitle_events[k]["body"] = nt
                    logger.info(_tr("OCRToASSOptimizer", "DeepSeek subtitle polishing applied."))

            if subtitle_events and self.translation_config is not None:
                # 翻译（T2.3）：设计顺序 润色 → 翻译 → ASS 生成，缝在事件
                # 写出之前。仅普通字幕行送翻；任何失败保留原文，不中断出片。
                self._translate_subtitle_events(
                    subtitle_events,
                    progress_callback=polish_progress_callback,
                    cancel_check=polish_cancel_check,
                )

            # T3.4：识别 → 合规决策 → 逐事件 \fn 落名（翻译联动映射链在
            # 闭包内感知 translation_config；§5.3 顺序：翻译 → 字体映射 →
            # 同一合规闸门 → ASS 生成）。合规关闭时零操作。轨迹事件同样
            # 过链（无识别快照的事件零操作；翻译拆分的译文行经快照映射
            # 目标语言字体，原文 Comment 行保持逐字原样）。
            self._apply_event_font_closure(subtitle_events)
            if self.motion_events:
                self._apply_event_font_closure(self.motion_events)

            self._write_final_file(subtitle_events)

            logger.info(_tr("OCRToASSOptimizer", "--- Conversion successful ---"))
            logger.info(_tr("OCRToASSOptimizer", "ASS subtitle file saved to: {}").format(self.output_path))

        except Exception as e:
            logger.error(_tr("OCRToASSOptimizer", "--- Conversion failed ---"))
            logger.error(_tr("OCRToASSOptimizer", "Error: {}").format(e), exc_info=True)
            raise
        finally:
            # 释放跨 ROI 共享的分析帧读取器(若曾创建)。
            self._close_analysis_reader()
            # 合规收尾(所有写出路径的统一终点,含空数据/空事件早退):
            # 有决策时写报告;合规关闭时零新文件;幂等,报告只写一次。
            self._write_compliance_report_if_needed()
            # T3.4 收尾:有缺口建议时写 <stem>_font_update_suggestions.json
            # (worker 不弹 GUI,包落盘即可;无建议零新文件)。
            self._write_font_update_suggestions_if_needed()

    def _translate_subtitle_events(
        self,
        subtitle_events: List[Dict[str, str]],
        *,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """对参与出片的字幕行执行翻译（T2.3，阶段 4：润色之后、写出之前）。

        输出形态（用户要求）：**原文行转 Comment 隐藏，译文行以 Dialogue
        写出**，两者时间/样式/标签（遮罩、\\move 等特效）逐项一致——

        - 只送普通字幕行（``policy`` 事件与空 body 不进模型，不给遮罩/
          NoteBox "补全"文本的机会）；静态事件与移动文字轨迹事件
          （``motion_events``，此前完全绕过翻译）都送翻；
        - 双语路由：行按所属 ROI 的 ``ocr_lang`` 分组送翻，每组以
          :data:`OCR_LANG_SOURCE_NAMES` 映射的人类语言名写入
          ``source_language`` 提示；未配置/未知 id 缺键时留空 = 模型自动
          检测源语言；
        - 拆分语义：译文与原文相同（该批失败/降级保留原文）→ 不拆分，
          保持单条 Dialogue 原文；有实际译文 → 原事件标 ``comment``
          （播放器不渲染，内容逐字保留），其后插入译文副本；
        - 字体识别快照（``_font_ident``）只留在译文副本上：后续字体映射
          链（T2.4 三级链）只给译文行写目标语言 ``\\fn``，原文行逐字
          原样，翻转 Comment → Dialogue 即可无损找回原文；
        - 进度与取消沿用润色的回调通道（``polish_progress_callback`` /
          ``polish_cancel_check`` 同款签名）；
        - 硬约束：任何异常（含模块导入失败）只记日志并保留原文，绝不
          中断出片——翻译模块自身已保证不抛，这里再兜一层防线。
        """
        try:
            from dataclasses import replace

            # 延迟导入：translation_config 为 None 的路径根本不进本方法，
            # font_intel.translation 不被导入（红线：翻译只挂主进程阶段 4；
            # chunk worker 子进程不经此处，且其载荷不含翻译配置）。
            from font_intel.translation import translate_subtitle_texts

            def _translate_split(events: List[Dict[str, str]]) -> List[Dict[str, str]]:
                """翻译一个事件列表并做 原文 Comment / 译文 Dialogue 拆分。"""
                trans_idx = [
                    k for k, ev in enumerate(events)
                    if not ev.get("policy") and not ev.get("comment")
                    and str(ev.get("body", ""))
                ]
                if not trans_idx:
                    return events
                cancel = cancel_check if cancel_check is not None else (lambda: False)
                # 按源语言分组（组间顺序 = 事件顺序；组内下标互不重叠）。
                groups: Dict[str, List[int]] = {}
                for k in trans_idx:
                    roi_id = str(events[k].get("roi", ""))
                    lang = str(self.roi_ocr_langs.get(roi_id, "") or "").strip()
                    groups.setdefault(lang, []).append(k)

                translated: Dict[int, str] = {}
                for lang, indices in groups.items():
                    if cancel():
                        logger.info(
                            _tr("OCRToASSOptimizer",
                                "Translation cancelled; keeping remaining original subtitles."))
                        break
                    cfg = self.translation_config
                    source = OCR_LANG_SOURCE_NAMES.get(lang, "")
                    if source:
                        cfg = replace(cfg, source_language=source)
                    bodies = [events[k]["body"] for k in indices]

                    def _on_batch(idx: int, total: int) -> None:
                        if progress_callback:
                            progress_callback(
                                99,
                                _tr(
                                    "OCRToASSOptimizer",
                                    "Step 4/4: Translating subtitles ({}/{} batches)...",
                                ).format(idx, total),
                            )

                    result = translate_subtitle_texts(
                        bodies, cfg, cancel_check=cancel, on_batch_done=_on_batch)
                    if len(result.texts) != len(indices):
                        logger.warning(
                            _tr("OCRToASSOptimizer",
                                "Translation output length mismatch, keeping original subtitles."))
                        continue
                    for k, text in zip(indices, result.texts):
                        translated[k] = text
                    stats = result.stats
                    logger.info(
                        _tr("OCRToASSOptimizer",
                            "Translation ({0}): {1}/{2} batches ok, {3} degraded, providers {4}, shortened {5} lines.")
                        .format(
                            source or "auto",
                            stats.batches_success, stats.batches_total,
                            stats.batches_degraded,
                            stats.provider_batches or {},
                            stats.shortened_lines,
                        ))

                if not translated:
                    return events
                # 有实际译文的行拆成 原文 Comment + 译文 Dialogue；译文与
                # 原文相同（失败/降级）的行保持单条原文 Dialogue 不拆。
                try:
                    from font_intel.closure import _IDENT_KEY
                except Exception:  # pragma: no cover - font_intel 必在（上方已导入 translation）
                    _IDENT_KEY = "_font_ident"
                out: List[Dict[str, str]] = []
                for k, ev in enumerate(events):
                    text = translated.get(k)
                    if text is None or text == ev.get("body"):
                        out.append(ev)
                        continue
                    original = dict(ev)
                    original["comment"] = True
                    original.pop(_IDENT_KEY, None)
                    translated_ev = dict(ev)
                    translated_ev["body"] = text
                    out.append(original)
                    out.append(translated_ev)
                return out

            new_events = _translate_split(subtitle_events)
            if new_events is not subtitle_events:
                subtitle_events[:] = new_events
            # 轨迹事件（此前完全绕过翻译）同样送翻拆分；worker 产出的
            # 轨迹事件带 roi（CLI 手动路径无 roi，走自动检测源语言）。
            if self.motion_events:
                self.motion_events[:] = _translate_split(self.motion_events)
        except Exception as exc:
            # 防线纵深：翻译模块承诺不抛，这里兜住导入失败等一切残漏，
            # 保留原文继续出片（全局硬约束：服务端故障绝不击穿任务链）。
            logger.warning(
                _tr("OCRToASSOptimizer",
                    "Translation stage failed; keeping original subtitles: {0}").format(exc),
                exc_info=True,
            )

    def _write_final_file(self, subtitle_events: List[Dict[str, str]]) -> None:
        """事件表(+ 轨迹事件)→ Dialogue/Comment 行 → 按起始时间排序 → 写 .ass。

        轨迹事件(Name=motion,scripts/motion_ass.build_motion_events 产出)
        在此处并入——位于噪声过滤/合并/LLM 润色之后;未开启翻译时脚本产出
        的 tags/body 逐字保留,开启翻译时轨迹事件同样经 原文 Comment /
        译文 Dialogue 拆分(见 _translate_subtitle_events)。Name 列缺省
        空串,既有事件输出逐字节不变。事件可选 ``comment`` 真值 → 写
        ``Comment:`` 行(mask_only 策略的排版参考行、翻译拆分的原文行,
        播放器不渲染),缺省 ``Dialogue:``。
        """
        events = list(subtitle_events) + [dict(e) for e in self.motion_events]
        all_dialogue_entries = []
        for ev in events:
            text = ev["tags"] + _sanitize_ass_body(ev["body"])
            # 事件可选 layer(策略事件:遮罩 0、文本 1)与 Name(轨迹事件
            # motion,供播放器/后续处理识别);缺省 0/空串,与既有输出一致。
            layer = int(ev.get("layer", 0) or 0)
            name = str(ev.get("name", "") or "")
            kind = "Comment" if ev.get("comment") else "Dialogue"
            entry = f"{kind}: {layer},{ev['start_time']},{ev['end_time']},{ev['style']},{name},0,0,0,,{text}"
            all_dialogue_entries.append(entry)

        if not all_dialogue_entries:
            logger.warning(_tr("OCRToASSOptimizer", "No valid subtitle groups formed for any ROI, an empty ASS file will be generated."))
            atomic_write_text(self.output_path, self._get_ass_header(),
                              encoding='utf-8-sig')
            return

        # Sort by parsed start time — lexicographic order on "H:MM:SS.CC"
        # only coincides with chronological order below 10 hours.
        all_dialogue_entries.sort(
            key=lambda x: self._parse_ass_time_to_seconds(x.split(',')[1])
        )

        header_content = self._get_ass_header()
        final_content = header_content + "\n".join(all_dialogue_entries)

        atomic_write_text(self.output_path, final_content, encoding='utf-8-sig')
