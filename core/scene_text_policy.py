# core/scene_text_policy.py
"""场景文字显示策略(overlap / mask / external / whitespace)+ 自动回退链。

对应《场景文字显示策略(mask / external / whitespace)设计》§3/§4/§6:
识别字幕锚定在原文字位置时,替换字体与原排版的字宽字重不可能一致,原字从
字幕笔画缝隙透出形成「双重曝光式重影」。本模块提供场景文字显示模式的事件生成:

- :func:`apply_policy` —— 模式入口:overlap 原样返回;mask 生成 ``\\p1``
  矢量遮罩盖住原文字(识别文本升到 layer 1);mask_only 只出遮罩——识别
  文本改写为 ASS ``Comment:`` 行(编辑器可见、播放器不渲染),layer 1 留给
  用户在遮罩上自行排版覆写(typesetting 工作流:盖掉片源烧录文字、重绘
  译文/矢量字);external 把文本挪出原区域、底带 ``NoteBox`` 展示框;
  whitespace 把文本放进原文字空白带(合成行框喂
  :func:`core.motion_ass.build_line_tracks`,轨迹与亮度标签零成本复用);
- :func:`apply_policy_static` —— 同一回退链的静态 ``\\pos`` 变体(主流水线
  用):输入行框 + 单帧平面图,输出不带时间的 spec dict(时间由调用方按
  所属组回填),遮罩为静态 ``\\an7\\pos\\p1`` 矩形;
- :class:`PolicyResult` —— 两个入口的返回值:兼容
  ``(events/specs, applied_mode, notes)`` 三元组解包,另以属性携带
  ``requested_mode`` 与 ``diagnostics``(轨迹参考帧及其来源、输入行框
  有效性统计);动态入口可选 ``ref_frame`` 显式指定行框参考帧(缺省隐式
  ``tracks[0].frame_num``,旧行为),输入行框统一做有限数/正宽高/边界
  裁剪校验,空轨迹与全无效行按回退链优雅降级并留痕;
- 自动回退链 **whitespace → mask → external**(仅当所选模式为三者之一且
  不可用时降级;overlap 不参与回退),降级原因记录在返回的 notes 里;
- :func:`sample_background_stats` —— 块区域剔除墨水像素后的中位色 +
  通道标准差(mask 可用性判据),并给出稳健离散(MAD/IQR 的 σ 等价)、
  采样像素数与置信度;``background_mode="robust"`` 启用双向墨剔除 +
  MAD/IQR 判定(默认 ``"std"`` 与旧阈值规则完全一致,置信度只作诊断
  不参与判定);
- :func:`merge_line_blocks` —— 段落块合并(整段一次盖住,行距缝隙不露字;
  实现迁至 :mod:`core.text_alignment`,此处 re-export 保持 API);
- :func:`find_whitespace_band_scored` —— 墨迹二值化 + 行占用剖面找空白带,
  额外返回候选带的 confidence 评分(面积/宽高/背景均匀性/可排版长度)
  与逐项分量,并支持 ``threshold_mode``
  = ``percentile``(行内百分位,适应渐变)/``otsu``(默认 ``global`` 与
  旧行为一致);低置信度带由调用方沿既定回退链降级并在 diagnostics 留痕;
- 透视遮罩安全边界(Task 5):mask 模式逐帧检查遮罩框四角经单应映射后
  与 ASS 可渲染矩形的偏差(角点最大偏移/对角线,无量纲),超过
  ``mask_max_perspective_error``(默认 0.1,轴对齐/规则缩放场景 ≈0 不受
  影响)或四角退化(零面积/共线/非有限)时降级 external 并留痕;
  ``mask_polygon_clip``(实验开关,默认关)改生成逐帧四角 ``\\p1`` 多边形
  遮罩事件,精确覆盖透视梯形;
- :func:`wrap_cjk` / :func:`fit_font_size` —— CJK 折行(行首禁则)与字号适配。

轨迹生成完全复用 :mod:`core.motion_ass`(块/合成行框角点经
``build_line_tracks`` 同款单应映射,遮罩沿用其 DP 分段与 ``\\t`` 叠加标签)。
本模块为纯函数:输入图像用 ndarray,不读视频、不 import PySide6。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core.motion_ass import (
    MotionAssConfig,
    _apply_homography,
    _fmt1,
    _frame_map,
    _homography_between,
    _merge_chains_with_hold,
    _next_frame_time,
    _overlay_tags,
    _pose_from_quad,
    _seg_ms,
    _segment_indices,
    _split_runs,
    build_line_tracks,
    format_ass_time,
    punct_comp_offset_px,
    smooth_line_track,
    synthesize_events,
)
from core.scene_timeline import TimedRow, active_slices
from core.text_alignment import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    detect_line_alignments,
    merge_line_blocks,
)

__all__ = [
    "SceneTextPolicyConfig",
    "PolicyResult",
    "BackgroundStats",
    "sample_background_stats",
    "merge_line_blocks",
    "find_whitespace_band_scored",
    "wrap_cjk",
    "fit_font_size",
    "apply_policy",
    "apply_policy_static",
]

POLICY_MODES = ("overlap", "mask", "mask_only", "external", "whitespace")

# sample_background_stats 的 background_mode 取值:
# "std"    —— 旧规则:非墨像素逐通道 std 的最大值 vs bg_max_std(默认,兼容);
# "robust" —— 双向墨剔除 + MAD/IQR σ 等价离散 + 样本数/置信度判定。
BACKGROUND_MODES = ("std", "robust")

# 空白带(_ink_mask / find_whitespace_band_scored)的 threshold_mode 取值:
# "global"     —— 旧规则:全图中位灰 − ink 阈值(默认,兼容);
# "percentile" —— 行内百分位阈值(逐行自适应,适应纵向渐变);
# "otsu"       —— 全图 Otsu 二值化阈值。
WS_THRESHOLD_MODES = ("global", "percentile", "otsu")

# diagnostics["ref_frame_source"] 取值:显式传入 / 由 tracks[0] 推断 / 不适用
_REF_EXPLICIT, _REF_INFERRED, _REF_NONE = "explicit", "inferred", "none"

_INK_DROP_DELTA = 40      # 墨水剔除/墨迹二值化阈值(低于背景估计 40 灰级)
_WRAP_BASE_FS = 40        # 折行用的基准字号(px)
_EXTERNAL_MIN_FS = 24     # external/whitespace 字号下限(px)
_NOTE_STYLE = "NoteBox"   # external 展示框样式(CLI 写入 ASS 头)
# 退化四边形判定:shoelace 面积 ≤ 该比例 × 对角线²(零面积/三点共线/自交
# ——有向面积抵消为 0)。1e-3 远低于任何真实遮罩框(最细长的合法框
# 2000×2px 也有 ≈1e-3 的面积/对角线²,一般行框 ≥0.01)。
_QUAD_DEGENERATE_AREA_RATIO = 1e-3


# ---------------------------------------------------------------------------
# 配置(设计 §5;CLI --config-json 以 policy_ 前缀覆盖字段)
# ---------------------------------------------------------------------------

@dataclass
class SceneTextPolicyConfig:
    """场景文字显示策略参数;``mode`` 为所选模式(CLI ``--scene-text-policy``)。"""

    mode: str = "overlap"          # overlap | mask | mask_only | external | whitespace
    mask_pad_ratio: float = 0.12   # 遮罩外扩(×行高)
    block_vgap_ratio: float = 0.35 # 并块的垂直间距阈值(×两行平均行高)
    bg_max_std: float = 18.0       # 遮罩降级的背景通道标准差上限
    background_mode: str = "std"   # std(旧规则) | robust(MAD/IQR+置信度)
    bg_min_samples: int = 512      # robust:采样充足度分母(少于则置信度降)
    bg_min_confidence: float = 0.2  # robust:mask 置信度下限(不足→降级)
    external_pos: str = "bottom"   # external 位置 bottom/top
    external_margin: int = 40      # external 边距(px)
    ws_min_lines: int = 2          # 空白带最小高度(行)
    ws_min_width_ratio: float = 0.6  # 空白带最小宽度(×平面宽)
    ws_threshold_mode: str = "global"  # global(旧规则) | percentile | otsu
    ws_percentile: float = 25.0    # percentile 模式的行内百分位
    ws_min_confidence: float = 0.0  # 候选带置信度下限(0=不筛,旧行为)
    # —— Task 5:透视遮罩安全边界 ——
    # 矩形近似误差上限(无量纲):逐帧把遮罩框四角映射到视频坐标后,与 ASS
    # 实际能渲染的「旋转+等比缩放矩形」按角点比较,取最大角点偏移 / 四边形
    # 对角线长。相似变换(平移/旋转/等比缩放)= 0;透视梯形/剪切 > 0。超限
    # → mask 降级 external。默认 0.1 为宽松值:轴对齐与规则缩放场景误差
    # ≈1e-16(浮点噪声),完全不受影响(测试证明)。
    mask_max_perspective_error: float = 0.1
    # 实验开关(默认关):开启后梯形场景不再降级,而是生成逐帧四角 ``\p1``
    # 多边形遮罩事件(精确覆盖透视形变;lost 帧无四角数据,区间无遮罩)。
    mask_polygon_clip: bool = False

    def __post_init__(self) -> None:
        if self.mode not in POLICY_MODES:
            raise ValueError(
                f"unknown scene text policy mode: {self.mode!r}; "
                f"valid modes: {', '.join(POLICY_MODES)}")
        if self.background_mode not in BACKGROUND_MODES:
            raise ValueError(
                f"unknown background mode: {self.background_mode!r}; "
                f"valid modes: {', '.join(BACKGROUND_MODES)}")
        if self.ws_threshold_mode not in WS_THRESHOLD_MODES:
            raise ValueError(
                f"unknown whitespace threshold mode: "
                f"{self.ws_threshold_mode!r}; "
                f"valid modes: {', '.join(WS_THRESHOLD_MODES)}")


# ---------------------------------------------------------------------------
# 结果契约与输入行框校验(PolicyResult 兼容三元组解包,字段只追加)
# ---------------------------------------------------------------------------

class PolicyResult(tuple):
    """策略结果:``(events, applied_mode, notes)`` 三元组 + 诊断属性。

    兼容既有消费方的三元组解包(``events, applied, notes = apply_policy(...)``
    与 ``== tuple`` 比较);同时以属性携带 ``events`` / ``applied_mode`` /
    ``notes`` / ``requested_mode`` / ``diagnostics``,新字段只追加、不改变
    既有返回值消费方。``diagnostics`` 含 requested/applied 模式、轨迹参考帧
    (``ref_frame`` 及其来源 explicit/inferred/none)与输入行框有效性统计
    (total/valid/invalid/clipped 行数、逐行原因)。
    """

    def __new__(cls, events, applied_mode, notes, requested_mode, diagnostics):
        obj = super().__new__(cls, (events, applied_mode, notes))
        obj.events = events
        obj.applied_mode = applied_mode
        obj.notes = notes
        obj.requested_mode = requested_mode
        obj.diagnostics = diagnostics
        return obj

    def __repr__(self) -> str:
        return (f"PolicyResult(applied_mode={self.applied_mode!r}, "
                f"requested_mode={self.requested_mode!r}, "
                f"notes={self.notes!r}, diagnostics={self.diagnostics!r})")


def _validate_rows(
    blocks_meta: Sequence[Row],
    plane_w: float,
    plane_h: float,
) -> Tuple[List[Row], List[int], Dict[str, object]]:
    """输入行框校验:有限数、正宽高、裁到平面边界。

    返回 ``(有效行, 原始输入索引, 统计)``。非有限坐标(NaN/inf)、坐标数
    不足、宽或高非正、完全越出平面的行整行剔除;部分越界行裁剪到
    ``(0, 0, plane_w, plane_h)`` 并计入 ``clipped_rows``。统计 dict 携带
    total/valid/invalid/clipped 行数与逐行剔除原因(供 diagnostics)。
    """
    valid: List[Row] = []
    indices: List[int] = []
    reasons: List[str] = []
    clipped = 0
    for i, item in enumerate(blocks_meta):
        try:
            text, box = item
            if len(box) != 4:
                raise ValueError("box must have 4 coordinates")
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            reasons.append(f"row {i}: malformed box {box!r}")
            continue
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
            reasons.append(f"row {i}: non-finite coordinate")
            continue
        if x2 <= x1 or y2 <= y1:
            reasons.append(
                f"row {i}: non-positive size (w={x2 - x1:g}, h={y2 - y1:g})")
            continue
        cx1, cy1 = max(0.0, x1), max(0.0, y1)
        cx2 = min(float(plane_w), x2)
        cy2 = min(float(plane_h), y2)
        if cx2 - cx1 < 1e-6 or cy2 - cy1 < 1e-6:
            reasons.append(f"row {i}: fully outside plane")
            continue
        if (cx1, cy1, cx2, cy2) != (x1, y1, x2, y2):
            clipped += 1
        valid.append((text, (cx1, cy1, cx2, cy2)))
        indices.append(i)
    stats: Dict[str, object] = {
        "total_rows": len(blocks_meta),
        "valid_rows": len(valid),
        "invalid_rows": len(blocks_meta) - len(valid),
        "clipped_rows": clipped,
        "invalid_reasons": reasons,
    }
    return valid, indices, stats


def _merge_bg_diag(diag: Dict[str, object],
                   diag_extra: Dict[str, object]) -> Dict[str, object]:
    """把背景/空白带/透视误差/逐行对齐诊断(逐块统计、候选带、误差度量、
    逐行判定结果)并入 diag。"""
    diag["background_blocks"] = list(diag_extra.get("background_blocks", []))
    diag["whitespace_band"] = diag_extra.get("whitespace_band")
    diag["mask_perspective"] = list(diag_extra.get("mask_perspective", []))
    diag["line_alignments"] = diag_extra.get("line_alignments")
    return diag


def _result_diagnostics(
    requested_mode: str,
    applied_mode: str,
    *,
    ref_frame: Optional[int],
    ref_source: str,
    row_stats: Dict[str, object],
) -> Dict[str, object]:
    """构造 ``PolicyResult.diagnostics``(参考帧 + 行框有效性统计)。"""
    return {
        "requested_mode": str(requested_mode),
        "applied_mode": str(applied_mode),
        "ref_frame": None if ref_frame is None else int(ref_frame),
        "ref_frame_source": str(ref_source),
        "total_rows": int(row_stats["total_rows"]),
        "valid_rows": int(row_stats["valid_rows"]),
        "invalid_rows": int(row_stats["invalid_rows"]),
        "clipped_rows": int(row_stats["clipped_rows"]),
        "invalid_reasons": list(row_stats["invalid_reasons"]),
    }


# ---------------------------------------------------------------------------
# 取色 / 并块 / 空白带检测 / 折行 / 字号适配(纯函数)
# ---------------------------------------------------------------------------

def _clip_box(box: Sequence[float], width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    """浮点行框 → 裁到图像边界的整数半开区间 (x1, y1, x2, y2);空则 None。"""
    x1 = max(0, int(math.floor(float(box[0]))))
    y1 = max(0, int(math.floor(float(box[1]))))
    x2 = min(int(width), int(math.ceil(float(box[2]))))
    y2 = min(int(height), int(math.ceil(float(box[3]))))
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return x1, y1, x2, y2


@dataclass(frozen=True)
class BackgroundStats:
    """背景取色统计(:func:`sample_background_stats` 的返回值)。

    ``color_bgr`` / ``std_max_channel`` 即旧二元组接口 ``(color, std)`` 的语义;其余字段为 Task 3 加固新增——``robust_spread_mad``
    = ``1.4826 × MAD``、``robust_spread_iqr`` = ``IQR / 1.349``(均为逐通道
    最大值的 σ 等价离散),``n_samples`` 为剔除墨水后的采样像素数,
    ``confidence`` ∈ [0, 1] = 均匀度 × 采样充足度,``uniform`` 为当前模式
    下的可用性判定(std 模式即旧 ``std <= bg_max_std`` 规则)。
    """

    color_bgr: Tuple[int, int, int]
    std_max_channel: float
    robust_spread_mad: float
    robust_spread_iqr: float
    n_samples: int
    confidence: float
    mode: str
    uniform: bool

    @property
    def robust_spread(self) -> float:
        """robust 模式的判定值:MAD 与 IQR 两种 σ 估计的较大者。"""
        return max(self.robust_spread_mad, self.robust_spread_iqr)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def sample_background_stats(
    plane_img_bgr: np.ndarray,
    box: Sequence[float],
    *,
    ink_drop_delta: int = _INK_DROP_DELTA,
    background_mode: str = "std",
    bg_max_std: float = 18.0,
    bg_min_samples: int = 512,
    bg_min_confidence: float = 0.2,
) -> BackgroundStats:
    """块区域背景取色的完整统计。

    区域内灰度中位值 ``m``;``background_mode="std"``(默认)沿用旧规则:
    灰度 < ``m - ink_drop_delta`` 判墨剔除,剩余像素按通道取中位 → 遮罩色,
    ``std_max_channel`` = 非墨像素逐通道 std 的最大值,``uniform`` = 旧
    ``std <= bg_max_std`` 判定(默认配置下与旧行为完全一致)。
    ``background_mode="robust"``:墨剔除改为双向(偏离中位超过
    ``ink_drop_delta``,暗底反白字的亮笔画同样剔除),并统计 MAD/IQR 的
    σ 等价离散、采样像素数;``confidence`` = ``clamp01(1 - spread/threshold)``
    × ``min(1, n_samples/bg_min_samples)``,``uniform`` 要求 robust spread
    ≤ ``bg_max_std`` 且 confidence ≥ ``bg_min_confidence``。

    剔除后无像素(std 模式的全墨防御)→ 全区域中位色、std=0;空区域
    (框在图外)→ ``n_samples=0``、confidence=0、``uniform=False``
    (旧接口仍返回 ``((0, 0, 0), 0.0)`` 不变)。``background_mode`` 非法时
    抛 :class:`ValueError`。
    """
    if background_mode not in BACKGROUND_MODES:
        raise ValueError(
            f"unknown background mode: {background_mode!r}; "
            f"valid modes: {', '.join(BACKGROUND_MODES)}")
    h, w = plane_img_bgr.shape[:2]
    clipped = _clip_box(box, w, h)
    if clipped is None:
        return BackgroundStats((0, 0, 0), 0.0, 0.0, 0.0, 0, 0.0,
                               background_mode, False)
    x1, y1, x2, y2 = clipped
    region = plane_img_bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float64)
    med_gray = float(np.median(gray))
    if background_mode == "robust":
        # 双向墨剔除:暗笔画与暗底上的反白亮字都算「墨」
        keep = np.abs(gray - med_gray) <= float(ink_drop_delta)
    else:
        keep = gray >= med_gray - float(ink_drop_delta)
    if not bool(keep.any()):
        # 全为墨(std 模式的数学边界,防御):退回全区域中位色
        flat = region.reshape(-1, 3)
        med = np.median(flat, axis=0)
        color = (int(round(float(med[0]))), int(round(float(med[1]))),
                 int(round(float(med[2]))))
        total = int(gray.size)
        confidence = min(1.0, total / max(1, int(bg_min_samples)))
        return BackgroundStats(color, 0.0, 0.0, 0.0, total, confidence,
                               background_mode, True)
    pixels = region[keep].astype(np.float64)
    med = np.median(pixels, axis=0)
    color = (int(round(float(med[0]))), int(round(float(med[1]))),
             int(round(float(med[2]))))
    std = float(np.max(np.std(pixels, axis=0)))
    n_samples = int(keep.sum())
    mad = np.median(np.abs(pixels - med), axis=0)
    robust_mad = float(np.max(1.4826 * mad))
    q1, q3 = np.percentile(pixels, (25.0, 75.0), axis=0)
    robust_iqr = float(np.max((q3 - q1) / 1.349))
    threshold = float(bg_max_std)
    spread = std if background_mode == "std" else max(robust_mad, robust_iqr)
    uniformity = (_clamp01(1.0 - spread / threshold) if threshold > 0.0
                  else (1.0 if spread <= 0.0 else 0.0))
    sufficiency = min(1.0, n_samples / max(1, int(bg_min_samples)))
    confidence = uniformity * sufficiency
    if background_mode == "robust":
        uniform = (spread <= threshold and n_samples > 0
                   and confidence >= float(bg_min_confidence))
    else:
        uniform = std <= threshold  # 旧判定规则,逐字保留
    return BackgroundStats(color, std, robust_mad, robust_iqr, n_samples,
                           confidence, background_mode, uniform)



def _ink_mask(
    gray: np.ndarray,
    threshold_mode: str,
    ws_percentile: float,
) -> np.ndarray:
    """墨迹二值化(布尔阵列,``True`` = 墨)。

    ``global``(默认)= 全图中位灰 − 阈值(旧行为);``percentile`` =
    逐行百分位 − 阈值(行内自适应,纵向渐变不再整块误判墨迹);``otsu``
    = 全图 Otsu 阈值。非法模式抛 :class:`ValueError`。
    """
    if threshold_mode not in WS_THRESHOLD_MODES:
        raise ValueError(
            f"unknown whitespace threshold mode: {threshold_mode!r}; "
            f"valid modes: {', '.join(WS_THRESHOLD_MODES)}")
    gray_f = gray.astype(np.float64)
    if threshold_mode == "percentile":
        row_thr = np.percentile(gray_f, float(ws_percentile), axis=1,
                                keepdims=True)
        return gray_f < row_thr - _INK_DROP_DELTA
    if threshold_mode == "otsu":
        otsu_thr, _bin = cv2.threshold(gray_f.astype(np.uint8), 0, 255,
                                       cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # cv2 的 Otsu 在直方图间隙上取下沿(如暗类 20/亮类 240 → 阈值 20),
        # 因此暗类判定用 <=;常数图 Otsu 返回 0 → 无墨(整面即为空白带)
        return gray_f <= float(otsu_thr)
    return gray_f < float(np.median(gray_f)) - _INK_DROP_DELTA


def _band_confidence(components: Dict[str, float], layout_score: float) -> float:
    """候选空白带 confidence = 面积/宽高/均匀性/可排版长度四项等权平均。"""
    return 0.25 * (float(components["c_area"]) + float(components["c_size"])
                   + float(components["c_uniformity"])
                   + _clamp01(layout_score))


def find_whitespace_band_scored(
    plane_img_gray: np.ndarray,
    occupied_boxes: Sequence[Sequence[float]],
    *,
    line_h: float,
    min_lines: int = 2,
    min_width_ratio: float = 0.6,
    threshold_mode: str = "global",
    ws_percentile: float = 25.0,
) -> Tuple[Optional[Tuple[int, int, int, int]], float, Dict[str, object]]:
    """空白带查找(带 confidence 评分):返回 ``(band, confidence, info)``。

    候选带选择与旧规则一致(面积最大者);confidence ∈ [0,1] 按「面积占比、
    宽高达标度、背景均匀性、可排版长度」四项等权平均——可排版长度一项需
    要实际折行结果,此处取满分 1.0,由调用方(:func:`_apply_whitespace` /
    :func:`_apply_static_whitespace`)在折行后用 :func:`_band_confidence`
    按真实 ``c_layout`` 重算。``info`` 携带阈值模式、候选带数量与各项分量
    (``c_area`` / ``c_size`` / ``c_uniformity`` / ``robust_sigma``),
    ``band`` 为 None 时 confidence = 0。
    """
    gray = np.asarray(plane_img_gray)
    h, w = gray.shape[:2]
    info: Dict[str, object] = {
        "threshold_mode": str(threshold_mode),
        "n_candidates": 0,
        "c_area": 0.0,
        "c_size": 0.0,
        "c_uniformity": 0.0,
        "robust_sigma": 0.0,
    }
    if h < 1 or w < 1:
        return None, 0.0, info
    ink = _ink_mask(gray, threshold_mode, ws_percentile)
    ink = ink.astype(np.uint8)
    k = max(1, int(round(float(line_h) / 4.0)))
    if k > 1:
        ink = cv2.dilate(ink, np.ones((k, k), np.uint8))

    row_occupied = ink.any(axis=1)
    for box in occupied_boxes:
        clipped = _clip_box(box, w, h)
        if clipped is not None:
            row_occupied[clipped[1]:clipped[3]] = True

    candidates: List[Tuple[int, int, int, int]] = []
    y = 0
    while y < h:
        if row_occupied[y]:
            y += 1
            continue
        y0 = y
        while y < h and not row_occupied[y]:
            y += 1
        if (y - y0) < float(min_lines) * float(line_h):
            continue
        col_free = ~ink[y0:y, :].any(axis=0)
        x = 0
        while x < w:
            if not col_free[x]:
                x += 1
                continue
            x0 = x
            while x < w and col_free[x]:
                x += 1
            if (x - x0) >= float(min_width_ratio) * float(w):
                candidates.append((x0, y0, x, y))
    if not candidates:
        return None, 0.0, info
    # 与旧实现一致:面积最大者(并列取扫描序先出现者)
    band = max(candidates, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    x1, y1, x2, y2 = band
    band_w = float(x2 - x1)
    band_h = float(y2 - y1)
    plane_area = float(h) * float(w)
    band_gray = gray[y1:y2, x1:x2].astype(np.float64)
    med = float(np.median(band_gray))
    robust_sigma = float(1.4826 * np.median(np.abs(band_gray - med)))
    c_area = _clamp01((band_w * band_h) / plane_area)
    c_size = 0.5 * (
        _clamp01(band_h / (float(min_lines) * float(line_h)))
        + _clamp01(band_w / (float(min_width_ratio) * float(w))))
    # 带内灰度稳健 σ 达到墨迹阈值即视为完全不均匀(与 _INK_DROP_DELTA 同锚)
    c_uniformity = _clamp01(1.0 - robust_sigma / float(_INK_DROP_DELTA))
    confidence = _band_confidence(
        {"c_area": c_area, "c_size": c_size, "c_uniformity": c_uniformity},
        1.0)
    info.update({
        "n_candidates": len(candidates),
        "c_area": c_area,
        "c_size": c_size,
        "c_uniformity": c_uniformity,
        "robust_sigma": robust_sigma,
    })
    return band, confidence, info



# 行首禁则字符(不得出现在折行后行首)
_KINSOKU_START = frozenset("」。，、!?:;…)】\"")


def _ascii_tokens(text: str) -> List[str]:
    """分词:ASCII 字母/数字连续段为单词token,其余逐字符(含空格)。"""
    tokens: List[str] = []
    word = ""
    for ch in text:
        if ch.isascii() and ch.isalnum():
            word += ch
        else:
            if word:
                tokens.append(word)
                word = ""
            tokens.append(ch)
    if word:
        tokens.append(word)
    return tokens


def wrap_cjk(text: str, max_chars: int) -> List[str]:
    """CJK 折行:行首禁则字符(」。，、等)不下头;ASCII 单词尽量不断。

    贪心逐 token 填行:单词放不下换行(超长单词硬切);禁则字符放不下时
    连同上一字符下挪;行首空格丢弃。空文本返回 ``[""]``。
    """
    max_chars = max(1, int(max_chars))
    lines: List[str] = []
    for para in str(text).split("\n"):
        line = ""
        for tok in _ascii_tokens(para):
            if tok == " ":
                if line and len(line) < max_chars:
                    line += " "
                continue
            if len(line) + len(tok) <= max_chars:
                line += tok
                continue
            if len(tok) > max_chars:
                if line:
                    lines.append(line.rstrip())
                for i in range(0, len(tok), max_chars):  # 超长单词硬切
                    lines.append(tok[i:i + max_chars])
                line = ""
            elif tok in _KINSOKU_START and line:
                # 行首禁则:连同上一字符下挪
                carry = line[-1]
                if len(line) > 1:
                    lines.append(line[:-1].rstrip())
                line = carry + tok
            else:
                if line:
                    lines.append(line.rstrip())
                line = tok
        if line:
            lines.append(line.rstrip())
    return lines or [""]


def fit_font_size(
    rows: int,
    band_h: float,
    longest_chars: int,
    band_w: float,
    *,
    base: int = _WRAP_BASE_FS,
    min_fs: int = _EXTERNAL_MIN_FS,
) -> int:
    """按可用高度/宽度适配字号:``min(base, band_h//rows, band_w//最长行)``,
    夹到 ``[min_fs, base]``。"""
    rows = max(1, int(rows))
    fs = min(int(base), int(float(band_h)) // rows,
             int(float(band_w)) // max(1, int(longest_chars)))
    return int(min(int(base), max(int(min_fs), fs)))


# ---------------------------------------------------------------------------
# 模式事件生成(§3)
# ---------------------------------------------------------------------------

Row = Tuple[str, Tuple[float, float, float, float]]


def _sorted_rows(blocks_meta: Sequence[Row]) -> List[Row]:
    """行按画面位置自上而下(次之按 x)排序,行序 = 阅读序。"""
    return sorted(blocks_meta, key=lambda tb: (float(tb[1][1]), float(tb[1][0])))


def _parse_ass_time(value: str) -> float:
    """ASS ``H:MM:SS.CC`` → 秒(与 scripts.motion_ass 同语义)。"""
    h, m, rest = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _pose_top_left(pose, w: float, h: float) -> Tuple[float, float]:
    """由 pose(中心/角度/缩放)还原矩形左上角。

    与 ``\\an7 + \\fscx\\fscy + \\frz`` 的渲染模型一致:左上角为锚点,
    旋转/缩放均绕左上角,``\\move`` 恰好跟踪该点。
    """
    ang = math.radians(pose.angle_deg)
    dx, dy = -w * pose.scale / 2.0, -h * pose.scale / 2.0
    cos, sin = math.cos(ang), math.sin(ang)
    return (pose.center[0] + dx * cos - dy * sin,
            pose.center[1] + dx * sin + dy * cos)


def _mask_events_for_block(
    box: Tuple[float, float, float, float],
    color_bgr: Tuple[int, int, int],
    tracks: Sequence,
    motion_cfg: MotionAssConfig,
    *,
    ref_frame: Optional[int] = None,
    style: str = "Scene",
) -> List[Dict]:
    """单块遮罩事件:``\\an7\\p1`` 矩形随轨迹平移/旋转/缩放(自己的 DP 分段)。

    块角点经 :func:`build_line_tracks` 同款映射得逐帧轨迹(纯色块,无需与
    文本事件对齐);遮罩不用 ``\\alpha``,调暗由调用方按 ``base_color`` 生成。
    ``ref_frame`` 为行框所在平面坐标系的参考帧;缺省回退旧行为
    (隐式 ``tracks[0].frame_num``,由入口在 diagnostics 注明)。
    ``style`` 为遮罩事件样式名(调用者自定义,缺省 ``Scene``)。
    """
    x1, y1, x2, y2 = box
    w = max(1.0, float(x2) - float(x1))
    h = max(1.0, float(y2) - float(y1))
    iw, ih = int(round(w)), int(round(h))
    b, g, r = (int(round(float(c))) for c in color_bgr)
    color_tag = f"\\1c&H{b:02X}{g:02X}{r:02X}&"
    drawing = (f"m 0 0 l {iw} 0 {iw} {ih} 0 {ih}{{\\p0}}")

    ref = tracks[0].frame_num if ref_frame is None else int(ref_frame)
    (lt,) = build_line_tracks([(x1, y1, x2, y2)], ["mask"], tracks,
                              ref_frame=ref)
    smooth_line_track(lt, window=motion_cfg.smooth_window,
                      max_gap=motion_cfg.smooth_max_gap)

    tmap = {t.frame_num: t for t in tracks}
    events: List[Dict] = []
    frames = sorted(lt.poses)
    for chain in _merge_chains_with_hold(_split_runs(frames), tmap,
                                         motion_cfg.lost_hold_sec):
        centers = np.array([lt.poses[f].center for f in chain], dtype=np.float64)
        angles = [lt.poses[f].angle_deg for f in chain]
        scales = [lt.poses[f].scale for f in chain]
        _raw, segs = _segment_indices(centers, angles, scales, motion_cfg)
        multi = len(segs) > 1
        last = len(segs) - 1
        # 链尾(含单段)结束时间延伸到下一帧:与文本事件(motion_ass 同款
        # 修复)对齐,否则链尾最后一帧遮罩先于文本消失,原字透出
        chain_times = [tmap[f].time_sec for f in chain]
        if len(chain_times) >= 2:
            chain_dts = sorted(b - a for a, b in zip(chain_times, chain_times[1:]))
            chain_dt = chain_dts[len(chain_dts) // 2]
        else:
            chain_dt = 0.0
        for si, (ai, bi) in enumerate(segs):
            f0 = chain[ai]
            # 非末段 end_frame = 下一段 start_frame(共享边界帧)
            f1 = chain[segs[si + 1][0]] if (multi and si < last) else chain[bi]
            t0 = tmap[f0].time_sec
            t1 = tmap[f1].time_sec
            if si == last:
                # 链尾先延伸再判零长:单帧链 f1 == f0(帧号相等不算零长),
                # 直接判会把整条链丢成无事件;延伸后仍无正时长才丢弃
                # (与 motion_ass 同款修复)。
                t1 = _next_frame_time(tmap, f1, t1, chain_dt)
            if t1 <= t0:
                continue
            tl0 = _pose_top_left(lt.poses[f0], w, h)
            tl1 = _pose_top_left(lt.poses[f1], w, h)
            if multi:
                move = (f"\\move({_fmt1(tl0[0])},{_fmt1(tl0[1])},"
                        f"{_fmt1(tl1[0])},{_fmt1(tl1[1])})")
            else:
                move = (f"\\move({_fmt1(tl0[0])},{_fmt1(tl0[1])},"
                        f"{_fmt1(tl1[0])},{_fmt1(tl1[1])},"
                        f"0,{_seg_ms(t0, t1)})")
            # 绘图命令与 {\p0} 必须原样进入 Text 字段,放进 tags(write_ass
            # 只对 body 做 ASCII 花括号安全化);\bord0 压掉样式描边,
            # 否则 \p 矩形会带一圈 Style 的 Outline 色边框
            tags = (f"{{\\an7\\p1\\bord0{color_tag}{move}"
                    + _overlay_tags(angles[ai], angles[bi],
                                    scales[ai], scales[bi], t0, t1, motion_cfg)
                    + "}")
            tags += drawing
            events.append({
                "start_time": format_ass_time(t0),
                "end_time": format_ass_time(t1),
                "style": str(style),
                "name": "motion",
                "tags": tags,
                "body": "",
                "layer": 0,
                "base_color": (b, g, r),
            })
    return events


def _shoelace_area(pts: np.ndarray) -> float:
    """多边形有向面积(shoelace;顶点按边界序)。自交时正负抵消 ≈ 0。"""
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _mask_quad_frames(
    box: Tuple[float, float, float, float],
    tracks: Sequence,
    ref: int,
) -> Tuple[List[Tuple[int, float, np.ndarray]], float, Optional[str]]:
    """遮罩框逐帧四角(视频坐标)+ 矩形近似误差 + 退化检测。

    **误差度量**(Task 5):把遮罩框四角经 ``H(ref→t)`` 映射到各帧视频
    坐标,与 ASS 实际能渲染的矩形(位姿模型与 ``_pose_top_left`` 一致:
    中心 = 四角均值、角度 = 顶边方向、等比 scale = 顶边长/参考长边,即
    ``\\an7\\p1 + \\move + \\frz\\fscx\\fscy`` 可表达的全部形状)按角点
    一一对应,取「最大角点偏移 / 四边形对角线长」为无量纲误差,再对帧取
    最大。纯平移/旋转/等比缩放(相似变换)= 0(浮点噪声量级);透视
    梯形、剪切等投影形变 > 0。选角点偏移而非面积差:面积守恒的剪切形变
    面积差为 0 但矩形同样盖不住。

    **退化判定**:任一 ok 帧四角含非有限坐标,或 shoelace 面积 ≤
    ``_QUAD_DEGENERATE_AREA_RATIO`` × 对角线²(零面积、三点共线、自交
    ——有向面积抵消),即整体退化(遮罩形状无定义)。

    返回 ``(逐帧 [frame_num, time_sec, 四角], 最大误差, 退化原因)``;
    退化时前两项为 ``([], 0.0, 原因)``。
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    horizontal = (x2 - x1) >= (y2 - y1)
    long_edge = max(w, h)
    corners = np.array(
        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
    hmap = _frame_map(tracks)
    frames: List[Tuple[int, float, np.ndarray]] = []
    max_err = 0.0
    for tq in tracks:
        if tq.status != "ok" or tq.homography is None:
            continue
        try:
            h_mat = _homography_between(hmap, ref, tq.frame_num)
            mapped = _apply_homography(corners, h_mat)
        except (KeyError, ValueError):
            return [], 0.0, (f"frame {tq.frame_num}: degenerate homography "
                             f"while mapping mask quad corners")
        if not np.all(np.isfinite(mapped)):
            return [], 0.0, (f"frame {tq.frame_num}: non-finite mask quad "
                             f"corners")
        area = abs(_shoelace_area(mapped))
        diag = float(np.linalg.norm(mapped[2] - mapped[0]))
        if area <= _QUAD_DEGENERATE_AREA_RATIO * max(diag * diag, 1e-9):
            return [], 0.0, (
                f"frame {tq.frame_num}: degenerate mask quad (shoelace area "
                f"{area:.3g}px^2 for diagonal {diag:.3g}px; corners "
                f"collapsed/collinear)")
        pose = _pose_from_quad(mapped, long_edge, horizontal)
        scale = pose.scale
        ang = math.radians(pose.angle_deg)
        cos, sin = math.cos(ang), math.sin(ang)
        tl = _pose_top_left(pose, w, h)
        # ASS 渲染矩形四角 = 平面框尺寸 (w, h) × 等比 scale,绕左上角旋转
        rect = np.array([
            [tl[0], tl[1]],
            [tl[0] + w * scale * cos, tl[1] + w * scale * sin],
            [tl[0] + w * scale * cos - h * scale * sin,
             tl[1] + w * scale * sin + h * scale * cos],
            [tl[0] - h * scale * sin, tl[1] + h * scale * cos],
        ], dtype=np.float64)
        err = (float(np.max(np.linalg.norm(mapped - rect, axis=1)))
               / max(diag, 1e-9))
        max_err = max(max_err, err)
        frames.append((int(tq.frame_num), float(tq.time_sec), mapped))
    return frames, max_err, None


def _polygon_mask_events_for_block(
    frames: Sequence[Tuple[int, float, np.ndarray]],
    color_bgr: Tuple[int, int, int],
    *,
    style: str = "Scene",
) -> List[Dict]:
    """逐帧四角 ``\\p1`` 多边形遮罩事件(实验开关 mask_polygon_clip=True)。

    每个跟踪 ok 帧一条事件,覆盖 ``[t_i, t_{i+1})``;末帧延续一个帧间隔
    (与 lost 保持语义一致)。drawing 坐标取四角相对包围盒左上角的偏移
    (全非负),``\\an7\\pos`` 精确定位包围盒左上角,保证 libass 的对齐
    基线与坐标一一对应;多边形精确覆盖透视梯形/剪切形变,无矩形近似的
    漏字。``frames`` 由 :func:`_mask_quad_frames` 产出(退化帧已在调用方
    拦截降级);lost 帧无四角数据,对应区间不产出遮罩(实验边界)。
    """
    b, g, r = (int(round(float(c))) for c in color_bgr)
    color_tag = f"\\1c&H{b:02X}{g:02X}{r:02X}&"
    n = len(frames)
    events: List[Dict] = []
    for i, (_fnum, t0, pts) in enumerate(frames):
        if i + 1 < n:
            t1 = frames[i + 1][1]
        else:
            t1 = t0 + (t0 - frames[i - 1][1]) if n >= 2 else t0
        if t1 <= t0:
            continue
        origin = pts.min(axis=0)
        rel = pts - origin
        drawing = (f"m {_fmt1(rel[0][0])} {_fmt1(rel[0][1])} l "
                   + " ".join(f"{_fmt1(x)} {_fmt1(y)}" for x, y in rel[1:])
                   + "{\\p0}")
        # 绘图命令与 {\p0} 原样进入 Text 字段;\bord0 压掉样式描边
        tags = (f"{{\\an7\\pos({_fmt1(origin[0])},{_fmt1(origin[1])})"
                f"\\p1\\bord0{color_tag}}}") + drawing
        events.append({
            "start_time": format_ass_time(t0),
            "end_time": format_ass_time(t1),
            "style": str(style),
            "name": "motion",
            "tags": tags,
            "body": "",
            "layer": 0,
            "base_color": (b, g, r),
        })
    return events


def _rendered_width(text: str, line_h: float) -> float:
    """估算替换字体渲染该行文本的宽度:全角 1.0×字高、半角/ASCII 0.5×。

    遮罩宽度须覆盖"渲染后的字幕"而不只是原 OCR 框——替换字体字宽通常
    大于原排版,不外扩会出现字幕字形悬出补丁边缘。
    """
    units = sum(0.5 if ord(ch) < 0x2E80 else 1.0 for ch in str(text))
    return units * float(line_h)


def _row_anchors_from_aligns(
    rows: Sequence[Row],
    aligns: Sequence[str],
) -> List[Optional[Tuple[int, float]]]:
    """逐行对齐判定 → 遮罩锚点 ``(an, anchor_x)``(与 ``rows`` 同序)。

    左对齐 → ``(4, 行框左缘)``,右对齐 → ``(6, 行框右缘)``,居中/未知 →
    ``None``(遮罩走旧的对称外扩,输出逐字节不变)。锚点 x 取行框**自身**
    的边:渲染文本由同一份对齐判定锚定在该边上,遮罩与文本共用一份判定
    结果,不重复投票。
    """
    out: List[Optional[Tuple[int, float]]] = []
    for i, (_text, box) in enumerate(rows):
        align = aligns[i] if i < len(aligns) else ALIGN_CENTER
        if align == ALIGN_LEFT:
            out.append((4, float(box[0])))
        elif align == ALIGN_RIGHT:
            out.append((6, float(box[2])))
        else:
            out.append(None)
    return out


_AN_TAG_RE = re.compile(r"\\an(\d+)")


def _row_anchors_from_events(
    events: Sequence[Dict],
    rows: Sequence[Row],
    orig_indices: Sequence[int],
) -> List[Optional[Tuple[int, float]]]:
    """轨迹路径:从文本事件的 ``\\an`` 标签取逐行锚点(与 ``rows`` 同序)。

    轨迹管线(:func:`core.motion_ass.synthesize_events`)已按逐行对齐判定
    锚定渲染文本:``\\an4`` 锚行框左缘、``\\an6`` 锚右缘、``\\an5`` 锚中心,
    事件带 ``line_idx``(= ``blocks_meta`` 输入序下标,``orig_indices`` 把
    ``rows`` 行序映射回同一输入序)。

    只取标签里的锚点**模式**(an),``anchor_x`` 取 ``rows`` 行框自身的
    左/右缘,而不是事件 tags 里 ``\\pos``/``\\move`` 的 x:遮罩框是
    ``rows``(参考帧平面坐标)的静态框、再经单应逐帧映射,而事件 tags 的
    坐标是**事件所在时刻**的锚点(带 ``\\move`` 分段时每段不同、还叠加
    跟踪/亚像素平滑),以其为锚会把参考帧的遮罩框整体带偏。取行框自身的
    边即与 ``rows`` 严格同坐标系,且与静态路径(:func:`_row_anchors_from_aligns`)
    语义一致。事件缺 ``line_idx``/``\\an`` 或锚点为居中 → ``None``(旧的
    对称外扩,旧调用方输出逐字节不变)。
    """
    an_by_input: Dict[int, int] = {}
    for ev in events:
        if ev.get("line_idx") is None:
            continue
        m = _AN_TAG_RE.search(str(ev.get("tags") or ""))
        if not m:
            continue
        try:
            idx = int(ev["line_idx"])
        except (TypeError, ValueError):
            continue
        an_by_input.setdefault(idx, int(m.group(1)))
    out: List[Optional[Tuple[int, float]]] = []
    for pos, (_text, box) in enumerate(rows):
        src = orig_indices[pos] if pos < len(orig_indices) else None
        an = an_by_input.get(int(src)) if src is not None else None
        if an == 4:
            out.append((4, float(box[0])))
        elif an == 6:
            out.append((6, float(box[2])))
        else:
            out.append(None)
    return out


def _padded_mask_box(
    rows_slice: Sequence[Row],
    plane_w: float,
    plane_h: float,
    cfg: SceneTextPolicyConfig,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    *,
    row_dx: Optional[Sequence[float]] = None,
    row_anchor: Optional[
        Sequence[Optional[Tuple[int, float]]]] = None,
) -> Tuple[Tuple[float, float, float, float], float]:
    """块遮罩外扩框:按行「渲染文本跨度 + pad + 标点补偿」后求 union。

    mask 动态/静态路径共用:遮罩须盖住"渲染后的字幕"(替换字体字宽通常
    大于原 OCR 框),并按 ``mask_pad_ratio`` 外扩行高方向的边距。块内不同
    文本行的渲染宽度与行尾标点补偿各不相同——先逐行算自己的跨度、平移
    ``row_dx[i]``,再对块内各行求联合框,保证每行都被自己补偿后的框覆盖
    (不取块级最大补偿整体平移,避免无标点行偏移或过宽);最后裁到平面/
    分析窗。返回 ``(union 框, 平均行高)``。

    ``row_anchor``(可选,与 ``rows_slice`` 等长)给出该行渲染文本的水平
    锚点 ``(an, anchor_x)``(平面坐标,与 rows 同坐标系):

    - ``(4, x)`` —— 渲染文本左锚于 ``x``(``\\an4``),占 ``[x, x+need_w]``;
      遮罩取「原 OCR 框」与「渲染跨度」的并集再外扩 pad,即
      ``[min(x1, x), max(x2, x+need_w)] ± pad``;
    - ``(6, x)`` —— 右锚(``\\an6``),占 ``[x-need_w, x]``,取法同上;
    - ``None``/``(5, x)`` —— 居中(``\\an5``,旧行为):以行中心 ``cx``
      对称外扩 ``max(box_w, need_w)/2 + pad``,表达式与旧实现逐字节一致。

    2.7.0 的对齐锚点把渲染文本钉在行框左/右缘后,旧实现的对称外扩在
    左/右锚下盖不住行尾方向伸出的字幕(CJK 行右侧漏 ≈pad,纯 ASCII 行
    ——渲染宽可达框宽两倍——漏出更多),原文字会从字幕旁露出;并入锚点
    后遮罩按"渲染文本真实跨度"外扩,``\\an5``/缺省路径输出不变。
    """
    boxes: List[Tuple[float, float, float, float]] = []
    line_h_sum = 0.0
    for i, (text, rbox) in enumerate(rows_slice):
        x1, y1, x2, y2 = (float(v) for v in rbox)
        line_h = max(1.0, y2 - y1)
        pad = float(cfg.mask_pad_ratio) * line_h
        # 渲染宽度外扩:遮罩水平方向取"原框"与"渲染行"的较大者,避免字幕
        # 字形悬出补丁
        need_w = _rendered_width(text, line_h)
        cx = (x1 + x2) / 2.0
        anchor = (row_anchor[i]
                  if row_anchor is not None and i < len(row_anchor) else None)
        an = int(anchor[0]) if anchor is not None else 5
        anchor_x = float(anchor[1]) if anchor is not None else cx
        if an == 4:
            # 左锚:渲染文本 [anchor_x, anchor_x+need_w];并集含原 OCR 框
            # (need_w < box_w 时不额外外扩,也不漏盖原框右段)
            left = min(x1, anchor_x) - pad
            right = max(x2, anchor_x + need_w) + pad
        elif an == 6:
            # 右锚:渲染文本 [anchor_x-need_w, anchor_x]
            left = min(x1, anchor_x - need_w) - pad
            right = max(x2, anchor_x) + pad
        else:
            # 居中/缺省:以行中心对称外扩(旧表达式与运算次序保持一致,
            # 保证缺省路径输出逐字节不变)
            half_w = (max(x2 - x1, need_w) + 2.0 * pad) / 2.0
            left, right = cx - half_w, cx + half_w
        dx = float(row_dx[i]) if row_dx is not None else 0.0
        boxes.append((left + dx, y1 - pad, right + dx, y2 + pad))
        line_h_sum += line_h
    ux1 = min(b[0] for b in boxes)
    uy1 = min(b[1] for b in boxes)
    ux2 = max(b[2] for b in boxes)
    uy2 = max(b[3] for b in boxes)
    mbox = (
        max(0.0, ux1),
        max(0.0, uy1),
        min(float(plane_w), ux2),
        min(float(plane_h), uy2),
    )
    if analysis_box is not None:  # 遮罩不越出文字平面(quad 窗口)
        mbox = (max(float(analysis_box[0]), mbox[0]),
                max(float(analysis_box[1]), mbox[1]),
                min(float(analysis_box[2]), mbox[2]),
                min(float(analysis_box[3]), mbox[3]))
    return mbox, line_h_sum / len(boxes)


def _apply_mask(
    events: List[Dict],
    rows: List[Row],
    blocks: List[Tuple[int, int]],
    plane_img_bgr: np.ndarray,
    tracks: Sequence,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    motion_cfg: MotionAssConfig,
    style: str,
    notes: List[str],
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    ref_frame: Optional[int] = None,
    orig_indices: Optional[Sequence[int]] = None,
    diag_extra: Optional[Dict[str, object]] = None,
    include_text: bool = True,
    row_anchor: Optional[
        Sequence[Optional[Tuple[int, float]]]] = None,
) -> Tuple[List[Dict], str, List[str]]:
    """mask / mask_only 模式:低层纯色遮罩盖住原文字(layer 0)+ 文本事件。

    ``include_text=True``(mask)时识别文本事件升到 layer 1 正常渲染;
    ``False``(mask_only,排版覆写工作流)时改写为 ``comment=True``——
    writer 输出 ASS ``Comment:`` 行,播放器不渲染,编辑器仍可见原文与
    时间供排版对照。其余行为两者完全一致。

    每块取色并检查背景均匀性:默认 ``background_mode="std"`` 任一块非墨
    像素通道 std > ``bg_max_std`` → 整体回退 external(旧规则不变);
    ``background_mode="robust"`` 用 :func:`sample_background_stats` 的
    MAD/IQR 离散、样本数与置信度判定。``diag_extra``(可选)就地累加
    ``background_blocks`` 逐块统计(std/robust spread/样本数/置信度)与
    ``mask_perspective`` 逐块透视误差,由入口合并进
    ``PolicyResult.diagnostics``。``ref_frame`` 透传给遮罩轨迹重建(缺省
    隐式 ``tracks[0].frame_num``,旧行为);行尾标点补偿按行计算,遮罩取
    「按行补偿后求 union」。``orig_indices`` 为排序后行位置 →
    ``blocks_meta`` 输入序下标的映射,供 external 回退时按 ``line_idx``
    建立事件归属。``row_anchor``(仅关键字,可选)为排序后行位置的渲染
    锚点 ``(an, anchor_x)`` 或 None(见 :func:`_row_anchors_from_events`),
    遮罩按渲染文本真实跨度外扩——``\\an4``/``\\an6`` 把文本钉在行框左/右缘,
    对称外扩会漏盖行尾方向伸出的字幕;None/``\\an5`` 保持居中对称外扩
    (缺省 None,旧调用方输出逐字节不变)。

    Task 5 透视安全边界:块遮罩框裁剪后宽/高 ≤ 0(退化裁剪框),或逐帧
    四角映射退化(:func:`_mask_quad_frames`),或矩形模式(实验开关
    ``mask_polygon_clip`` 关闭)下矩形近似误差超过
    ``mask_max_perspective_error`` 时,整体沿既定回退链降级 external 并在
    notes 记录 ``mask->external`` / ``mask_only->external`` 原因(含误差
    值/帧号)。开关打开时改生成逐帧四角 ``\\p1`` 多边形遮罩
    (:func:`_polygon_mask_events_for_block`),精确覆盖透视形变,不再做
    误差降级(退化仍降级)。
    """
    mode_label = "mask" if include_text else "mask_only"
    plane_h, plane_w = plane_img_bgr.shape[:2]
    out: List[Dict] = []
    for bi, (s, e) in enumerate(blocks):
        # 行尾标点字形补偿:文本事件 x 右移 ≤7px(与 core.motion_ass 同一
        # 偏移),遮罩按行同步平移后求 union,否则补偿后的字幕字形悬出补丁
        # 边缘(块级最大补偿整体平移会让无标点行偏移)。
        row_dx = [
            punct_comp_offset_px(
                text, max(1.0, float(rbox[3] - rbox[1])),
                enabled=motion_cfg.punct_comp_enabled,
                max_px=motion_cfg.punct_comp_max_px,
                video_height=video_h)
            for text, rbox in rows[s:e + 1]]
        mbox, _line_h = _padded_mask_box(
            rows[s:e + 1], plane_w, plane_h, cfg, analysis_box,
            row_dx=row_dx,
            row_anchor=(row_anchor[s:e + 1] if row_anchor is not None
                        else None))
        mbox_w = float(mbox[2]) - float(mbox[0])
        mbox_h = float(mbox[3]) - float(mbox[1])
        if mbox_w <= 0.0 or mbox_h <= 0.0:
            # 退化裁剪框(裁剪后宽或高为 0):矩形遮罩无定义,沿回退链
            # 降级 external 并记录原因(旧行为经空采样窗判 uniform=False
            # 降级,结果一致,原因更明确)
            notes.append(
                f"{mode_label}->external: block {bi} degenerate mask box after "
                f"clipping (w={mbox_w:g}, h={mbox_h:g})")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes,
                                  orig_indices=orig_indices)
            return ext, "external", notes
        stats = sample_background_stats(
            plane_img_bgr, mbox,
            background_mode=cfg.background_mode,
            bg_max_std=float(cfg.bg_max_std),
            bg_min_samples=int(cfg.bg_min_samples),
            bg_min_confidence=float(cfg.bg_min_confidence))
        if diag_extra is not None:
            diag_extra.setdefault("background_blocks", []).append({
                "block": int(bi),
                "box": [round(float(v), 2) for v in mbox],
                "mode": stats.mode,
                "std": round(stats.std_max_channel, 2),
                "robust_spread_mad": round(stats.robust_spread_mad, 2),
                "robust_spread_iqr": round(stats.robust_spread_iqr, 2),
                "n_samples": int(stats.n_samples),
                "confidence": round(stats.confidence, 4),
                "uniform": bool(stats.uniform),
            })
        if not stats.uniform:
            if stats.mode == "robust":
                if stats.n_samples <= 0:
                    reason = "no background samples"
                elif stats.robust_spread > float(cfg.bg_max_std):
                    reason = (f"background robust spread "
                              f"{stats.robust_spread:.1f} > bg_max_std "
                              f"{float(cfg.bg_max_std):g}")
                else:
                    reason = (f"background confidence "
                              f"{stats.confidence:.2f} < bg_min_confidence "
                              f"{float(cfg.bg_min_confidence):g}")
            else:
                reason = (f"background channel std {stats.std_max_channel:.1f} "
                          f"> bg_max_std {float(cfg.bg_max_std):g}")
            notes.append(f"{mode_label}->external: block {bi} {reason}")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes,
                                  orig_indices=orig_indices)
            return ext, "external", notes
        # —— Task 5:透视安全边界 ——
        # 逐帧四角与矩形近似误差(遮罩框 → 各帧视频坐标);诊断先留痕,
        # 退化或(矩形模式下)误差超限 → 沿既定回退链降级 external。
        ref = tracks[0].frame_num if ref_frame is None else int(ref_frame)
        frames, persp_err, degenerate = _mask_quad_frames(mbox, tracks, ref)
        if diag_extra is not None:
            diag_extra.setdefault("mask_perspective", []).append({
                "block": int(bi),
                "frames": len(frames),
                "max_error": round(persp_err, 4),
                "polygon_clip": bool(cfg.mask_polygon_clip),
            })
        if degenerate is not None:
            notes.append(f"{mode_label}->external: block {bi} {degenerate}")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes,
                                  orig_indices=orig_indices)
            return ext, "external", notes
        if (not cfg.mask_polygon_clip
                and persp_err > float(cfg.mask_max_perspective_error)):
            notes.append(
                f"{mode_label}->external: block {bi} perspective rectangle "
                f"approximation error {persp_err:.3f} > "
                f"mask_max_perspective_error "
                f"{float(cfg.mask_max_perspective_error):g} (max corner "
                f"offset / quad diagonal over {len(frames)} frames)")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes,
                                  orig_indices=orig_indices)
            return ext, "external", notes
        if cfg.mask_polygon_clip:
            # 实验开关:逐帧四角 \p1 多边形精确覆盖透视形变(不做误差降级)
            out.extend(_polygon_mask_events_for_block(
                frames, stats.color_bgr, style=style))
        else:
            out.extend(_mask_events_for_block(mbox, stats.color_bgr, tracks,
                                              motion_cfg,
                                              ref_frame=ref_frame, style=style))
    # mask:所有识别行都归属某块 → 文本事件整体升到 layer 1(遮罩之下);
    # mask_only:同样升层并标记 comment,由 writer 写成 Comment 行(不渲染)。
    if include_text:
        out.extend(dict(ev, layer=1) for ev in events)
    else:
        out.extend(dict(ev, layer=1, comment=True) for ev in events)
    return out, mode_label, notes


