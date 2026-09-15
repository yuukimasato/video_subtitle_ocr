# tests/test_chunk_equivalence.py
"""Equivalence gate for chunk-parallel OCR (T5 acceptance).

Runs the REAL pipeline (real spawn processes, real PaddleOCR tiny models)
on the bundled benchmark video twice — single-process vs chunk-parallel —
and asserts the generated ASS files carry identical events (start, end,
text). This is the accuracy red line: splitting must not change output.

Skipped when paddleocr or the benchmark video is unavailable (fresh CI
without models); on dev machines with cached models this runs the real gate.
"""

from __future__ import annotations

import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import cv2
import pytest

pytest.importorskip("paddleocr")

from core import chunk_planner, chunk_parallel_runner
from core import pipeline_stages
from core import subtitle_generator
from core.pipeline_stages import PipelineContext

VIDEO = os.path.join(PROJECT_ROOT, "benchmarks", "test_video_subtitle.mp4")

pytestmark = pytest.mark.skipif(
    not os.path.exists(VIDEO), reason="benchmark video not found")


def _probe(path):
    cap = cv2.VideoCapture(path)
    info = {
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    cap.release()
    return info


def _make_ctx(video_path, info, work_dir):
    roi_entry = {
        "type": "rect",
        "points": [0, max(0, info["height"] - 160), info["width"], 160],
        "start_time": 0.0,
        "end_time": info["total_frames"] / info["fps"],
    }
    return PipelineContext(
        video_path=video_path,
        roi_data=[roi_entry],
        total_frames=info["total_frames"],
        fps=info["fps"],
        work_dir=work_dir,
        debug_mode=False,
        in_memory_ocr=True,
        visualize=False,
        save_intermediate_json=False,
        ocr_engine_id="paddle",
        engine_options={"lang": "ch", "model_tier": "tiny"},
    )


def _run_sequential(ctx):
    ocr_results, stats = pipeline_stages.extract_and_ocr_stage(
        ctx, progress_cb=lambda p, m: None, cancel_check=lambda: False)
    refined = pipeline_stages.refine_stage(
        ctx, ocr_results, ocr_stats=stats,
        progress_cb=lambda p, m: None, cancel_check=lambda: False)
    return pipeline_stages.restore_stage(
        ctx, refined, progress_cb=lambda p, m: None, cancel_check=lambda: False)


def _run_chunked(ctx, info):
    plan = chunk_planner.plan_chunks(
        info["total_frames"], info["fps"],
        cpu_count=8, available_ram_mb=16384, gpu_mode="cpu",
        window_target_s=5.0, overlap_s=2.5, min_total_s=1.0, max_workers=3,
    )
    assert plan is not None and plan.workers >= 2
    return chunk_parallel_runner.run_chunk_parallel(
        ctx, plan, progress_cb=lambda p, m: None,
        cancel_check=lambda: False)


def _ass_events(path):
    """Extract (start, end, text) from Dialogue lines of an ASS file."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("Dialogue:"):
                continue
            parts = line[len("Dialogue:"):].strip().split(",", 9)
            if len(parts) < 10:
                continue
            start, end, text = parts[1], parts[2], parts[9]
            events.append((start, end, text.strip()))
    return sorted(events)


def _generate_ass(video_path, info, restored, out_path):
    converter = subtitle_generator.OCRToASSOptimizer(
        video_path=video_path,
        output_path=out_path,
        fps=info["fps"],
        width=info["width"],
        height=info["height"],
        template_path=None,
        subtitle_polisher=None,
        source_filter_config=None,
        roi_pose_tags=None,
        roi_text_filter_policies=None,
        watermark_filter_config=None,
    )
    converter.convert_from_memory(iter(restored))
    return out_path


def test_chunk_parallel_equivalence():
    info = _probe(VIDEO)
    with tempfile.TemporaryDirectory() as tmp:
        seq_ctx = _make_ctx(VIDEO, info, os.path.join(tmp, "seq"))
        chunk_ctx = _make_ctx(VIDEO, info, os.path.join(tmp, "chunk"))

        seq_records = _run_sequential(seq_ctx)
        chunk_records = _run_chunked(chunk_ctx, info)

        # Same frames recognized on both sides (dedup by global key).
        seq_keys = {(r[2], r[1]) for r in seq_records}
        chunk_keys = {(r[2], r[1]) for r in chunk_records}
        assert seq_keys == chunk_keys

        seq_ass = _generate_ass(VIDEO, info, seq_records,
                                os.path.join(tmp, "seq.ass"))
        chunk_ass = _generate_ass(VIDEO, info, chunk_records,
                                  os.path.join(tmp, "chunk.ass"))
        assert _ass_events(seq_ass) == _ass_events(chunk_ass)
        assert len(_ass_events(seq_ass)) > 0  # sanity: benchmark has subtitles
