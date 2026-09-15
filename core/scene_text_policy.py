# core/scene_text_policy.py
"""场景文字显示策略(overlap / mask / external / whitespace)+ 自动回退链。

对应《场景文字显示策略(mask / external / whitespace)设计》§3/§4/§6:
识别字幕锚定在原文字位置时,替换字体与原排版的字宽字重不可能一致,原字从
字幕笔画缝隙透出形成「双重曝光式重影」。本模块提供四种显示模式的事件生成:

- :func:`apply_policy` —— 模式入口:overlap 原样返回;mask 生成 ``\\p1``
  矢量遮罩盖住原文字(识别文本升到 layer 1);external 把文本挪出原区域、
  底带 ``NoteBox`` 展示框;whitespace 把文本放进原文字空白带(合成行框喂
  :func:`core.motion_ass.build_line_tracks`,轨迹与亮度标签零成本复用);
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
    smooth_line_track,
    synthesize_events,
)

__all__ = [
    "SceneTextPolicyConfig",
    "sample_background_color",
    "merge_line_blocks",
    "find_whitespace_band",
    "wrap_cjk",
    "fit_font_size",
    "apply_policy",
]

POLICY_MODES = ("overlap", "mask", "external", "whitespace")

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


def _block_union(rows: Sequence[Row]) -> Tuple[Tuple[float, float, float, float], float]:
    """块内行的联合外接框与平均行高。"""
    x1 = min(float(b[0]) for _t, b in rows)
    y1 = min(float(b[1]) for _t, b in rows)
    x2 = max(float(b[2]) for _t, b in rows)
    y2 = max(float(b[3]) for _t, b in rows)
    line_h = sum(float(b[3]) - float(b[1]) for _t, b in rows) / len(rows)
    return (x1, y1, x2, y2), line_h


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
) -> List[Dict]:
    """单块遮罩事件:``\\an7\\p1`` 矩形随轨迹平移/旋转/缩放(自己的 DP 分段)。

    块角点经 :func:`build_line_tracks` 同款映射得逐帧轨迹(纯色块,无需与
    文本事件对齐);遮罩不用 ``\\alpha``,调暗由调用方按 ``base_color`` 生成。
    """
    x1, y1, x2, y2 = box
    w = max(1.0, float(x2) - float(x1))
    h = max(1.0, float(y2) - float(y1))
    iw, ih = int(round(w)), int(round(h))
    b, g, r = (int(round(float(c))) for c in color_bgr)
    color_tag = f"\\1c&H{b:02X}{g:02X}{r:02X}&"
    drawing = (f"m 0 0 l {iw} 0 {iw} {ih} 0 {ih}{{\\p0}}")

    (lt,) = build_line_tracks([(x1, y1, x2, y2)], ["mask"], tracks,
                              ref_frame=tracks[0].frame_num)
    smooth_line_track(lt, window=motion_cfg.smooth_window)

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
            # 只对 body 做 ASCII 花括号安全化)
            tags = (f"{{\\an7\\p1{color_tag}{move}"
                    + _overlay_tags(angles[ai], angles[bi],
                                    scales[ai], scales[bi], t0, t1, motion_cfg)
                    + "}")
            tags += drawing
            events.append({
                "start_time": format_ass_time(t0),
                "end_time": format_ass_time(t1),
                "style": "Scene",
                "name": "motion",
                "tags": tags,
                "body": "",
                "layer": 0,
                "base_color": (b, g, r),
            })
    return events


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
) -> Tuple[List[Dict], str, List[str]]:
    """mask 模式:低层纯色遮罩盖住原文字(layer 0)+ 原文本事件(layer 1)。

    每块取色并检查背景均匀性:任一块非墨像素通道 std > ``bg_max_std``
    (背景杂色,纯色补丁观感突兀)→ 整体回退 external。
    """
    plane_h, plane_w = plane_img_bgr.shape[:2]
    out: List[Dict] = []
    for bi, (s, e) in enumerate(blocks):
        union, line_h = _block_union(rows[s:e + 1])
        pad = float(cfg.mask_pad_ratio) * line_h
        mbox = (max(0.0, union[0] - pad), max(0.0, union[1] - pad),
                min(float(plane_w), union[2] + pad),
                min(float(plane_h), union[3] + pad))
        if analysis_box is not None:  # 遮罩不越出文字平面(quad 窗口)
            mbox = (max(float(analysis_box[0]), mbox[0]),
                    max(float(analysis_box[1]), mbox[1]),
                    min(float(analysis_box[2]), mbox[2]),
                    min(float(analysis_box[3]), mbox[3]))
        color, std = sample_background_color(plane_img_bgr, mbox)
        if std > float(cfg.bg_max_std):
            notes.append(
                f"mask->external: block {bi} background channel std "
                f"{std:.1f} > bg_max_std {float(cfg.bg_max_std):g}")
            ext = _apply_external(events, rows, blocks, tracks, cfg,
                                  video_w, video_h, motion_cfg, style, notes)
            return ext, "external", notes
        out.extend(_mask_events_for_block(mbox, color, tracks, motion_cfg))
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
) -> List[Dict]:
    """external 模式:文本挪出原区域,时间跨度重叠的块合并为一条 NoteBox 事件。"""
    ok_times = [t.time_sec for t in tracks if t.status == "ok"]
    full_span = ((min(ok_times), max(ok_times)) if ok_times else (0.0, 0.0))

    # 块时间跨度 = 块内行事件时间的并集(按正文归属;无匹配用全程兜底)
    spans: List[Tuple[float, float]] = []
    for (s, e) in blocks:
        want = {t for t, _b in rows[s:e + 1]}
        starts, ends = [], []
        for ev in events:
            if ev.get("body") in want:
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
) -> Tuple[List[Dict], str, List[str]]:
    """whitespace 模式:全部块文本合并放进原文字空白带(合成行框复用轨迹)。"""
    gray_full = cv2.cvtColor(plane_img_bgr, cv2.COLOR_BGR2GRAY)
    # 墨迹/空白带检测限制在文字平面(quad 窗口)内:窗口外的展开区域是
    # 无效画面,不得参与取色与空白带候选
    ox, oy = 0, 0
    if analysis_box is not None:
        clipped = _clip_box(analysis_box, gray_full.shape[1], gray_full.shape[0])
        if clipped is not None:
            ox, oy = clipped[0], clipped[1]
            gray = gray_full[clipped[1]:clipped[3], clipped[0]:clipped[2]]
        else:
            gray = gray_full
    else:
        gray = gray_full
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
                           analysis_box=analysis_box)
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
    new_tracks = build_line_tracks(boxes, parts, tracks,
                                   ref_frame=tracks[0].frame_num)
    for lt in new_tracks:
        smooth_line_track(lt, window=motion_cfg.smooth_window)
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
) -> Tuple[List[Dict], str, List[str]]:
    """对合成后的 motion 事件应用场景文字显示策略(设计 §3/§4)。

    ``blocks_meta`` 为识别行 ``[(文本, (x1, y1, x2, y2)), ...]``(统一坐标 =
    初始帧平面坐标,与 ``build_line_tracks`` 输入一致);``plane_img_bgr``
    为锚定关键帧的同坐标系展开图;``tracks`` 为逐帧平面跟踪结果;
    ``analysis_box`` 可选,限定文字平面(quad 窗口)在展开图内的范围,
    窗口外的展开区域不参与取色与空白带检测。

    返回 ``(events, applied_policy, notes)``:notes 记录回退原因
    (whitespace→mask→external,仅所选模式不可用时降级;overlap 不回退),
    由 CLI 打 warn 日志。overlap 原样返回事件(不修改输入)。
    """
    mode = cfg.mode
    if mode == "overlap":
        return list(events), "overlap", []
    mcfg = motion_cfg if motion_cfg is not None else MotionAssConfig()
    rows = _sorted_rows(blocks_meta)
    blocks = merge_line_blocks([b for _t, b in rows],
                               vgap_ratio=float(cfg.block_vgap_ratio))
    if mode == "mask":
        return _apply_mask(list(events), rows, blocks, plane_img_bgr, tracks,
                           cfg, video_w, video_h, mcfg, style, [],
                           analysis_box=analysis_box)
    if mode == "external":
        return (_apply_external(list(events), rows, blocks, tracks, cfg,
                                video_w, video_h, mcfg, style, []),
                "external", [])
    if mode == "whitespace":
        return _apply_whitespace(list(events), rows, blocks, plane_img_bgr,
                                 tracks, cfg, video_w, video_h, mcfg, style,
                                 [], analysis_box=analysis_box)
    raise ValueError(
        f"unknown scene text policy mode: {mode!r}; "
        f"valid modes: {', '.join(POLICY_MODES)}")
