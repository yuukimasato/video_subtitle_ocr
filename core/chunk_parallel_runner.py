# core/chunk_parallel_runner.py
"""Coordinator for chunk-parallel OCR (long-video speedup).

Spawns one worker process per planned window (``core.chunk_worker``), drains
their message queue, rescales per-window progress onto the global axis, and
enforces the bounded failure policy from the design doc:

- a failed/stuck window is retried **once** with a fresh process;
- on the second failure the window falls back to sequential processing
  inside the coordinator process (same stage functions, same code path);
- a worker heartbeat silence beyond ``heartbeat_timeout_s`` counts as a
  failure (the worker emits heartbeats every 15s);
- cancellation is propagated through a shared Event and re-raised as
  ``PipelineCancelled`` — a worker failure never aborts the whole run.

Records from all windows are deduplicated by the seam merger
(``core.chunk_merger``) and returned as one frame-sorted stream, ready for
stage 4 (ASS generation + LLM polish), which stays in the coordinator
process so LLM calls remain single-point with bounded backoff.
"""
import logging
import multiprocessing as mp
import os
import queue as queue_mod
import time
from dataclasses import replace as dc_replace
from typing import Callable, Dict, List

from core.chunk_merger import merge_frame_records
from core.chunk_planner import ChunkPlan
from core.chunk_worker import chunk_worker_main, run_window_stages
from core.pipeline_stages import PipelineCancelled, PipelineContext

logger = logging.getLogger(__name__)

POLL_TIMEOUT_S = 0.5
HEARTBEAT_TIMEOUT_S = 300.0
MAX_RETRIES_PER_WINDOW = 1


