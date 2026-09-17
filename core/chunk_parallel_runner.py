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
    msg_queue = mp_context.Queue()
    cancel_event = mp_context.Event()

    # Paddle/MKLDNN sizes its thread pool to the whole machine by default;
    # N such instances thrash each other. Give each worker a fair slice of
    # the cores unless the caller pinned cpu_threads explicitly.
    worker_opts = dict(ctx.engine_options or {})
    if not worker_opts.get("cpu_threads"):
        worker_opts["cpu_threads"] = max(1, (os.cpu_count() or 4) // plan.workers)
    worker_ctx = dc_replace(ctx, engine_options=worker_opts)

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
            "ctx": worker_ctx,
            "window": st.window,
            "queue": msg_queue,
            "cancel_event": cancel_event,
            # Echoed back on every message so stale output from a killed
            # attempt can be told apart from the live one.
            "attempt": st.retries,
        }
        proc = mp_context.Process(
            target=worker_target, args=(payload,),
            name=f"ocr-chunk-{st.window.index}",
        )
        proc.start()
        st.proc = proc
        logger.info("chunk worker %d launched (pid=%s, frames %d..%d)",
                    st.window.index, proc.pid, st.window.core_start_frame,
                    st.window.core_end_frame)

    def _terminate_all() -> None:
        # 先请求合作式退出：意外异常路径上 worker 未被通知过取消，直接
        # join 会让每个存活进程空等最多 10s。成功路径上 worker 已全部
        # 退出，此调用无副作用。
        cancel_event.set()
        # Give finished workers a grace period to exit on their own —
        # SIGTERM mid-paddle-teardown prints a scary (harmless) C++ trace.
        for st in states.values():
            if st.proc is not None and st.proc.is_alive():
                st.proc.join(timeout=10)
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

    def _handle_failure(st: _WindowState) -> None:
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

    def _process_message(msg: dict) -> None:
        """Handle one worker message (shared by the drain loop and liveness)."""
        idx = msg.get("window")
        st = states.get(idx)
        if st is None or st.done:
            return  # late message from a replaced/finished worker
        if msg.get("attempt") != st.retries:
            return  # stale output from a killed previous attempt
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
            _handle_failure(st)
        # A failure may have relaunched or finished the window; drop it
        # from pending here so liveness is re-checked even when the
        # queue keeps producing messages from chatty siblings.
        if st.done and idx in pending:
            pending.discard(idx)

    def _drain_queue() -> None:
        """Non-blocking drain so queue-backed results are not mistaken for
        a dead window (see _check_liveness)."""
        while True:
            try:
                msg = msg_queue.get(timeout=0)
            except (queue_mod.Empty, OSError, ValueError):
                break
            except Exception:
                break
            _process_message(msg)

    def _check_liveness(now: float) -> None:
        """Fail pending windows whose process died or went silent."""
        for idx in list(pending):
            st = states[idx]
            if st.done:
                # Liveness 触发的兜底路径(_run_sequential)只置 done 不发消息,
                # 必须在这里出队,否则窗口永远留在 pending 里空转死循环。
                pending.discard(idx)
                continue
            alive = st.proc is not None and st.proc.is_alive()
            if not alive:
                # worker 发完 records/done 就会退出；这些消息可能还在共享
                # 队列里排队。先非阻塞排空队列再复核，否则会把"已完成
                # 未消费"的窗口误判为死亡，烧掉一次重试整窗重 OCR。
                _drain_queue()
                if st.done:
                    continue
                alive = st.proc is not None and st.proc.is_alive()
            silent = (now - st.last_msg_ts) > heartbeat_timeout_s
            if (not alive) or silent:
                if silent and alive:
                    logger.error("chunk worker %d heartbeat timeout", idx)
                _handle_failure(st)

    try:
        for st in states.values():
            _launch(st)

        pending = set(states)
        while pending:
            if cancel_check():
                # Cooperative cancel first: workers poll this Event inside
                # their frame loops and exit within seconds; SIGTERM in the
                # finally block is only the backstop.
                cancel_event.set()
                raise PipelineCancelled()
            try:
                msg = msg_queue.get(timeout=poll_timeout_s)
            except queue_mod.Empty:
                _check_liveness(time.monotonic())
                continue

            _process_message(msg)
            _check_liveness(time.monotonic())
        return merge_frame_records(
            {idx: st.records for idx, st in states.items()}, plan)
    finally:
        _terminate_all()
        try:
            msg_queue.close()
        except Exception:
            pass
