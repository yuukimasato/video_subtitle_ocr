# core/refine_executor.py
"""Two-pass boundary refinement: scan jobs once, refine them in parallel.

Legacy ``boundary_refine.refine_boundaries`` interleaves edge DETECTION and
edge REFINEMENT in one sequential scan, so a fade-out walk's writes influence
which edges later scan positions detect. That coupling is what made
parallelizing hard; this module splits the two passes:

1. ``scan_boundary_jobs`` — a pure scan over the ORIGINAL OCR results that
   emits one job per empty<->text transition (plus the clipped-tail job),
   writing nothing.
2. ``apply_refine_jobs`` / ``refine_boundaries_parallel`` — refine each job
   (ground window ±3 + outward probe walk) and merge the per-job writes in
   JOB ORDER (the order the legacy serial scan applies writes in), so the
   merged result is deterministic regardless of thread completion order.

Jobs whose frames do not overlap (the overwhelming majority — different
edges) are order-independent; edges closer than the ±3 ground windows are
the residual divergence risk vs the interleaved legacy scan, and they are
covered by equivalence gates: the pytest gate
(``tests/test_chunk_equivalence.py``), the seam-hostile fixture
(``benchmarks/run_seam_equivalence.py``) and ``tests/test_refine_executor.py``
assert record/event equality between this module and the legacy path.

All OCR access goes through :class:`StandaloneOCR` — a per-thread engine
instance built via ``ocr_engine_manager.build_standalone_engine`` so probe
calls never serialize on the process-wide singleton lock.
"""
import logging
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2

from core import boundary_refine
from core import ocr_engine_manager
from core import roi_extractor

logger = logging.getLogger(__name__)

REFINE_ENGINE_MEM_BUDGET_MB = 600
REFINE_MAX_THREADS = 4


