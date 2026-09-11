# core/chunk_planner.py
"""Decide whether and how to split a video into chunk-parallel OCR windows.

Pure geometry/logic module: no video access, no engines, no Qt. The caller
(ChunkParallelRunner) feeds probed facts — total frame count, fps, CPU count,
available RAM, GPU mode — and either gets ``None`` (run the single-process
path) or a :class:`ChunkPlan` of windows.

Rules (mirrors docs/superpowers/specs/2026-09-12-chunk-parallel-speedup-design.md):

- GPU mode never splits: batched inference is the GPU lever, extra processes
  would only duplicate VRAM/engine state.
- Videos shorter than ``min_total_s`` bypass splitting: per-worker engine
  startup (spawn + Paddle import) would outweigh the gain.
- ``workers = min(cpu_count, available_ram // engine_budget, window_count)``
  with a floor of 1; fewer than 2 means "not worth it" (bypass).
- Core windows partition the timeline exactly (no gap, no overlap). Each is
  padded with an ``overlap_s`` *work* window (clamped at video bounds) so any
  subtitle's fade-in/out boundary — including the refinement probe window of
  up to ``fps*2`` frames — falls fully inside at least one worker's window.
  ``grab_start_frame`` adds a 1s decode preroll to absorb seek imprecision.

Frame ranges are half-open: ``[start, end)``. Frame numbers stay global, so
the seam merger can deduplicate by ``(roi_identifier, frame_num)``.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ChunkWindow:
    """One worker's slice of the timeline.

    ``core_*`` is the frame range this window OWNS (results used in the
    merge); ``work_*`` is what it actually extracts/OCRs (core padded with
    overlap); ``grab_start_frame`` is where decoding starts (preroll).
    """

    index: int
    core_start_frame: int
    core_end_frame: int  # exclusive
    grab_start_frame: int
    work_start_frame: int
    work_end_frame: int  # exclusive

    @property
    def core_frames(self) -> int:
        return self.core_end_frame - self.core_start_frame


@dataclass(frozen=True)
class ChunkPlan:
    windows: Tuple[ChunkWindow, ...]
    workers: int
    total_frames: int


def plan_chunks(
    total_frames: int,
    fps: float,
    *,
    cpu_count: int,
    available_ram_mb: int,
    gpu_mode: str,
    window_target_s: float = 240.0,
    overlap_s: float = 15.0,
    min_total_s: float = 600.0,
    engine_mem_budget_mb: int = 600,
    preroll_s: float = 1.0,
    max_workers: int = 0,
) -> Optional[ChunkPlan]:
    """Return a ChunkPlan, or None when the video should run single-process.

    ``max_workers`` (0 = uncapped) is a user cap on the auto worker count;
    the plan still respects cores/RAM/window limits, so it is an upper bound,
    never a force.

    Window sizing note (measured on the 720p benchmark workload): small
    crops + tiny models scale poorly with intra-process threads beyond
    ~4 (240s window: 57s@1thr / 36s@2thr / 28s@4thr), while every spawned
    worker costs a full paddle import + model init (~15-25s). Few, fat
    windows beat many thin ones once startup dominates — 90s/16-worker
    measured ~1.6x slower than 240s/6-worker.
    """
    if gpu_mode and str(gpu_mode).lower() != "cpu":
        return None
    if not fps or fps <= 0:
        return None
    if total_frames < 1:
        return None

    duration_s = total_frames / float(fps)
    if duration_s < min_total_s:
        return None

    window_count = max(1, int(math.ceil(duration_s / float(window_target_s))))
    ram_workers = int(available_ram_mb // engine_mem_budget_mb)
    workers = max(1, min(int(cpu_count or 1), ram_workers, window_count))
    if max_workers and max_workers > 0:
        workers = min(workers, int(max_workers))
    if workers < 2:
        return None

    # Partition core frames: first `rem` windows take one extra frame.
    base, rem = divmod(total_frames, workers)
    overlap_frames = int(round(overlap_s * fps))
    preroll_frames = int(round(preroll_s * fps))

    windows = []
    start = 0
    for i in range(workers):
        span = base + (1 if i < rem else 0)
        core_start, core_end = start, start + span
        start = core_end
        windows.append(ChunkWindow(
            index=i,
            core_start_frame=core_start,
            core_end_frame=core_end,
            grab_start_frame=max(0, max(0, core_start - overlap_frames) - preroll_frames),
            work_start_frame=max(0, core_start - overlap_frames),
            work_end_frame=min(total_frames, core_end + overlap_frames),
        ))
    return ChunkPlan(windows=tuple(windows), workers=workers, total_frames=total_frames)


def owner_window(plan: ChunkPlan, frame_num: int) -> ChunkWindow:
    """Return the window whose CORE range contains ``frame_num``.

    This is the deterministic ownership rule for the seam merger: a frame
    extracted by two workers inside an overlap zone belongs to the window
    that owns it by core range — no content comparison involved.
    """
    frame = int(frame_num)
    if frame < 0 or frame >= plan.total_frames:
        raise ValueError(f"frame {frame_num} outside plan range [0, {plan.total_frames})")
    starts = [w.core_start_frame for w in plan.windows]
    idx = bisect.bisect_right(starts, frame) - 1
    return plan.windows[idx]
