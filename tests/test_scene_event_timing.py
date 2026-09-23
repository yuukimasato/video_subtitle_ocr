# tests/test_scene_event_timing.py
"""A1：同屏共存行不得被端点齐平截成零时长。

覆盖：
- Scene 同 ROI 同起点不同位置的行完整保留（真实 phone12 形态）；
- Default 相同 tags 的先后字幕仍做毫秒级端点齐平；
- 等起点（无先后依据）不再齐平；不同 ROI / 策略事件不触碰；
- 输入字典不被就地修改；合并链路不再把不同位置的同文行合并。
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.subtitle_generator import OCRToASSOptimizer


def _make_optimizer(tmp_path, fps=25):
    return OCRToASSOptimizer(
        video_path="unused",
        output_path=str(tmp_path / "out.ass"),
        fps=fps,
        width=1920,
        height=1080,
    )


def _scene_row(roi, start, end, y, body):
    return {
        "roi": roi, "style": "Scene",
        "start_time": start, "end_time": end,
        "tags": f"{{\\pos(100,{y})}}", "body": body,
    }


def _ch_event(roi, start, end, body, tags="", policy=None):
    ev = {
        "roi": roi, "style": "CH",
        "start_time": start, "end_time": end,
        "tags": tags, "body": body,
    }
    if policy:
        ev["policy"] = policy
    return ev


def test_scene_rows_coexist(tmp_path):
    opt = _make_optimizer(tmp_path)
    rows = [
        dict(roi="roi_0", style="Scene", start_time="0:00:01.00",
             end_time="0:00:01.12", tags=f"{{\\pos(100,{y})}}", body=t)
        for y, t in [(100, "first"), (200, "second")]
    ]
    out = opt._clamp_same_roi_event_overlaps(rows)
    assert [e["end_time"] for e in out] == ["0:00:01.12"] * 2
    # 输入字典不被就地修改（复制语义）。
    assert rows[0]["end_time"] == "0:00:01.12"


def test_scene_rows_survive_merge_chain(tmp_path):
    """同起点不同位置的 Scene 行经完整合并链仍各自保留原时长。"""
    opt = _make_optimizer(tmp_path)
    rows = [
        _scene_row("roi_0", "0:00:01.00", "0:00:01.12", 100, "first"),
        _scene_row("roi_0", "0:00:01.00", "0:00:01.12", 200, "second"),
    ]
    merged = opt._merge_temporal_near_duplicate_events(rows)
    assert len(merged) == 2
    assert {e["body"] for e in merged} == {"first", "second"}
    assert all(e["end_time"] == "0:00:01.12" for e in merged)


def test_default_same_tags_sequential_still_clamped(tmp_path):
    """传统字幕：后条开始严格晚于前条开始、tags 相同 → 毫秒重叠齐平。"""
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:01.00", "0:00:02.04", "甲", tags="{\\an2}"),
        _ch_event("roi_0", "0:00:02.00", "0:00:03.00", "甲", tags="{\\an2}"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:02.00"
    # 齐平后仍是正时长。
    assert opt._parse_ass_time_to_seconds(out[0]["end_time"]) > \
        opt._parse_ass_time_to_seconds(out[0]["start_time"])


def test_default_equal_start_not_clamped_to_zero(tmp_path):
    """等起点：没有先后依据，不截零。"""
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:57.09", "0:00:57.10", "好熱"),
        _ch_event("roi_0", "0:00:57.09", "0:01:04.60", "祝你生日快樂"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:57.10"


def test_different_roi_untouched(tmp_path):
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:01.00", "0:00:02.04", "甲", tags="{\\an2}"),
        _ch_event("roi_1", "0:00:02.00", "0:00:03.00", "乙", tags="{\\an2}"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:02.04"


def test_policy_events_not_clamped(tmp_path):
    """策略遮罩事件的时间由所属组推导，不做端点齐平。"""
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:01.00", "0:00:02.04", "", tags="{\\an2}",
                  policy="mask"),
        _ch_event("roi_0", "0:00:02.00", "0:00:03.00", "", tags="{\\an2}",
                  policy="mask"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:02.04"


def test_different_tags_not_clamped(tmp_path):
    """非 Scene 无明确通道：tags 不同（不同显示通道）不齐平。"""
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:01.00", "0:00:02.04", "甲", tags="{\\an8}"),
        _ch_event("roi_0", "0:00:02.00", "0:00:03.00", "乙", tags="{\\an2}"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:02.04"


def test_large_overlap_not_clamped(tmp_path):
    """真正跨条的事件（重叠 >0.2s）不触碰。"""
    opt = _make_optimizer(tmp_path)
    events = [
        _ch_event("roi_0", "0:00:01.00", "0:00:03.00", "甲", tags="{\\an2}"),
        _ch_event("roi_0", "0:00:02.50", "0:00:04.00", "甲", tags="{\\an2}"),
    ]
    out = opt._clamp_same_roi_event_overlaps(events)
    assert out[0]["end_time"] == "0:00:03.00"