def auto_refine_workers(
    cpu_count: Optional[int] = None,
    available_ram_mb: Optional[int] = None,
    gpu_mode: Optional[str] = None,
) -> int:
    """Threads for the parallel refine executor (0-ish clamp: >=1).

    Each thread owns an engine instance (~600MB RAM; on GPU its VRAM
    equivalent); more threads help only until memory bandwidth saturates,
    so the cap is conservative. On GPU the cap drops to 2 — VRAM is the
    scarce resource there, and the GPU throughput lever is batched
    inference, not threads.
    """
    if available_ram_mb is None:
        available_ram_mb = _probe_available_ram_mb()
    if cpu_count is None:
        cpu_count = os.cpu_count() or 2
    by_ram = max(1, int(available_ram_mb // REFINE_ENGINE_MEM_BUDGET_MB))
    cap = REFINE_MAX_THREADS
    if gpu_mode and str(gpu_mode).lower() == "gpu":
        cap = min(cap, 2)
    return max(1, min(cap, int(cpu_count), by_ram))


def _probe_available_ram_mb() -> int:
    """Best-effort available memory (Linux MemAvailable), conservative fallback."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return 2048


@dataclass(frozen=True)
class RefineJob:
    """One empty<->text transition to refine (legacy scan order)."""

    roi_id: str
    roi_index: int
    edge_frame: int
    direction: str  # "in" | "out" | "tail"
    roi_start: int
    roi_end: int


def scan_boundary_jobs(
    ocr_results: List[tuple],
    roi_data: List[Dict],
    *,
    min_score: float = 0.6,
) -> List[RefineJob]:
    """Detect refinement jobs on the ORIGINAL results, writing nothing.

    Mirrors the legacy scan's detection rules: per-ROI ascending frame scan,
    presence transitions, and the clipped-tail check (last frame shows text
    → tail job at ``frames[-1] + 1``).
    """
    if not ocr_results:
        return []

    by_roi: Dict[str, Dict[int, tuple]] = defaultdict(dict)
    for item in ocr_results:
        try:
            by_roi[str(item[3])][int(item[2])] = item
        except (TypeError, ValueError, IndexError):
            continue

    jobs: List[RefineJob] = []
    for roi_id in sorted(by_roi.keys()):
        roi_idx = boundary_refine.parse_roi_index(roi_id)
        if roi_idx is None or not (0 <= roi_idx < len(roi_data)):
            continue
        if not bool(roi_data[roi_idx].get("fade_in_refine_enabled", True)):
            continue
        roi_entry = roi_data[roi_idx]
        frame_map = by_roi[roi_id]
        frames = sorted(frame_map.keys())
        if not frames:
            continue
        roi_start = int(roi_entry.get("start_frame", 0))
        roi_end = int(roi_entry.get("end_frame", 0))
        if not roi_end:
            roi_start = roi_start if roi_start else frames[0]
            roi_end = frames[-1]

        present = {
            f: boundary_refine.ocr_text_present(frame_map[f][1], min_score)
            for f in frames
        }

        for pos, frame_num in enumerate(frames):
            has = present[frame_num]
            prev_has = present[frames[pos - 1]] if pos > 0 else False
            if has and not prev_has:
                jobs.append(RefineJob(roi_id, roi_idx, frame_num, "in", roi_start, roi_end))
            elif (not has) and prev_has:
                jobs.append(RefineJob(roi_id, roi_idx, frame_num, "out", roi_start, roi_end))

        if present[frames[-1]]:
            jobs.append(RefineJob(roi_id, roi_idx, frames[-1] + 1, "tail", roi_start, roi_end))
    return jobs


class _ResultStore:
    """Index-mapped view over a results list (legacy get/set semantics)."""

    def __init__(self, results: List[tuple]):
        self.results = results
        self.index_map: Dict[Tuple[str, int], int] = {}
        for idx, item in enumerate(results):
            try:
                self.index_map[(str(item[3]), int(item[2]))] = idx
            except (TypeError, ValueError, IndexError):
                continue

    def get(self, roi_id: str, frame_num: int) -> Optional[tuple]:
        pos = self.index_map.get((roi_id, int(frame_num)))
        return self.results[pos] if pos is not None else None

    def set(self, roi_id: str, item: tuple) -> None:
        key = (roi_id, int(item[2]))
        pos = self.index_map.get(key)
        if pos is not None:
            self.results[pos] = item
        else:
            self.results.append(item)
            self.index_map[key] = len(self.results) - 1

    def dump(self) -> Dict[Tuple[str, int], tuple]:
        """All items currently held (for an empty per-job store: its writes)."""
        return {key: self.results[pos] for key, pos in self.index_map.items()}


def apply_refine_jobs(
    results: List[tuple],
    roi_data: List[Dict],
    jobs: List[RefineJob],
    *,
    ocr_frame_func: Callable[[Dict, str, int], Optional[tuple]],
    probe_frame_func: Callable[[Dict, str, int], Optional[tuple]],
    is_cancelled_func: Optional[Callable[[], bool]] = None,
    max_backtrack_frames: int = 25,
    max_forward_frames: int = 12,
    min_score: float = 0.6,
    content_similarity_threshold: float = 0.5,
    edge_extend_frames: int = 0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[tuple]:
    """Refine ``jobs`` sequentially into ``results`` (single-threaded path).

    Same ground+walk semantics per job as the legacy edge refiners; jobs are
    applied in scan order so the write order matches the legacy scan.
    """
    if is_cancelled_func is None:
        is_cancelled_func = lambda: False
    store = _ResultStore(results)

    def ground_window(roi_id: str, roi_entry: Dict, back: int, fwd: int) -> None:
        for f in range(back, fwd + 1):
            if is_cancelled_func():
                return
            r = ocr_frame_func(roi_entry, roi_id, f)
            if r is None:
                continue
            _ground_set(store, roi_id, r, min_score)

    done = 0
    if progress_callback is not None:
        progress_callback(0, len(jobs))
    for job in jobs:
        if is_cancelled_func():
            break
        roi_entry = roi_data[job.roi_index]
        if job.direction == "in":
            boundary_refine._refine_fade_in_edge(
                job.roi_id, roi_entry, job.roi_start, job.roi_end, job.edge_frame,
                ground_window, probe_frame_func, store.get,
                lambda rid, f: _present(store, rid, f, min_score),
                lambda rid, anchor, f, ts: _set_extended(store, rid, anchor, f, ts),
                is_cancelled_func,
                max_backtrack_frames, max_forward_frames,
                min_score, content_similarity_threshold, edge_extend_frames)
        else:
            if job.direction == "tail":
                _tail_reground(job, store, roi_entry, ocr_frame_func,
                               is_cancelled_func, max_backtrack_frames, min_score)
                if not _present(store, job.roi_id, job.edge_frame - 1, min_score):
                    done += 1
                    if progress_callback is not None:
                        progress_callback(done, len(jobs))
                    continue
            boundary_refine._refine_fade_out_edge(
                job.roi_id, roi_entry, job.roi_start, job.roi_end, job.edge_frame,
                ground_window, probe_frame_func, store.get,
                lambda rid, f: _present(store, rid, f, min_score),
                lambda rid, anchor, f, ts: _set_extended(store, rid, anchor, f, ts),
                is_cancelled_func,
                max_backtrack_frames, max_forward_frames,
                min_score, content_similarity_threshold, edge_extend_frames)
        done += 1
        if progress_callback is not None:
            progress_callback(done, len(jobs))
    return results


def _ground_set(store: _ResultStore, roi_id: str, r: tuple, min_score: float) -> None:
    existing = store.get(roi_id, int(r[2]))
    if (
        existing is not None
        and isinstance(existing[1], dict)
        and existing[1].get(boundary_refine._EXTENDED_MARKER)
        and not boundary_refine.ocr_text_present(r[1], min_score)
    ):
        return  # never downgrade a walk-extended boundary frame
    store.set(roi_id, r)


def _present(store: _ResultStore, roi_id: str, frame_num: int, min_score: float) -> bool:
    item = store.get(roi_id, frame_num)
    return item is not None and boundary_refine.ocr_text_present(item[1], min_score)


def _set_extended(store: _ResultStore, roi_id: str, anchor_item: tuple,
                  frame_num: int, time_sec: float) -> None:
    content = anchor_item[1]
    if isinstance(content, dict):
        content = dict(content)
        content[boundary_refine._EXTENDED_MARKER] = True
    store.set(roi_id, (anchor_item[0], content, int(frame_num), roi_id,
                       float(time_sec or 0.0)))


def _tail_reground(job: RefineJob, store: _ResultStore, roi_entry: Dict,
                   ocr_frame_func, is_cancelled_func, max_backtrack_frames: int,
                   min_score: float) -> None:
    """Legacy clipped-tail block: re-ground backwards from the last frame."""
    f = job.edge_frame - 1
    limit = max(job.roi_start, f - int(max_backtrack_frames))
    while f >= limit:
        if is_cancelled_func():
            break
        r = ocr_frame_func(roi_entry, job.roi_id, f)
        if r is None:
            break
        if not boundary_refine.ocr_text_present(r[1], min_score):
            store.set(job.roi_id, r)  # correct a filled frame to truly-empty
            f -= 1
            continue
        break  # real text reached


class StandaloneOCR:
    """A private engine instance for one refine thread.

    Mirrors ``manager.run_batch_ocr``'s per-frame semantics (in-memory mode,
    no JSON output) without touching the process-wide singleton, so probe
    calls from parallel threads run truly concurrently.
    """

    def __init__(self, engine_id: str, engine_options: Optional[Dict[str, Any]]):
        self.engine = ocr_engine_manager.build_standalone_engine(engine_id, engine_options)
        self.calls = 0

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"dt_polys": [], "rec_polys": [], "rec_texts": [],
                "rec_scores": [], "rec_boxes": []}

    @staticmethod
    def _with_timestamp(frame_data: tuple, data: Dict[str, Any]) -> tuple:
        ts = float(frame_data[4]) if len(frame_data) >= 5 and frame_data[4] is not None else 0.0
        return (frame_data[0], data, frame_data[2], frame_data[3], ts)

    def run(self, frame_data: tuple) -> tuple:
        self.calls += 1
        try:
            raw = self.engine.predict(frame_data[1])
            data = self.engine.normalize_result(raw)
        except Exception:
            logger.warning("Standalone OCR failed at frame %s",
                           frame_data[2], exc_info=True)
            return self._with_timestamp(frame_data, self._empty())
        return self._with_timestamp(frame_data, data)

    def run_batch(self, frame_datas: List[tuple]) -> List[tuple]:
        """Batch variant: one native predict_batch call, aligned outputs."""
        if not frame_datas:
            return []
        self.calls += len(frame_datas)
        try:
            raws = self.engine.predict_batch([fd[1] for fd in frame_datas])
            datas = self.engine.normalize_batch_result(raws)
            if len(datas) != len(frame_datas):
                raise RuntimeError("predict_batch length mismatch")
        except Exception:
            logger.warning("Batch OCR failed (%d frames); falling back to per-frame",
                           len(frame_datas), exc_info=True)
            return [self.run(fd) for fd in frame_datas]
        return [self._with_timestamp(fd, data) for fd, data in zip(frame_datas, datas)]

    def cleanup(self) -> None:
        try:
            self.engine.cleanup()
        except Exception:
            logger.warning("Standalone engine cleanup failed", exc_info=True)


class _ThreadResources:
    """Per-thread VideoCapture + engine for probe closures."""

    def __init__(self, video_path: str, fps: float, engine_id: str,
                 engine_options: Optional[Dict[str, Any]], min_score: float,
                 cancel_check: Callable[[], bool]):
        self.video_path = video_path
        self.fps = fps
        self.min_score = min_score
        self.cancel_check = cancel_check
        self.cap = cv2.VideoCapture(video_path)
        try:
            if not self.cap.isOpened():
                raise RuntimeError("could not open video for refinement")
            # 引擎构造失败时也释放已打开的 VideoCapture,否则每次失败的
            # 线程初始化都泄漏一个句柄。
            self.ocr = StandaloneOCR(engine_id, engine_options)
        except Exception:
            self.cap.release()
            raise

    def _ocr_at(self, roi_entry: Dict, roi_id: str, frame_num: int, upscale: bool) -> Optional[tuple]:
        if self.cancel_check():
            return None
        ct = roi_extractor.extract_single_roi_crop_with_time(
            self.video_path, roi_entry, int(frame_num), cap=self.cap, fps=self.fps)
        if ct is None:
            return None
        crop, time_sec = ct
        if upscale:
            h, w = crop.shape[:2]
            if w > 0 and h > 0 and max(w * 2, h * 2) <= 8192:
                crop = cv2.resize(crop, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        return self.ocr.run((roi_entry, crop, int(frame_num), roi_id, float(time_sec)))

    def ocr_frame_func(self, roi_entry: Dict, roi_id: str, frame_num: int) -> Optional[tuple]:
        return self._ocr_at(roi_entry, roi_id, frame_num, upscale=False)

    def probe_frame_func(self, roi_entry: Dict, roi_id: str, frame_num: int) -> Optional[tuple]:
        # Sensitive detector: retry with a 2x upscale when the plain crop
        # reads empty (fade frames often only become readable upscaled).
        r = self._ocr_at(roi_entry, roi_id, frame_num, upscale=False)
        if r is not None and boundary_refine.ocr_text_present(r[1], self.min_score):
            return r
        return self._ocr_at(roi_entry, roi_id, frame_num, upscale=True)

    def close(self) -> None:
        try:
            self.cap.release()
        except Exception:
            pass
        self.ocr.cleanup()


def refine_boundaries_parallel(
    ocr_results: List[tuple],
    roi_data: List[Dict],
    *,
    video_path: str,
    fps: float,
    engine_id: str,
    engine_options: Optional[Dict[str, Any]],
    workers: int,
    max_backtrack_frames: int = 25,
    max_forward_frames: int = 12,
    min_score: float = 0.6,
    content_similarity_threshold: float = 0.5,
    edge_extend_frames: int = 0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    is_cancelled_func: Optional[Callable[[], bool]] = None,
) -> List[tuple]:
    """Scan jobs once, refine them on ``workers`` threads, merge in job order.

    Per-job writes are collected from the threads and applied in scan order,
    so the merged result does not depend on thread completion order.
    """
    if is_cancelled_func is None:
        is_cancelled_func = lambda: False
    jobs = scan_boundary_jobs(ocr_results, roi_data, min_score=min_score)
    results = list(ocr_results)
    if not jobs:
        return results
    workers = max(1, min(int(workers), len(jobs)))

    store = _ResultStore(results)
    local = threading.local()
    resources: List[_ThreadResources] = []
    resources_lock = threading.Lock()

    def get_resources() -> _ThreadResources:
        res = getattr(local, "res", None)
        if res is None:
            res = _ThreadResources(video_path, fps, engine_id, engine_options,
                                   min_score, is_cancelled_func)
            local.res = res
            with resources_lock:
                resources.append(res)
        return res

    def run_job(job: RefineJob) -> Dict[Tuple[str, int], tuple]:
        """Refine one job into a private store; return its writes."""
        res = get_resources()
        job_store = _ResultStore([])

        def ground_window(roi_id: str, roi_entry: Dict, back: int, fwd: int) -> None:
            # Batch the whole ground window into one native predict_batch.
            frames = list(range(back, fwd + 1))
            crops: List[Optional[tuple]] = []
            for f in frames:
                if is_cancelled_func():
                    return
                ct = roi_extractor.extract_single_roi_crop_with_time(
                    res.video_path, roi_entry, int(f), cap=res.cap, fps=res.fps)
                crops.append(ct)
            valid = [(f, ct) for f, ct in zip(frames, crops) if ct is not None]
            if not valid:
                return
            frame_datas = [(roi_entry, ct[0], f, roi_id, float(ct[1]))
                           for f, ct in valid]
            for f, r in zip([f for f, _ in valid], res.ocr.run_batch(frame_datas)):
                _ground_set(job_store, roi_id, r, min_score)

        def item_at(roi_id: str, frame_num: int) -> Optional[tuple]:
            # Job-local writes first (this job's ground/walk), then the
            # shared results as of the start of the parallel pass.
            return job_store.get(roi_id, frame_num) or store.get(roi_id, frame_num)

        def present(roi_id: str, frame_num: int) -> bool:
            item = item_at(roi_id, frame_num)
            return item is not None and boundary_refine.ocr_text_present(item[1], min_score)

        def probe(roi_entry: Dict, roi_id: str, frame_num: int) -> Optional[tuple]:
            return res.probe_frame_func(roi_entry, roi_id, frame_num)

        def set_extended(roi_id: str, anchor_item: tuple, frame_num: int, ts: float) -> None:
            content = anchor_item[1]
            if isinstance(content, dict):
                content = dict(content)
                content[boundary_refine._EXTENDED_MARKER] = True
            job_store.set(roi_id, (anchor_item[0], content, int(frame_num), roi_id,
                                   float(ts or 0.0)))

        roi_entry = roi_data[job.roi_index]
        if job.direction == "in":
            boundary_refine._refine_fade_in_edge(
                job.roi_id, roi_entry, job.roi_start, job.roi_end, job.edge_frame,
                ground_window, probe, item_at, present, set_extended,
                is_cancelled_func,
                max_backtrack_frames, max_forward_frames,
                min_score, content_similarity_threshold, edge_extend_frames)
        else:
            if job.direction == "tail":
                _tail_reground(job, job_store, roi_entry, res.ocr_frame_func,
                               is_cancelled_func, max_backtrack_frames, min_score)
                if not present(job.roi_id, job.edge_frame - 1):
                    return job_store.dump()
            boundary_refine._refine_fade_out_edge(
                job.roi_id, roi_entry, job.roi_start, job.roi_end, job.edge_frame,
                ground_window, probe, item_at, present, set_extended,
                is_cancelled_func,
                max_backtrack_frames, max_forward_frames,
                min_score, content_similarity_threshold, edge_extend_frames)
        return job_store.dump()

    done = 0
    if progress_callback is not None:
        progress_callback(0, len(jobs))
    try:
        if workers < 2:
            # Not worth extra engines: one worker, sequential submission.
            writes_list = [run_job(job) for job in jobs]
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(run_job, job) for job in jobs]
                writes_list = [fut.result() for fut in futures]  # job order
        # Deterministic merge in job order — independent of completion order.
        for writes in writes_list:
            for key, item in writes.items():
                store.set(key[0], item)
        done = len(jobs)
        if progress_callback is not None:
            progress_callback(done, len(jobs))
    finally:
        for res in resources:
            res.close()
    return results
