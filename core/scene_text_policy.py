# core/scene_text_policy.py
"""场景文字显示策略(overlap / mask / external / whitespace)+ 自动回退链。

对应《场景文字显示策略(mask / external / whitespace)设计》§3/§4/§6:
识别字幕锚定在原文字位置时,替换字体与原排版的字宽字重不可能一致,原字从
字幕笔画缝隙透出形成「双重曝光式重影」。本模块提供四种显示模式的事件生成:

- :func:`apply_policy` —— 模式入口:overlap 原样返回;mask 生成 ``\\p1``
  矢量遮罩盖住原文字(识别文本升到 layer 1);external 把文本挪出原区域、
  底带 ``NoteBox`` 展示框;whitespace 把文本放进原文字空白带(合成行框喂
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
- :func:`sample_background_color` —— 块区域剔除墨水像素后的中位色 +
  通道标准差(mask 可用性判据);
- :func:`merge_line_blocks` —— 段落块合并(整段一次盖住,行距缝隙不露字);
- :func:`find_whitespace_band` —— 墨迹二值化 + 行占用剖面找空白带;
- :func:`wrap_cjk` / :func:`fit_font_size` —— CJK 折行(行首禁则)与字号适配。

轨迹生成完全复用 :mod:`core.motion_ass`(块/合成行框角点经
``build_line_tracks`` 同款单应映射,遮罩沿用其 DP 分段与 ``\\t`` 叠加标签)。
本模块为纯函数:输入图像用 ndarray,不读视频、不 import PySide6。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core.motion_ass import (
    MotionAssConfig,
    _fmt1,
    _merge_chains_with_hold,
    _overlay_tags,
    _seg_ms,
    _segment_indices,
    _split_runs,
    build_line_tracks,
    format_ass_time,
    punct_comp_offset_px,
    smooth_line_track,
    synthesize_events,
)

__all__ = [
    "SceneTextPolicyConfig",
    "PolicyResult",
    "sample_background_color",
    "merge_line_blocks",
    "find_whitespace_band",
    "wrap_cjk",
    "fit_font_size",
    "apply_policy",
    "apply_policy_static",
]

POLICY_MODES = ("overlap", "mask", "external", "whitespace")

# diagnostics["ref_frame_source"] 取值:显式传入 / 由 tracks[0] 推断 / 不适用
_REF_EXPLICIT, _REF_INFERRED, _REF_NONE = "explicit", "inferred", "none"

_INK_DROP_DELTA = 40      # 墨水剔除/墨迹二值化阈值(低于背景估计 40 灰级)
_WRAP_BASE_FS = 40        # 折行用的基准字号(px)
_EXTERNAL_MIN_FS = 24     # external/whitespace 字号下限(px)
_NOTE_STYLE = "NoteBox"   # external 展示框样式(CLI 写入 ASS 头)


# ---------------------------------------------------------------------------
# 配置(设计 §5;CLI --config-json 以 policy_ 前缀覆盖字段)
# ---------------------------------------------------------------------------

@dataclass
class SceneTextPolicyConfig:
    """场景文字显示策略参数;``mode`` 为所选模式(CLI ``--scene-text-policy``)。"""

    mode: str = "overlap"          # overlap | mask | external | whitespace
    mask_pad_ratio: float = 0.12   # 遮罩外扩(×行高)
    block_vgap_ratio: float = 0.35 # 并块的垂直间距阈值(×两行平均行高)
    bg_max_std: float = 18.0       # 遮罩降级的背景通道标准差上限
    external_pos: str = "bottom"   # external 位置 bottom/top
    external_margin: int = 40      # external 边距(px)
    ws_min_lines: int = 2          # 空白带最小高度(行)
    ws_min_width_ratio: float = 0.6  # 空白带最小宽度(×平面宽)

    def __post_init__(self) -> None:
        if self.mode not in POLICY_MODES:
            raise ValueError(
                f"unknown scene text policy mode: {self.mode!r}; "
                f"valid modes: {', '.join(POLICY_MODES)}")


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


def sample_background_color(
    plane_img_bgr: np.ndarray,
    box: Sequence[float],
    *,
    ink_drop_delta: int = _INK_DROP_DELTA,
) -> Tuple[Tuple[int, int, int], float]:
    """块区域内剔除墨水像素后的背景色 (B, G, R) 与均匀性(最大通道 std)。

    区域内灰度中位值 ``m``,灰度 < ``m - ink_drop_delta`` 判为墨水剔除
    (识别文字笔画);剩余像素按通道取中位 → 遮罩色,同时返回非墨像素的
    通道标准差(最大通道,> ``bg_max_std`` 视为背景杂色、mask 降级)。
    剔除后无像素 → 全区域中位色,std=0;空区域(框在图外)→ ((0,0,0), 0.0)。
    """
    h, w = plane_img_bgr.shape[:2]
    clipped = _clip_box(box, w, h)
    if clipped is None:
        return (0, 0, 0), 0.0
    x1, y1, x2, y2 = clipped
    region = plane_img_bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    keep = gray >= float(np.median(gray)) - float(ink_drop_delta)
    if not bool(keep.any()):
        # 全为墨(数学上罕见,防御):退回全区域中位色
        flat = region.reshape(-1, 3)
        med = np.median(flat, axis=0)
        return (int(round(float(med[0]))), int(round(float(med[1]))),
                int(round(float(med[2])))), 0.0
    pixels = region[keep].astype(np.float64)
    med = np.median(pixels, axis=0)
    std = float(np.max(np.std(pixels, axis=0)))
    return (int(round(float(med[0]))), int(round(float(med[1]))),
            int(round(float(med[2])))), std


def merge_line_blocks(
    boxes: Sequence[Sequence[float]],
    *,
    vgap_ratio: float = 0.35,
) -> List[Tuple[int, int]]:
    """段落块合并:返回块的 (起, 止) 行索引列表(闭区间,指向按 y 升序后的序列)。

    行按 y 升序内部排序后,相邻两行「垂直间距 < ``vgap_ratio`` × 两行平均
    行高 且 水平范围重叠」则并入同块(邮件正文 10 行 → 一个块)。
    """
    order = sorted(range(len(boxes)),
                   key=lambda i: (float(boxes[i][1]), float(boxes[i][0])))
    blocks: List[Tuple[int, int]] = []
    cur: List[int] = []
    for idx in order:
        if not cur:
            cur = [idx]
            continue
        prev = boxes[cur[-1]]
        box = boxes[idx]
        gap = float(box[1]) - float(prev[3])
        avg_h = ((float(prev[3]) - float(prev[1]))
                 + (float(box[3]) - float(box[1]))) / 2.0
        h_overlap = min(float(prev[2]), float(box[2])) - max(float(prev[0]), float(box[0])) > 0
        if gap < float(vgap_ratio) * avg_h and h_overlap:
            cur.append(idx)
        else:
            blocks.append((cur[0], cur[-1]))
            cur = [idx]
    if cur:
        blocks.append((cur[0], cur[-1]))
    return blocks


def find_whitespace_band(
    plane_img_gray: np.ndarray,
    occupied_boxes: Sequence[Sequence[float]],
    *,
    line_h: float,
    min_lines: int = 2,
    min_width_ratio: float = 0.6,
) -> Optional[Tuple[int, int, int, int]]:
    """在平面展开图上找可放文本的空白带,返回平面坐标 (x1, y1, x2, y2);无则 None。

    墨迹 = 灰度 < (全图中位灰 - 40),膨胀(核 ≈ ``line_h``/4)后得行占用
    剖面;``occupied_boxes`` 所在行同样算占用。候选带 = 连续未占用行段
    (首块上方/块间空隙/末块下方),高 ≥ ``min_lines``×``line_h``、宽 ≥
    ``min_width_ratio``×平面宽;取面积最大者。
    """
    gray = np.asarray(plane_img_gray)
    h, w = gray.shape[:2]
    if h < 1 or w < 1:
        return None
    ink = (gray.astype(np.float64) < float(np.median(gray)) - _INK_DROP_DELTA)
    ink = ink.astype(np.uint8)
    k = max(1, int(round(float(line_h) / 4.0)))
    if k > 1:
        ink = cv2.dilate(ink, np.ones((k, k), np.uint8))

    row_occupied = ink.any(axis=1)
    for box in occupied_boxes:
        clipped = _clip_box(box, w, h)
        if clipped is not None:
            row_occupied[clipped[1]:clipped[3]] = True

    best: Optional[Tuple[int, int, int, int]] = None
    best_area = 0
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
                area = (x - x0) * (y - y0)
                if area > best_area:
                    best_area = area
                    best = (x0, y0, x, y)
    return best


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
        for si, (ai, bi) in enumerate(segs):
            f0 = chain[ai]
            # 非末段 end_frame = 下一段 start_frame(共享边界帧)
            f1 = chain[segs[si + 1][0]] if (multi and si < last) else chain[bi]
            if f1 <= f0:
                continue
            t0 = tmap[f0].time_sec
            t1 = tmap[f1].time_sec
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


def _rendered_width(text: str, line_h: float) -> float:
    """估算替换字体渲染该行文本的宽度:全角 1.0×字高、半角/ASCII 0.5×。

    遮罩宽度须覆盖"渲染后的字幕"而不只是原 OCR 框——替换字体字宽通常
    大于原排版,不外扩会出现字幕字形悬出补丁边缘。
    """
    units = sum(0.5 if ord(ch) < 0x2E80 else 1.0 for ch in str(text))
    return units * float(line_h)


def _padded_mask_box(
    rows_slice: Sequence[Row],
    plane_w: float,
    plane_h: float,
    cfg: SceneTextPolicyConfig,
    analysis_box: Optional[Tuple[int, int, int, int]] = None,
    *,
    row_dx: Optional[Sequence[float]] = None,
) -> Tuple[Tuple[float, float, float, float], float]:
    """块遮罩外扩框:按行「渲染宽度居中外扩 + pad + 标点补偿」后求 union。

    mask 动态/静态路径共用:遮罩须盖住"渲染后的字幕"(替换字体字宽通常
    大于原 OCR 框),并按 ``mask_pad_ratio`` 外扩行高方向的边距。块内不同
    文本行的渲染宽度与行尾标点补偿各不相同——先逐行以自己的行中心外扩、
    平移 ``row_dx[i]``,再对块内各行求联合框,保证每行都被自己补偿后的框
    覆盖(不取块级最大补偿整体平移,避免无标点行偏移或过宽);最后裁到
    平面/分析窗。返回 ``(union 框, 平均行高)``。
    """
    boxes: List[Tuple[float, float, float, float]] = []
    line_h_sum = 0.0
    for i, (text, rbox) in enumerate(rows_slice):
        x1, y1, x2, y2 = (float(v) for v in rbox)
        line_h = max(1.0, y2 - y1)
        pad = float(cfg.mask_pad_ratio) * line_h
        # 渲染宽度外扩:遮罩水平方向取"原框"与"渲染行"的较大者(以本行
        # 中心对称扩展),避免字幕字形悬出补丁
        need_w = _rendered_width(text, line_h)
        cx = (x1 + x2) / 2.0
        half_w = (max(x2 - x1, need_w) + 2.0 * pad) / 2.0
        dx = float(row_dx[i]) if row_dx is not None else 0.0
        boxes.append((cx - half_w + dx, y1 - pad, cx + half_w + dx, y2 + pad))
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
) -> Tuple[List[Dict], str, List[str]]:
    """mask 模式:低层纯色遮罩盖住原文字(layer 0)+ 原文本事件(layer 1)。

    每块取色并检查背景均匀性:任一块非墨像素通道 std > ``bg_max_std``
    (背景杂色,纯色补丁观感突兀)→ 整体回退 external。``ref_frame`` 透传
    给遮罩轨迹重建(缺省隐式 ``tracks[0].frame_num``,旧行为);行尾标点
    补偿按行计算,遮罩取「按行补偿后求 union」。``orig_indices`` 为排序后
    行位置 → ``blocks_meta`` 输入序下标的映射,供 external 回退时按
    ``line_idx`` 建立事件归属。
    """
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
        mbox, _line_h = _padded_mask_box(rows[s:e + 1], plane_w, plane_h,
                                         cfg, analysis_box, row_dx=row_dx)
        color, std = sample_background_color(plane_img_bgr, mbox)
        if std > float(cfg.bg_max_std):
            notes.append(
                f"mask->external: block {bi} background channel std "
                f"{std:.1f} > bg_max_std {float(cfg.bg_max_std):g}")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes,
                                  orig_indices=orig_indices)
            return ext, "external", notes
        out.extend(_mask_events_for_block(mbox, color, tracks, motion_cfg,
                                          ref_frame=ref_frame, style=style))
    # 所有识别行都归属某块 → 文本事件整体升到 layer 1(遮罩之下)
    out.extend(dict(ev, layer=1) for ev in events)
    return out, "mask", notes


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
    """external 模式:文本挪出原区域,时间跨度重叠的块合并为一条 NoteBox 事件。

    ``orig_indices`` 为排序后行位置 → ``blocks_meta`` 输入序下标的映射。
    事件归属优先用 ``line_idx``(:func:`synthesize_events` 写入的行下标 =
    ``blocks_meta`` 输入序):同文案多行(body 相同)不会把别行的事件时间
    并入本块跨度;事件不带 ``line_idx`` 时按正文匹配兼容回退(旧路径)。
    """
    ok_times = [t.time_sec for t in tracks if t.status == "ok"]
    full_span = ((min(ok_times), max(ok_times)) if ok_times else (0.0, 0.0))

    # 块时间跨度 = 块内行事件时间的并集(无匹配用全程兜底)
    have_ids = (orig_indices is not None
                and any(isinstance(ev.get("line_idx"), int) for ev in events))
    spans: List[Tuple[float, float]] = []
    for (s, e) in blocks:
        if have_ids:
            ids = {orig_indices[k] for k in range(s, e + 1)}
            mine = [ev for ev in events
                    if isinstance(ev.get("line_idx"), int)
                    and ev["line_idx"] in ids]
        else:
            want = {t for t, _b in rows[s:e + 1]}
            mine = [ev for ev in events if ev.get("body") in want]
        starts, ends = [], []
        for ev in mine:
            starts.append(_parse_ass_time(ev["start_time"]))
            ends.append(_parse_ass_time(ev["end_time"]))
        spans.append((min(starts), max(ends)) if starts else full_span)

    # 时间跨度重叠的块合并为一组(行序保持画面自上而下)
    groups: List[Dict] = []
    for bi in sorted(range(len(blocks)), key=lambda i: spans[i]):
        if groups and spans[bi][0] <= groups[-1]["end"]:
            groups[-1]["end"] = max(groups[-1]["end"], spans[bi][1])
            groups[-1]["blocks"].append(bi)
        else:
            groups.append({"start": spans[bi][0], "end": spans[bi][1],
                           "blocks": [bi]})

    margin = float(cfg.external_margin)
    band_w = max(1.0, float(video_w) - 2.0 * margin)
    band_h = max(1.0, float(video_h) - margin)
    max_chars = max(1, int(band_w) // _WRAP_BASE_FS)
    an, pos_y = ((8, margin) if str(cfg.external_pos).lower() == "top"
                 else (2, float(video_h) - margin))

    out: List[Dict] = []
    for g in groups:
        parts: List[str] = []
        for bi in g["blocks"]:  # 组内块保持自上而下
            s, e = blocks[bi]
            for text, _box in rows[s:e + 1]:
                parts.extend(wrap_cjk(text, max_chars))
        if not parts:
            continue
        longest = max(len(p) for p in parts)
        fs = fit_font_size(len(parts), band_h, longest, band_w)
        out.append({
            "start_time": format_ass_time(g["start"]),
            "end_time": format_ass_time(g["end"]),
            "style": _NOTE_STYLE,
            "name": "motion",
            "tags": f"{{\\an{an}\\pos({_fmt1(float(video_w) / 2.0)},"
                    f"{_fmt1(pos_y)})\\fs{fs}}}",
            "body": "\\N".join(parts),
        })
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
) -> Tuple[List[Dict], str, List[str]]:
    """whitespace 模式:全部块文本合并放进原文字空白带(合成行框复用轨迹)。

    ``ref_frame`` 为行框所在平面坐标系的参考帧;缺省回退旧行为
    (隐式 ``tracks[0].frame_num``,由入口在 diagnostics 注明)。
    ``orig_indices`` 供回退链(mask → external)建立事件归属。
    """
    gray, ox, oy = _analysis_gray(plane_img_bgr, analysis_box)
    line_h = sum(float(b[3]) - float(b[1]) for _t, b in rows) / max(1, len(rows))
    local_boxes = [(float(b[0]) - ox, float(b[1]) - oy,
                    float(b[2]) - ox, float(b[3]) - oy) for _t, b in rows]
    band = find_whitespace_band(
        gray, local_boxes, line_h=line_h,
        min_lines=int(cfg.ws_min_lines),
        min_width_ratio=float(cfg.ws_min_width_ratio))
    if band is None:
        notes.append(
            f"whitespace->mask: no whitespace band "
            f"(need >= {int(cfg.ws_min_lines)} x line_h {line_h:.1f}px, "
            f"width >= {float(cfg.ws_min_width_ratio):g} x plane width)")
        return _apply_mask(events, rows, blocks, plane_img_bgr, tracks, cfg,
                           video_w, video_h, motion_cfg, style, notes,
                           analysis_box=analysis_box, ref_frame=ref_frame,
                           orig_indices=orig_indices)
    band = (band[0] + ox, band[1] + oy, band[2] + ox, band[3] + oy)

    band_w = float(band[2] - band[0])
    band_h = float(band[3] - band[1])
    max_chars = max(1, int(band_w) // _WRAP_BASE_FS)
    parts: List[str] = []
    for text, _box in rows:  # 行序自上而下
        parts.extend(wrap_cjk(text, max_chars))
    longest = max(len(p) for p in parts)
    fs = fit_font_size(len(parts), band_h, longest, band_w)

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
    return (synthesize_events(new_tracks, tracks, motion_cfg, style=style),
            "whitespace", notes)


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
    三元组解包;notes 记录回退原因(whitespace→mask→external,仅所选模式
    不可用时降级;overlap 不回退),由 CLI 打 warn 日志。diagnostics 携带
    requested/applied mode、参考帧与行框有效性统计。``tracks`` 为空时
    mask/whitespace 沿回退链降级 external(记录原因),overlap 原样返回
    事件(不修改输入)。
    """
    mode = cfg.mode
    plane_h, plane_w = plane_img_bgr.shape[:2]
    rows_kept, valid_indices, row_stats = _validate_rows(
        blocks_meta, plane_w, plane_h)
    if ref_frame is not None:
        ref, ref_source = int(ref_frame), _REF_EXPLICIT
    elif len(tracks):
        ref, ref_source = int(tracks[0].frame_num), _REF_INFERRED
    else:
        ref, ref_source = None, _REF_NONE
    diag = _result_diagnostics(mode, mode, ref_frame=ref,
                               ref_source=ref_source, row_stats=row_stats)
    if mode == "overlap":
        return PolicyResult(list(events), "overlap", [], mode, diag)
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
        return PolicyResult(list(events), "overlap", [note], mode, diag)
    blocks = merge_line_blocks([b for _t, b in rows],
                               vgap_ratio=float(cfg.block_vgap_ratio))
    # 行 ID:排序后行位置 → blocks_meta 输入序下标(行框校验保持输入序,
    # 再按与 _sorted_rows 相同的键排序)。合成事件 line_idx 即输入序下标,
    # external 的事件归属优先按它匹配,正文匹配仅作兼容回退。
    order = sorted(
        range(len(rows_kept)),
        key=lambda j: (float(rows_kept[j][1][1]), float(rows_kept[j][1][0])))
    orig_indices = [valid_indices[j] for j in order]
    if mode in ("mask", "whitespace") and not tracks:
        # 轨迹重建需要至少一帧跟踪结果;空轨迹沿回退链降级 external
        #(external 只按事件时间排版,不依赖轨迹几何)。
        notes = [f"{mode}->external: no tracking frames (empty tracks)"]
        out = _apply_external(list(events), rows, blocks, tracks, cfg,
                              video_w, video_h, mcfg, style, notes,
                              orig_indices=orig_indices)
        diag["applied_mode"] = "external"
        return PolicyResult(out, "external", notes, mode, diag)
    if mode == "mask":
        out, applied, notes = _apply_mask(
            list(events), rows, blocks, plane_img_bgr, tracks, cfg,
            video_w, video_h, mcfg, style, [], analysis_box=analysis_box,
            ref_frame=ref, orig_indices=orig_indices)
    elif mode == "external":
        out, applied, notes = (_apply_external(list(events), rows, blocks,
                                               tracks, cfg, video_w, video_h,
                                               mcfg, style, [],
                                               orig_indices=orig_indices),
                               "external", [])
    elif mode == "whitespace":
        out, applied, notes = _apply_whitespace(
            list(events), rows, blocks, plane_img_bgr, tracks, cfg,
            video_w, video_h, mcfg, style, [], analysis_box=analysis_box,
            ref_frame=ref, orig_indices=orig_indices)
    else:
        raise ValueError(
            f"unknown scene text policy mode: {mode!r}; "
            f"valid modes: {', '.join(POLICY_MODES)}")
    diag["applied_mode"] = applied
    return PolicyResult(out, applied, notes, mode, diag)


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
    }


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
    parts: List[str] = []
    for text, _box in rows:  # 行序自上而下
        parts.extend(wrap_cjk(text, max_chars))
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
) -> Tuple[List[Dict], str, List[str]]:
    """mask 静态路径:每块一条静态矩形 + 全部原行(layer 1)。

    取色/背景杂色检查/外扩框与 motion 版共用(按行外扩后求 union);任一
    块背景 std 超限 → 回退 external(单条 NoteBox spec)。
    """
    plane_h, plane_w = plane_img_bgr.shape[:2]
    out: List[Dict] = []
    for bi, (s, e) in enumerate(blocks):
        mbox, _line_h = _padded_mask_box(rows[s:e + 1], plane_w, plane_h,
                                         cfg, analysis_box)
        color, std = sample_background_color(plane_img_bgr, mbox)
        if std > float(cfg.bg_max_std):
            notes.append(
                f"mask->external: block {bi} background channel std "
                f"{std:.1f} > bg_max_std {float(cfg.bg_max_std):g}")
            spec = _static_external_spec(rows, cfg, video_w, video_h, base_fs)
            return ([spec] if spec else []), "external", notes
        out.append(_static_mask_spec(mbox, color, orig_indices[s:e + 1],
                                     style=style))
    for i, (text, box) in enumerate(rows):
        cx = int((float(box[0]) + float(box[2])) / 2.0)
        cy = int((float(box[1]) + float(box[3])) / 2.0)
        line_h = max(1.0, float(box[3]) - float(box[1]))
        # 显式 \fs=行高:与遮罩/渲染宽度估算同一尺寸体系(Scene 样式字号
        # 是画面高度常数,与原文字大小无关,会让字幕远大于原字)
        out.append({
            "kind": "text",
            "style": str(style),
            "tags": f"{{\\an5\\pos({cx},{cy})\\fs({round(line_h)})}}",
            "body": text,
            "layer": 1,
            "row": int(orig_indices[i]),
        })
    return out, "mask", notes


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
) -> Optional[List[Dict]]:
    """whitespace 静态路径:全部行文本合并放进空白带(单条 \\an5\\pos spec)。

    带检测与 motion 版共用 :func:`find_whitespace_band`;无带时记录原因并
    返回 None(由 :func:`apply_policy_static` 沿回退链降级到 mask)。
    """
    gray, ox, oy = _analysis_gray(plane_img_bgr, analysis_box)
    line_h = sum(float(b[3]) - float(b[1]) for _t, b in rows) / max(1, len(rows))
    local_boxes = [(float(b[0]) - ox, float(b[1]) - oy,
                    float(b[2]) - ox, float(b[3]) - oy) for _t, b in rows]
    band = find_whitespace_band(
        gray, local_boxes, line_h=line_h,
        min_lines=int(cfg.ws_min_lines),
        min_width_ratio=float(cfg.ws_min_width_ratio))
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
    parts: List[str] = []
    for text, _box in rows:  # 行序自上而下
        parts.extend(wrap_cjk(text, max_chars))
    fs = fit_font_size(len(parts), band_h, max(len(p) for p in parts), band_w,
                       base=base_fs)
    tags = (f"{{\\an5\\pos({_fmt1((float(band[0]) + float(band[2])) / 2.0)},"
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

    与 :func:`apply_policy` 同一回退链(whitespace → mask → external)与
    取色/并块/空白带/折行实现,但不读轨迹、不产时间:输入 ``rows_meta`` 为
    ``[(文本, (x1, y1, x2, y2)), ...]]``(行框与 ``plane_img_bgr`` 同一平面
    坐标系);输出为**不带时间**的 spec dict 列表,时间由调用方按所属组回填:

    - ``{"kind": "mask", "tags", "layer": 0, "rows": [行索引], ...}`` ——
      每块一条静态 ``\\an7\\pos\\p1`` 矩形(平面坐标);
    - ``{"kind": "text", "tags", "body", "layer": 1, "row": 行索引}`` ——
      原识别行(平面坐标 ``\\an5\\pos`` 中心);
    - ``{"kind": "note", "style": "NoteBox", "tags", "body"}`` —— external
      单条展示框(``tags`` 已是视频坐标);
    - ``{"kind": "scene_ws", "tags", "body"}`` —— whitespace 单条空白带放置
      (平面坐标)。

    ``style`` 为场景文字事件(mask 矩形 / 原行文本 / whitespace 放置)的
    样式名,缺省 ``Scene``;external 的展示框仍用 ``NoteBox`` 样式。
    ``base_font_size`` 为折行/字号适配的基准字号(缺省 40;主流水线传
    Scene 样式字号)。overlap 原样返回空 spec 列表。输入行框先经校验
    (非有限/非正宽高/完全越界剔除,部分越界裁剪到平面边界);全部行无效时
    返回空 spec 并留痕。返回 :class:`PolicyResult`(兼容
    ``(specs, applied, notes)`` 三元组解包),diagnostics 携带
    requested/applied mode 与行框有效性统计(静态路径无轨迹,
    ``ref_frame`` 为 None)。
    """
    mode = cfg.mode
    plane_h, plane_w = plane_img_bgr.shape[:2]
    rows_kept, kept_ids, row_stats = _validate_rows(
        rows_meta, plane_w, plane_h)
    diag = _result_diagnostics(mode, mode, ref_frame=None,
                               ref_source=_REF_NONE, row_stats=row_stats)
    if mode == "overlap":
        return PolicyResult([], "overlap", [], mode, diag)
    base_fs = int(base_font_size) if base_font_size else _WRAP_BASE_FS
    notes: List[str] = []
    rows = _sorted_rows(rows_kept)
    if not rows:
        if row_stats["total_rows"]:
            notes.append(
                f"{mode}: all {row_stats['total_rows']} text rows invalid "
                f"after box validation; policy not applied")
        return PolicyResult([], mode, notes, mode, diag)
    # 行序 = 阅读序;orig_indices 把排序后位置映射回输入行索引
    # (rows_kept 保持输入序,kept_ids[j] = rows_kept[j] 的原始输入索引)
    order = sorted(
        range(len(rows_kept)),
        key=lambda j: (float(rows_kept[j][1][1]), float(rows_kept[j][1][0])))
    orig_indices = [kept_ids[j] for j in order]
    blocks = merge_line_blocks([b for _t, b in rows],
                               vgap_ratio=float(cfg.block_vgap_ratio))
    while True:
        if mode == "mask":
            out, applied, notes = _apply_static_mask(
                rows, blocks, plane_img_bgr, cfg, video_w, video_h, notes,
                orig_indices, analysis_box=analysis_box, base_fs=base_fs,
                style=style)
            diag["applied_mode"] = applied
            return PolicyResult(out, applied, notes, cfg.mode, diag)
        if mode == "external":
            spec = _static_external_spec(rows, cfg, video_w, video_h, base_fs)
            diag["applied_mode"] = "external"
            return (PolicyResult([spec] if spec else [], "external", notes,
                                 cfg.mode, diag))
        if mode == "whitespace":
            specs = _apply_static_whitespace(
                rows, plane_img_bgr, cfg, video_w, video_h, notes,
                analysis_box=analysis_box, base_fs=base_fs, style=style)
            if specs is not None:
                diag["applied_mode"] = "whitespace"
                return PolicyResult(specs, "whitespace", notes, cfg.mode, diag)
            mode = "mask"  # 回退链:whitespace → mask
            continue
        raise ValueError(
            f"unknown scene text policy mode: {mode!r}; "
            f"valid modes: {', '.join(POLICY_MODES)}")
