# tests/test_chunk_worker.py
"""Unit tests for the chunk worker: ROI window clipping and the worker
message protocol (in-process, stages monkeypatched — no real engines)."""

from __future__ import annotations

import os
import pickle
import queue as queue_mod
import sys
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from core import chunk_worker
from core.chunk_planner import ChunkWindow
from core.chunk_worker import chunk_worker_main, clip_roi_data_to_window
from core.pipeline_stages import PipelineCancelled, PipelineContext


def _window(work_start, work_end, core_start=None, core_end=None, index=0):
    return ChunkWindow(
        index=index,
        core_start_frame=core_start if core_start is not None else work_start,
        core_end_frame=core_end if core_end is not None else work_end,
        grab_start_frame=max(0, work_start - 30),
        work_start_frame=work_start,
        work_end_frame=work_end,
    )


# ── clip_roi_data_to_window ───────────────────────────────────────────


def test_roi_fully_inside_window_kept_unchanged():
    w = _window(100, 500)
    rois = [{"start_frame": 200, "end_frame": 300, "label": "a"}]
    out = clip_roi_data_to_window(rois, w, fps=30)
    assert out == [{"start_frame": 200, "end_frame": 300, "label": "a"}]


def test_roi_spanning_window_clipped_to_work_range():
    w = _window(100, 500)
    rois = [{"start_frame": 0, "end_frame": 900}]
    out = clip_roi_data_to_window(rois, w, fps=30)
    assert out == [{"start_frame": 100, "end_frame": 499}]


def test_roi_outside_window_dropped():
    w = _window(100, 500)
    assert clip_roi_data_to_window([{"start_frame": 0, "end_frame": 99}], w, 30) == []
    assert clip_roi_data_to_window([{"start_frame": 500, "end_frame": 900}], w, 30) == []


def test_roi_touching_window_edges_kept():
    w = _window(100, 500)
    # end_frame is inclusive in the extractor: frame 499 is the last kept.
    out = clip_roi_data_to_window([{"start_frame": 50, "end_frame": 120}], w, 30)
    assert out == [{"start_frame": 100, "end_frame": 120}]
    out = clip_roi_data_to_window([{"start_frame": 480, "end_frame": 499}], w, 30)
    assert out == [{"start_frame": 480, "end_frame": 499}]
    # One frame past the window end (499+1=500) is dropped.
    out = clip_roi_data_to_window([{"start_frame": 480, "end_frame": 500}], w, 30)
    assert out == [{"start_frame": 480, "end_frame": 499}]


def test_time_based_roi_clipped_via_frame_keys():
    w = _window(300, 600)
    rois = [{"start_time": 0.0, "end_time": 30.0}]  # frames 0..900 @30fps
    out = clip_roi_data_to_window(rois, w, fps=30)
    assert out == [{"start_time": 0.0, "end_time": 30.0, "start_frame": 300, "end_frame": 599}]


def test_string_time_roi_clipped():
    w = _window(300, 600)
    # "00:30.000" = 30s = frame 900 @30fps -> clipped to the window.
    rois = [{"start_time": "00:00.000", "end_time": "00:30.000"}]
    out = clip_roi_data_to_window(rois, w, fps=30)
    assert out and out[0]["start_frame"] == 300 and out[0]["end_frame"] == 599


def test_non_dict_entries_skipped():
    w = _window(100, 500)
    out = clip_roi_data_to_window(["junk", {"start_frame": 100, "end_frame": 200}], w, 30)
    assert out == [{"start_frame": 100, "end_frame": 200}]


# ── payload picklability (spawn requirement) ──────────────────────────


def test_static_payload_is_picklable():
    ctx = PipelineContext(
        video_path="/tmp/v.mp4", roi_data=[{"start_frame": 0, "end_frame": 10}],
        total_frames=100, fps=30.0, work_dir="/tmp/w", debug_mode=False,
        engine_options={"lang": "ch"},
    )
    w = _window(0, 100)
    blob = pickle.dumps({"ctx": ctx, "window": w})
    clone = pickle.loads(blob)
    assert clone["ctx"] == ctx and clone["window"] == w


# ── worker message protocol (stages monkeypatched) ────────────────────


def _payload(cancel_event=None, queue=None, roi_data=None):
    ctx = PipelineContext(
        video_path="/tmp/v.mp4", roi_data=roi_data or [{"start_frame": 0, "end_frame": 900}],
        total_frames=1000, fps=30.0, work_dir="/tmp/w", debug_mode=False,
    )
    return {
        "ctx": ctx,
        "window": _window(0, 1000, core_start=0, core_end=1000),
        "queue": queue if queue is not None else queue_mod.Queue(),
        "cancel_event": cancel_event or threading.Event(),
    }


