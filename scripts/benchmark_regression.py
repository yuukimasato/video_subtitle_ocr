#!/usr/bin/env python3
# scripts/benchmark_regression.py
"""
回归基准脚本（headless，可独立运行，不依赖 GUI/线程）。

直接组合流水线阶段：roi_extractor.extract_roi_frames → OcrOptimizer.process_roi_group
→ coordinate_restorer.restore_coordinates → subtitle_generator.OCRToASSOptimizer，
与 core/pipeline_worker.py 的 run() 中段调用方式一致（但不用 PipelineWorker/QThread）。

测试视频:
    推荐（无 shell 引号问题，同时导出 ground-truth JSON）:
        python benchmarks/make_test_video.py --video test_video_subtitle.mp4 --gt gt.json
    仓库外层的 generate_test_video.sh 内置同一份 SUBTITLES ground-truth，
    但目前在 bash 5.2 下存在引号解析 bug（第 34/39 行），暂不可用。
    用 --make-ground-truth 也可直接导出默认 ground-truth JSON:
        python scripts/benchmark_regression.py --make-ground-truth gt.json

用法:
    python scripts/benchmark_regression.py --video <mp4> --ground-truth <json> \
        --output <report.json> [--lang ch] [--model-tier auto] [--engine paddle] \
        [--roi x,y,w,h] [--baseline <prev_report.json>] [--keep-temp]

ground-truth JSON 格式:
    [{"start": 0.5, "end": 3.5, "text": "这是一段测试字幕"}, ...]

指标:
    - 字幕行准确率: 每条 ground-truth 字幕按时间重叠匹配 OCR 输出，
      Levenshtein 相似度 >= 0.95 记为正确，行准确率 = 正确条数 / 总条数。
    - ocr_calls / frames_filled（来自 OcrOptimizer 统计）。
    - 各阶段耗时（ROI 提取 / OCR / 坐标还原 / ASS 生成 / 总计）。
    - ru_maxrss 内存峰值（KB, Linux）。

输出:
    JSON 报告写入 --output；--baseline 可与历史报告对比并打印 delta。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import tempfile
import time
import datetime

# Make `import core.*` work when running as a script from anywhere.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _install_llm_import_fallbacks() -> None:
    """Offline fallbacks so headless benchmarking works without openai/tenacity.

    The benchmark never invokes the LLM polish path (subtitle_polisher=None),
    but core.subtitle_generator -> subtitle_llm_polish -> llm_client imports
    openai/tenacity at module level. Minimal stubs keep the import chain alive
    when these optional packages are not installed in the benchmark venv.
    """
    import types

    try:
        import openai  # noqa: F401
    except ImportError:
        openai_stub = types.ModuleType("openai")

        class _OpenAI:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("LLM client is not available in the benchmark")

        class RateLimitError(Exception):
            pass

        openai_stub.OpenAI = _OpenAI
        openai_stub.RateLimitError = RateLimitError
        sys.modules["openai"] = openai_stub

    try:
        import tenacity  # noqa: F401
    except ImportError:
        tenacity_stub = types.ModuleType("tenacity")

        class _PolicySpec:
            def __init__(self, *args, **kwargs):
                pass

            def __or__(self, other):
                return self

        def _retry(**dkw):
            def decorator(fn):
                return fn

            return decorator

        tenacity_stub.retry = _retry
        tenacity_stub.RetryCallState = object
        tenacity_stub.retry_if_exception_type = _PolicySpec
        tenacity_stub.stop_after_attempt = _PolicySpec
        tenacity_stub.stop_after_delay = _PolicySpec
        tenacity_stub.wait_random_exponential = _PolicySpec
        sys.modules["tenacity"] = tenacity_stub


_install_llm_import_fallbacks()

import cv2  # noqa: E402

from PySide6.QtCore import QCoreApplication  # noqa: E402

from core import coordinate_restorer, roi_extractor, subtitle_generator  # noqa: E402
from core.ocr_optimizer import OcrOptimizer  # noqa: E402
from core.pipeline_worker import PipelineWorker  # noqa: E402

try:
    import Levenshtein
except ImportError:  # pragma: no cover
    Levenshtein = None

# ---------------------------------------------------------------------------
# Default ground truth: mirrors the SUBTITLES array in the outer-repo
# ../generate_test_video.sh (keep both in sync when the script changes).
# ---------------------------------------------------------------------------
DEFAULT_GROUND_TRUTH = [
    {"start": 0.5, "end": 3.5, "text": "这是一段测试字幕"},
    {"start": 4.0, "end": 7.0, "text": "欢迎使用视频字幕OCR系统"},
    {"start": 7.5, "end": 10.5, "text": "今天天气真不错"},
    {"start": 11.0, "end": 13.0, "text": "我们一起去公园散步吧"},
    # Fade-in 13.5→14.0, fade-out 15.0→15.5（边界精修用例）。
    {"start": 13.5, "end": 15.5, "text": "淡入淡出的字幕"},
]

ROW_ACCURACY_SIMILARITY_THRESHOLD = 0.95
MIN_OVERLAP_RATIO = 0.3  # min overlap fraction of the shorter duration to count as a match


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_qt_app():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
    return app


def levenshtein_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0, 1]."""
    a, b = a or "", b or ""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if Levenshtein is not None:
        return 1.0 - Levenshtein.distance(a, b) / max(len(a), len(b))
    # Fallback: SequenceMatcher ratio (close cousin of Levenshtein).
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_roi(spec: str | None, video_w: int, video_h: int) -> dict:
    """Build a rect ROI entry. Default: bottom 160px, full width, full duration."""
    if spec:
        x, y, w, h = (int(p) for p in spec.split(","))
    else:
        x, y, w, h = 0, max(0, video_h - 160), video_w, 160
    return {"type": "rect", "points": [x, y, w, h], "start_time": 0.0, "end_time": None}