def _merge_span_instances(
    spans: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """同一行的多个事件跨度 → 连续可见实例(重叠/相接合并,间隔保留)。"""
    instances: List[List[float]] = []
    for start, end in sorted(spans):
        if instances and start <= instances[-1][1]:
            instances[-1][1] = max(instances[-1][1], end)
        else:
            instances.append([start, end])
    return [(s, e) for s, e in instances if e > s]


def _apply_external(
    events: List[Dict],
    rows: List[Row],
    blocks: List[Tuple[int, int]],
    tracks: Sequence,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    motion_cfg: MotionAssConfig,
    style: str,
    notes: List[str],
    *,
    orig_indices: Optional[Sequence[int]] = None,
) -> List[Dict]:
    """external 模式：按活动行集合切片布局 NoteBox(A2 时间契约)。

    每个半开时间切片(:func:`core.scene_timeline.active_slices`)只把该时刻
    **有效**的行折行进一条 NoteBox;没有行活跃的区间不输出事件,相邻切片
    仅在活跃行身份与布局完全一致时呈现连续时间。行→事件归属优先用
    ``line_idx``(:func:`synthesize_events` 写入的 ``blocks_meta`` 输入序
    下标;``orig_indices`` 把 ``rows`` 行序映射回同一输入序);同一行的多
    个事件跨度合并成连续实例,不重叠的间隔即该行的离场时段。事件不带
    ``line_idx`` 时按正文**唯一**匹配兼容回退;同文多行无法确定归属——
    记录诊断并原样保留该事件(不虚构其时间,也不退回「全轨迹跨度拼所有
    行」的旧行为,V2)。

    ``blocks`` 仅为保持既有调用形状保留(切片身份已取代块分组);空行集合
    输出空列表。
    """
    del blocks  # 切片身份取代块分组;参数保留以兼容调用方
    row_spans: List[List[Tuple[float, float]]] = [[] for _ in rows]
    kept_events: List[Dict] = []
    ambiguous = 0
    have_ids = (orig_indices is not None
                and any(isinstance(ev.get("line_idx"), int) for ev in events))
    if have_ids:
        pos_by_input: Dict[int, int] = {}
        for pos in range(len(rows)):
            src = orig_indices[pos]
            if isinstance(src, int):
                pos_by_input.setdefault(int(src), pos)
        for ev in events:
            idx = ev.get("line_idx")
            pos = pos_by_input.get(int(idx)) if isinstance(idx, int) else None
            if pos is None:
                # 不属于本策略行集合的事件:原样保留,不静默丢内容。
                kept_events.append(ev)
                continue
            row_spans[pos].append(
                (_parse_ass_time(ev["start_time"]),
                 _parse_ass_time(ev["end_time"])))
    else:
        text_positions: Dict[str, List[int]] = {}
        for pos, (text, _box) in enumerate(rows):
            text_positions.setdefault(str(text), []).append(pos)
        for ev in events:
            poss = text_positions.get(str(ev.get("body", "")), [])
            if len(poss) == 1:
                row_spans[poss[0]].append(
                    (_parse_ass_time(ev["start_time"]),
                     _parse_ass_time(ev["end_time"])))
            else:
                if len(poss) > 1:
                    # 同文多行:归属歧义,保留原事件(时间不可虚构)。
                    ambiguous += 1
                kept_events.append(ev)
    if ambiguous:
        notes.append(
            f"external: {ambiguous} event(s) with ambiguous duplicate-text "
            f"rows kept as-is (no line_idx; timing not fabricable)")

    margin = float(cfg.external_margin)
    band_w = max(1.0, float(video_w) - 2.0 * margin)
    band_h = max(1.0, float(video_h) - margin)
    max_chars = max(1, int(band_w) // _WRAP_BASE_FS)
    an, pos_y = ((8, margin) if str(cfg.external_pos).lower() == "top"
                 else (2, float(video_h) - margin))

    timed: List[TimedRow] = []
    for pos, spans in enumerate(row_spans):
        for start, end in _merge_span_instances(spans):
            timed.append(TimedRow(pos,
                                  int(round(start * 100.0)),
                                  int(round(end * 100.0))))
    out: List[Dict] = []
    for sl in active_slices(timed):
        parts: List[str] = []
        for pos in sl.row_ids:  # row_id 升序 = 画面自上而下
            parts.extend(wrap_cjk(rows[pos][0], max_chars))
        parts = [p for p in parts if p]
        if not parts:
            continue
        longest = max(len(p) for p in parts)
        fs = fit_font_size(len(parts), band_h, longest, band_w)
        out.append({
            "start_time": format_ass_time(sl.start_cs / 100.0),
            "end_time": format_ass_time(sl.end_cs / 100.0),
            "style": _NOTE_STYLE,
            "name": "motion",
            "tags": f"{{\\an{an}\\pos({_fmt1(float(video_w) / 2.0)},"
                    f"{_fmt1(pos_y)})\\fs{fs}}}",
            "body": "\\N".join(parts),
        })
    out.extend(dict(ev) for ev in kept_events)
    return out


def _analysis_gray(
    plane_img_bgr: np.ndarray,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[np.ndarray, int, int]:
    """文字平面内的灰度图与窗口原点 (ox, oy)。

    ``analysis_box``(quad 窗口)之外的展开区域是无效画面,不得参与墨迹
    二值化与空白带候选;返回的灰度图已裁到窗口内,坐标需加回 (ox, oy)。
    """
    gray_full = cv2.cvtColor(plane_img_bgr, cv2.COLOR_BGR2GRAY)
    if analysis_box is None:
        return gray_full, 0, 0
    clipped = _clip_box(analysis_box, gray_full.shape[1], gray_full.shape[0])
    if clipped is None:
        return gray_full, 0, 0
    return (gray_full[clipped[1]:clipped[3], clipped[0]:clipped[2]],
            int(clipped[0]), int(clipped[1]))


def _apply_whitespace(
    events: List[Dict],
    rows: List[Row],
    blocks: List[Tuple[int, int]],
    plane_img_bgr: np.ndarray,
    tracks: Sequence,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    motion_cfg: MotionAssConfig,
    style: str,
    notes: List[str],
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    ref_frame: Optional[int] = None,
    orig_indices: Optional[Sequence[int]] = None,
    diag_extra: Optional[Dict[str, object]] = None,
) -> Tuple[List[Dict], str, List[str]]:
    """whitespace 模式:全部块文本合并放进原文字空白带(合成行框复用轨迹)。

    ``ref_frame`` 为行框所在平面坐标系的参考帧;缺省回退旧行为
    (隐式 ``tracks[0].frame_num``,由入口在 diagnostics 注明)。
    ``orig_indices`` 供回退链(mask → external)建立事件归属。
    候选带 confidence(面积/宽高/均匀性/可排版长度)低于
    ``ws_min_confidence`` 时按既定回退链降级 mask,候选带与置信度先写入
    ``diag_extra["whitespace_band"]`` 留痕。
    """
    gray, ox, oy = _analysis_gray(plane_img_bgr, analysis_box)
    line_h = sum(float(b[3]) - float(b[1]) for _t, b in rows) / max(1, len(rows))
    local_boxes = [(float(b[0]) - ox, float(b[1]) - oy,
                    float(b[2]) - ox, float(b[3]) - oy) for _t, b in rows]
    band, _scored_conf, binfo = find_whitespace_band_scored(
        gray, local_boxes, line_h=line_h,
        min_lines=int(cfg.ws_min_lines),
        min_width_ratio=float(cfg.ws_min_width_ratio),
        threshold_mode=cfg.ws_threshold_mode,
        ws_percentile=float(cfg.ws_percentile))
    if band is None:
        notes.append(
            f"whitespace->mask: no whitespace band "
            f"(need >= {int(cfg.ws_min_lines)} x line_h {line_h:.1f}px, "
            f"width >= {float(cfg.ws_min_width_ratio):g} x plane width)")
        return _apply_mask(events, rows, blocks, plane_img_bgr, tracks, cfg,
                           video_w, video_h, motion_cfg, style, notes,
                           analysis_box=analysis_box, ref_frame=ref_frame,
                           orig_indices=orig_indices, diag_extra=diag_extra)
    band = (band[0] + ox, band[1] + oy, band[2] + ox, band[3] + oy)

    band_w = float(band[2] - band[0])
    band_h = float(band[3] - band[1])
    max_chars = max(1, int(band_w) // _WRAP_BASE_FS)
    parts: List[str] = []
    for text, _box in rows:  # 行序自上而下
        parts.extend(wrap_cjk(text, max_chars))
    longest = max(len(p) for p in parts)
    fs = fit_font_size(len(parts), band_h, longest, band_w)
    # 可排版长度评分:折行后的总行高/最长行宽与带尺寸之比(不足则降分)
    need_h = max(1.0, float(fs) * len(parts))
    need_w = max(1.0, float(fs) * max(1, longest))
    c_layout = _clamp01(min(band_h / need_h, band_w / need_w))
    confidence = _band_confidence(binfo, c_layout)
    if diag_extra is not None:
        diag_extra["whitespace_band"] = {
            "box": [int(v) for v in band],
            "confidence": round(confidence, 4),
            "threshold_mode": str(binfo["threshold_mode"]),
            "n_candidates": int(binfo["n_candidates"]),
            "c_area": round(float(binfo["c_area"]), 4),
            "c_size": round(float(binfo["c_size"]), 4),
            "c_uniformity": round(float(binfo["c_uniformity"]), 4),
            "c_layout": round(c_layout, 4),
            "uniformity_sigma": round(float(binfo["robust_sigma"]), 2),
        }
    if confidence < float(cfg.ws_min_confidence):
        notes.append(
            f"whitespace->mask: whitespace band confidence "
            f"{confidence:.2f} < ws_min_confidence "
            f"{float(cfg.ws_min_confidence):g}")
        return _apply_mask(events, rows, blocks, plane_img_bgr, tracks, cfg,
                           video_w, video_h, motion_cfg, style, notes,
                           analysis_box=analysis_box, ref_frame=ref_frame,
                           orig_indices=orig_indices, diag_extra=diag_extra)

    # 合成行框(带内垂直居中、左对齐,行高 = 字号)直接喂 build_line_tracks
    y0 = float(band[1]) + (band_h - fs * len(parts)) / 2.0
    boxes = [(float(band[0]), y0 + i * fs,
              float(band[0]) + max(1, len(p)) * fs,
              y0 + (i + 1) * fs) for i, p in enumerate(parts)]
    ref = tracks[0].frame_num if ref_frame is None else int(ref_frame)
    new_tracks = build_line_tracks(boxes, parts, tracks, ref_frame=ref)
    for lt in new_tracks:
        smooth_line_track(lt, window=motion_cfg.smooth_window,
                          max_gap=motion_cfg.smooth_max_gap)
    # 逐行对齐判定结果并入 diagnostics(合成行框按约定左对齐于带左缘,
    # 判定应全为 left;偏出即说明锚点选型与布局约定脱钩,留痕便于排查)。
    # video_height 透传:静止塌缩阈值按 video_height/1080 等比缩放,
    # 缺省会退回 1080p 基准(高分辨率下塌缩偏激进)。
    align_diag: Dict[str, object] = {}
    events = synthesize_events(new_tracks, tracks, motion_cfg, style=style,
                               video_height=video_h,
                               diagnostics=align_diag)
    if diag_extra is not None and align_diag:
        diag_extra["line_alignments"] = align_diag
    return (events, "whitespace", notes)


def apply_policy(
    events: List[Dict],
    blocks_meta: Sequence[Row],
    plane_img_bgr: np.ndarray,
    tracks: Sequence,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    *,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    motion_cfg: Optional[MotionAssConfig] = None,
    style: str = "Scene",
    ref_frame: Optional[int] = None,
) -> PolicyResult:
    """对合成后的 motion 事件应用场景文字显示策略(设计 §3/§4)。

    ``blocks_meta`` 为识别行 ``[(文本, (x1, y1, x2, y2)), ...]``(统一坐标 =
    初始帧平面坐标,与 ``build_line_tracks`` 输入一致);``plane_img_bgr``
    为锚定关键帧的同坐标系展开图;``tracks`` 为逐帧平面跟踪结果;
    ``analysis_box`` 可选,限定文字平面(quad 窗口)在展开图内的范围,
    窗口外的展开区域不参与取色与空白带检测。``events`` 若由
    :func:`synthesize_events` 合成(携带 ``line_idx`` = ``blocks_meta`` 输入
    序下标),external 模式的块时间跨度优先按 ``line_idx`` 归属——同文案多行
    (body 相同)不会互相串跨度;事件不带 ``line_idx`` 时按正文匹配兼容回退。

    ``ref_frame`` 为 ``blocks_meta`` 行框平面坐标系的参考帧(动态入口应传
    OCR 锚定帧):mask/whitespace 的轨迹重建以它为参考;缺省保持向后兼容
    (隐式 ``tracks[0].frame_num``)并在 diagnostics 注明 ``ref_frame_source
    = "inferred"``。输入行框先经校验:非有限坐标、宽高非正或完全越界的行
    剔除,部分越界行裁剪到平面边界;全部行无效或无识别行时原样返回事件并
    留痕(不抛异常)。

    返回 :class:`PolicyResult`:兼容 ``(events, applied_policy, notes)``
    三元组解包;notes 记录回退原因(whitespace→mask→external、
    mask_only→external,仅所选模式不可用时降级;overlap 不回退),由 CLI
    打 warn 日志。diagnostics 携带
    requested/applied mode、参考帧、行框有效性统计,以及逐块背景统计
    (``background_blocks``:std、robust spread、样本数、confidence)、
    候选空白带(``whitespace_band``:box、confidence 及评分分量)与逐块
    透视误差(``mask_perspective``:帧数、最大角点偏移比、多边形开关)。
    ``tracks`` 为空时
    mask/mask_only/whitespace 沿回退链降级 external(记录原因),overlap
    原样返回事件(不修改输入)。mask/mask_only 模式另有透视安全边界:退化
    裁剪框/退化四角/矩形近似误差超 ``mask_max_perspective_error`` 时降级
    external 并留痕(``mask_polygon_clip`` 开启时改生成逐帧 ``\\p1``
    多边形遮罩)。
    """
    mode = cfg.mode
    plane_h, plane_w = plane_img_bgr.shape[:2]
    rows_kept, valid_indices, row_stats = _validate_rows(
        blocks_meta, plane_w, plane_h)
    diag_extra: Dict[str, object] = {}
    if ref_frame is not None:
        ref, ref_source = int(ref_frame), _REF_EXPLICIT
    elif len(tracks):
        ref, ref_source = int(tracks[0].frame_num), _REF_INFERRED
    else:
        ref, ref_source = None, _REF_NONE
    diag = _result_diagnostics(mode, mode, ref_frame=ref,
                               ref_source=ref_source, row_stats=row_stats)
    if mode == "overlap":
        return PolicyResult(list(events), "overlap", [], mode,
                            _merge_bg_diag(diag, diag_extra))
    mcfg = motion_cfg if motion_cfg is not None else MotionAssConfig()
    rows = _sorted_rows(rows_kept)
    if not rows:
        # 无识别行,或识别行全部未通过行框校验:mask/external/whitespace 都
        # 没有可布局的对象,空 rows 还会让后续行高/取最值计算崩溃;原样返回
        # 事件并留痕。
        if row_stats["total_rows"]:
            note = (f"{mode}: all {row_stats['total_rows']} text rows invalid "
                    f"after box validation; policy not applied")
        else:
            note = f"{mode}: no recognized text rows; policy not applied"
        diag["applied_mode"] = "overlap"
        return PolicyResult(list(events), "overlap", [note], mode,
                            _merge_bg_diag(diag, diag_extra))
    blocks = merge_line_blocks([b for _t, b in rows],
                               vgap_ratio=float(cfg.block_vgap_ratio))
    # 行 ID:排序后行位置 → blocks_meta 输入序下标(行框校验保持输入序,
    # 再按与 _sorted_rows 相同的键排序)。合成事件 line_idx 即输入序下标,
    # external 的事件归属优先按它匹配,正文匹配仅作兼容回退。
    order = sorted(
        range(len(rows_kept)),
        key=lambda j: (float(rows_kept[j][1][1]), float(rows_kept[j][1][0])))
    orig_indices = [valid_indices[j] for j in order]
    if mode in ("mask", "mask_only", "whitespace") and not tracks:
        # 轨迹重建需要至少一帧跟踪结果;空轨迹沿回退链降级 external
        #(external 只按事件时间排版,不依赖轨迹几何)。
        notes = [f"{mode}->external: no tracking frames (empty tracks)"]
        out = _apply_external(list(events), rows, blocks, tracks, cfg,
                              video_w, video_h, mcfg, style, notes,
                              orig_indices=orig_indices)
        diag["applied_mode"] = "external"
        return PolicyResult(out, "external", notes, mode,
                            _merge_bg_diag(diag, diag_extra))
    if mode in ("mask", "mask_only"):
        # 逐行渲染锚点:轨迹管线的文本事件已按同一份对齐判定锚定
        # (\an4/\an6/\an5),\an4/\an6 时遮罩须按渲染文本真实跨度外扩,
        # 否则行尾方向伸出的字幕会露出原文字(见 _padded_mask_box)。
        anchors = _row_anchors_from_events(events, rows, orig_indices)
        out, applied, notes = _apply_mask(
            list(events), rows, blocks, plane_img_bgr, tracks, cfg,
            video_w, video_h, mcfg, style, [], analysis_box=analysis_box,
            ref_frame=ref, orig_indices=orig_indices, diag_extra=diag_extra,
            include_text=(mode == "mask"), row_anchor=anchors)
    elif mode == "external":
        ext_notes: List[str] = []
        out = _apply_external(list(events), rows, blocks, tracks, cfg,
                              video_w, video_h, mcfg, style, ext_notes,
                              orig_indices=orig_indices)
        applied, notes = "external", ext_notes
    elif mode == "whitespace":
        out, applied, notes = _apply_whitespace(
            list(events), rows, blocks, plane_img_bgr, tracks, cfg,
            video_w, video_h, mcfg, style, [], analysis_box=analysis_box,
            ref_frame=ref, orig_indices=orig_indices, diag_extra=diag_extra)
    else:
        raise ValueError(
            f"unknown scene text policy mode: {mode!r}; "
            f"valid modes: {', '.join(POLICY_MODES)}")
    diag["applied_mode"] = applied
    return PolicyResult(out, applied, notes, mode,
                        _merge_bg_diag(diag, diag_extra))


# ---------------------------------------------------------------------------
# 静态 \pos 路径(主流水线):同一回退链,输出不带时间的 spec dict
# ---------------------------------------------------------------------------

def _static_mask_spec(
    box: Tuple[float, float, float, float],
    color_bgr: Tuple[int, int, int],
    row_indices: Sequence[int],
    style: str = "Scene",
) -> Dict:
    """单块静态遮罩 spec:``\\an7\\pos`` + ``\\p1`` 矩形(无 \\move/\\t)。

    ``tags`` 为平面坐标,由调用方(生成器)按外接框原点平移到视频坐标;
    ``rows`` 记录块覆盖的行索引(输入序),供时间回填;``style`` 为遮罩
    事件样式名(调用者自定义,缺省 ``Scene``)。
    """
    x1, y1, x2, y2 = box
    w = max(1.0, float(x2) - float(x1))
    h = max(1.0, float(y2) - float(y1))
    iw, ih = int(round(w)), int(round(h))
    b, g, r = (int(round(float(c))) for c in color_bgr)
    # 绘图命令与 {\p0} 必须原样进入 Text 字段;\bord0 压掉样式描边
    tags = (f"{{\\an7\\pos({_fmt1(float(x1))},{_fmt1(float(y1))})"
            f"\\p1\\bord0\\1c&H{b:02X}{g:02X}{r:02X}&}}"
            f"m 0 0 l {iw} 0 {iw} {ih} 0 {ih}{{\\p0}}")
    return {
        "kind": "mask",
        "style": str(style),
        "tags": tags,
        "layer": 0,
        "rows": [int(i) for i in row_indices],
        "base_color": (b, g, r),
        # B3:平面坐标采样矩形(生成器加回外接框原点得到视频坐标矩形),
        # 供自动亮度按帧采样遮罩覆盖的背景亮度。
        "box": [float(x1), float(y1), float(x2), float(y2)],
    }


def _wrap_rows_dedup(
    rows: Sequence[Row],
    max_chars: int,
) -> List[str]:
    """行文本折行,并按整行文本去重(保持首次出现顺序)。

    静态路径的 rows 跨整个 ROI 时间段,常驻文字(状态栏/标题栏等)会随
    每个 OCR 组重复进入;单条展示框/空白带放置按文本去重,否则同一行
    重复几十次撑爆版面(11.mp4 聊天状态栏「べにっぽ」25 秒重复 55 次)。
    同屏两处出现相同文字属罕见情形,去重对人工整理也无信息损失。
    """
    parts: List[str] = []
    seen: set = set()
    for text, _box in rows:  # 行序自上而下
        if text in seen:
            continue
        seen.add(text)
        parts.extend(wrap_cjk(text, max_chars))
    return parts


def _static_external_spec(
    rows: Sequence[Row],
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    base_fs: int,
) -> Optional[Dict]:
    """external 静态 spec:全部行折行合并为单条 NoteBox 事件(底/顶带)。

    ``tags`` 直接用视频坐标(与行框坐标系无关)。
    """
    margin = float(cfg.external_margin)
    band_w = max(1.0, float(video_w) - 2.0 * margin)
    band_h = max(1.0, float(video_h) - margin)
    max_chars = max(1, int(band_w) // max(1, int(base_fs)))
    parts = _wrap_rows_dedup(rows, max_chars)
    if not parts:
        return None
    longest = max(len(p) for p in parts)
    fs = fit_font_size(len(parts), band_h, longest, band_w, base=base_fs)
    an, pos_y = ((8, margin) if str(cfg.external_pos).lower() == "top"
                 else (2, float(video_h) - margin))
    return {
        "kind": "note",
        "style": _NOTE_STYLE,
        "tags": (f"{{\\an{an}\\pos({_fmt1(float(video_w) / 2.0)},"
                 f"{_fmt1(pos_y)})\\fs{fs}}}"),
        "body": "\\N".join(parts),
    }


def _apply_static_mask(
    rows: List[Row],
    blocks: List[Tuple[int, int]],
    plane_img_bgr: np.ndarray,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    notes: List[str],
    orig_indices: Sequence[int],
    *,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    base_fs: int = _WRAP_BASE_FS,
    style: str = "Scene",
    diag_extra: Optional[Dict[str, object]] = None,
    include_text: bool = True,
) -> Tuple[List[Dict], str, List[str]]:
    """mask / mask_only 静态路径:每块一条静态矩形 + 全部原行(layer 1)。

    ``include_text=False``(mask_only)时 text spec 附 ``comment=True``,
    由生成器写成 ``Comment:`` 行(不渲染,layer 1 留给用户自行排版)。
    取色/背景杂色检查/外扩框与 motion 版共用(按行外扩后求 union);默认
    ``background_mode="std"`` 任一块背景 std 超限 → 回退 external(旧规则
    不变),robust 模式按 MAD/IQR + 样本数 + 置信度判定;逐块统计与逐行
    对齐判定(``line_alignments``:行 idx → 对齐边 + 三边票数 + 剪切斜率)
    经 ``diag_extra`` 累加供入口并入 diagnostics。回退 notes 前缀与 applied
    模式按实际模式(mask/mask_only)。

    逐行对齐判定在**块循环之前**做一次,同一份结果同时供遮罩外扩(按行
    锚点 ``\\an4``/``\\an6`` 的真实渲染跨度,见 :func:`_padded_mask_box`)与
    文本 spec 锚点使用——遮罩与文本锚点必须同源,分别投票会在低置信行上
    给出不一致的锚(字幕与补丁错边)。
    """
    mode_label = "mask" if include_text else "mask_only"
    plane_h, plane_w = plane_img_bgr.shape[:2]
    out: List[Dict] = []
    # 逐行投票检测原文对齐方式(左/中/右,同 core.text_alignment,整组
    # ROI 投票,聊天混排/邮件松行距都能归类):识别行以行高为字号回贴,
    # 渲染宽度与原框必有出入——居中锚点会让左/右对齐的行失去公共边距。
    # 按行选锚:左→\an4 锚行框左缘,右→\an6 锚右缘,中→\an5 锚中心(旧行为)。
    # 遮罩框必须跟着锚点走:对称外扩只覆盖到 cx ± max(box_w,need_w)/2,
    # 而左锚文本占 [x1, x1+need_w]——CJK 行右侧漏 ≈pad、纯 ASCII 行(渲染
    # 宽可达框宽两倍)漏 (need_w−box_w)/2−pad,原文字从字幕旁露出(2.7.0
    # 锚点改动打破了旧注释「对称外扩同时盖住左/右锚渲染文本」的对应关系)。
    # 去趋势与轨迹管线同一开关:行框虽是视频坐标的轴对齐裁剪(非 quad
    # 展开),但斜放平面上的竖直 UI 列在视频坐标里本就随 y 线性倾斜;
    # 正面版式估计斜率为 0,开关等价于无操作。逐行判定结果(行 idx →
    # 对齐边 + 三边票数)并入 diagnostics["line_alignments"] 供真实视频排查。
    align_diag: Dict[str, object] = {}
    row_align = detect_line_alignments([b for _t, b in rows],
                                       detrend_shear=True,
                                       diagnostics=align_diag)
    if diag_extra is not None:
        diag_extra["line_alignments"] = align_diag
    row_anchor = _row_anchors_from_aligns(rows, row_align)
    for bi, (s, e) in enumerate(blocks):
        mbox, _line_h = _padded_mask_box(rows[s:e + 1], plane_w, plane_h,
                                         cfg, analysis_box,
                                         row_anchor=row_anchor[s:e + 1])
        stats = sample_background_stats(
            plane_img_bgr, mbox,
            background_mode=cfg.background_mode,
            bg_max_std=float(cfg.bg_max_std),
            bg_min_samples=int(cfg.bg_min_samples),
            bg_min_confidence=float(cfg.bg_min_confidence))
        if diag_extra is not None:
            diag_extra.setdefault("background_blocks", []).append({
                "block": int(bi),
                "box": [round(float(v), 2) for v in mbox],
                "mode": stats.mode,
                "std": round(stats.std_max_channel, 2),
                "robust_spread_mad": round(stats.robust_spread_mad, 2),
                "robust_spread_iqr": round(stats.robust_spread_iqr, 2),
                "n_samples": int(stats.n_samples),
                "confidence": round(stats.confidence, 4),
                "uniform": bool(stats.uniform),
            })
        if not stats.uniform:
            if stats.mode == "robust":
                if stats.n_samples <= 0:
                    reason = "no background samples"
                elif stats.robust_spread > float(cfg.bg_max_std):
                    reason = (f"background robust spread "
                              f"{stats.robust_spread:.1f} > bg_max_std "
                              f"{float(cfg.bg_max_std):g}")
                else:
                    reason = (f"background confidence "
                              f"{stats.confidence:.2f} < bg_min_confidence "
                              f"{float(cfg.bg_min_confidence):g}")
            else:
                reason = (f"background channel std {stats.std_max_channel:.1f} "
                          f"> bg_max_std {float(cfg.bg_max_std):g}")
            notes.append(f"{mode_label}->external: block {bi} {reason}")
            spec = _static_external_spec(rows, cfg, video_w, video_h, base_fs)
            return ([spec] if spec else []), "external", notes
        out.append(_static_mask_spec(mbox, stats.color_bgr,
                                     orig_indices[s:e + 1], style=style))
    # 文本 spec:锚点用块循环前那份**同一**对齐判定(row_align),与遮罩
    # 外扩同源;左→\an4 锚行框左缘,右→\an6 锚右缘,中→\an5 锚中心。
    for i, (text, box) in enumerate(rows):
        cx = int((float(box[0]) + float(box[2])) / 2.0)
        cy = int((float(box[1]) + float(box[3])) / 2.0)
        if row_align[i] == ALIGN_LEFT:
            anchor_x, an = int(float(box[0])), 4
        elif row_align[i] == ALIGN_RIGHT:
            anchor_x, an = int(float(box[2])), 6
        else:
            anchor_x, an = cx, 5
        line_h = max(1.0, float(box[3]) - float(box[1]))
        # 显式 \fs=行高:与遮罩/渲染宽度估算同一尺寸体系(Scene 样式字号
        # 是画面高度常数,与原文字大小无关,会让字幕远大于原字)。
        # \fs 数值标签不带圆括号(带括号会被 libass 静默忽略,字号失效)。
        spec = {
            "kind": "text",
            "style": str(style),
            "tags": f"{{\\an{an}\\pos({anchor_x},{cy})\\fs{round(line_h)}}}",
            "body": text,
            "layer": 1,
            "row": int(orig_indices[i]),
        }
        if not include_text:  # mask_only:排版参考行 → Comment(不渲染)
            spec["comment"] = True
        out.append(spec)
    return out, mode_label, notes


def _apply_static_whitespace(
    rows: List[Row],
    plane_img_bgr: np.ndarray,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    notes: List[str],
    *,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    base_fs: int = _WRAP_BASE_FS,
    style: str = "Scene",
    diag_extra: Optional[Dict[str, object]] = None,
) -> Optional[List[Dict]]:
    """whitespace 静态路径:全部行文本合并放进空白带(单条 \\an4\\pos spec)。

    带检测与 motion 版共用 :func:`find_whitespace_band_scored`(含
    threshold_mode / 候选带 confidence 评分);无带或带 confidence 低于
    ``ws_min_confidence`` 时记录原因(候选带先写入 ``diag_extra`` 留痕)
    并返回 None(由 :func:`apply_policy_static` 沿回退链降级到 mask)。
    """
    gray, ox, oy = _analysis_gray(plane_img_bgr, analysis_box)
    line_h = sum(float(b[3]) - float(b[1]) for _t, b in rows) / max(1, len(rows))
    local_boxes = [(float(b[0]) - ox, float(b[1]) - oy,
                    float(b[2]) - ox, float(b[3]) - oy) for _t, b in rows]
    band, _scored_conf, binfo = find_whitespace_band_scored(
        gray, local_boxes, line_h=line_h,
        min_lines=int(cfg.ws_min_lines),
        min_width_ratio=float(cfg.ws_min_width_ratio),
        threshold_mode=cfg.ws_threshold_mode,
        ws_percentile=float(cfg.ws_percentile))
    if band is None:
        notes.append(
            f"whitespace->mask: no whitespace band "
            f"(need >= {int(cfg.ws_min_lines)} x line_h {line_h:.1f}px, "
            f"width >= {float(cfg.ws_min_width_ratio):g} x plane width)")
        return None
    band = (band[0] + ox, band[1] + oy, band[2] + ox, band[3] + oy)
    band_w = float(band[2] - band[0])
    band_h = float(band[3] - band[1])
    max_chars = max(1, int(band_w) // max(1, int(base_fs)))
    parts = _wrap_rows_dedup(rows, max_chars)
    fs = fit_font_size(len(parts), band_h, max(len(p) for p in parts), band_w,
                       base=base_fs)
    # 可排版长度评分(motion 版同一公式)
    need_h = max(1.0, float(fs) * len(parts))
    need_w = max(1.0, float(fs) * max(1, max(len(p) for p in parts)))
    c_layout = _clamp01(min(band_h / need_h, band_w / need_w))
    confidence = _band_confidence(binfo, c_layout)
    if diag_extra is not None:
        diag_extra["whitespace_band"] = {
            "box": [int(v) for v in band],
            "confidence": round(confidence, 4),
            "threshold_mode": str(binfo["threshold_mode"]),
            "n_candidates": int(binfo["n_candidates"]),
            "c_area": round(float(binfo["c_area"]), 4),
            "c_size": round(float(binfo["c_size"]), 4),
            "c_uniformity": round(float(binfo["c_uniformity"]), 4),
            "c_layout": round(c_layout, 4),
            "uniformity_sigma": round(float(binfo["robust_sigma"]), 2),
        }
    if confidence < float(cfg.ws_min_confidence):
        notes.append(
            f"whitespace->mask: whitespace band confidence "
            f"{confidence:.2f} < ws_min_confidence "
            f"{float(cfg.ws_min_confidence):g}")
        return None
    # 源行框逐行对齐判定留痕(与 mask 路径同一投票,detrend_shear 开关
    # 也一致):diag 是**源行**的判定结果;whitespace 的合成单块按约定
    # 固定 \an4 左对齐于带左缘,放置锚点不受该判定影响——勿据 diag 误读
    # 放置行为。
    if diag_extra is not None:
        align_diag: Dict[str, object] = {}
        detect_line_alignments([b for _t, b in rows],
                               detrend_shear=True, diagnostics=align_diag)
        diag_extra["line_alignments"] = align_diag
    # 左对齐放在带内(锚 \an4 于带左缘):与 motion 版 whitespace 的合成行框
    # (带内垂直居中、左对齐)同一布局约定;多行 \N 文本整块左对齐不随行长
    # 摇摆,居中锚点会让不同长度的行左缘参差。
    tags = (f"{{\\an4\\pos({_fmt1(float(band[0]))},"
            f"{_fmt1((float(band[1]) + float(band[3])) / 2.0)})\\fs{fs}}}")
    return [{
        "kind": "scene_ws",
        "style": str(style),
        "tags": tags,
        "body": "\\N".join(parts),
    }]


def apply_policy_static(
    rows_meta: Sequence[Row],
    plane_img_bgr: np.ndarray,
    cfg: SceneTextPolicyConfig,
    video_w: float,
    video_h: float,
    *,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    base_font_size: Optional[int] = None,
    style: str = "Scene",
) -> PolicyResult:
    """对静态 ``\\pos`` 路径的识别行应用场景文字显示策略(静态变体)。

    与 :func:`apply_policy` 同一回退链(whitespace → mask → external、
    mask_only → external)与
    取色/并块/空白带/折行实现,但不读轨迹、不产时间:输入 ``rows_meta`` 为
    ``[(文本, (x1, y1, x2, y2)), ...]]``(行框与 ``plane_img_bgr`` 同一平面
    坐标系);输出为**不带时间**的 spec dict 列表,时间由调用方按所属组回填:

    - ``{"kind": "mask", "tags", "layer": 0, "rows": [行索引], ...}`` ——
      每块一条静态 ``\\an7\\pos\\p1`` 矩形(平面坐标);
    - ``{"kind": "text", "tags", "body", "layer": 1, "row": 行索引}`` ——
      原识别行(平面坐标,锚点按整组逐行投票检测的对齐方式:\an4 左缘 /
      \an5 中心 / \an6 右缘);mask_only 模式附
      ``"comment": True``(写成 Comment 行,不渲染);
    - ``{"kind": "note", "style": "NoteBox", "tags", "body"}`` —— external
      单条展示框(``tags`` 已是视频坐标);
    - ``{"kind": "scene_ws", "tags", "body"}`` —— whitespace 单条空白带放置
      (平面坐标,\an4 左对齐于带左缘)。

    ``style`` 为场景文字事件(mask 矩形 / 原行文本 / whitespace 放置)的
    样式名,缺省 ``Scene``;external 的展示框仍用 ``NoteBox`` 样式。
    ``base_font_size`` 为折行/字号适配的基准字号(缺省 40;主流水线传
    Scene 样式字号)。overlap 原样返回空 spec 列表。输入行框先经校验
    (非有限/非正宽高/完全越界剔除,部分越界裁剪到平面边界);全部行无效时
    返回空 spec 并留痕。返回 :class:`PolicyResult`(兼容
    ``(specs, applied, notes)`` 三元组解包),diagnostics 携带
    requested/applied mode、行框有效性统计(静态路径无轨迹,
    ``ref_frame`` 为 None)、逐块背景统计(``background_blocks``)、候选
    空白带(``whitespace_band``)与逐行对齐判定(``line_alignments``,
    mask/mask_only 与 whitespace 路径)。
    """
    mode = cfg.mode
    plane_h, plane_w = plane_img_bgr.shape[:2]
    rows_kept, kept_ids, row_stats = _validate_rows(
        rows_meta, plane_w, plane_h)
    diag_extra: Dict[str, object] = {}
    diag = _result_diagnostics(mode, mode, ref_frame=None,
                               ref_source=_REF_NONE, row_stats=row_stats)
    if mode == "overlap":
        return PolicyResult([], "overlap", [], mode,
                            _merge_bg_diag(diag, diag_extra))
    base_fs = int(base_font_size) if base_font_size else _WRAP_BASE_FS
    notes: List[str] = []
    rows = _sorted_rows(rows_kept)
    if not rows:
        if row_stats["total_rows"]:
            notes.append(
                f"{mode}: all {row_stats['total_rows']} text rows invalid "
                f"after box validation; policy not applied")
        return PolicyResult([], mode, notes, mode,
                            _merge_bg_diag(diag, diag_extra))
    # 行序 = 阅读序;orig_indices 把排序后位置映射回输入行索引
    # (rows_kept 保持输入序,kept_ids[j] = rows_kept[j] 的原始输入索引)
    order = sorted(
        range(len(rows_kept)),
        key=lambda j: (float(rows_kept[j][1][1]), float(rows_kept[j][1][0])))
    orig_indices = [kept_ids[j] for j in order]
    blocks = merge_line_blocks([b for _t, b in rows],
                               vgap_ratio=float(cfg.block_vgap_ratio))
    while True:
        if mode in ("mask", "mask_only"):
            out, applied, notes = _apply_static_mask(
                rows, blocks, plane_img_bgr, cfg, video_w, video_h, notes,
                orig_indices, analysis_box=analysis_box, base_fs=base_fs,
                style=style, diag_extra=diag_extra,
                include_text=(mode == "mask"))
            diag["applied_mode"] = applied
            return PolicyResult(out, applied, notes, cfg.mode,
                                _merge_bg_diag(diag, diag_extra))
        if mode == "external":
            spec = _static_external_spec(rows, cfg, video_w, video_h, base_fs)
            diag["applied_mode"] = "external"
            return (PolicyResult([spec] if spec else [], "external", notes,
                                 cfg.mode,
                                 _merge_bg_diag(diag, diag_extra)))
        if mode == "whitespace":
            specs = _apply_static_whitespace(
                rows, plane_img_bgr, cfg, video_w, video_h, notes,
                analysis_box=analysis_box, base_fs=base_fs, style=style,
                diag_extra=diag_extra)
            if specs is not None:
                diag["applied_mode"] = "whitespace"
                return PolicyResult(specs, "whitespace", notes, cfg.mode,
                                    _merge_bg_diag(diag, diag_extra))
            mode = "mask"  # 回退链:whitespace → mask
            continue
        raise ValueError(
            f"unknown scene text policy mode: {mode!r}; "
            f"valid modes: {', '.join(POLICY_MODES)}")
