# core/motion_ass.py
"""移动文字轨迹 → ASS 轨迹字幕合成器(纯函数,不依赖 PySide6)。

对应《移动文字轨迹 → ASS 轨迹字幕 设计》§4.1 / §5:

- :func:`build_line_tracks` —— 参考关键帧平面坐标的行框,经帧间单应链乘
  ``H(ref→t) = H(init→t)·inv(H(init→ref))`` 逐 ok 帧映射为行四边形,
  得到每行逐帧的 ``LinePose``(中心 / 角度 / 缩放);lost 帧无条目。
- :func:`smooth_line_track` —— 滑动平均去抖(窗口只数有 pose 的帧),
  只去高频抖动、不改缓慢运动趋势。
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
from typing import Dict, List, Sequence, Tuple

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
    rot_thresh_deg: float = 1.0     # 分段点/±t 叠加的角度阈值
    scale_thresh: float = 0.02      # 缩放变化阈值
    dense_pos_fallback: bool = True
    dense_stride: int = 2           # 兜底 \pos 每 N 好帧一条
    max_segments_per_sec: float = 6.0
    lost_hold_sec: float = 0.0      # lost 保持时长(0=切段)

    # —— 屏幕亮度自适应(增量特性;--auto-brightness 开启,--config-json 可覆盖)——
    brightness_tol: float = 8.0                   # 亮度曲线 DP 简化容差(0-255 亮度级)
    brightness_baseline_percentile: float = 90.0  # 亮度基线分位(各帧中位值的分位)
    brightness_use_color: bool = True             # \1c 颜色跟随
    brightness_use_alpha: bool = True             # \alpha 透明度跟随


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
    cs_total = int(float(sec) * 100)
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


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

def smooth_line_track(track: LineTrack, window: int = 5) -> None:
    """原地滑动平均 center/angle/scale;窗口(帧)只数有 pose 的帧。

    中心窗口 ``[f - window//2, f + window//2]`` 内的缺失(lost)帧自然跳过;
    角度先按序列展开(unwrap)再平均,避免 ±180° 边界跳变。窗口 <2 时不变。
    """
    if window is None or window < 2 or len(track.poses) < 2:
        return
    frames = sorted(track.poses)
    radius = max(1, int(window) // 2)
    angles = np.unwrap(np.deg2rad(
        [track.poses[f].angle_deg for f in frames]))
    xs = np.array([track.poses[f].center[0] for f in frames])
    ys = np.array([track.poses[f].center[1] for f in frames])
    scales = np.array([track.poses[f].scale for f in frames])

    new_poses: Dict[int, LinePose] = {}
    for i, f in enumerate(frames):
        lo = bisect_left(frames, f - radius)
        hi = bisect_right(frames, f + radius)
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
    events: List[Dict] = []
    for k in range(0, n, stride):
        f0 = chain[k]
        t0 = times[k]
        t1 = times[min(k + stride, n - 1)]
        if t1 <= t0:
            t1 = _next_frame_time(tmap, f0, t0, dt)
        if t1 <= t0:
            continue  # 零长段丢弃
        tags = f"{{\\an5\\fs{fs_h}\\pos({_fmt1(centers[k][0])},{_fmt1(centers[k][1])})}}"
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
) -> List[Dict]:
    centers = np.array([lt.poses[f].center for f in chain], dtype=np.float64)
    angles = [lt.poses[f].angle_deg for f in chain]
    scales = [lt.poses[f].scale for f in chain]
    raw_segs, segs = _segment_indices(centers, angles, scales, cfg)
    if not segs:
        return []
    fs_h = int(round(lt.height))

    # 阶梯 5:段数爆炸 → 帧级 \pos 兜底(按原始 DP 分段判定;单段永不触发)
    if cfg.dense_pos_fallback and len(raw_segs) >= 2 and _is_exploded(chain, raw_segs, tmap, cfg):
        return _dense_events(chain, centers, tmap, cfg, style, fs_h, lt.text)

    events: List[Dict] = []
    multi = len(segs) > 1
    last = len(segs) - 1
    for si, (ai, bi) in enumerate(segs):
        f0 = chain[ai]
        # 非末段的 end_frame = 下一段的 start_frame(共享边界帧)
        f1 = chain[segs[si + 1][0]] if (multi and si < last) else chain[bi]
        if f1 <= f0:
            continue  # 零长段丢弃
        t0 = tmap[f0].time_sec
        t1 = tmap[f1].time_sec
        if t1 <= t0:
            continue
        if multi:
            move = (f"\\move({_fmt1(centers[ai][0])},{_fmt1(centers[ai][1])},"
                    f"{_fmt1(centers[bi][0])},{_fmt1(centers[bi][1])})")
        else:
            move = (f"\\move({_fmt1(centers[ai][0])},{_fmt1(centers[ai][1])},"
                    f"{_fmt1(centers[bi][0])},{_fmt1(centers[bi][1])},"
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
) -> List[Dict]:
    """行轨迹 + 平面跟踪轨迹 → ASS 事件列表(标签阶梯)。

    每行独立处理:pose 按 lost 间隔切链(``lost_hold_sec`` > 0 时短间隔
    合并跨越);每条链先分段(单段直线/多段 ``\\move``),段数爆炸时整条
    链改帧级 ``\\pos`` 兜底。事件按行分组、行内按时间排序;不修改输入。
    """
    tmap = _frame_map(tracks)
    events: List[Dict] = []
    for lt in line_tracks:
        frames = sorted(lt.poses)
        if not frames:
            continue
        chains = _merge_chains_with_hold(_split_runs(frames), tmap, cfg.lost_hold_sec)
        for chain in chains:
            events.extend(_chain_events(lt, chain, tmap, cfg, style))
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
) -> str:
    """把全局亮度关键帧折线换算成一条事件的局部标签串(不含最外层大括号)。

    - 事件起点处插值得到 r0:基值标签 ``\\1c&H..&``(灰度 g=round(255·r0),
      三通道同值)+ ``\\alpha&H..&``(a=round(255·(1-r0)),00=不透明);
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
            g = int(round(255.0 * ratio))
            out += f"\\1c&H{g:02X}{g:02X}{g:02X}&"
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