def probe_video(video_path: str) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    info = {
        "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": float(cap.get(cv2.CAP_PROP_FPS)) or 30.0,
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    cap.release()
    info["duration_sec"] = info["total_frames"] / info["fps"] if info["fps"] > 0 else 0.0
    return info


def collect_ocr_segments(restored_results, fps: float) -> list:
    """Turn per-frame restored OCR results into time-tagged text segments.

    restored_results items: (ocr_data, frame_num, roi_identifier, frame_time_sec).
    Consecutive frames with the same text fingerprint merge into one segment.
    """
    per_frame = []
    for item in restored_results:
        data, frame_num = item[0], int(item[1])
        t = float(item[3]) if len(item) >= 4 and item[3] is not None else 0.0
        if t <= 0 and fps > 0:
            t = frame_num / fps
        texts = [str(x).strip() for x in (data.get("rec_texts") or []) if str(x).strip()]
        if not texts:
            continue
        per_frame.append((t, frame_num, texts))

    per_frame.sort(key=lambda x: x[0])
    max_gap_frames = max(2, int(round(fps * 0.2)))
    segments = []
    cur = None
    for t, frame_num, texts in per_frame:
        fp = tuple(texts)
        if cur and fp == cur["fingerprint"] and frame_num - cur["last_frame"] <= max_gap_frames:
            cur["last_frame"] = frame_num
            cur["end"] = t + (1.0 / fps if fps > 0 else 0.033)
        else:
            if cur:
                segments.append(cur)
            cur = {
                "fingerprint": fp,
                "texts": texts,
                "start": t,
                "end": t + (1.0 / fps if fps > 0 else 0.033),
                "last_frame": frame_num,
            }
    if cur:
        segments.append(cur)
    return segments


def match_ground_truth(ground_truth: list, segments: list) -> dict:
    """Match each ground-truth subtitle to the best overlapping OCR segment."""
    per_subtitle = []
    correct = 0
    for gt in ground_truth:
        gt_start, gt_end, gt_text = float(gt["start"]), float(gt["end"]), str(gt["text"])
        best_sim, best_seg_text = 0.0, ""
        best_seg = None
        for seg in segments:
            overlap = min(gt_end, seg["end"]) - max(gt_start, seg["start"])
            if overlap <= 0:
                continue
            shorter = max(1e-6, min(gt_end - gt_start, seg["end"] - seg["start"]))
            if overlap < MIN_OVERLAP_RATIO * shorter:
                continue
            candidates = [" ".join(seg["texts"])] + list(seg["texts"])
            sim = max(levenshtein_similarity(gt_text, c) for c in candidates)
            if sim > best_sim:
                best_sim, best_seg_text = sim, " ".join(seg["texts"])
                best_seg = seg
        hit = best_sim >= ROW_ACCURACY_SIMILARITY_THRESHOLD
        correct += 1 if hit else 0
        matched_start = float(best_seg["start"]) if best_seg else None
        matched_end = float(best_seg["end"]) if best_seg else None
        per_subtitle.append({
            "text": gt_text,
            "start": gt_start,
            "end": gt_end,
            "best_similarity": round(best_sim, 4),
            "matched_ocr": best_seg_text,
            "correct": hit,
            "matched_start": matched_start,
            "matched_end": matched_end,
            "start_err_sec": (
                round(matched_start - gt_start, 4) if matched_start is not None else None
            ),
            "end_err_sec": (
                round(matched_end - gt_end, 4) if matched_end is not None else None
            ),
        })

    total = max(1, len(ground_truth))
    start_errs = [abs(s["start_err_sec"]) for s in per_subtitle if s["start_err_sec"] is not None]
    end_errs = [abs(s["end_err_sec"]) for s in per_subtitle if s["end_err_sec"] is not None]
    return {
        "row_accuracy": round(correct / total, 4),
        "correct_rows": correct,
        "total_rows": len(ground_truth),
        "avg_best_similarity": round(
            sum(s["best_similarity"] for s in per_subtitle) / total, 4
        ),
        "avg_abs_start_err_sec": round(sum(start_errs) / len(start_errs), 4) if start_errs else None,
        "avg_abs_end_err_sec": round(sum(end_errs) / len(end_errs), 4) if end_errs else None,
        "max_abs_boundary_err_sec": (
            round(max(start_errs + end_errs), 4) if (start_errs and end_errs) else None
        ),
        "per_subtitle": per_subtitle,
    }


def load_json_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_with_baseline(report: dict, baseline_path: str) -> None:
    base = load_json_file(baseline_path)

    def _get(rep, path):
        cur = rep
        for key in path.split("."):
            cur = cur.get(key, {}) if isinstance(cur, dict) else {}
        return cur

    rows = [
        ("row_accuracy", ["results", "row_accuracy"], None),
        ("avg_best_similarity", ["results", "avg_best_similarity"], None),
        ("ocr_calls", ["results", "ocr_calls"], None),
        ("frames_filled", ["results", "frames_filled"], None),
        ("total_sec", ["stage_timings_sec", "total"], 4),
        ("extract_sec", ["stage_timings_sec", "extract_roi"], 4),
        ("ocr_sec", ["stage_timings_sec", "ocr"], 4),
        ("restore_sec", ["stage_timings_sec", "restore"], 4),
        ("ass_sec", ["stage_timings_sec", "ass"], 4),
        ("ru_maxrss_kb", ["memory", "ru_maxrss_kb"], None),
    ]
    print(f"\n=== Baseline comparison (baseline: {baseline_path}) ===")
    print(f"{'metric':<24}{'baseline':>14}{'current':>14}{'delta':>14}")
    for name, path, rnd in rows:
        b, c = _get(base, ".".join(path)), _get(report, ".".join(path))
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            continue
        delta = c - b
        print(f"{name:<24}{b:>14.4f}{c:>14.4f}{delta:>+14.4f}")
    print()


def write_report(report: dict, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(args, video_info: dict, roi_entry: dict) -> dict:
    video_path = os.path.abspath(args.video)
    output_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    ass_path = os.path.join(output_dir, f"{video_name}.benchmark.ass")

    work_dir = tempfile.mkdtemp(prefix=f"{video_name}_bench_")
    report = {"work_dir": work_dir}
    fps = video_info["fps"]
    total_frames = video_info["total_frames"]

    try:
        # ── Stage 1: ROI frame extraction (in-memory) ──
        t0 = time.perf_counter()
        roi_frames = list(
            roi_extractor.extract_roi_frames(
                video_path,
                [roi_entry],
                total_frames,
                fps,
                work_dir,
                save_to_disk=False,
            )
        )
        t1 = time.perf_counter()

        # ── Stage 2: optimized OCR ──
        model_tier = None if (args.model_tier or "auto").lower() in ("", "auto", "none", "null") \
            else args.model_tier
        engine_options = {"lang": args.lang, "model_tier": model_tier}
        if getattr(args, "ocr_version", None):
            engine_options["ocr_version"] = args.ocr_version
        # Only pass kwargs the installed OcrOptimizer actually supports, so the
        # same script can benchmark both the current and the legacy (pre-upgrade)
        # code base (e.g. via a git worktree).
        import inspect

        supported = inspect.signature(OcrOptimizer.__init__).parameters
        optimizer_kwargs = {
            "work_dir": work_dir,
            "visualize": False,
            "in_memory_mode": True,
            "save_ocr_json": False,
            "ocr_engine_id": (args.engine or ""),
            "engine_options": engine_options,
        }
        optimizer_kwargs = {k: v for k, v in optimizer_kwargs.items() if k in supported}
        if "engine_options" not in supported and getattr(args, "ocr_version", None):
            print("NOTE: legacy code base ignores --ocr-version (no engine_options support).")
        optimizer = OcrOptimizer(**optimizer_kwargs)
        t2 = time.perf_counter()
        ocr_results = optimizer.process_roi_group(
            roi_frames,
            is_cancelled_func=lambda: False,
            progress_callback=None,
        )
        optimizer.cleanup()
        t3 = time.perf_counter()

        # ── Stage 2.5: boundary refinement (fade in/out) — mirrors the GUI
        # pipeline. The benchmark ROI mirrors auto-detected ROIs, which enable
        # refinement by default.
        if not roi_entry.get("fade_in_refine_enabled"):
            roi_entry["fade_in_refine_enabled"] = True
        refine_worker = PipelineWorker(
            video_path=video_path,
            roi_data=[roi_entry],
            total_frames=video_info["total_frames"],
            fps=fps,
            video_width=video_info["width"],
            video_height=video_info["height"],
            output_ass_path=ass_path,
            debug_mode=False,
            template_path=None,
        )
        ocr_results = refine_worker._refine_fade_in_boundaries(
            ocr_results,
            max_backtrack_frames=max(3, int(fps * 1.0)) if fps > 0 else 25,
            max_forward_frames=max(2, int(fps * 0.5)) if fps > 0 else 12,
            edge_extend_frames=max(2, int(fps * 2.0)) if fps > 0 else 50,
        )

        # ── Stage 3: coordinate restoration (ROI crop -> full video space) ──
        restored_results = list(
            coordinate_restorer.restore_coordinates(
                iter(ocr_results),
                work_dir,
                save_json=False,
            )
        )
        t4 = time.perf_counter()

        # ── Stage 4: ASS subtitle generation ──
        converter = subtitle_generator.OCRToASSOptimizer(
            video_path=video_path,
            output_path=ass_path,
            fps=fps,
            width=video_info["width"],
            height=video_info["height"],
            template_path=None,
            subtitle_polisher=None,
            source_filter_config=None,
        )
        converter.convert_from_memory(iter(restored_results))
        t5 = time.perf_counter()

        segments = collect_ocr_segments(restored_results, fps)

        report.update({
            "ass_path": ass_path,
            "total_roi_frames": len(roi_frames),
            "ocr_calls": int(getattr(optimizer, "ocr_calls", 0)),
            "frames_filled": int(getattr(optimizer, "frames_filled", 0)),
            "ocr_segment_count": len(segments),
            "stage_timings_sec": {
                "extract_roi": round(t1 - t0, 4),
                "ocr": round(t3 - t2, 4),
                "restore": round(t4 - t3, 4),
                "ass": round(t5 - t4, 4),
                "total": round(t5 - t0, 4),
            },
            # Consumed by main() for ground-truth matching (not serialized).
            "_segments": segments,
        })
        return report
    finally:
        if not args.keep_temp:
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="video_subtitle_ocr regression benchmark (headless pipeline runner)"
    )
    parser.add_argument("--video", help="input test video (mp4)")
    parser.add_argument("--ground-truth", dest="ground_truth", help="ground-truth JSON path")
    parser.add_argument("--output", help="output report JSON path")
    parser.add_argument("--lang", default="ch", help="OCR language id (default: ch)")
    parser.add_argument("--model-tier", dest="model_tier", default="auto",
                        help="model tier: auto/tiny/small/medium (default: auto)")
    parser.add_argument("--ocr-version", dest="ocr_version", default=None,
                        help="explicit model generation override, e.g. PP-OCRv5 "
                             "(current code only; ignored by the legacy code base)")
    parser.add_argument("--engine", default="paddle", help="engine id (default: paddle)")
    parser.add_argument("--roi", default=None,
                        help="ROI as x,y,w,h (default: bottom 160px full width, full duration)")
    parser.add_argument("--baseline", default=None, help="previous report JSON to compare against")
    parser.add_argument("--keep-temp", dest="keep_temp", action="store_true",
                        help="keep the temporary working directory")
    parser.add_argument("--make-ground-truth", dest="make_ground_truth", metavar="JSON_PATH",
                        help="write the default ground-truth JSON (from generate_test_video.sh) "
                             "to the given path and exit")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.make_ground_truth:
        write_report(DEFAULT_GROUND_TRUTH, args.make_ground_truth)
        print(f"Default ground truth written: {args.make_ground_truth} "
              f"({len(DEFAULT_GROUND_TRUTH)} subtitles)")
        return 0

    missing = [name for name in ("video", "ground_truth", "output") if not getattr(args, name)]
    if missing:
        print("ERROR: missing required arguments: "
              + ", ".join("--" + m.replace("_", "-") for m in missing))
        return 2
    if not os.path.isfile(args.video):
        print(f"ERROR: video not found: {args.video}")
        return 2

    make_qt_app()

    ground_truth = load_json_file(args.ground_truth)
    if not isinstance(ground_truth, list) or not ground_truth:
        print(f"ERROR: ground truth must be a non-empty JSON list: {args.ground_truth}")
        return 2

    video_info = probe_video(args.video)
    roi_entry = parse_roi(args.roi, video_info["width"], video_info["height"])
    if roi_entry.get("end_time") is None:
        roi_entry["end_time"] = video_info["duration_sec"]

    print(f"Video: {args.video}")
    print(f"  {video_info['width']}x{video_info['height']} @ {video_info['fps']:.2f} fps, "
          f"{video_info['total_frames']} frames ({video_info['duration_sec']:.2f}s)")
    print(f"ROI: {roi_entry['points']}  lang={args.lang}  tier={args.model_tier}  "
          f"engine={args.engine}")

    result = run_pipeline(args, video_info, roi_entry)

    def _env_version(module_name: str):
        try:
            import importlib

            mod = importlib.import_module(module_name)
            return str(getattr(mod, "__version__", None) or "unknown")
        except Exception:
            return None

    segs_for_match = result.pop("_segments")
    match = match_ground_truth(ground_truth, segs_for_match)

    report = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "video": {
            "path": os.path.abspath(args.video),
            "width": video_info["width"],
            "height": video_info["height"],
            "fps": round(video_info["fps"], 4),
            "total_frames": video_info["total_frames"],
            "duration_sec": round(video_info["duration_sec"], 4),
        },
        "config": {
            "lang": args.lang,
            "model_tier": args.model_tier,
            "ocr_version": getattr(args, "ocr_version", None),
            "engine": args.engine,
            "roi": roi_entry["points"],
        },
        "environment": {
            "python": platform.python_version(),
            "paddleocr": _env_version("paddleocr"),
            "rapidocr": _env_version("rapidocr"),
        },
        "results": {
            "ass_path": result.get("ass_path"),
            "total_roi_frames": result.get("total_roi_frames"),
            "ocr_calls": result.get("ocr_calls"),
            "frames_filled": result.get("frames_filled"),
            "ocr_segment_count": result.get("ocr_segment_count"),
            **match,
        },
        "stage_timings_sec": result.get("stage_timings_sec"),
        "memory": {
            # ru_maxrss is peak RSS in KB on Linux.
            "ru_maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }

    write_report(report, args.output)
    print("\n=== Benchmark results ===")
    print(f"  row accuracy       : {report['results']['row_accuracy']:.2%} "
          f"({report['results']['correct_rows']}/{report['results']['total_rows']})")
    print(f"  avg best similarity: {report['results']['avg_best_similarity']:.4f}")
    print(f"  ocr_calls          : {report['results']['ocr_calls']} "
          f"(frames filled: {report['results']['frames_filled']})")
    print(f"  stage timings (s)  : {report['stage_timings_sec']}")
    print(f"  peak RSS (KB)      : {report['memory']['ru_maxrss_kb']}")
    print(f"  ASS output         : {report['results']['ass_path']}")
    print(f"Report written: {args.output}")

    if args.baseline:
        compare_with_baseline(report, args.baseline)

    return 0


if __name__ == "__main__":
    sys.exit(main())
