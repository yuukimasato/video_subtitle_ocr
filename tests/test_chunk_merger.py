# tests/test_chunk_merger.py
"""Unit tests for the chunk-parallel seam merger.

Workers process overlapping windows of the same timeline, so frames inside
an overlap zone are OCR'd twice. The merger deduplicates by the global key
``(roi_identifier, frame_num)`` using the planner's core-range ownership
rule, and emits one deterministic, frame-sorted record stream for stage 4
(ASS generation) — the same shape the single-process path feeds it.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

from core.chunk_merger import merge_frame_records
from core.chunk_planner import plan_chunks


def _two_window_plan(total_frames=1000, fps=10):
    # 100s @10fps -> 2 windows of 50s, overlap 15s=150 frames.
    plan = plan_chunks(
        total_frames, fps,
        cpu_count=8, available_ram_mb=8192, gpu_mode="cpu",
        window_target_s=50.0, overlap_s=15.0, min_total_s=1.0,
    )
    assert plan is not None
    return plan


def _rec(frame_num, roi="roi_0", marker=None, time_sec=0.0):
    data = {"rec_texts": [marker if marker is not None else f"f{frame_num}"]}
    return (data, frame_num, roi, time_sec)


def _key(record):
    return (record[2], record[1])  # (roi_identifier, frame_num)


def test_owner_record_wins_in_overlap():
    plan = _two_window_plan()
    w0, w1 = plan.windows
    # A frame inside w0's core, covered by w1's leading work window too.
    frame = w1.work_start_frame + 10
    assert w1.work_start_frame <= frame < w1.core_start_frame
    assert w0.core_start_frame <= frame < w0.core_end_frame
    streams = {
        0: [_rec(frame, marker="owner")],
        1: [_rec(frame, marker="shadow")],
    }
    merged = merge_frame_records(streams, plan)
    assert len(merged) == 1
    assert merged[0][0]["rec_texts"] == ["owner"]


def test_core_records_pass_through():
    plan = _two_window_plan()
    w0, w1 = plan.windows
    core0 = w0.core_start_frame + 5
    core1 = w1.core_start_frame + 5
    merged = merge_frame_records(
        {0: [_rec(core0, marker="a")], 1: [_rec(core1, marker="b")]}, plan)
    assert [_key(r) for r in merged] == sorted([("roi_0", core0), ("roi_0", core1)])
    assert merged[0][0]["rec_texts"] == ["a"]
    assert merged[1][0]["rec_texts"] == ["b"]


def test_output_sorted_by_frame_then_roi():
    plan = _two_window_plan()
    w0, w1 = plan.windows
    f_mid = w0.core_start_frame + 50
    streams = {
        1: [_rec(f_mid + 1, roi="roi_B"), _rec(w1.core_start_frame, roi="roi_A")],
        0: [_rec(f_mid, roi="roi_B"), _rec(f_mid, roi="roi_A")],
    }
    merged = merge_frame_records(streams, plan)
    # Merger emits frame-major order: (frame_num, roi_identifier).
    keys = [(r[1], r[2]) for r in merged]
    assert keys == sorted(keys)


def test_multiple_rois_same_frame_all_kept():
    plan = _two_window_plan()
    w0 = plan.windows[0]
    frame = w0.core_start_frame + 7
    merged = merge_frame_records(
        {0: [_rec(frame, roi="roi_0"), _rec(frame, roi="roi_1")], 1: []}, plan)
    assert len(merged) == 2
    assert {r[2] for r in merged} == {"roi_0", "roi_1"}


def test_missing_owner_record_falls_back_to_neighbor():
    plan = _two_window_plan()
    w0, w1 = plan.windows
    frame = w0.core_start_frame + 10
    # Owner (w0) did not produce this frame (shouldn't happen; defensive).
    merged = merge_frame_records({1: [_rec(frame, marker="fallback")]}, plan)
    assert len(merged) == 1
    assert merged[0][0]["rec_texts"] == ["fallback"]


def test_duplicate_within_one_stream_rejected():
    plan = _two_window_plan()
    frame = plan.windows[0].core_start_frame + 3
    with pytest.raises(ValueError):
        merge_frame_records({0: [_rec(frame), _rec(frame)]}, plan)


def test_empty_streams_merge_to_empty():
    plan = _two_window_plan()
    assert merge_frame_records({}, plan) == []
    assert merge_frame_records({0: [], 1: []}, plan) == []


def test_time_sec_preserved_from_chosen_record():
    plan = _two_window_plan()
    w0 = plan.windows[0]
    frame = w0.core_start_frame + 2
    merged = merge_frame_records(
        {0: [_rec(frame, marker="o", time_sec=1.5)],
         1: [_rec(frame, marker="s", time_sec=9.9)]}, plan)
    assert merged[0][3] == 1.5
