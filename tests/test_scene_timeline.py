# tests/test_scene_timeline.py
"""A2:活动行集合时间切片(core.scene_timeline)的单元测试。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from core.scene_timeline import ActiveSlice, TimedRow, active_slices  # noqa: E402


def test_active_rows_do_not_leak_to_other_times():
    rows = [TimedRow(0, 0, 200), TimedRow(1, 100, 300), TimedRow(2, 400, 500)]
    assert active_slices(rows) == [
        ActiveSlice(0, 100, (0,)),
        ActiveSlice(100, 200, (0, 1)),
        ActiveSlice(200, 300, (1,)),
        ActiveSlice(400, 500, (2,)),
    ]


def test_empty_input_returns_empty():
    assert active_slices([]) == []


def test_single_row_single_slice():
    assert active_slices([TimedRow(3, 50, 150)]) == [ActiveSlice(50, 150, (3,))]


def test_gap_produces_no_slice():
    rows = [TimedRow(0, 0, 100), TimedRow(0, 200, 300)]
    assert active_slices(rows) == [
        ActiveSlice(0, 100, (0,)),
        ActiveSlice(200, 300, (0,)),
    ]


def test_adjacent_same_identity_slices_merge():
    # 两个相邻区间、同一身份:扫描时在边界处合并为同一切片。
    rows = [TimedRow(0, 0, 100), TimedRow(0, 100, 200)]
    assert active_slices(rows) == [ActiveSlice(0, 200, (0,))]


def test_same_row_id_overlapping_instances_use_counter():
    # 同 row_id 的重叠实例:计数器处理,一个实例结束不误删另一个。
    rows = [TimedRow(0, 0, 200), TimedRow(0, 100, 300)]
    assert active_slices(rows) == [ActiveSlice(0, 300, (0,))]


def test_non_positive_duration_raises():
    with pytest.raises(ValueError):
        active_slices([TimedRow(0, 100, 100)])
    with pytest.raises(ValueError):
        active_slices([TimedRow(0, 200, 100)])


def test_row_ids_sorted_in_slice():
    rows = [TimedRow(2, 0, 100), TimedRow(0, 0, 100), TimedRow(1, 0, 100)]
    assert active_slices(rows) == [ActiveSlice(0, 100, (0, 1, 2))]


def test_boundary_identity_changes_split_slices():
    # 行 0 在 100 处离场、行 1 继续到 200:身份集合在边界处切换。
    rows = [TimedRow(0, 0, 100), TimedRow(1, 0, 200)]
    assert active_slices(rows) == [
        ActiveSlice(0, 100, (0, 1)),
        ActiveSlice(100, 200, (1,)),
    ]
