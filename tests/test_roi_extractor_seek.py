# tests/test_roi_extractor_seek.py
"""Chunk-parallel decode-start tests: the sequential ROI extractors must
honor ``start_frame`` (each worker's planned ``grab_start_frame``) instead of
always decoding from frame 0, while keeping global frame labels and ROI
activity consistent with a full sequential run."""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import pipeline_stages, roi_extractor
from core.pipeline_stages import PipelineContext

FPS = 25
W, H = 320, 180
TOTAL = 300
PATCH = 24  # flat brightness block encoding the frame index
PATCH_XY = (100, 60)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("seekvid") / "seek_video.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    assert writer.isOpened()
    for f in range(TOTAL):
        frame = np.full((H, W, 3), 30, dtype=np.uint8)
        x0, y0 = PATCH_XY
        # Flat block in [3, 252]: JPEG keeps the block mean within ~10.
        frame[y0:y0 + PATCH, x0:x0 + PATCH] = (f % 250) + 3
        writer.write(frame)
    writer.release()
    return path


def _roi():
    x0, y0 = PATCH_XY
    return {"type": "rect", "points": [x0 - 8, y0 - 8, PATCH + 16, PATCH + 16],
            "start_frame": 100, "end_frame": TOTAL - 1}


# ── sequential extractor honors start_frame ───────────────────────────


def test_seek_with_preroll_yields_same_frames_as_full_decode(synthetic_video):
    ref = [fd[2] for fd in roi_extractor.extract_roi_frames(
        synthetic_video, [_roi()], TOTAL, FPS, work_dir="", save_to_disk=False)]
    assert ref == list(range(100, TOTAL))

    # Decode starts 40 frames before the first active ROI frame — the same
    # shape as a chunk worker's grab_start_frame preroll.
    got = [fd[2] for fd in roi_extractor.extract_roi_frames(
        synthetic_video, [_roi()], TOTAL, FPS, work_dir="", save_to_disk=False,
        start_frame=60)]
    assert got == ref


def test_seek_inside_active_roi_preapplies_interval_state(synthetic_video):
    got = [fd[2] for fd in roi_extractor.extract_roi_frames(
        synthetic_video, [_roi()], TOTAL, FPS, work_dir="", save_to_disk=False,
        start_frame=150)]
    assert got == list(range(150, TOTAL))


def test_seek_keeps_content_aligned_with_global_labels(synthetic_video):
    gen = roi_extractor.extract_roi_frames(
        synthetic_video, [_roi()], TOTAL, FPS, work_dir="", save_to_disk=False,
        start_frame=150)
    yielded = []
    for roi_entry, crop, frame_num, roi_id, t in gen:
        yielded.append(frame_num)
        if frame_num == 150 or frame_num % 25 == 0:
            inner = crop[8:8 + PATCH, 8:8 + PATCH]
            assert abs(float(inner.mean()) - ((frame_num % 250) + 3)) <= 12
            assert abs(t - frame_num / FPS) < 0.5
    assert yielded == list(range(150, TOTAL))


def test_merged_extractor_honors_start_frame(synthetic_video):
    got = [fd[2] for fd in roi_extractor.extract_merged_roi_frames(
        synthetic_video, [_roi()], TOTAL, FPS, work_dir="", save_to_disk=False,
        start_frame=150)]
    assert got == list(range(150, TOTAL))


# ── _seek_decode_start unit behaviour ─────────────────────────────────


class _FakeCap:
    def __init__(self, seek_ok=True):
        self.seek_ok = seek_ok
        self.seeked = None

    def set(self, prop, value):
        self.seeked = value
        return self.seek_ok


def _events():
    # roi_0 active 10..59, roi_1 active 50..199 (end events keyed at end+1).
    return (defaultdict(list, {10: [0], 50: [1]}),
            defaultdict(list, {60: [0], 200: [1]}))


def test_seek_start_returns_zero_when_backend_cannot_seek():
    start_events, end_events = _events()
    assert roi_extractor._seek_decode_start(
        _FakeCap(seek_ok=False), 45, TOTAL, start_events, end_events) == (0, set())