def _fake_stages(monkeypatch, records, extract_exc=None):
    calls = {}

    def fake_extract(ctx, *, progress_cb, cancel_check):
        calls["extract"] = ctx
        if extract_exc is not None:
            raise extract_exc
        return [("ocr", 0, "roi_0", 0.0)], {"total_roi_frames": 10, "total_ocr_calls": 3}

    def fake_refine(ctx, ocr_results, *, ocr_stats, progress_cb, cancel_check):
        calls["refine"] = list(ocr_results)
        return ocr_results

    def fake_restore(ctx, ocr_results, *, progress_cb, cancel_check):
        calls["restore"] = list(ocr_results)
        return records

    monkeypatch.setattr(chunk_worker, "extract_and_ocr_stage", fake_extract)
    monkeypatch.setattr(chunk_worker, "refine_stage", fake_refine)
    monkeypatch.setattr(chunk_worker, "restore_stage", fake_restore)
    return calls


def _drain(q):
    msgs = []
    while True:
        try:
            msgs.append(q.get_nowait())
        except queue_mod.Empty:
            return msgs


def test_worker_emits_records_then_done(monkeypatch):
    records = [({"t": [i]}, i, "roi_0", i / 30.0) for i in range(120)]
    calls = _fake_stages(monkeypatch, records)
    q = queue_mod.Queue()
    payload = _payload(queue=q)
    chunk_worker_main(payload)

    msgs = _drain(q)
    types = [m["type"] for m in msgs]
    assert types[-1] == "done"
    assert types.count("records") == 1  # 120 records fit in one 500 batch
    batch = next(m for m in msgs if m["type"] == "records")
    assert batch["records"] == records
    assert batch["window"] == 0
    done = msgs[-1]
    assert done["stats"]["records"] == 120
    # Stages saw the window-clipped context (full window here).
    assert calls["extract"].roi_data[0]["start_frame"] == 0


def test_worker_batches_large_record_sets(monkeypatch):
    records = [({"t": [i]}, i, "roi_0", 0.0) for i in range(1200)]
    _fake_stages(monkeypatch, records)
    q = queue_mod.Queue()
    chunk_worker_main(_payload(queue=q))
    msgs = _drain(q)
    record_msgs = [m for m in msgs if m["type"] == "records"]
    assert [len(m["records"]) for m in record_msgs] == [500, 500, 200]
    assert sum(len(m["records"]) for m in record_msgs) == 1200


def test_worker_reports_cancelled(monkeypatch):
    _fake_stages(monkeypatch, [], extract_exc=PipelineCancelled())
    q = queue_mod.Queue()
    chunk_worker_main(_payload(queue=q))
    types = [m["type"] for m in _drain(q)]
    assert "cancelled" in types and "error" not in types and "done" not in types


def test_worker_reports_error_with_type(monkeypatch):
    _fake_stages(monkeypatch, [], extract_exc=RuntimeError("boom"))
    q = queue_mod.Queue()
    chunk_worker_main(_payload(queue=q))
    msgs = [m for m in _drain(q) if m["type"] == "error"]
    assert len(msgs) == 1 and "RuntimeError" in msgs[0]["error"] and msgs[0]["window"] == 0


def test_worker_with_no_intersecting_rois_done_immediately(monkeypatch):
    calls = _fake_stages(monkeypatch, [])
    q = queue_mod.Queue()
    payload = _payload(queue=q, roi_data=[{"start_frame": 5000, "end_frame": 6000}])
    chunk_worker_main(payload)
    msgs = _drain(q)
    assert msgs[-1] == {"type": "done", "window": 0, "attempt": 0,
                        "stats": {"records": 0}}
    assert "extract" not in calls  # stages never ran


def test_worker_progress_relayed(monkeypatch):
    def fake_extract(ctx, *, progress_cb, cancel_check):
        progress_cb(42, "step")
        return [], {"total_roi_frames": 1, "total_ocr_calls": 0}

    monkeypatch.setattr(chunk_worker, "extract_and_ocr_stage", fake_extract)

    def fake_refine(ctx, ocr_results, *, ocr_stats, progress_cb, cancel_check):
        return []

    def fake_restore(ctx, ocr_results, *, progress_cb, cancel_check):
        return []

    monkeypatch.setattr(chunk_worker, "refine_stage", fake_refine)
    monkeypatch.setattr(chunk_worker, "restore_stage", fake_restore)

    q = queue_mod.Queue()
    chunk_worker_main(_payload(queue=q))
    msgs = _drain(q)
    progress = [m for m in msgs if m["type"] == "progress"]
    assert any(m["pct"] == 42 and m["msg"] == "step" for m in progress)
