# tests/test_refine_executor.py
"""Tests for the two-pass parallel boundary-refinement executor.

Uses a brightness-reading stub engine (mirroring the pipeline integration
test) over a real synthesized video, then asserts:

- scan_boundary_jobs finds exactly the fade-in/fade-out edges of the known
  schedule;
- the two-pass serial application AND the parallel executor produce results
  identical to the legacy interleaved refine_boundaries;
- ground windows go through the engine's native predict_batch;
- auto_refine_workers respects its memory/core caps.
"""

from __future__ import annotations

import copy
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import cv2
import numpy as np
import pytest

from core import boundary_refine
from core import roi_extractor
from core.chunk_worker import clip_roi_data_to_window  # noqa: F401  (import sanity)
from core.ocr_engine_base import BaseOCREngine, OCREngineInfo, OCREngineRegistry
from core.refine_executor import (
    RefineJob,
    _ThreadResources,
    apply_refine_jobs,
    auto_refine_workers,
    refine_boundaries_parallel,
    scan_boundary_jobs,
)

FPS = 25
TOTAL = 90
W, H = 320, 240
PATCH = (70, 170, 250, 230)  # x1, y1, x2, y2 — inside the ROI strip
TRUE_FIRST, TRUE_LAST = 30, 59
READABLE_FIRST, READABLE_LAST = 31, 58  # alpha >= 0.5


def _patch_alpha(frame_num: int) -> float:
    if TRUE_FIRST <= frame_num <= 33:
        return 0.25 * (frame_num - TRUE_FIRST + 1)
    if 34 <= frame_num <= 56:
        return 1.0
    if 57 <= frame_num <= TRUE_LAST:
        return 0.25 * (TRUE_LAST - frame_num + 1)
    return 0.0


def _empty_ocr():
    return {"dt_polys": [], "rec_polys": [], "rec_texts": [],
            "rec_scores": [], "rec_boxes": []}


class CountingBrightEngine(BaseOCREngine):
    """Reads crop brightness; counts predict vs predict_batch usage."""

    predict_calls = 0
    batch_calls = 0
    batched_frames = 0

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="counting_bright", name="CountingBright", version="0",
            description="refine executor test double", supports_gpu=False)

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, **kwargs) -> None:
        pass

    def _read(self, img):
        if not isinstance(img, np.ndarray) or img.size == 0:
            return _empty_ocr()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        if int((gray > 150).sum()) > 50:
            poly = [[0, 0], [10, 0], [10, 10], [0, 10]]
            return {
                "dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["字幕"], "rec_scores": [0.95],
                "rec_boxes": [[0, 0, 10, 10]],
            }
        return _empty_ocr()

    def predict(self, img_input):
        CountingBrightEngine.predict_calls += 1
        return self._read(img_input)

    def predict_batch(self, images):
        CountingBrightEngine.batch_calls += 1
        CountingBrightEngine.batched_frames += len(images)
        return [self._read(img) for img in images]

    def normalize_result(self, raw_result):
        return raw_result if isinstance(raw_result, dict) else _empty_ocr()

    def normalize_batch_result(self, raw_results):
        return [self.normalize_result(r) for r in raw_results]

    def cleanup(self) -> None:
        pass


@pytest.fixture
def counting_engine():
    CountingBrightEngine.predict_calls = 0
    CountingBrightEngine.batch_calls = 0
    CountingBrightEngine.batched_frames = 0
    OCREngineRegistry.register(CountingBrightEngine)
    yield CountingBrightEngine
    OCREngineRegistry._engines.pop("counting_bright", None)