def test_seek_start_preapplies_interval_events_before_target():
    start_events, end_events = _events()
    cap = _FakeCap()
    pos, active = roi_extractor._seek_decode_start(
        cap, 45, TOTAL, start_events, end_events)
    assert (pos, cap.seeked) == (45, 45)
    # roi_0 active since 10 and still running at 45; roi_1 starts at 50.
    assert active == {0}
    pos, active = roi_extractor._seek_decode_start(
        cap, 60, TOTAL, start_events, end_events)
    assert active == {0, 1}  # 60 itself is not pre-applied
    pos, active = roi_extractor._seek_decode_start(
        cap, 70, TOTAL, start_events, end_events)
    assert active == {1}  # roi_0's end event (60) fired before the target


def test_seek_start_clamps_target_into_video_range():
    start_events, end_events = _events()
    cap = _FakeCap()
    pos, active = roi_extractor._seek_decode_start(
        cap, 10 * TOTAL, TOTAL, start_events, end_events)
    assert pos == TOTAL - 1
    # Every interval ended long before the clamped target.
    assert active == set()
    assert roi_extractor._seek_decode_start(
        cap, 0, TOTAL, start_events, end_events) == (0, set())
    assert cap.seeked == TOTAL - 1  # target<=0 short-circuits before set()


# ── stage wiring: PipelineContext.decode_start_frame → extractor ──────


def test_extract_and_ocr_stage_passes_decode_start_to_extractor(monkeypatch):
    captured = {}

    def fake_extract_roi_frames(video_path, roi_data, total_frames, fps,
                                work_dir, *, save_to_disk, color_presence_gate,
                                start_frame, **kw):
        captured["start_frame"] = start_frame
        yield ({"type": "rect"}, "img", 500, "roi_0", 20.0)

    monkeypatch.setattr(roi_extractor, "extract_roi_frames", fake_extract_roi_frames)

    class StubOptimizer:
        def __init__(self, **kw):
            self.ocr_calls = 1
            self.frames_filled = 1

        def process_roi_group(self, frames, is_cancelled_func=None, progress_callback=None):
            return [({"t": ["x"]}, 500, "roi_0", 20.0)]

        def cleanup(self):
            pass

    monkeypatch.setattr(pipeline_stages, "OcrOptimizer", StubOptimizer)
    monkeypatch.setenv("MODE", "cpu")

    def _ctx(decode_start):
        return PipelineContext(
            video_path="/tmp/v.mp4", roi_data=[{"start_frame": 400, "end_frame": 900}],
            total_frames=1000, fps=FPS, work_dir="/tmp/w", debug_mode=False,
            enable_boundary_refine=False, decode_start_frame=decode_start)

    results, stats = pipeline_stages.extract_and_ocr_stage(
        _ctx(42), progress_cb=lambda p, m: None, cancel_check=lambda: False)
    assert captured["start_frame"] == 42
    assert stats["extracted_count"] == 1 and results

    pipeline_stages.extract_and_ocr_stage(
        _ctx(0), progress_cb=lambda p, m: None, cancel_check=lambda: False)
    assert captured["start_frame"] == 0  # single-process default unchanged


@pytest.mark.parametrize('extractor', [roi_extractor.extract_roi_frames,
                                       roi_extractor.extract_merged_roi_frames])
@pytest.mark.parametrize('ranges, expected', [([(2, 4)], [2, 3, 4]),
    ([(2, 4), (8, 9)], [2, 3, 4, 8, 9]), ([(99, 99)], [99]), ([], [])])
def test_no_decode_after_last_active_roi(monkeypatch, extractor, ranges, expected):
    class Capture:
        grabs = 0
        released = False

        def isOpened(self):
            return True

        def grab(self):
            self.grabs += 1
            return self.grabs <= 100

        def retrieve(self):
            return True, np.full((20, 20, 3), 255, np.uint8)

        def get(self, prop):
            return 0

        def release(self):
            self.released = True

    cap = Capture()
    monkeypatch.setattr(roi_extractor.cv2, 'VideoCapture', lambda _: cap)
    monkeypatch.setattr(roi_extractor, '_has_cuda_gpu', False)
    rois = [{'type': 'rect', 'points': [0, 0, 10, 10],
             'start_frame': start, 'end_frame': end} for start, end in ranges]
    records = list(extractor('fake.mp4', rois, 100, 25, '', save_to_disk=False))
    assert [r[2] for r in records] == expected
    assert cap.grabs == (max(expected) + 1 if expected else 0)
    if expected:
        assert cap.released