def get_device_mode_light() -> str:
    """GPU detection without importing paddle.

    Mirrors ``ocr_processor.get_device_mode`` strategies 1-2 (MODE env var,
    ``.gpu_mode`` file) and replaces the runtime paddle check with an
    installed-package check, so planning never loads the engine into the
    coordinator process. Conservative default: "cpu".
    """
    import re

    env_mode = os.environ.get("MODE", "").strip().lower()
    if env_mode in ("gpu", "cpu"):
        return env_mode

    gpu_mode_file = os.path.join(os.path.dirname(__file__), "..", ".gpu_mode")
    try:
        if os.path.exists(gpu_mode_file):
            with open(gpu_mode_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.search(r'(?:export\s+)?MODE\s*=\s*["\']?(\w+)["\']?', line)
                    if m:
                        file_mode = m.group(1).strip().lower()
                        if file_mode in ("gpu", "cpu"):
                            return file_mode
    except Exception:
        pass

    try:
        from importlib import metadata
        metadata.version("paddlepaddle-gpu")
        return "gpu"
    except Exception:
        return "cpu"


def probe_available_ram_mb() -> int:
    """Best-effort available memory (Linux MemAvailable), conservative fallback."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return 2048


class _WindowState:
    __slots__ = ("window", "proc", "pct", "records", "done", "retries", "last_msg_ts")

    def __init__(self, window):
        self.window = window
        self.proc = None
        self.pct = 0
        self.records: List[tuple] = []
        self.done = False
        self.retries = 0
        self.last_msg_ts = time.monotonic()


def run_chunk_parallel(
    ctx: PipelineContext,
    plan: ChunkPlan,
    *,
    progress_cb: Callable[[int, str], None],
    cancel_check: Callable[[], bool],
    poll_timeout_s: float = POLL_TIMEOUT_S,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    mp_context=None,
    worker_target=chunk_worker_main,
) -> List[tuple]:
    """Run stages 1-3 across ``plan.workers`` processes; return merged records.

    ``mp_context``/``worker_target`` are injectable for tests (fork context +
    a fake worker) — production always uses spawn and the real worker.
    """
    if mp_context is None:
        mp_context = mp.get_context("spawn")
    spawn = mp_context
    msg_queue = spawn.Queue()
    cancel_event = spawn.Event()

    states: Dict[int, _WindowState] = {w.index: _WindowState(w) for w in plan.windows}
    total_weight = float(sum(w.core_frames for w in plan.windows))

    def _global_pct() -> int:
        acc = sum(st.pct * st.window.core_frames for st in states.values())
        return int(acc / total_weight)

    def _emit_progress():
        progress_cb(min(90, max(0, _global_pct())), "")

    def _launch(st: _WindowState) -> None:
        st.records = []
        st.done = False
        st.last_msg_ts = time.monotonic()
        payload = {
            "ctx": ctx,
            "window": st.window,
            "queue": msg_queue,
            "cancel_event": cancel_event,
        }
        proc = spawn.Process(
            target=worker_target, args=(payload,),
            name=f"ocr-chunk-{st.window.index}",
        )
        proc.start()
        st.proc = proc
        logger.info("chunk worker %d launched (pid=%s, frames %d..%d)",
                    st.window.index, proc.pid, st.window.core_start_frame,
                    st.window.core_end_frame)

    def _terminate_all() -> None:
        for st in states.values():
            if st.proc is not None and st.proc.is_alive():
                st.proc.terminate()
        for st in states.values():
            if st.proc is not None:
                st.proc.join(timeout=5)

    def _run_sequential(st: _WindowState) -> None:
        logger.warning("chunk worker %d: falling back to in-process sequential OCR",
                       st.window.index)
        w = st.window

        def seq_progress(pct: int, msg: str) -> None:
            st.pct = min(90, max(0, int(pct)))
            _emit_progress()

        st.records = run_window_stages(
            ctx, w, progress_cb=seq_progress, cancel_check=cancel_check)
        st.pct = 90
        st.done = True
        _emit_progress()

    def _fail(st: _WindowState) -> None:
        if st.done:
            return
        if st.proc is not None and st.proc.is_alive():
            st.proc.terminate()
            st.proc.join(timeout=5)
        if st.retries < MAX_RETRIES_PER_WINDOW:
            st.retries += 1
            logger.warning("chunk worker %d failed; retrying (attempt %d)",
                           st.window.index, st.retries + 1)
            _launch(st)
        else:
            _run_sequential(st)

    try:
        for st in states.values():
            _launch(st)

        pending = set(states)
        while pending:
            if cancel_check():
                raise PipelineCancelled()
            try:
                msg = msg_queue.get(timeout=poll_timeout_s)
            except queue_mod.Empty:
                now = time.monotonic()
                for idx in list(pending):
                    st = states[idx]
                    if st.done:
                        continue
                    alive = st.proc is not None and st.proc.is_alive()
                    silent = (now - st.last_msg_ts) > heartbeat_timeout_s
                    if (not alive) or silent:
                        if silent and alive:
                            logger.error("chunk worker %d heartbeat timeout", idx)
                        _fail(st)
                        if st.done:
                            pending.discard(idx)
                continue

            idx = msg.get("window")
            st = states.get(idx)
            if st is None or st.done:
                continue  # late message from a replaced/finished worker
            st.last_msg_ts = time.monotonic()
            mtype = msg.get("type")
            if mtype == "progress":
                st.pct = min(90, max(0, int(msg.get("pct", 0))))
                _emit_progress()
            elif mtype == "records":
                st.records.extend(msg.get("records", []))
            elif mtype == "done":
                st.done = True
                st.pct = 90
                _emit_progress()
                pending.discard(idx)
                logger.info("chunk worker %d done: %d records",
                            idx, len(st.records))
            elif mtype == "cancelled":
                st.done = True
                pending.discard(idx)
            elif mtype == "error":
                logger.error("chunk worker %d error: %s", idx, msg.get("error"))
                _fail(st)
                if st.done:
                    pending.discard(idx)
        return merge_frame_records(
            {idx: st.records for idx, st in states.items()}, plan)
    finally:
        _terminate_all()
        try:
            msg_queue.close()
        except Exception:
            pass