@pytest.fixture
def synthetic_video(tmp_path):
    path = str(tmp_path / "fade_video.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    assert writer.isOpened()
    rng = np.random.default_rng(42)
    for f in range(TOTAL):
        frame = rng.integers(40, 70, size=(H, W, 3), dtype=np.uint8)
        alpha = _patch_alpha(f)
        if alpha > 0:
            x1, y1, x2, y2 = PATCH
            patch = frame[y1:y2, x1:x2].astype(np.float32)
            frame[y1:y2, x1:x2] = (patch * (1 - alpha) + 255.0 * alpha).astype(np.uint8)
        writer.write(frame)
    writer.release()
    return path


def _roi_entry():
    return {
        "type": "rect",
        "points": [60, 160, 260, 80],
        "start_frame": 0,
        "end_frame": TOTAL - 1,
        "fade_in_refine_enabled": True,
    }


def _build_initial_results(video_path):
    """Run the stub OCR over real crops; inject one stale empty fill at 33."""
    engine = CountingBrightEngine()
    results = []
    gen = roi_extractor.extract_roi_frames(
        video_path, [_roi_entry()], TOTAL, FPS, work_dir="", save_to_disk=False)
    for frame_data in gen:
        _, crop, frame_num, roi_id, t = frame_data
        ocr_data = engine.normalize_result(engine.predict(crop))
        results.append((_roi_entry(), ocr_data, frame_num, roi_id, t))
    results = [(r[0], r[1], r[2], r[3], r[4]) for r in results]
    for i, r in enumerate(results):
        if int(r[2]) == 33:
            results[i] = (r[0], _empty_ocr(), r[2], r[3], r[4])  # stale fill
    return results


_EDGE_PARAMS = dict(
    max_backtrack_frames=25,
    max_forward_frames=12,
    edge_extend_frames=50,
)


def test_scan_boundary_jobs_finds_fades(synthetic_video):
    results = _build_initial_results(synthetic_video)
    jobs = scan_boundary_jobs(results, [_roi_entry()])
    got = [(j.direction, j.edge_frame) for j in jobs]
    # The injected stale empty at frame 33 is a real presence dip on the
    # original results — the ground windows exist precisely to fix it.
    assert got == [("in", 31), ("out", 33), ("in", 34), ("out", 59)]


def _sig(results):
    """Output-relevant signature: frame → (text, timestamp).

    The internal ``_boundary_extended`` marker is intentionally NOT part of
    the signature: it only guards later ground windows against downgrades,
    and the two-pass executor marks some frames the interleaved legacy scan
    never re-visits. Event output derives from text + timestamps only.
    """
    return [(int(r[2]), "".join(r[1].get("rec_texts", [])), round(float(r[4]), 4))
            for r in results]


def test_serial_two_pass_matches_legacy(synthetic_video, counting_engine):
    results = _build_initial_results(synthetic_video)
    jobs = scan_boundary_jobs(results, [_roi_entry()])
    res = _ThreadResources(synthetic_video, FPS, "counting_bright", {}, 0.6,
                           lambda: False)
    try:
        legacy = boundary_refine.refine_boundaries(
            copy.deepcopy(results), [_roi_entry()],
            ocr_frame_func=res.ocr_frame_func,
            probe_frame_func=res.probe_frame_func,
            **_EDGE_PARAMS)
        twopass = apply_refine_jobs(
            copy.deepcopy(results), [_roi_entry()], jobs,
            ocr_frame_func=res.ocr_frame_func,
            probe_frame_func=res.probe_frame_func,
            **_EDGE_PARAMS)
    finally:
        res.close()
    assert _sig(twopass) == _sig(legacy)


def test_parallel_matches_legacy(synthetic_video, counting_engine):
    results = _build_initial_results(synthetic_video)
    res = _ThreadResources(synthetic_video, FPS, "counting_bright", {}, 0.6,
                           lambda: False)
    try:
        legacy = boundary_refine.refine_boundaries(
            copy.deepcopy(results), [_roi_entry()],
            ocr_frame_func=res.ocr_frame_func,
            probe_frame_func=res.probe_frame_func,
            **_EDGE_PARAMS)
    finally:
        res.close()
    parallel = refine_boundaries_parallel(
        copy.deepcopy(results), [_roi_entry()],
        video_path=synthetic_video, fps=FPS,
        engine_id="counting_bright", engine_options={},
        workers=3, **_EDGE_PARAMS)
    assert _sig(parallel) == _sig(legacy)
    # Frame 33's stale fill must have been re-grounded back to real text.
    text33 = next("".join(r[1].get("rec_texts", [])) for r in parallel
                  if int(r[2]) == 33)
    assert text33 == "字幕"


def test_parallel_ground_windows_use_predict_batch(synthetic_video, counting_engine):
    results = _build_initial_results(synthetic_video)
    refine_boundaries_parallel(
        copy.deepcopy(results), [_roi_entry()],
        video_path=synthetic_video, fps=FPS,
        engine_id="counting_bright", engine_options={},
        workers=2, **_EDGE_PARAMS)
    assert CountingBrightEngine.batch_calls > 0
    assert CountingBrightEngine.batched_frames >= 4  # ±3 ground window ≈ 7 frames


def test_single_job_falls_back_to_sequential_path(synthetic_video, counting_engine):
    # One job only: workers clamps to 1 — must still produce refined output.
    results = _build_initial_results(synthetic_video)
    jobs = scan_boundary_jobs(results, [_roi_entry()])
    assert len(jobs) >= 2
    out = refine_boundaries_parallel(
        copy.deepcopy(results[:40]), [_roi_entry()],
        video_path=synthetic_video, fps=FPS,
        engine_id="counting_bright", engine_options={},
        workers=4, **_EDGE_PARAMS)
    text31 = next("".join(r[1].get("rec_texts", [])) for r in out
                  if int(r[2]) == READABLE_FIRST)
    assert text31 == "字幕"


def test_auto_refine_workers_caps():
    assert auto_refine_workers(cpu_count=2, available_ram_mb=32768) == 2
    assert auto_refine_workers(cpu_count=32, available_ram_mb=32768) == 4
    assert auto_refine_workers(cpu_count=32, available_ram_mb=600) == 1


def test_scan_respects_refine_disable():
    roi = _roi_entry()
    roi["fade_in_refine_enabled"] = False
    fake = [(roi, _empty_ocr(), 10, "roi_0", 0.0),
            (roi, {"rec_texts": ["字幕"], "rec_scores": [0.95]}, 11, "roi_0", 0.0)]
    assert scan_boundary_jobs(fake, [roi]) == []


def test_clip_window_job_fields():
    job = RefineJob("roi_0", 0, 5, "in", 0, 100)
    assert job.direction == "in" and job.roi_end == 100


def test_auto_refine_workers_gpu_caps_at_two():
    # VRAM is the scarce resource on GPU: threads cap at 2 regardless of cores.
    assert auto_refine_workers(cpu_count=32, available_ram_mb=32768, gpu_mode="gpu") == 2
    # CPU keeps the normal cap.
    assert auto_refine_workers(cpu_count=32, available_ram_mb=32768, gpu_mode="cpu") == 4
    # RAM still binds first.
    assert auto_refine_workers(cpu_count=32, available_ram_mb=700, gpu_mode="gpu") == 1
