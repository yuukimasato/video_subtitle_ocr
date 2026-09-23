# core/occlusion_timeline.py
r"""B2:遮挡蒙版的时间局部化原语(采样选择 + 逐帧姿态事件)。

V3/V2 评审确认的两个时间风险:
- 旧 ``attach_occlusion_clips`` 把首末命中蒙版的动画铺到整个事件跨度
  ——遮挡出现前的清晰帧也被裁;
- 逐帧离散切片必须从**当前帧**的单应与采样证据重建 ``pos/frz/scale/fs``
  与 ``\iclip``,切分后的事件各自携带绝对时间,禁止复制相对 ``\t`` 再改
  事件起点。

:func:`sample_for_frame` —— 为任意帧选最近的**已计划**采样(平局取较早
的帧);先选最近再判 ``None``:未知点就近阻断,不得越过未知点去找更远的
命中;超距返回 ``None``。

:func:`pose_events_for_frames` —— 单行在 ``[start_sec, end_sec)`` 内的逐
帧姿态事件(与 :func:`core.motion_ass._chain_events` 的 dense 兜底共享同
一份单帧标签构造):每帧 ``\\an\\fs\\pos``(+ 显著角度/缩放的绝对标签,
不写 ``\\t`` 插值);相邻同属性帧在量化前合并;末帧由相邻帧时间差补结束
并夹到原区间;量化坍缩抛 :class:`ValueError`(调用方走有证据的保守回退,
不静默丢弃、不自动延长)。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from core.motion_ass import (
    LineTrack,
    MotionAssConfig,
    _fmt1,
    _static_frame_tags,
    format_ass_time,
    punct_comp_offset_px,
)
from core.scene_plane_tracker import TrackedQuad

__all__ = [
    "sample_for_frame",
    "frame_end_sec",
    "pose_events_for_frames",
]


def sample_for_frame(
    frame: int,
    samples: Dict[int, Optional[list]],
    max_distance: int,
) -> Optional[int]:
    """选距 ``frame`` 最近的已计划采样帧;无可用样本返回 ``None``。

    - 平局(左右等距)取较早的帧(键序小者);
    - **先选最近再判值**:最近样本是 ``None``(未知/失败)时直接返回
      ``None``,不允许跳过未知点改用更远的命中;
    - 距离超过 ``max_distance`` 返回 ``None``。
    """
    if not samples:
        return None
    key = min(samples, key=lambda f: (abs(f - frame), f))
    if abs(key - frame) > max_distance or samples[key] is None:
        return None
    return key


def frame_end_sec(
    frame: int,
    times: Dict[int, float],
    ok_frames: Sequence[int],
    end_sec: float,
) -> float:
    """帧事件的排他结束时间:下一 ok 帧时间;末帧用相邻帧距外推并夹到
    ``end_sec``(不越过原事件边界)。"""
    idx = ok_frames.index(frame)
    if idx + 1 < len(ok_frames):
        nxt = float(times[ok_frames[idx + 1]])
        if nxt > float(times[frame]):
            return min(nxt, float(end_sec))
    prev_dt = 0.0
    if idx >= 1:
        prev_dt = float(times[frame]) - float(times[ok_frames[idx - 1]])
    est = float(times[frame]) + max(0.0, prev_dt)
    return min(est, float(end_sec))


def pose_events_for_frames(
    line_track: LineTrack,
    tracks: Sequence[TrackedQuad],
    start_sec: float,
    end_sec: float,
    cfg: MotionAssConfig,
    alignment: str,
    line_idx: int,
    *,
    video_height: Optional[float] = None,
) -> List[Dict]:
    """逐帧姿态事件(遮挡离散切片的几何骨架,无 clip 标签)。

    每个输出事件的 ``tags`` 只含该帧自身的 ``\\an\\fs\\pos``(+ 显著角度
    ``\\frz`` / 缩放 ``\\fscx\\fscy`` 的**绝对**标签,与
    :func:`core.motion_ass._chain_events` dense 兜底同一构造);``_frames``
    记录合并进该事件的原始帧号(量化前相邻同属性帧合并),供调用方按帧
    插入 clip 后再细分。末帧结束时间 = 相邻帧时间差外推,夹到 ``end_sec``。
    任何事件量化后 ``end <= start`` 抛 :class:`ValueError`(调用方走有证据
    的保守回退)。
    """
    tmap = {t.frame_num: t for t in tracks}
    ok_frames = sorted(
        f for f, t in tmap.items()
        if t.status == "ok" and t.time_sec is not None
        and start_sec - 1e-6 <= float(t.time_sec) <= end_sec + 1e-6
        and f in line_track.poses)
    if not ok_frames:
        return []

    fs_h = int(round(line_track.height))
    x_off = punct_comp_offset_px(
        line_track.text, fs_h, enabled=cfg.punct_comp_enabled,
        max_px=cfg.punct_comp_max_px, video_height=video_height)
    box_w = float(line_track.ref_box[2]) - float(line_track.ref_box[0])
    times = {f: float(tmap[f].time_sec) for f in ok_frames}

    def _frame_tags(f: int) -> str:
        pose = line_track.poses[f]
        # 与 core.motion_ass 的 dense 兜底共享同一单帧标签构造;显著的
        # 角度/缩放以绝对标签逐帧重建(切片后无 \\t 插值可复制)。
        tags = _static_frame_tags(pose.center, pose.angle_deg, pose.scale,
                                  box_w, alignment, x_off, fs_h)
        if abs(float(pose.angle_deg)) > 0.05:
            tags = tags[:-1] + f"\\frz{_fmt1(pose.angle_deg)}}}"
        if abs(float(pose.scale) - 1.0) > 0.001:
            pct = float(pose.scale) * 100.0
            tags = tags[:-1] + f"\\fscx{_fmt1(pct)}\\fscy{_fmt1(pct)}}}"
        return tags

    # 量化前合并:相邻同属性帧(tags 完全一致)并入同一事件。
    groups: List[List[int]] = []
    for f in ok_frames:
        if groups and _frame_tags(groups[-1][-1]) == _frame_tags(f):
            groups[-1].append(f)
        else:
            groups.append([f])

    events: List[Dict] = []
    for gi, group in enumerate(groups):
        first, last = group[0], group[-1]
        if gi + 1 < len(groups):
            t1 = times[groups[gi + 1][0]]
        else:
            t1 = frame_end_sec(last, times, ok_frames, float(end_sec))
        t0 = times[first]
        events.append({
            "start_time": format_ass_time(t0),
            "end_time": format_ass_time(t1),
            "style": "Scene",
            "name": "motion",
            "tags": _frame_tags(first),
            "body": line_track.text,
            "line_idx": int(line_idx),
            "_frames": tuple(group),
        })
    collapsed = [e for e in events
                 if _parse_cs(e["end_time"]) <= _parse_cs(e["start_time"])]
    if collapsed:
        raise ValueError(
            "pose_events_for_frames: events collapsed after centisecond "
            f"quantization ({len(collapsed)}/{len(events)}); refusing to "
            "silently drop or extend them")
    return events


def _parse_cs(value: str) -> float:
    h, m, rest = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)
