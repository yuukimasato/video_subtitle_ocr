# tests/test_trajectory_coverage_gate.py
"""轨迹接管覆盖门限(trajectory_takeover_ok)的单元测试。

背景:轨迹管线是单链设计,固定歌词带的平面跟踪常只在头一两行锁定
(「一周的朋友」OP 顶部繁中带 ok 率 14.8%、事件仅覆盖前 13.7s/92s),
低覆盖接管会抑制静态事件、产出大段漏字幕。门限之下整 ROI 回退静态路径。
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.pipeline_worker import (  # noqa: E402
    TRAJECTORY_MIN_EVENT_COVERAGE,
    trajectory_event_coverage_sec,
    trajectory_takeover_ok,
)

FPS = 24000 / 1001  # 23.976,与「一周的朋友」OP 一致
ROI_FRAMES = {"start_frame": 0, "end_frame": 2209}  # ≈92.13s


def _event(start: str, end: str) -> dict:
    return {"start_time": start, "end_time": end}


def test_coverage_union_of_adjacent_events():
    # 9.59-16.55 / 16.59-16.68 / 16.80-23.31 / 23.44-23.52(实跑产物形态):
    # 相邻段间只有小空隙,并集 ≈ 13.7s 而非逐段相加的重复计。
    events = [
        _event("0:00:09.59", "0:00:16.55"),
        _event("0:00:16.59", "0:00:16.68"),
        _event("0:00:16.80", "0:00:23.31"),
        _event("0:00:23.44", "0:00:23.52"),
    ]
    covered = trajectory_event_coverage_sec(events, FPS)
    assert 13.0 <= covered <= 14.5


def test_coverage_overlap_merged_not_double_counted():
    events = [
        _event("0:00:00.00", "0:00:10.00"),
        _event("0:00:05.00", "0:00:20.00"),  # 与前者重叠
    ]
    assert abs(trajectory_event_coverage_sec(events, FPS) - 20.0) < 1e-6


def test_coverage_empty_or_invalid_events():
    assert trajectory_event_coverage_sec([], FPS) == 0.0
    assert trajectory_event_coverage_sec([{"start_time": "x"}], FPS) == 0.0
    # end <= start 的退化事件不计入
    assert trajectory_event_coverage_sec(
        [_event("0:00:05.00", "0:00:05.00")], FPS) == 0.0


def test_takeover_rejected_below_threshold():
    # 实测「一周的朋友」形态:13.7s / 92.1s ≈ 15% → 拒绝接管,回退静态。
    events = [
        _event("0:00:09.59", "0:00:16.55"),
        _event("0:00:16.80", "0:00:23.31"),
    ]
    ok, coverage = trajectory_takeover_ok(events, ROI_FRAMES, FPS)
    assert not ok
    assert coverage < 0.2


def test_takeover_accepted_for_full_span_motion_text():
    # 真正跟随画面运动的文字(滚动屏等):事件覆盖接近全程。
    events = [_event("0:00:00.50", "0:01:31.50")]
    ok, coverage = trajectory_takeover_ok(events, ROI_FRAMES, FPS)
    assert ok
    assert coverage >= TRAJECTORY_MIN_EVENT_COVERAGE


def test_takeover_fail_open_on_bad_roi_or_fps():
    events = [_event("0:00:00.00", "0:00:01.00")]
    # ROI 帧范围非法 / fps 无效:判定按通过处理,不误杀轨迹结果。
    assert trajectory_takeover_ok(events, {"start_frame": "x", "end_frame": 5}, FPS)[0]
    assert trajectory_takeover_ok(events, ROI_FRAMES, 0.0)[0]
    assert trajectory_takeover_ok(events, ROI_FRAMES, None)[0]
    assert trajectory_takeover_ok(events, {}, FPS)[0]


def test_takeover_threshold_at_half():
    # 50% 边界:覆盖一半恰好通过(>= 门限)。
    half_sec = (2209 + 1) / FPS / 2
    minutes = int(half_sec // 60)
    seconds = half_sec - minutes * 60
    events = [_event("0:00:00.00", f"0:{minutes:02d}:{seconds:06.3f}")]
    ok, coverage = trajectory_takeover_ok(events, ROI_FRAMES, FPS)
    assert ok and abs(coverage - 0.5) < 0.01
