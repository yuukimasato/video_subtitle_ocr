# tests/test_chunk_planner.py
"""Unit tests for the chunk planner (pure functions: no video, no engines).

The planner decides whether a video is worth splitting into per-worker
windows (multiprocessing chunk-parallel OCR) and, if so, how. Hard rules
under test:

- GPU mode never splits (batching is the GPU lever, not processes).
- Short videos bypass splitting entirely (spawn/engine startup overhead
  would outweigh the gain).
- Worker count is capped by CPU cores, RAM budget and window count.
- Core windows partition the timeline exactly (no gaps, no overlap);
  only the surrounding *work* windows overlap, clamped at video bounds.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.chunk_planner import ChunkWindow, plan_chunks, owner_window


def _plan(total_frames, fps=30, cores=8, ram_mb=8192, gpu="cpu", **kw):
    return plan_chunks(
        total_frames,
        fps,
        cpu_count=cores,
        available_ram_mb=ram_mb,
        gpu_mode=gpu,
        **kw,
    )


# ── bypass cases ──────────────────────────────────────────────────────


def test_gpu_mode_returns_none():
    # 24 min of frames, plenty of cores/RAM — but GPU is a batching job.
    assert _plan(43200, gpu="gpu") is None


def test_short_video_bypasses():
    # 9:59 @30fps just under the 10-minute bypass threshold.
    assert _plan(599 * 30) is None


def test_single_core_bypasses():
    assert _plan(43200, cores=1) is None


def test_ram_too_small_for_two_engines_bypasses():
    # 600MB budget fits exactly one engine -> no parallelism possible.
    assert _plan(43200, ram_mb=600) is None
    assert _plan(43200, ram_mb=599) is None


def test_invalid_fps_returns_none():
    assert _plan(43200, fps=0) is None
    assert _plan(43200, fps=-30) is None


def test_tiny_frame_count_returns_none():
    assert _plan(0) is None


# ── worker count caps ─────────────────────────────────────────────────


def test_ram_budget_caps_workers():
    # 1500MB // 600MB = 2 engines, even with 8 cores.
    plan = _plan(43200, ram_mb=1500)
    assert plan is not None
    assert plan.workers == 2
    assert len(plan.windows) == 2


def test_window_count_caps_workers():
    # 10 min @30fps, 240s target window -> ceil(600/240)=3 windows.
    plan = _plan(600 * 30)
    assert plan is not None
    assert plan.workers == 3
    assert len(plan.windows) == 3


def test_core_count_caps_workers():
    # 4 cores, 16GB RAM, 24 min, 240s windows -> 6 windows but only 4 workers.
    plan = _plan(43200, cores=4)
    assert plan is not None
    assert plan.workers == 4


# ── window geometry ───────────────────────────────────────────────────


def test_core_windows_partition_timeline_exactly():
    # 601s @10fps = 6010 frames / 3 windows -> remainder on some window.
    plan = _plan(6010, fps=10)
    assert plan is not None and len(plan.windows) == 3
    pos = 0
    for w in plan.windows:
        assert w.core_start_frame == pos
        pos = w.core_end_frame
    assert pos == 6010


def test_work_windows_overlap_and_clamp():
    plan = _plan(43200)  # 24 min @30fps -> 6 windows, overlap 15s = 450 frames
    assert plan is not None
    frames = plan.windows
    assert frames[0].work_start_frame == 0
    assert frames[-1].work_end_frame == 43200
    for i, w in enumerate(frames):
        assert w.work_start_frame <= w.core_start_frame < w.core_end_frame <= w.work_end_frame
        if i > 0:
            expected = max(0, w.core_start_frame - 450)
            assert w.work_start_frame == expected
        if i < len(frames) - 1:
            expected = min(43200, w.core_end_frame + 450)
            assert w.work_end_frame == expected


def test_grab_start_has_one_second_preroll():
    plan = _plan(43200)
    assert plan is not None
    second = 30
    for w in plan.windows:
        assert w.grab_start_frame == max(0, w.work_start_frame - second)
        assert w.grab_start_frame <= w.work_start_frame


def test_overlap_can_exceed_neighbor_core_when_windows_are_thin():
    # 10 min in 3 windows -> core 2000 frames wide, overlap 450 < core width.
    # Thin windows: force a 2-window plan on a 20 min video then shrink the
    # target so overlap (450) is still smaller than cores; the real guard is
    # that work windows may overlap more than one neighbor but ownership
    # stays unique — verified via owner_window below.
    plan = _plan(1200 * 30, cores=2, ram_mb=1200)
    assert plan is not None and plan.workers == 2
    w0, w1 = plan.windows
    assert w0.work_end_frame == min(36000, w0.core_end_frame + 450)
    assert w1.work_start_frame == max(0, w1.core_start_frame - 450)


# ── ownership rule (used by the seam merger) ──────────────────────────


def test_owner_window_by_core_range():
    plan = _plan(43200)
    assert plan is not None
    for w in plan.windows:
        mid = (w.core_start_frame + w.core_end_frame) // 2
        assert owner_window(plan, mid) is w
    # Frame 0 belongs to the first window, last frame to the last window.
    assert owner_window(plan, 0) is plan.windows[0]
    assert owner_window(plan, 43199) is plan.windows[-1]


def test_owner_window_inside_overlap_belongs_to_core_owner():
    # A frame in window 1's leading overlap sits inside window 0's core.
    plan = _plan(43200)
    assert plan is not None
    w0, w1 = plan.windows[0], plan.windows[1]
    probe = w1.work_start_frame  # strictly inside w0's core
    assert w0.core_start_frame <= probe < w0.core_end_frame
    assert owner_window(plan, probe) is w0


def test_plan_is_picklable():
    import pickle

    plan = _plan(43200)
    assert plan is not None
    clone = pickle.loads(pickle.dumps(plan))
    assert clone == plan
