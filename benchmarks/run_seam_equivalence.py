#!/usr/bin/env python3
"""One-off T8 acceptance: seam-hostile fixture, chunked vs sequential.

Generates nothing itself; run make_test_video.py --seam-hostile first.
Compares ASS events from the full single-process pipeline against the
chunk-parallel path (50s windows -> seams at 50s/100s) on the hostile
schedule. Exit 0 == identical events.

Usage:
  python benchmarks/run_seam_equivalence.py --video /tmp/seam_hostile.mp4
"""

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import cv2


def probe(path):
    cap = cv2.VideoCapture(path)
    info = {
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    cap.release()
    return info


def ass_events(path):
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("Dialogue:"):
                continue
            parts = line[len("Dialogue:"):].strip().split(",", 9)
            if len(parts) < 10:
                continue
            events.append((parts[1], parts[2], parts[9].strip()))
    return sorted(events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="/tmp/seam_hostile.mp4")
    args = ap.parse_args()

    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.instance() is None:
        QCoreApplication(sys.argv)

    from core import chunk_planner, chunk_parallel_runner, pipeline_stages
    from core import subtitle_generator
    from core.pipeline_stages import PipelineContext

    info = probe(args.video)
    print(f"video: {info['width']}x{info['height']} @ {info['fps']:.0f} fps, "
          f"{info['total_frames']} frames")
    roi = {
        "type": "rect",
        "points": [0, max(0, info["height"] - 160), info["width"], 160],
        "start_time": 0.0,
        "end_time": info["total_frames"] / info["fps"],
        "fade_in_refine_enabled": True,
    }

    def make_ctx(work_dir):
        return PipelineContext(
            video_path=args.video,
            roi_data=[dict(roi)],
            total_frames=info["total_frames"],
            fps=info["fps"],
            work_dir=work_dir,
            debug_mode=False,
            in_memory_ocr=True,
            save_intermediate_json=False,
            ocr_engine_id="paddle",
            engine_options={"lang": "ch", "model_tier": "tiny"},
        )

    def run_ass(restored, out_path):
        conv = subtitle_generator.OCRToASSOptimizer(
            video_path=args.video, output_path=out_path, fps=info["fps"],
            width=info["width"], height=info["height"],
            template_path=None, subtitle_polisher=None,
            source_filter_config=None, roi_pose_tags=None,
            roi_text_filter_policies=None, watermark_filter_config=None)
        conv.convert_from_memory(iter(restored))
        return out_path

    with tempfile.TemporaryDirectory() as tmp:
        # sequential (GUI-equivalent semantics: refinement ON)
        seq = make_ctx(os.path.join(tmp, "seq"))
        ocr_results, stats = pipeline_stages.extract_and_ocr_stage(
            seq, progress_cb=lambda p, m: None, cancel_check=lambda: False)
        refined = pipeline_stages.refine_stage(
            seq, ocr_results, ocr_stats=stats,
            progress_cb=lambda p, m: None, cancel_check=lambda: False)
        seq_restored = pipeline_stages.restore_stage(
            seq, refined, progress_cb=lambda p, m: None, cancel_check=lambda: False)

        # chunk-parallel with seams exactly at 50s / 100s
        plan = chunk_planner.plan_chunks(
            info["total_frames"], info["fps"],
            cpu_count=8, available_ram_mb=16384, gpu_mode="cpu",
            window_target_s=50.0, overlap_s=15.0, min_total_s=1.0, max_workers=3)
        assert plan is not None and plan.workers == 3, plan
        chunk = make_ctx(os.path.join(tmp, "chunk"))
        chunk_restored = chunk_parallel_runner.run_chunk_parallel(
            chunk, plan, progress_cb=lambda p, m: None,
            cancel_check=lambda: False)

        seq_events = ass_events(run_ass(seq_restored, os.path.join(tmp, "seq.ass")))
        chunk_events = ass_events(run_ass(chunk_restored, os.path.join(tmp, "chunk.ass")))

    print(f"sequential events: {len(seq_events)}")
    print(f"chunked events:    {len(chunk_events)}")
    if seq_events == chunk_events:
        print("EQUIVALENT: chunked output == single-process output")
        return 0
    print("MISMATCH:")
    for e in seq_events:
        if e not in chunk_events:
            print("  seq-only:", e)
    for e in chunk_events:
        if e not in seq_events:
            print("  chunk-only:", e)
    return 1


if __name__ == "__main__":
    sys.exit(main())
