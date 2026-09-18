# core/motion_ass.py
"""移动文字轨迹 → ASS 轨迹字幕合成器(纯函数,不依赖 PySide6)。

对应《移动文字轨迹 → ASS 轨迹字幕 设计》§4.1 / §5:

- :func:`build_line_tracks` —— 参考关键帧平面坐标的行框,经帧间单应链乘
  ``H(ref→t) = H(init→t)·inv(H(init→ref))`` 逐 ok 帧映射为行四边形,
  得到每行逐帧的 ``LinePose``(中心 / 角度 / 缩放);lost 帧无条目。
- :func:`smooth_line_track` —— 滑动平均去抖(窗口只数有 pose 的帧,且不
  跨越长 lost 遮挡段),只去高频抖动、不改缓慢运动趋势。
- :func:`simplify_and_segment` —— 中心序列 Douglas-Peucker 简化得分段点,
  并入角度/缩放显著变化帧,强制最小段长,输出连续无缝的帧号区间。
- :func:`synthesize_events` —— 标签阶梯:单段直线 → 一条 ``\\move`` 事件;
  多段 → 每段一条 ``\\move`` 事件(边界共享同一格式化时间字符串,
  段内角度/缩放超阈值叠加 ``\\t``);段数爆炸 → 帧级 ``\\pos`` 兜底;
  lost 间隔切段(可选 ``lost_hold_sec`` 保持)。

平滑(阶梯 step 1)由调用方在合成前调用 :func:`smooth_line_track` 完成;
:func:`synthesize_events` 只消费给定的 pose,不修改输入行轨迹。

时间一律用 ``TrackedQuad.time_sec`` 浮点,仅在格式化时转 centisecond
(:func:`format_ass_time` 与主流水线 ``H:MM:SS.CC`` 截断语义一致)。

增量特性「屏幕亮度自适应」:文字平面(手机屏幕)渐变变暗/变亮时,字幕用
``\\t`` 驱动 ``\\1c``(颜色变暗)+ ``\\alpha``(变透明)忠实跟随——
:func:`simplify_luma_curve` 简化 ``core.screen_luma`` 测得的亮度比值折线,
:func:`brightness_tag_chain` 把折线换算成单条事件的局部标签串。
两者均为纯函数,不依赖视频 I/O。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.scene_plane_tracker import TrackedQuad

__all__ = [
    "MotionAssConfig",
    "LinePose",
    "LineTrack",
    "format_ass_time",
    "homography_between",
    "build_line_tracks",
    "smooth_line_track",
    "simplify_and_segment",
    "synthesize_events",
    "simplify_luma_curve",
    "brightness_tag_chain",
    "punct_comp_offset_px",
]


# ---------------------------------------------------------------------------
# 配置与数据结构
# ---------------------------------------------------------------------------

@dataclass
class MotionAssConfig:
    """合成器参数(默认值见设计 §5.1)。"""

    move_tol_px: float = 2.0        # DP 简化/单段判定容差
    min_seg_frames: int = 3         # 最小段长(帧)
    smooth_window: int = 5          # 滑动平均窗口(帧,只数有 pose 的帧)
    smooth_max_gap: int = 2         # 平滑可跨越的最大帧号间隔(更长 lost 段分段平滑)
    rot_thresh_deg: float = 1.0     # 分段点/±t 叠加的角度阈值
    scale_thresh: float = 0.02      # 缩放变化阈值
    dense_pos_fallback: bool = True
    dense_stride: int = 2           # 兜底 \pos 每 N 好帧一条
    max_segments_per_sec: float = 6.0
    lost_hold_sec: float = 0.0      # lost 保持时长(0=切段)

    # —— 融合行噪声过滤(手机状态栏/导航栏图标误读:<、>、000、单字象形)——
    # 判据与静态路径共用(core.text_utils.is_noise_text),行融合后、建轨迹前剔除。
    junk_line_filter: bool = True

    # —— 屏幕亮度自适应(增量特性;--auto-brightness 开启,--config-json 可覆盖)——
    brightness_tol: float = 8.0                   # 亮度曲线 DP 简化容差(0-255 亮度级)
    brightness_baseline_percentile: float = 90.0  # 亮度基线分位(各帧中位值的分位)
    brightness_use_color: bool = True             # \1c 颜色跟随
    brightness_use_alpha: bool = True             # \alpha 透明度跟随
    brightness_per_line: bool = False             # 逐行测亮度(屏幕局部调暗的背景适配)

    # —— 行尾全角标点字形补偿(。、等墨迹偏左的标点,渲染墨水整体左偏 ≤7px)——
    punct_comp_enabled: bool = True
    punct_comp_max_px: float = 7.0                # 补偿上限(1080p 基准,随 PlayRes 高度缩放)

    # —— \iclip 手部遮挡蒙版(FR-9;core.occlusion_mask 参数,--occlusion-clip 开启)——
    occlusion_clip: bool = False                  # 部分遮挡跨度内的事件追加 \iclip
    occlusion_diff_tol: float = 26.0              # 展开图灰度差阈值(变化像素判定)
    occlusion_min_area_px: float = 400.0          # 变化区最小面积(平面 px)
    occlusion_min_line_overlap: float = 0.06      # 与行框相交面积占比下限
    occlusion_max_coverage: float = 0.55          # 变化覆盖率上限(超过=调暗/切镜,不判遮挡)
    occlusion_sample_max_frames: int = 7          # 每条事件最多取的遮挡采样帧数
    occlusion_edge_margin_px: int = 8             # 展开图四边忽略带(单应边界采样不稳定)
    occlusion_edge_sliver_max_px: float = 24.0    # 贴边条带判定的最大厚度(px)
    occlusion_edge_sliver_min_frac: float = 0.5   # 条带须覆盖对应边长的比例


@dataclass
class LinePose:
    """单行文字在某帧的几何:中心点、行方向角(度)、长边缩放比。"""

    center: Tuple[float, float]
    angle_deg: float
    scale: float


@dataclass
class LineTrack:
    """单行文字的逐帧轨迹;``poses`` 的 key 为 frame_num,lost 帧无条目。"""

    text: str
    ref_box: tuple                  # 参考帧平面坐标 (x1, y1, x2, y2)
    height: float                   # 行参考高度(y2 - y1),输出 \fs 用
    poses: Dict[int, LinePose]


# ---------------------------------------------------------------------------
# 时间格式化(与主流水线 H:MM:SS.CC 一致)
# ---------------------------------------------------------------------------

def format_ass_time(sec: float) -> str:
    """秒 → ASS ``H:MM:SS.CC``(centisecond 截断,与 generator 时间轴一致)。"""
    if sec < 0:
        sec = 0.0
    # 1e-6 只抵消浮点表示噪声:POS_MSEC/1000 的秒值经常落在真值一个 ULP
    # 之下(如 1.16*100 == 115.99999...),直接截断会把整帧时间戳提前 1cs
    # (与 subtitle_generator/timeline.py 的同源修复保持一致)。
    cs_total = int(float(sec) * 100 + 1e-6)
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# 行尾全角标点字形补偿
# ---------------------------------------------------------------------------

# 行尾全角标点(墨迹位于字身框左半、右半留白的标点):渲染排版宽度计入
# 空白半格,libass \an5 按排版盒居中会使墨水整体左偏(2026-09-16 DMG 验收
# 实测:带标点行 dx -6~-7px、无标点行 ≈ -0.5px)。补偿 = 右移半个空白格
# ≈ 0.25×行高,上限随 PlayRes 高度缩放(默认 7px@1080p)。
_TRAILING_BLANK_RIGHT_PUNCT = frozenset("。，、．,・")


def punct_comp_offset_px(
    text: str,
    fs_px: float,
    *,
    enabled: bool = True,
    max_px: float = 7.0,
    video_height: Optional[float] = None,
) -> float:
    """行尾全角标点的字形度量补偿量(x 向右移,画面 px);无标点返回 0。

    ``fs_px`` 为行高(≈字号,``\\fs`` 同单位);补偿量 = ``min(上限, 0.25×行高)``,
    上限 = ``max_px``×(video_height/1080)(PlayRes 非空时按高度等比缩放,
    1080p 时恰为 ``max_px``)。
    """
    if not enabled:
        return 0.0
    stripped = str(text or "").strip()
    if not stripped or not stripped[-1] in _TRAILING_BLANK_RIGHT_PUNCT:
        return 0.0
    fs_px = float(fs_px or 0.0)
    if fs_px <= 0:
        return 0.0
    cap = float(max_px)
    if video_height is not None and float(video_height) > 0:
        cap *= float(video_height) / 1080.0
    return float(min(cap, 0.25 * fs_px))


# ---------------------------------------------------------------------------
# 帧间单应
# ---------------------------------------------------------------------------

def _frame_map(tracks: Sequence[TrackedQuad]) -> Dict[int, TrackedQuad]:
    return {t.frame_num: t for t in tracks}


def _homography_between(
    hmap: Dict[int, TrackedQuad], frame_a: int, frame_b: int,
) -> np.ndarray:
    for f in (frame_a, frame_b):
        tq = hmap.get(f)
        if tq is None or tq.status != "ok" or tq.homography is None:
            raise KeyError(f"frame {f} is lost or missing; no homography available")
    h_a = np.asarray(hmap[frame_a].homography, dtype=np.float64)
    h_b = np.asarray(hmap[frame_b].homography, dtype=np.float64)
    # H(a→b) = H(init→b) · inv(H(init→a))
    return h_b @ np.linalg.inv(h_a)


def homography_between(tracks: Sequence[TrackedQuad], frame_a: int, frame_b: int) -> np.ndarray:
    """帧 a 平面坐标 → 帧 b 画面的单应;端点 lost/不存在时抛 :class:`KeyError`。"""
    return _homography_between(_frame_map(tracks), frame_a, frame_b)


def _nearest_ok_frame(hmap: Dict[int, TrackedQuad], ref_frame: int) -> int:
    best: Tuple[int, int] | None = None  # (距离, 帧号),并列取更早帧
    for f, tq in hmap.items():
        if tq.status != "ok" or tq.homography is None:
            continue
        key = (abs(f - ref_frame), f)
        if best is None or key < best:
            best = key
    if best is None:
        raise KeyError("no ok frame in tracks; cannot anchor line reference")
    return best[0]


def _apply_homography(pts: np.ndarray, h_mat: np.ndarray) -> np.ndarray:
    hom = np.hstack([pts, np.ones((len(pts), 1))])
    proj = hom @ h_mat.T
    w = proj[:, 2:3]
    if not np.all(np.isfinite(proj)) or np.any(np.abs(w) < 1e-12):
        raise ValueError("degenerate homography while mapping line corners")
    return proj[:, :2] / w


# ---------------------------------------------------------------------------
# 行轨迹重建
# ---------------------------------------------------------------------------

def _pose_from_quad(mapped: np.ndarray, ref_long_edge: float, horizontal: bool) -> LinePose:
    center = mapped.mean(axis=0)
    edge = mapped[1] - mapped[0] if horizontal else mapped[2] - mapped[1]
    length = float(math.hypot(float(edge[0]), float(edge[1])))
    angle = math.degrees(math.atan2(float(edge[1]), float(edge[0])))
    scale = length / ref_long_edge if ref_long_edge > 1e-6 else 1.0
    return LinePose(
        center=(float(center[0]), float(center[1])),
        angle_deg=angle,
        scale=scale,
    )


def build_line_tracks(
    line_boxes: Sequence[Sequence[float]],
    texts: Sequence[str],
    tracks: Sequence[TrackedQuad],
    ref_frame: int,
) -> List[LineTrack]:
    """参考帧平面坐标的行框 → 逐 ok 帧的 :class:`LineTrack`。

    ``line_boxes`` / ``texts`` 为参考关键帧平面坐标系下的行框与文本,
    行顺序保持。``ref_frame`` 若为 lost,则把参考重锚定到最近的 ok 帧
    (并列取更早帧)后再参与逐帧映射;lost 帧不产出 pose。
    """
    if len(line_boxes) != len(texts):
        raise ValueError("line_boxes and texts must have the same length")
    hmap = _frame_map(tracks)
    ref = ref_frame
    ref_tq = hmap.get(ref_frame)
    if ref_tq is None or ref_tq.status != "ok" or ref_tq.homography is None:
        ref = _nearest_ok_frame(hmap, ref_frame)

    out: List[LineTrack] = []
    for box, text in zip(line_boxes, texts):
        x1, y1, x2, y2 = (float(v) for v in box)
        corners = np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
        horizontal = abs(x2 - x1) >= abs(y2 - y1)
        long_edge = max(abs(x2 - x1), abs(y2 - y1))
        poses: Dict[int, LinePose] = {}
        for tq in tracks:
            if tq.status != "ok" or tq.homography is None:
                continue  # lost 帧无条目
            h_mat = _homography_between(hmap, ref, tq.frame_num)
            mapped = _apply_homography(corners, h_mat)
            poses[tq.frame_num] = _pose_from_quad(mapped, long_edge, horizontal)
        out.append(LineTrack(
            text=str(text),
            ref_box=(x1, y1, x2, y2),
            height=float(y2 - y1),
            poses=poses,
        ))
    return out


# ---------------------------------------------------------------------------
# 平滑
# ---------------------------------------------------------------------------

def smooth_line_track(track: LineTrack, window: int = 5, max_gap: int = 2) -> None:
    """原地滑动平均 center/angle/scale;窗口(帧)只数有 pose 的帧。

    中心窗口 ``[f - window//2, f + window//2]`` 内的缺失(lost)帧自然跳过;
    角度先按序列展开(unwrap)再平均,避免 ±180° 边界跳变。窗口 <2 时不变。
    相邻 OK 帧号差 > ``max_gap`` 视为长遮挡段:平滑窗口不跨越(按连续 OK
    run 分段平滑),两侧姿态各自平均;短间隙(默认 ≤2 帧)保持旧行为可跨越
    (窗口半径内的缺失帧跳过后两侧仍落入同一窗口)。
    """
    if window is None or window < 2 or len(track.poses) < 2:
        return
    frames = sorted(track.poses)
    radius = max(1, int(window) // 2)
    max_gap = max(0, int(max_gap))
    angles = np.unwrap(np.deg2rad(
        [track.poses[f].angle_deg for f in frames]))
    xs = np.array([track.poses[f].center[0] for f in frames])
    ys = np.array([track.poses[f].center[1] for f in frames])
    scales = np.array([track.poses[f].scale for f in frames])

    # 连续 OK run(帧号差 > max_gap 处切段):每个窗口限制在自己的 run 内
    n = len(frames)
    run_lo = [0] * n
    run_hi = [0] * n
    rl = 0
    for i in range(n):
        if i and frames[i] - frames[i - 1] > max_gap:
            rl = i
        run_lo[i] = rl
    rr = n - 1
    for i in range(n - 1, -1, -1):
        if i < n - 1 and frames[i + 1] - frames[i] > max_gap:
            rr = i
        run_hi[i] = rr

    new_poses: Dict[int, LinePose] = {}
    for i, f in enumerate(frames):
        lo = bisect_left(frames, f - radius, run_lo[i], run_hi[i] + 1)
        hi = bisect_right(frames, f + radius, run_lo[i], run_hi[i] + 1)
        new_poses[f] = LinePose(
            center=(float(xs[lo:hi].mean()), float(ys[lo:hi].mean())),
            angle_deg=float(math.degrees(angles[lo:hi].mean())),
            scale=float(scales[lo:hi].mean()),
        )
    track.poses = new_poses


# ---------------------------------------------------------------------------
# 分段(Douglas-Peucker + 角度/缩放显著点 + 最小段长)
# ---------------------------------------------------------------------------

def _ang_diff(a: float, b: float) -> float:
    """两角度的最小绝对差(处理 ±180° 环绕),单位度。"""
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _dp_breakpoints(pts: np.ndarray, tol: float) -> List[int]:
    """中心序列的 Douglas-Peucker 简化,返回保留点下标(含首末)。"""
    n = len(pts)
    if n < 3:
        return list(range(n))
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        a = pts[i]
        seg = pts[j] - a
        seg_len = math.hypot(float(seg[0]), float(seg[1]))
        rel = pts[i + 1:j] - a
        if seg_len < 1e-9:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / seg_len
        k = int(np.argmax(dist))
        if float(dist[k]) > tol:
            keep[i + 1 + k] = True
            stack.append((i, i + 1 + k))
            stack.append((i + 1 + k, j))
    return [idx for idx in range(n) if keep[idx]]


def _split_runs(frames: List[int]) -> List[List[int]]:
    """按帧号连续性切分(帧号跳变 >1 视为 lost 间隔)。"""
    runs: List[List[int]] = []
    cur: List[int] = []
    prev: int | None = None
    for f in frames:
        if prev is not None and f - prev > 1:
            runs.append(cur)
            cur = []
        cur.append(f)
        prev = f
    if cur:
        runs.append(cur)
    return runs


def _raw_segments(
    centers: np.ndarray,
    angles: Sequence[float],
    scales: Sequence[float],
    cfg: MotionAssConfig,
) -> List[Tuple[int, int]]:
    """DP 简化 + 角度/缩放显著点 → 原始分段(未强制最小段长)。"""
    n = len(centers)
    if n == 0:
        return []
    if n == 1:
        return [(0, 0)]
    bps = set(_dp_breakpoints(centers, cfg.move_tol_px))
    bps.add(0)
    bps.add(n - 1)
    for idx in range(1, n):
        if (_ang_diff(angles[idx], angles[idx - 1]) >= cfg.rot_thresh_deg
                or abs(scales[idx] - scales[idx - 1]) >= cfg.scale_thresh):
            bps.add(idx)  # 显著变化落在边界帧上,突变恰好在段间发生
    order = sorted(bps)
    return [(order[k], order[k + 1]) for k in range(len(order) - 1)]


def _enforce_min_len(
    segs: List[Tuple[int, int]], min_len: int,
) -> List[Tuple[int, int]]:
    """强制最小段长:最短段并入前邻段(无前邻则并入后邻),直至全部达标。"""
    segs = list(segs)
    min_len = max(1, int(min_len))
    while len(segs) > 1:
        lengths = [b - a + 1 for a, b in segs]
        si = min(range(len(segs)), key=lambda k: lengths[k])
        if lengths[si] >= min_len:
            break
        if si > 0:
            a0, _ = segs[si - 1]
            _, b1 = segs[si]
            segs[si - 1:si + 1] = [(a0, b1)]
        else:
            _, b1 = segs[1]
            segs[0:2] = [(segs[0][0], b1)]
    return segs


def _segment_indices(
    centers: np.ndarray,
    angles: Sequence[float],
    scales: Sequence[float],
    cfg: MotionAssConfig,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """单条链分段,返回 (原始分段, 强制最小段长后的分段)。

    段数爆炸判定(spec §5「混乱手持抖动会使段数爆炸」)基于原始 DP 分段;
    最小段长只用于整理 ``\\move`` 链的输出事件。
    """
    raw = _raw_segments(centers, angles, scales, cfg)
    return raw, _enforce_min_len(raw, cfg.min_seg_frames)


def simplify_and_segment(
    track: LineTrack, cfg: MotionAssConfig,
) -> List[Tuple[int, int]]:
    """行轨迹 → 帧号闭区间分段列表(连续无缝;lost 间隔处切开)。"""
    frames = sorted(track.poses)
    out: List[Tuple[int, int]] = []
    for run in _split_runs(frames):
        centers = np.array([track.poses[f].center for f in run], dtype=np.float64)
        angles = [track.poses[f].angle_deg for f in run]
        scales = [track.poses[f].scale for f in run]
        _, final_segs = _segment_indices(centers, angles, scales, cfg)
        for a, b in final_segs:
            out.append((run[a], run[b]))
    return out


# ---------------------------------------------------------------------------
# 合成(标签阶梯)
# ---------------------------------------------------------------------------

def _fmt1(v: float) -> str:
    v = float(v)
    if v == 0.0:
        v = 0.0  # 归一 -0.0
    return f"{v:.1f}"


def _fmt2(v: float) -> str:
    v = float(v)
    if v == 0.0:
        v = 0.0
    return f"{v:.2f}"


def _seg_ms(t0: float, t1: float) -> int:
    return max(0, int(round((float(t1) - float(t0)) * 1000)))


def _overlay_tags(
    angle0: float, angle1: float,
    scale0: float, scale1: float,
    t0: float, t1: float,
    cfg: MotionAssConfig,
) -> str:
    """段起止角度/缩放超阈值时生成「基值 + \\t」叠加标签段。"""
    rot = _ang_diff(angle0, angle1) >= cfg.rot_thresh_deg
    scl = abs(float(scale0) - float(scale1)) >= cfg.scale_thresh
    if not (rot or scl):
        return ""
    base = ""
    target = ""
    if rot:
        base += f"\\frz{_fmt2(angle0)}"
        target += f"\\frz{_fmt2(angle1)}"
    if scl:
        p0, p1 = float(scale0) * 100.0, float(scale1) * 100.0
        base += f"\\fscx{_fmt1(p0)}\\fscy{_fmt1(p0)}"
        target += f"\\fscx{_fmt1(p1)}\\fscy{_fmt1(p1)}"
    return f"{base}\\t(0,{_seg_ms(t0, t1)},{target})"


def _merge_chains_with_hold(
    runs: List[List[int]],
    tmap: Dict[int, TrackedQuad],
    hold_sec: float,
) -> List[List[int]]:
    """``lost_hold_sec`` > 0 时,间隔时长 ≤ hold 的相邻链合并为一条
    (事件跨越 lost 间隔,期间保持前段轨迹)。"""
    if hold_sec <= 0 or len(runs) < 2:
        return runs
    merged: List[List[int]] = [list(runs[0])]
    for run in runs[1:]:
        prev = merged[-1]
        gap = tmap[run[0]].time_sec - tmap[prev[-1]].time_sec
        if gap <= hold_sec + 1e-9:
            merged[-1] = prev + run
        else:
            merged.append(list(run))
    return merged


def _is_exploded(
    chain: List[int],
    segs: List[Tuple[int, int]],
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
) -> bool:
    span = chain[segs[-1][1]] - chain[segs[0][0]] + 1
    if span / len(segs) < 2.0 * cfg.min_seg_frames:
        return True
    dur = tmap[chain[segs[-1][1]]].time_sec - tmap[chain[segs[0][0]]].time_sec
    per_sec = len(segs) / dur if dur > 1e-9 else float("inf")
    return per_sec > cfg.max_segments_per_sec


def _next_frame_time(
    tmap: Dict[int, TrackedQuad], frame: int, t0: float, dt: float,
) -> float:
    """链尾兜底:取轨迹里下一帧(任意状态)的时间,否则按中位帧距顺延。"""
    for f in range(frame + 1, frame + 32):
        tq = tmap.get(f)
        if tq is not None and tq.time_sec > t0:
            return tq.time_sec
    return t0 + dt


def _dense_events(
    chain: List[int],
    centers: np.ndarray,
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
    style: str,
    fs_h: int,
    text: str,
    x_offset: float = 0.0,
    video_height: Optional[float] = None,
) -> List[Dict]:
    """帧级 ``\\pos`` 兜底:每隔 dense_stride 个好帧一条事件,
    时间取该帧 time_sec 到下一取样好帧 time_sec。"""
    n = len(chain)
    stride = max(1, int(cfg.dense_stride))
    times = [tmap[f].time_sec for f in chain]
    if n >= 2:
        dts = sorted(b - a for a, b in zip(times, times[1:]))
        dt = dts[len(dts) // 2]
    else:
        dt = 0.0
    if not x_offset:
        x_offset = punct_comp_offset_px(
            text, fs_h, enabled=cfg.punct_comp_enabled,
            max_px=cfg.punct_comp_max_px, video_height=video_height)
    events: List[Dict] = []
    for k in range(0, n, stride):
        f0 = chain[k]
        t0 = times[k]
        t1 = times[min(k + stride, n - 1)]
        if t1 <= t0:
            t1 = _next_frame_time(tmap, f0, t0, dt)
        if t1 <= t0:
            continue  # 零长段丢弃
        tags = (f"{{\\an5\\fs{fs_h}"
                f"\\pos({_fmt1(centers[k][0] + x_offset)},{_fmt1(centers[k][1])})}}")
        events.append({
            "start_time": format_ass_time(t0),
            "end_time": format_ass_time(t1),
            "style": style,
            "name": "motion",
            "tags": tags,
            "body": text,
        })
    return events


def _chain_events(
    lt: LineTrack,
    chain: List[int],
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
    style: str,
    video_height: Optional[float] = None,
) -> List[Dict]:
    centers = np.array([lt.poses[f].center for f in chain], dtype=np.float64)
    angles = [lt.poses[f].angle_deg for f in chain]
    scales = [lt.poses[f].scale for f in chain]
    raw_segs, segs = _segment_indices(centers, angles, scales, cfg)
    if not segs:
        return []
    fs_h = int(round(lt.height))
    # 行尾全角标点字形补偿:渲染墨水整体左偏 ≤7px(见 punct_comp_offset_px),
    # x 端点统一右移补偿(mask 遮罩由 scene_text_policy 对同一偏移同步外扩)。
    x_off = punct_comp_offset_px(
        lt.text, fs_h, enabled=cfg.punct_comp_enabled,
        max_px=cfg.punct_comp_max_px, video_height=video_height)

    # 阶梯 5:段数爆炸 → 帧级 \pos 兜底(按原始 DP 分段判定;单段永不触发)
    if cfg.dense_pos_fallback and len(raw_segs) >= 2 and _is_exploded(chain, raw_segs, tmap, cfg):
        return _dense_events(chain, centers, tmap, cfg, style, fs_h, lt.text,
                             x_offset=x_off, video_height=video_height)

    events: List[Dict] = []
    multi = len(segs) > 1
    last = len(segs) - 1
    # 链尾(含单段)结束时间延伸到下一帧:ASS 的 End 是排他边界,取尾帧自身
    # 的 time_sec 会让最后一个 ok 帧整帧无字幕;与 dense 兜底/主流水线
    # generator 的「尾帧时间 + 1 帧距」语义对齐。
    chain_times = [tmap[f].time_sec for f in chain]
    if len(chain_times) >= 2:
        chain_dts = sorted(b - a for a, b in zip(chain_times, chain_times[1:]))
        chain_dt = chain_dts[len(chain_dts) // 2]
    else:
        chain_dt = 0.0
    for si, (ai, bi) in enumerate(segs):
        f0 = chain[ai]
        # 非末段的 end_frame = 下一段的 start_frame(共享边界帧)
        f1 = chain[segs[si + 1][0]] if (multi and si < last) else chain[bi]
        t0 = tmap[f0].time_sec
        t1 = tmap[f1].time_sec
        if si == last:
            # 链尾先延伸再判零长:单帧链 f1 == f0(帧号相等不算零长),
            # 直接判会把整条链丢成无事件;延伸后仍无正时长(时间戳重复)
            # 才丢弃。f1 < f0 不可能出现(分段下标严格递增)。
            t1 = _next_frame_time(tmap, f1, t1, chain_dt)
        if t1 <= t0:
            continue  # 零长段丢弃
        if multi:
            move = (f"\\move({_fmt1(centers[ai][0] + x_off)},{_fmt1(centers[ai][1])},"
                    f"{_fmt1(centers[bi][0] + x_off)},{_fmt1(centers[bi][1])})")
        else:
            move = (f"\\move({_fmt1(centers[ai][0] + x_off)},{_fmt1(centers[ai][1])},"
                    f"{_fmt1(centers[bi][0] + x_off)},{_fmt1(centers[bi][1])},"
                    f"0,{_seg_ms(t0, t1)})")
        tags = f"{{\\an5\\fs{fs_h}{move}"
        tags += _overlay_tags(angles[ai], angles[bi], scales[ai], scales[bi],
                              t0, t1, cfg)
        tags += "}"
        events.append({
            "start_time": format_ass_time(t0),
            "end_time": format_ass_time(t1),
            "style": style,
            "name": "motion",
            "tags": tags,
            "body": lt.text,
        })
    return events


def synthesize_events(
    line_tracks: Sequence[LineTrack],
    tracks: Sequence[TrackedQuad],
    cfg: MotionAssConfig,
    style: str = "Scene",
    *,
    video_height: Optional[float] = None,
) -> List[Dict]:
    """行轨迹 + 平面跟踪轨迹 → ASS 事件列表(标签阶梯)。

    每行独立处理:pose 按 lost 间隔切链(``lost_hold_sec`` > 0 时短间隔
    合并跨越);每条链先分段(单段直线/多段 ``\\move``),段数爆炸时整条
    链改帧级 ``\\pos`` 兜底。事件按行分组、行内按时间排序;不修改输入。
    ``video_height``(PlayResY)供行尾标点补偿的上限按分辨率缩放,缺省按
    ``punct_comp_max_px`` 原值封顶。事件携带 ``line_idx``(行下标,行序同
    输入),供逐行亮度适配等调用方回溯所属行;写入 .ass 时忽略。
    """
    tmap = _frame_map(tracks)
    events: List[Dict] = []
    for line_idx, lt in enumerate(line_tracks):
        frames = sorted(lt.poses)
        if not frames:
            continue
        chains = _merge_chains_with_hold(_split_runs(frames), tmap, cfg.lost_hold_sec)
        for chain in chains:
            for ev in _chain_events(lt, chain, tmap, cfg, style,
                                    video_height=video_height):
                ev["line_idx"] = line_idx
                events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 屏幕亮度自适应(增量特性;曲线来自 core.screen_luma,纯函数不碰视频 I/O)
# ---------------------------------------------------------------------------

def _interp_ratio(ts: Sequence[float], rs: Sequence[float], t: float) -> float:
    """折线在时刻 t 的插值比值;越界侧钳到端点(不外推)。"""
    if t <= ts[0]:
        return rs[0]
    if t >= ts[-1]:
        return rs[-1]
    idx = bisect_left(ts, t)
    t1, t2 = ts[idx - 1], ts[idx]
    r1, r2 = rs[idx - 1], rs[idx]
    if t2 <= t1 + 1e-12:
        return r1
    return r1 + (r2 - r1) * (t - t1) / (t2 - t1)


def simplify_luma_curve(
    curve: List[Tuple[float, float]],
    tol_luma: float = 8.0,
    baseline_luma: float = 247.0,
) -> List[Tuple[float, float]]:
    """对 ``(time, ratio)`` 亮度折线做 Douglas-Peucker 简化。

    容差由亮度级换算:``tol_ratio = tol_luma / baseline_luma``;偏差按
    「还原亮度后」的值偏差计(``luma = baseline * ratio``,等价于比值空间内
    对段的垂直偏差),与时间轴尺度无关。首末点恒保留,容差内的过渡抖动
    舍弃、拐点保留;输入按 time 升序,不修改输入。
    """
    out = [(float(t), float(r)) for t, r in curve]
    n = len(out)
    if n < 3:
        return out
    ts = [t for t, _r in out]
    rs = [r for _t, r in out]
    tol = abs(float(tol_luma)) / abs(float(baseline_luma)) if baseline_luma else 0.0
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        span = ts[j] - ts[i]
        k, dmax = -1, -1.0
        for m in range(i + 1, j):
            r_line = (rs[i] + (rs[j] - rs[i]) * (ts[m] - ts[i]) / span
                      if span > 1e-12 else rs[i])
            d = abs(rs[m] - r_line)
            if d > dmax:
                k, dmax = m, d
        if k >= 0 and dmax > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [out[idx] for idx in range(n) if keep[idx]]


def brightness_tag_chain(
    curve: List[Tuple[float, float]],
    ev_start: float,
    ev_end: float,
    *,
    use_color: bool = True,
    use_alpha: bool = True,
    base_color: Optional[Tuple[int, int, int]] = None,
) -> str:
    """把全局亮度关键帧折线换算成一条事件的局部标签串(不含最外层大括号)。

    - 事件起点处插值得到 r0:基值标签 ``\\1c&H..&``(缺省 ``base_color`` 时
      灰度 g=round(255·r0),三通道同值;给定 BGR ``base_color`` 时按通道
      缩放 ``round(c·r0)``)+ ``\\alpha&H..&``(a=round(255·(1-r0)),00=不透明);
    - 对每一段落在 ``(ev_start, ev_end)`` 内的相邻关键帧区间 [t1,t2] 各输出
      一个 ``\\t(ms1,ms2,\\1c..\\alpha..)``,ms 相对事件开始取整,相邻端点
      相接;区间与事件跨度求交集,交集 <1ms 丢弃;段目标值 = 段终点时间的
      插值比值;``use_color``/``use_alpha`` 为 False 时省略对应部分,两者都
      False 返回 "";
    - 曲线恒为 1.0(所有点比值==1.0),或事件跨度内无任何有效变化
      (r0==1.0 且所有段落入跨度部分的目标值也全为 1.0)→ 返回 ""
      (不加任何标签)。
    """
    if not curve or ev_end <= ev_start or not (use_color or use_alpha):
        return ""
    ts = [float(t) for t, _r in curve]
    rs = [float(r) for _t, r in curve]
    if all(r == 1.0 for r in rs):
        return ""

    def fmt_tags(ratio: float) -> str:
        ratio = min(1.0, max(0.0, ratio))
        out = ""
        if use_color:
            if base_color is None:
                g = int(round(255.0 * ratio))
                out += f"\\1c&H{g:02X}{g:02X}{g:02X}&"
            else:  # 给定基色:按通道缩放(遮罩与字幕一起调暗)
                channels = [int(round(float(c) * ratio)) for c in base_color]
                out += "\\1c&H{0:02X}{1:02X}{2:02X}&".format(*channels)
        if use_alpha:
            a = int(round(255.0 * (1.0 - ratio)))
            out += f"\\alpha&H{a:02X}&"
        return out

    ev_start = float(ev_start)
    ev_end = float(ev_end)
    r0 = _interp_ratio(ts, rs, ev_start)
    changed = r0 != 1.0
    parts = [fmt_tags(r0)]
    for (t1, _r1), (t2, _r2) in zip(curve, curve[1:]):
        lo = max(t1, ev_start)
        hi = min(t2, ev_end)
        if hi - lo < 0.001:  # 交集 <1ms 丢弃
            continue
        target = _interp_ratio(ts, rs, hi)
        if target != 1.0:
            changed = True
        ms1 = int(round((lo - ev_start) * 1000))
        ms2 = int(round((hi - ev_start) * 1000))
        parts.append(f"\\t({ms1},{ms2},{fmt_tags(target)})")
    if not changed:
        return ""  # 跨度内恒亮:基值与 \t 都是空操作
    return "".join(parts)
