# tests/test_occlusion_timeline.py
"""B2:遮挡蒙版时间局部化原语(core.occlusion_timeline)单元测试。"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import LinePose, LineTrack, MotionAssConfig  # noqa: E402
from core.occlusion_timeline import (  # noqa: E402
    frame_end_sec,
    pose_events_for_frames,
    sample_for_frame,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FPS = 25.0


# ---------------------------------------------------------------------------
# sample_for_frame
# ---------------------------------------------------------------------------

def test_nearest_sample_preserves_clear_and_unknown():
    samples = {100: [], 103: ["polygon"], 106: [], 109: None}
    assert sample_for_frame(100, samples, 1) == 100
    assert sample_for_frame(103, samples, 1) == 103
    assert sample_for_frame(106, samples, 1) == 106
    # 最近样本未知(None)→ 直接 None,不越过未知点找更远命中。
    assert sample_for_frame(109, samples, 1) is None
    assert sample_for_frame(120, samples, 1) is None


def test_sample_tie_takes_earlier_frame():
    samples = {100: [], 104: ["poly"]}
    assert sample_for_frame(102, samples, 2) == 100
    # 103 距 104 更近(距离 1 < 3):最近样本是命中 → 返回 104。
    assert sample_for_frame(103, samples, 2) == 104


def test_sample_tie_breaks_to_nearest_hit():
    # 102 与 100/104 等距:平局取较早(100)。
    samples = {100: ["p"], 104: ["p"]}
    assert sample_for_frame(102, samples, 2) == 100


def test_sample_distance_bound():
    samples = {100: ["p"]}
    assert sample_for_frame(100, samples, 0) == 100
    assert sample_for_frame(101, samples, 0) is None


# ---------------------------------------------------------------------------
# pose_events_for_frames
# ---------------------------------------------------------------------------

def _make_tracks(n, dx_per_sec=0.0, w=320, h=120):
    tracks = []
    for i in range(n):
        t = i / FPS
        x = dx_per_sec * t
        hom = np.array([[1.0, 0.0, x], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=t, status="ok",
            quad=[[0, 0], [1, 0], [1, 1], [0, 1]],
            homography=[[float(v) for v in row] for row in hom],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(hom)]))
    return tracks


def _moving_line_track(n, speed_px_s=10.0, box=(100.0, 40.0, 200.0, 60.0)):
    poses = {}
    for i in range(n):
        t = i / FPS
        poses[i] = LinePose(center=(150.0 + speed_px_s * t, 50.0),
                            angle_deg=0.0, scale=1.0)
    return LineTrack(text="TEST", ref_box=box, height=20.0, poses=poses)


def test_pose_events_cover_span_and_preserve_fields():
    n = 50  # 2s
    tracks = _make_tracks(n)
    lt = _moving_line_track(n, speed_px_s=10.0)
    events = pose_events_for_frames(lt, tracks, 0.0, 2.0,
                                    MotionAssConfig(), "center", 3)
    assert events, "per-frame events must be produced"
    assert events[0]["start_time"] == "0:00:00.00"
    assert abs(_cs(events[-1]["end_time"]) - 2.0) <= 0.04
    for ev in events:
        assert ev["line_idx"] == 3
        assert ev["name"] == "motion"
        assert "\\move(" not in ev["tags"]
        assert "\\pos(" in ev["tags"]
        assert "_frames" in ev
    # 10px/s 匀速运动在 1.2s 处的位置 ≈ 150 + 12 = 162。
    target = [ev for ev in events
              if _cs(ev["start_time"]) <= 1.2 < _cs(ev["end_time"])]
    assert target, "1.2s must be covered"
    import re

    m = re.search(r"\\pos\(([\d.]+),([\d.]+)\)", target[0]["tags"])
    assert abs(float(m.group(1)) - 162.0) <= 1.0


def test_pose_events_static_frames_merge():
    n = 30
    tracks = _make_tracks(n)
    lt = _moving_line_track(n, speed_px_s=0.0)
    events = pose_events_for_frames(lt, tracks, 0.0, 1.2,
                                    MotionAssConfig(), "center", 0)
    # 完全静止:量化前合并后只应有一条事件。
    assert len(events) == 1
    assert len(events[0]["_frames"]) == n


def test_pose_events_slice_boundary_position_continuity():
    """在绝对 1.2s 处切片,前后事件的位置连续(≤1px)。"""
    n = 60  # 2.4s
    tracks = _make_tracks(n)
    lt = _moving_line_track(n, speed_px_s=10.0)
    events = pose_events_for_frames(lt, tracks, 0.0, 2.4,
                                    MotionAssConfig(), "center", 0)
    import re

    def pos_at(ev):
        m = re.search(r"\\pos\(([\d.]+),([\d.]+)\)", ev["tags"])
        return float(m.group(1)), float(m.group(2))

    for a, b in zip(events, events[1:]):
        # 相邻事件端点位置差应与帧距×速度一致(10px/s × ~0.04s = 0.4px)。
        (ax, _ay), (bx, _by) = pos_at(a), pos_at(b)
        dt = _cs(b["start_time"]) - _cs(a["start_time"])
        assert abs((bx - ax) - 10.0 * dt) <= 1.0


def test_pose_events_reject_quantization_collapse():
    # 单帧跨度:量化后 start==end → 明确失败,不静默丢弃。
    tracks = _make_tracks(2)
    lt = _moving_line_track(2)
    with pytest.raises(ValueError):
        pose_events_for_frames(lt, tracks, 0.0, 0.0,
                               MotionAssConfig(), "center", 0)


def test_frame_end_sec_clamps_to_span():
    times = {i: i / FPS for i in range(50)}
    ok = sorted(times)
    assert frame_end_sec(10, times, ok, 2.4) == pytest.approx(11 / FPS)
    # 末帧:相邻帧距外推并夹到 end_sec(1.96 + 0.04 = 2.00)。
    assert frame_end_sec(49, times, ok, 3.0) == pytest.approx(2.0)
    assert frame_end_sec(49, times, ok, 1.98) == pytest.approx(1.98)


def _cs(value):
    h, m, s = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)
