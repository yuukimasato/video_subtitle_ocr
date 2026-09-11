# tests/test_pipeline_refine_integration.py
"""Integration test: PipelineWorker boundary refinement with a real video.

Synthesizes a small video whose "subtitle" (a bright patch) fades in/out over
frames 30..59. The stub OCR engine reads pixels directly, so it can only
"recognize" the patch once it is bright enough (alpha >= 0.5 -> frames 31..58).
One frame (33) starts with an empty result to simulate a stale skip/fill;
re-grounding must restore it. Frame timestamps must stay on the video's own
timeline (POS_MSEC), proving refinement doesn't shift the timeline.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import ocr_engine_manager as mgr
from core import roi_extractor
from core.ocr_engine_base import BaseOCREngine, OCREngineInfo, OCREngineRegistry
from core.pipeline_worker import PipelineWorker

FPS = 25
TOTAL = 90
W, H = 320, 240
PATCH = (80, 180, 240, 220)  # x1, y1, x2, y2 (subtitle patch area)
READABLE_FIRST, READABLE_LAST = 31, 58  # alpha >= 0.5 frames
TRUE_FIRST, TRUE_LAST = 30, 59  # any visible alpha


def _patch_alpha(frame_num: int) -> float:
    if TRUE_FIRST <= frame_num <= 33:
        return 0.25 * (frame_num - TRUE_FIRST + 1)
    if 34 <= frame_num <= 56:
        return 1.0
    if 57 <= frame_num <= TRUE_LAST:
        return 0.25 * (TRUE_LAST - frame_num + 1)
    return 0.0


def _empty_ocr():
    return {"dt_polys": [], "rec_polys": [], "rec_texts": [], "rec_scores": [], "rec_boxes": []}


class BrightPatchEngine(BaseOCREngine):
    """Reads brightness directly: patch readable only when alpha >= 0.5."""

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="bright_patch",
            name="BrightPatch",
            version="0.0",
            description="integration test double",
            supports_gpu=False,
        )

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, **kwargs) -> None:
        pass

    def predict(self, img_input):
        return img_input

    def normalize_result(self, raw_result):
        img = raw_result
        if not isinstance(img, np.ndarray) or img.size == 0:
            return _empty_ocr()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        bright = int((gray > 150).sum())
        if bright > 50:
            poly = [[0, 0], [10, 0], [10, 10], [0, 10]]
            return {
                "dt_polys": [poly],
                "rec_polys": [poly],
                "rec_texts": ["字幕"],
                "rec_scores": [0.95],
                "rec_boxes": [[0, 0, 10, 10]],
            }
        return _empty_ocr()

    def cleanup(self) -> None:
        pass


@pytest.fixture
def bright_engine():
    OCREngineRegistry.register(BrightPatchEngine)
    mgr.set_engine("bright_patch", {})
    yield BrightPatchEngine
    OCREngineRegistry._engines.pop("bright_patch", None)
    mgr.set_engine(None, {})


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


def _build_initial_results(video_path, roi_entry):
    """Run the stub OCR over real extracted crops (sequential, in-memory)."""
    results = []
    gen = roi_extractor.extract_roi_frames(
        video_path, [roi_entry], TOTAL, FPS, work_dir="", save_to_disk=False
    )
    for frame_data in gen:
        _, crop, frame_num, roi_id, t = frame_data
        engine = mgr.get_engine()
        ocr_data = engine.normalize_result(engine.predict(crop))
        results.append((roi_entry, ocr_data, frame_num, roi_id, t))
    return results


def test_boundary_refinement_on_real_video(bright_engine, synthetic_video, tmp_path):
    roi_entry = {
        "type": "rect",
        "points": [70, 170, 250, 60],
        "start_frame": 0,
        "end_frame": TOTAL - 1,
        "fade_in_refine_enabled": True,
    }
    results = _build_initial_results(synthetic_video, roi_entry)
    # Simulate a stale skip/fill: frame 33 wrongly empty (OCR actually reads it).
    frame33 = next(it for it in results if int(it[2]) == 33)
    results[results.index(frame33)] = (roi_entry, _empty_ocr(), 33, "roi_0", frame33[4])

    def text_at(frame_num):
        for it in results:
            if int(it[2]) == frame_num:
                return "".join(it[1].get("rec_texts", []))
        return None

    assert text_at(READABLE_FIRST) == "字幕"
    assert text_at(33) == ""  # stale fill visible before refinement

    worker = PipelineWorker(
        video_path=synthetic_video,
        roi_data=[roi_entry],
        total_frames=TOTAL,
        fps=FPS,
        video_width=W,
        video_height=H,
        output_ass_path=str(tmp_path / "out.ass"),
        debug_mode=False,
        template_path=None,
    )
    worker.work_dir = ""
    refined = worker._refine_fade_in_boundaries(
        results, max_backtrack_frames=25, max_forward_frames=12
    )

    def rtext(frame_num):
        for it in refined:
            if int(it[2]) == frame_num:
                return "".join(it[1].get("rec_texts", []))
        return None

    def rtime(frame_num):
        for it in refined:
            if int(it[2]) == frame_num:
                return float(it[4])
        return None

    # Re-grounding restored the wrongly-empty frame inside the run.
    assert rtext(33) == "字幕"
    # Boundaries stay at the OCR-readable range (probe == plain OCR blindness).
    assert rtext(READABLE_FIRST) == "字幕"
    assert rtext(READABLE_LAST) == "字幕"
    assert rtext(TRUE_FIRST) in ("", None)
    assert rtext(TRUE_LAST) in ("", None)
    # Timestamps stay on the video's own timeline (POS_MSEC, ~frame/fps).
    assert abs(rtime(READABLE_FIRST) - READABLE_FIRST / FPS) < 0.05
    assert abs(rtime(READABLE_LAST) - READABLE_LAST / FPS) < 0.05
