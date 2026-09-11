#!/usr/bin/env python3
"""video-subtitle-ocr-cli — headless subtitle OCR pipeline (no GUI required).

Runs the same four pipeline stages as the GUI (ROI frame extraction →
optimized OCR → coordinate restoration → ASS subtitle generation) directly
from the command line, so subtitle extraction can be scripted or run on
servers without a display.

Examples:
    # Bottom subtitle strip, whole video:
    video-subtitle-ocr-cli video.mp4

    # Custom ROI active only between 5s and 20s, plus a style template:
    video-subtitle-ocr-cli video.mp4 --roi 0,560,1280,160@5-20 --template style.ass

    # RapidOCR engine, explicit output path, keep temp frames for debugging:
    video-subtitle-ocr-cli video.mp4 --engine rapid -o out.ass --keep-temp
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time

__version__ = "2.4.2"

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _info(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def _positive_int(value: str) -> int:
    iv = int(value)
    if iv < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer: {value!r}")
    return iv


def parse_roi_spec(spec: str) -> dict:
    """Parse "x,y,w,h" or "x,y,w,h@start-end" into a rect ROI entry.

    start/end are seconds; either side may be omitted ("@5-", "@-20").
    Omitted start = 0, omitted end = None (video end).
    """
    m = re.match(
        r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
        r"(?:\s*@\s*([\d.]*)\s*-\s*([\d.]*)\s*)?$",
        spec,
    )
    if not m:
        raise ValueError(
            f"Invalid ROI spec: {spec!r} (expected x,y,w,h[@start-end])"
        )
    x, y, w, h = (int(g) for g in m.group(1, 2, 3, 4))
    if w <= 0 or h <= 0:
        raise ValueError(f"ROI width/height must be positive: {spec!r}")
    start_raw, end_raw = m.group(5), m.group(6)
    start_time = float(start_raw) if start_raw else 0.0
    end_time = float(end_raw) if end_raw else None
    if end_time is not None and end_time <= start_time:
        raise ValueError(f"ROI end time must be after start time: {spec!r}")
    return {
        "type": "rect",
        "points": [x, y, w, h],
        "start_time": start_time,
        "end_time": end_time,
        # 帧级边界精修（首/末帧逐帧复核）默认开启。
        "fade_in_refine_enabled": True,
    }


def probe_video(video_path: str) -> dict:
    import cv2

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
    return info


def run_pipeline(args: argparse.Namespace) -> int:
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        QCoreApplication(sys.argv)

    from core import coordinate_restorer, roi_extractor, subtitle_generator
    from core.ocr_optimizer import OcrOptimizer

    video_path = os.path.abspath(args.video)
    if not os.path.isfile(video_path):
        _info(f"Error: video not found: {video_path}", args.quiet)
        return 1

    out_path = os.path.abspath(args.output) if args.output else \
        os.path.splitext(video_path)[0] + ".ass"

    info = probe_video(video_path)
    _info(
        f"Video: {video_path}\n"
        f"  {info['width']}x{info['height']} @ {info['fps']:.2f} fps, "
        f"{info['total_frames']} frames",
        args.quiet,
    )

    watermark_config = None
    if getattr(args, "scan", False):
        # ── Scan mode: full-frame sampling → band/scene/watermark census ──
        from core.fullframe_scanner import perform_scan

        engine_id = "" if args.engine == "auto" else args.engine
        _info(
            f"[0/4] Scanning whole video ({args.scan_samples} samples)...",
            args.quiet,
        )
        report = perform_scan(
            video_path,
            sample_count=args.scan_samples,
            lang=args.lang,
            model_tier=(args.model_tier or "auto"),
            ocr_engine_id=engine_id or "paddle",
            with_snapshots=False,
        )
        if report.is_empty:
            _info("Scan found no subtitle bands, watermarks or scene text.", args.quiet)
            return 1

        roi_entries = list(report.band_rois)
        if not args.no_scene_roi:
            roi_entries.extend(c["roi_entry"] for c in report.scene_candidates)
        if not roi_entries:
            _info("Scan found no usable subtitle bands/scene regions.", args.quiet)
            return 1

        for w in report.watermarks:
            _info(
                f"  watermark: {w['text']!r} presence={w['presence']:.0%} "
                f"bbox={tuple(round(v) for v in w['bbox'])}",
                args.quiet,
            )
        for c in report.scene_candidates:
            _info(
                f"  scene: {c['text']!r} hits={c['hit_count']} "
                f"bbox={tuple(round(v) for v in c['bbox'])}",
                args.quiet,
            )
        if args.watermark_mode == "auto" and report.watermarks:
            watermark_config = {
                "enabled": True,
                "entries": [
                    {"text": w.get("text", ""), "bbox": w.get("bbox")}
                    for w in report.watermarks
                ],
            }
    elif args.roi:
        try:
            roi_entries = [parse_roi_spec(s) for s in args.roi]
        except ValueError as e:
            _info(f"Error: {e}", args.quiet)
            return 2
    else:
        # 默认底部条带取帧高的 20%（至少 160px）：固定 160px 在 1080p 及更高
        # 分辨率下容不下双行字幕，首行会被条带上边缘截掉导致整行丢失。
        strip_h = max(160, round(info["height"] * 0.2))
        roi_entries = [{
            "type": "rect",
            "points": [0, max(0, info["height"] - strip_h), info["width"], strip_h],
            "start_time": 0.0,
            "end_time": None,
            # 帧级边界精修（首/末帧逐帧复核）默认开启。
            "fade_in_refine_enabled": True,
        }]
    # roi_extractor.get_roi_frame_number expects numeric times; resolve an
    # open-ended ROI ("end": None) to the video duration, like the benchmark does.
    duration_sec = info["total_frames"] / info["fps"] if info["fps"] > 0 else 0.0
    for r in roi_entries:
        if r.get("end_time") is None:
            r["end_time"] = duration_sec
    for r in roi_entries:
        _info(
            f"ROI: {r['points']} time=[{r['start_time']}, {r['end_time']}]",
            args.quiet,
        )

    work_dir = tempfile.mkdtemp(prefix="vso_cli_")
    if args.keep_temp:
        _info(f"Temp work dir: {work_dir} (kept)", args.quiet)

    try:
        # ── Stage 1: ROI frame extraction (in-memory) ──
        t0 = time.perf_counter()
        _info("[1/4] Extracting ROI frames...", args.quiet)
        roi_frames = list(
            roi_extractor.extract_roi_frames(
                video_path,
                roi_entries,
                info["total_frames"],
                info["fps"],
                work_dir,
                save_to_disk=False,
            )
        )
        t1 = time.perf_counter()
        _info(f"      {len(roi_frames)} ROI frames extracted ({t1 - t0:.2f}s)", args.quiet)
        if not roi_frames:
            _info("No frames fell inside any ROI time range; nothing to do.", args.quiet)
            return 1

        # ── Stage 2: optimized OCR ──
        _info(f"[2/4] OCR ({args.engine} engine, lang={args.lang})...", args.quiet)
        model_tier = None if (args.model_tier or "auto").lower() in ("", "auto") \
            else args.model_tier
        engine_options = {"lang": args.lang, "model_tier": model_tier}
        import inspect

        supported = inspect.signature(OcrOptimizer.__init__).parameters
        optimizer_kwargs = {
            k: v for k, v in {
                "work_dir": work_dir,
                "visualize": False,
                "in_memory_mode": True,
                "save_ocr_json": False,
                "ocr_engine_id": ("" if args.engine == "auto" else args.engine),
                "engine_options": engine_options,
            }.items() if k in supported
        }
        optimizer = OcrOptimizer(**optimizer_kwargs)

        group_count = 0

        def _progress_cb(_count: int) -> None:
            nonlocal group_count
            group_count += 1
            if not args.quiet:
                print(f"\r      OCR groups processed: {group_count}", end="", file=sys.stderr, flush=True)

        ocr_results = optimizer.process_roi_group(
            roi_frames,
            is_cancelled_func=lambda: False,
            progress_callback=None if args.quiet else _progress_cb,
        )
        optimizer.cleanup()
        t2 = time.perf_counter()
        ocr_calls = int(getattr(optimizer, "ocr_calls", 0))
        if not args.quiet:
            print(file=sys.stderr)
        _info(
            f"      {ocr_calls} OCR calls covered {len(ocr_results)} frames "
            f"({t2 - t1:.2f}s)",
            args.quiet,
        )

        # ── Stage 3: coordinate restoration ──
        _info("[3/4] Restoring ROI coordinates to full-frame space...", args.quiet)
        restored_results = list(
            coordinate_restorer.restore_coordinates(
                iter(ocr_results),
                work_dir,
                save_json=False,
            )
        )
        t3 = time.perf_counter()
        _info(f"      {len(restored_results)} frames restored ({t3 - t2:.2f}s)", args.quiet)

        # ── Stage 4: ASS subtitle generation ──
        _info("[4/4] Generating ASS subtitles...", args.quiet)
        # Per-ROI text filter policy: manually specified ROIs (and the legacy
        # default strip) are keep-all; scan-detected band ROIs carry "auto"
        # and honor the scene preset below.
        roi_policies = {
            f"roi_{i}": str(entry.get("text_filter_policy") or "keep_all")
            for i, entry in enumerate(roi_entries)
        }
        source_filter_config = None
        if args.preset:
            try:
                from core.scene_presets import get_preset_by_id
                preset = get_preset_by_id(args.preset)
            except Exception:
                preset = None
            if preset is None:
                _info(f"Error: unknown preset id: {args.preset}", args.quiet)
                return 2
            source_filter_config = {
                "enabled": True,
                "preset_id": args.preset,
                "keep_overlay": preset.keep_overlay,
                "keep_scene": preset.keep_scene,
                "keep_unknown": preset.keep_unknown,
                "classifier_weights": preset.classifier_weight_override,
                "classification_bias": preset.classification_bias,
                "min_classification_confidence": preset.min_classification_confidence,
                "llm_assist_enabled": False,
                "llm_config": None,
            }
        converter = subtitle_generator.OCRToASSOptimizer(
            video_path=video_path,
            output_path=out_path,
            fps=info["fps"],
            width=info["width"],
            height=info["height"],
            template_path=args.template,
            subtitle_polisher=None,
            source_filter_config=source_filter_config,
            roi_text_filter_policies=roi_policies or None,
            watermark_filter_config=watermark_config,
        )
        converter.convert_from_memory(iter(restored_results))
        t4 = time.perf_counter()
        _info(f"      done ({t4 - t3:.2f}s)", args.quiet)

        _info(
            f"\nFinished in {t4 - t0:.2f}s total.\n"
            f"ASS subtitle file: {out_path}",
            args.quiet,
        )
        if not os.path.isfile(out_path):
            _info("Warning: output file was not created.", args.quiet)
            return 1
        return 0
    finally:
        if not args.keep_temp:
            shutil.rmtree(work_dir, ignore_errors=True)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="video-subtitle-ocr-cli",
        description="Extract hard subtitles from a video into an ASS subtitle "
                    "file (headless pipeline, no GUI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[-1],
    )
    parser.add_argument("video", help="input video file")
    parser.add_argument("-o", "--output", help="output .ass path (default: <video>.ass)")
    parser.add_argument(
        "--roi", action="append", metavar="SPEC",
        help='rect ROI "x,y,w,h" or "x,y,w,h@start-end" (seconds, e.g. '
             '"0,560,1280,160@5-20"). Repeatable. Default: bottom strip of 20%% '
             "of the frame height (min 160px), whole video.",
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="full-video scan mode: sample frames across the whole video, "
             "auto-detect subtitle bands (top+bottom) and scene text, and "
             "detect stationary watermark text; replaces the default ROI",
    )
    parser.add_argument(
        "--scan-samples", type=_positive_int, default=24, metavar="N",
        help="frames sampled by --scan (default: 24)",
    )
    parser.add_argument(
        "--no-scene-roi", action="store_true",
        help="with --scan, do not import mid-frame scene-text candidates as ROIs",
    )
    parser.add_argument(
        "--watermark-mode", default="auto", choices=["auto", "keep"],
        help="with --scan: 'auto' drops detected watermark text lines, "
             "'keep' only reports them (default: auto)",
    )
    parser.add_argument(
        "--preset", metavar="ID",
        help="scene preset id (e.g. film_tv, anime, live_stream) enabling "
             "text-source filtering for auto-detected band ROIs",
    )
    parser.add_argument(
        "--engine", default="auto", choices=["auto", "paddle", "rapid"],
        help="OCR engine (default: auto = first available)",
    )
    parser.add_argument("--lang", default="ch", help="recognition language (default: ch)")
    parser.add_argument(
        "--model-tier", default="auto", choices=["auto", "tiny", "small", "medium"],
        help="PP-OCRv6 model tier (default: auto)",
    )
    parser.add_argument("--template", help="external .ass file as style template")
    parser.add_argument("--keep-temp", action="store_true", help="keep temp work dir")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run_pipeline(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        # Keep the traceback on stderr for diagnosis (quiet mode keeps the
        # one-line message only).
        if not args.quiet:
            import traceback
            traceback.print_exc()
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
