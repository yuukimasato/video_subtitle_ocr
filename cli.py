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

    # Moving-text plane (phone screen / letter / sign): track a hand-picked
    # quad between 6.5s and 10.5s, emit \\move trajectory events that follow
    # the motion (and the screen brightness) into the same ASS:
    video-subtitle-ocr-cli video.mp4 --motion-quad "820,300 1090,300 1090,520 820,520@6.5-10.5" --auto-brightness
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time

__version__ = "2.6.11"

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


def _non_negative_int(value: str) -> int:
    iv = int(value)
    if iv < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer: {value!r}")
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


def parse_motion_quad_spec(spec: str) -> tuple:
    """Parse a moving-text plane quad spec into (quad, start_sec, end_sec).

    Geometry follows scripts/motion_ass.parse_quad_spec:
    "x1,y1 x2,y2 x3,y3 x4,y4" (clockwise TL,TR,BR,BL; any winding is
    auto-corrected later). An optional "@start-end" tail limits tracking to
    a seconds range like --roi; either side may be omitted ("@5-", "@-20").
    """
    from scripts.motion_ass import parse_quad_spec

    base, rng = spec, None
    if "@" in spec:
        base, rng = spec.split("@", 1)
    quad = parse_quad_spec(base)
    start_sec = end_sec = None
    if rng is not None:
        m = re.match(r"^\s*([\d.]*)\s*-\s*([\d.]*)\s*$", rng)
        if not m:
            raise ValueError(
                f"Invalid motion quad time range: {spec!r} "
                "(expected x1,y1 ... x4,y4[@start-end])"
            )
        if m.group(1):
            start_sec = float(m.group(1))
        if m.group(2):
            end_sec = float(m.group(2))
        if (start_sec is not None and end_sec is not None
                and end_sec <= start_sec):
            raise ValueError(
                f"Motion quad end time must be after start time: {spec!r}")
    return quad, start_sec, end_sec


def load_roi_file(path: str) -> list:
    """Load a GUI-saved ROI json into ROI entries (same dict shape as the GUI).

    Accepts either {"rois": [...]} (the GUI save format) or a bare list of
    ROI entries. Each entry must carry at least "type" and "points"; all
    per-ROI flags (write_pose_tags / motion_auto_brightness /
    scene_text_policy / text_filter_policy ...) are kept so the CLI run
    behaves exactly like the GUI pipeline with the same ROI list.
    """
    import json

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    entries = payload.get("rois") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError(
            f"ROI file {path!r}: expected {{\"rois\": [...]}} or a bare list")
    out = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"ROI file {path!r}: entry {i} is not an object")
        if not entry.get("type") or "points" not in entry:
            raise ValueError(
                f"ROI file {path!r}: entry {i} missing 'type'/'points'")
        out.append(entry)
    if not out:
        raise ValueError(f"ROI file {path!r}: no ROI entries")
    return out


def _time_to_sec(value) -> float:
    """ROI 时间字段 → 秒(兼容数值秒与 GUI 的 HH:MM:SS.mmm 字符串)。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = re.match(r"^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$", text)
    if m:
        h, mi, sec = (float(g) for g in m.groups())
        return h * 3600 + mi * 60 + sec
    return 0.0


def _entry_rect(entry: dict):
    """ROI entry → 画面坐标外接矩形 (x1, y1, x2, y2);无法解析返回 None。"""
    rtype = str(entry.get("type", ""))
    points = entry.get("points")
    try:
        if rtype == "rect":
            if not (isinstance(points, (list, tuple)) and len(points) == 4):
                return None
            x, y, w, h = (float(v) for v in points)
            if w <= 0 or h <= 0:
                return None
            return (x, y, x + w, y + h)
        if rtype == "poly":
            if not (isinstance(points, (list, tuple)) and len(points) >= 3):
                return None
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, ValueError, IndexError):
        return None
    return None


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


def _plan_workers(args: argparse.Namespace, info: dict):
    """Decide the chunk-parallel plan for this run (None = single process)."""
    if args.workers == 1:
        return None
    from core import chunk_planner, chunk_parallel_runner

    return chunk_planner.plan_chunks(
        info["total_frames"],
        info["fps"],
        cpu_count=(os.cpu_count() or 1),
        available_ram_mb=chunk_parallel_runner.probe_available_ram_mb(),
        gpu_mode=chunk_parallel_runner.get_device_mode_light(),
        max_workers=max(0, args.workers),
    )


def _engine_options_from_args(args: argparse.Namespace) -> dict:
    model_tier = None if (args.model_tier or "auto").lower() in ("", "auto") \
        else args.model_tier
    return {"lang": args.lang, "model_tier": model_tier}


def _run_chunk_stages(args: argparse.Namespace, video_path: str,
                      roi_entries: list, info: dict, work_dir: str, plan):
    """Stages 1-3 through chunk-parallel workers; returns (records, elapsed).

    Matches the CLI's single-process semantics: no boundary refinement.
    """
    from core import chunk_parallel_runner
    from core.pipeline_stages import PipelineContext

    ctx = PipelineContext(
        video_path=video_path,
        roi_data=roi_entries,
        total_frames=info["total_frames"],
        fps=info["fps"],
        work_dir=work_dir,
        debug_mode=False,
        in_memory_ocr=True,
        visualize=False,
        save_intermediate_json=False,
        ocr_engine_id=("" if args.engine == "auto" else args.engine),
        engine_options=_engine_options_from_args(args),
        enable_boundary_refine=False,
    )
    if not args.quiet:
        print(
            f"[1-3/4] Chunk-parallel OCR: {plan.workers} workers, "
            f"{len(plan.windows)} windows...",
            file=sys.stderr,
        )
    t0 = time.perf_counter()

    def _progress(pct: int, _msg: str) -> None:
        if not args.quiet:
            print(f"\r      parallel progress: {pct:3d}%", end="", file=sys.stderr, flush=True)

    restored = chunk_parallel_runner.run_chunk_parallel(
        ctx, plan, progress_cb=_progress, cancel_check=lambda: False)
    if not args.quiet:
        print(file=sys.stderr)
    return restored, time.perf_counter() - t0


def run_pipeline(args: argparse.Namespace) -> int:
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        # 保留引用:临时对象会被引用计数立即销毁,instance() 复回 None。
        _qt_app = QCoreApplication(sys.argv)

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
    elif args.roi or args.roi_file:
        try:
            roi_entries = [parse_roi_spec(s) for s in (args.roi or [])]
        except ValueError as e:
            _info(f"Error: {e}", args.quiet)
            return 2
        if args.roi_file:
            try:
                # GUI-saved entries first (roi_0... numbering matches the GUI
                # list order), then any interactive --roi specs.
                roi_entries = load_roi_file(args.roi_file) + roi_entries
            except (OSError, ValueError) as e:
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
            f"ROI: {r['points']} time=[{r.get('start_time', 0)}, "
            f"{r.get('end_time')}]", args.quiet
        )

    work_dir = tempfile.mkdtemp(prefix="vso_cli_")
    if args.keep_temp:
        _info(f"Temp work dir: {work_dir} (kept)", args.quiet)

    try:
        t0 = time.perf_counter()
        # ── Long-video speedup: chunk-parallel path when it pays off ──
        chunk_results = None
        if args.workers != 1:
            chunk_plan = _plan_workers(args, info)
            if chunk_plan is not None:
                chunk_results = _run_chunk_stages(
                    args, video_path, roi_entries, info, work_dir, chunk_plan)
        if chunk_results is not None:
            restored_results, chunk_elapsed = chunk_results
            t3 = time.perf_counter()
            _info(f"      {len(restored_results)} frames restored ({chunk_elapsed:.2f}s)", args.quiet)
            if not restored_results:
                _info("No frames fell inside any ROI time range; nothing to do.", args.quiet)
                return 1

        if chunk_results is None:
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

        # ── Moving-text trajectory quads (independent of ROI entries) ──
        # Each --motion-quad / --motion-quad-file runs the trajectory
        # pipeline (track → keyframe OCR → fuse → \move events) and merges
        # the resulting events into the output ASS. A quad failure degrades
        # to a warning and the rest of the pipeline continues.
        # ROI-bound specs (from --roi-file with write_pose_tags on) take the
        # same path; their roi_id is recorded so the generator suppresses the
        # static events of that ROI (fallback keeps them on failure) — same
        # contract as the GUI worker.
        motion_events_all: list = []
        motion_roi_ids: set = set()
        from core.pipeline_worker import collect_motion_roi_specs
        roi_motion_specs = collect_motion_roi_specs(roi_entries)
        if (args.motion_quad or args.motion_quad_file or roi_motion_specs
                or args.motion_auto):
            from scripts.motion_ass import (
                build_motion_events,
                load_quad_file,
                normalize_quad_winding,
                validate_quad,
            )

            motion_specs: list = []
            for spec in args.motion_quad or []:
                try:
                    motion_specs.append((parse_motion_quad_spec(spec), spec))
                except ValueError as e:
                    _info(f"Error: {e}", args.quiet)
                    return 2
            for path in args.motion_quad_file or []:
                try:
                    motion_specs.append(
                        ((load_quad_file(path), None, None), path))
                except (OSError, ValueError) as e:
                    _info(f"Error: cannot load quad file {path}: {e}", args.quiet)
                    return 2

            engine_id = None if args.engine == "auto" else args.engine

            def _run_motion_quad(quad, start_sec, end_sec, label,
                                 auto_brightness, occlusion_clip=None,
                                 start_frame=None, end_frame=None,
                                 scene_text_policy=None):
                nonlocal motion_events_all
                quad = validate_quad(normalize_quad_winding(quad))
                if start_frame is None:
                    start_frame = (
                        int(round(start_sec * info["fps"]))
                        if start_sec is not None else 0)
                if end_frame is None and end_sec is not None:
                    end_frame = int(round(end_sec * info["fps"]))
                _info(
                    f"[3.5/4] Motion trajectory for quad {label} "
                    f"(frames [{start_frame}, "
                    f"{end_frame if end_frame is not None else 'end'}])...",
                    args.quiet,
                )
                events, summary = build_motion_events(
                    video_path, quad,
                    start_frame=start_frame, end_frame=end_frame,
                    scene_text_policy=scene_text_policy,
                    auto_brightness=auto_brightness,
                    brightness_per_line=(
                        args.brightness_per_line if auto_brightness else None),
                    occlusion_clip=occlusion_clip,
                    ocr_engine=engine_id,
                    log=lambda m: _info(f"      {m}", args.quiet),
                )
                motion_events_all.extend(events)
                _info(
                    f"      motion-quad {label}: {len(events)} event(s) "
                    f"({summary['ok_frames']}/{summary['total_frames']} "
                    f"frames ok, keyframes {summary['keyframes']})",
                    args.quiet,
                )

            for (quad, start_sec, end_sec), label in motion_specs:
                try:
                    _run_motion_quad(quad, start_sec, end_sec,
                                     label, args.auto_brightness,
                                     occlusion_clip=args.occlusion_clip)
                except Exception as exc:
                    _info(
                        f"Warning: motion quad {label} failed ({exc}); "
                        "skipped, continuing without it.",
                        args.quiet,
                    )

            for spec in roi_motion_specs:
                try:
                    _run_motion_quad(
                        spec["quad"],
                        None,
                        None,
                        f"{spec['roi_id']} (frames "
                        f"[{spec['start_frame']}, "
                        f"{spec['end_frame'] if spec['end_frame'] is not None else 'end'}])",
                        bool(spec["auto_brightness"]) or args.auto_brightness,
                        occlusion_clip=bool(spec.get("occlusion_clip", False))
                        or args.occlusion_clip,
                        start_frame=spec["start_frame"],
                        end_frame=spec["end_frame"],
                        scene_text_policy=spec.get("scene_text_policy"),
                    )
                    motion_roi_ids.add(str(spec["roi_id"]))
                except Exception as exc:
                    _info(
                        f"Warning: motion ROI {spec['roi_id']} failed ({exc}); "
                        "falling back to its static events.",
                        args.quiet,
                    )

            # ── FR-1 自动检测触发:每个 ROI 外接矩形内采样 OCR 行心位移,
            #    超阈值区域走轨迹管线,接管成功即抑制该 ROI 静态碎片事件。
            if args.motion_auto:
                from core.motion_detector import detect_moving_text

                detected_any = False
                for idx, entry in enumerate(roi_entries):
                    rect = _entry_rect(entry)
                    if rect is None:
                        continue
                    t0f = int(round(_time_to_sec(entry.get("start_time"))
                                    * info["fps"]))
                    t1 = entry.get("end_time")
                    t1f = (int(round(_time_to_sec(t1) * info["fps"]))
                           if t1 is not None else None)
                    try:
                        regions = detect_moving_text(
                            video_path, engine_id=engine_id,
                            start_frame=t0f, end_frame=t1f,
                            region=rect,
                            sample_stride_sec=args.motion_auto_stride,
                            move_thresh_px=args.motion_auto_threshold,
                            log=lambda m: _info(f"      {m}", args.quiet))
                    except Exception as exc:
                        _info(
                            f"Warning: motion auto-detection on roi_{idx} "
                            f"failed ({exc}); skipping it.",
                            args.quiet,
                        )
                        continue
                    for k, region in enumerate(regions):
                        try:
                            _run_motion_quad(
                                region.quad,
                                region.start_frame / info["fps"],
                                region.end_frame / info["fps"],
                                f"auto[roi_{idx}]#{k}",
                                args.auto_brightness,
                                occlusion_clip=args.occlusion_clip)
                            motion_roi_ids.add(f"roi_{idx}")
                            detected_any = True
                        except Exception as exc:
                            _info(
                                f"Warning: auto motion region #{k} of "
                                f"roi_{idx} failed ({exc}); its static "
                                "events remain.",
                                args.quiet,
                            )
                if not detected_any:
                    _info(
                        "motion-auto: no moving text detected (detection is "
                        "scoped to ROI rects; use --roi to cover the moving "
                        "area).",
                        args.quiet,
                    )

        # ── Stage 4: ASS subtitle generation ──
        _info("[4/4] Generating ASS subtitles...", args.quiet)
        # Per-ROI text filter policy: manually specified ROIs (and the legacy
        # default strip) are keep-all; scan-detected band ROIs carry "auto"
        # and honor the scene preset below.
        roi_policies = {
            f"roi_{i}": str(entry.get("text_filter_policy") or "keep_all")
            for i, entry in enumerate(roi_entries)
        }
        # Per-ROI scene-text display policy (overlap/mask/external/whitespace):
        # an explicit (non-overlap) --scene-text-policy overrides the ROI
        # JSON policies; the overlap default leaves them untouched.
        from core.pipeline_worker import (
            apply_cli_scene_text_policy, collect_roi_scene_text_options)
        apply_cli_scene_text_policy(roi_entries, args.scene_text_policy)
        roi_scene_policies, roi_analysis_rects = collect_roi_scene_text_options(
            roi_entries
        )
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
            roi_scene_text_policies=roi_scene_policies or None,
            roi_analysis_rects=roi_analysis_rects or None,
            watermark_filter_config=watermark_config,
            motion_events=motion_events_all or None,
            motion_roi_ids=motion_roi_ids or None,
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
        "--roi-file", metavar="JSON", default=None,
        help='GUI-saved ROI json ({"rois": [...]} or a bare list). Per-ROI '
             "flags are kept: a poly/rect ROI with write_pose_tags on runs "
             "the motion trajectory pipeline (see --motion-quad), "
             "motion_auto_brightness enables brightness-adaptive tags",
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
    parser.add_argument(
        "--workers", type=_non_negative_int, default=0, metavar="N",
        help="chunk-parallel OCR workers for long videos (default: 0 = auto "
             "by cores/RAM; 1 = single process; N = cap the auto count at N)",
    )
    parser.add_argument("--template", help="external .ass file as style template")
    parser.add_argument(
        "--scene-text-policy", default="overlap",
        choices=["overlap", "mask", "mask_only", "external", "whitespace"],
        help="scene-text display policy for --roi entries (default: overlap; "
             "mask_only = cover patch only, recognized text written as "
             "Comment lines for hand re-typesetting; unavailable modes fall "
             "back whitespace->mask->external, mask_only->external)",
    )
    parser.add_argument(
        "--motion-quad", action="append", metavar="SPEC",
        help='moving-text plane quad "x1,y1 x2,y2 x3,y3 x4,y4[@start-end]" '
             "(clockwise TL,TR,BR,BL; @range in seconds like --roi, e.g. "
             '"820,300 1090,300 1090,520 820,520@6.5-10.5"). Runs the '
             "\\move trajectory pipeline and merges the events into the "
             "output ASS. Repeatable.",
    )
    parser.add_argument(
        "--motion-quad-file", action="append", metavar="Q.json",
        help='quad JSON file {"video": ..., "frame": ..., "quad": [[x,y]x4]} '
             "(a bare 4x2 list is also accepted); whole video is tracked. "
             "Repeatable.",
    )
    parser.add_argument(
        "--auto-brightness", action="store_true",
        help="with --motion-quad/--motion-quad-file: measure the text-plane "
             "brightness per frame and append \\1c/\\alpha \\t chains so "
             "trajectory subtitles follow screen dimming/brightening "
             "(default: off)",
    )
    parser.add_argument(
        "--brightness-per-line", action="store_true",
        help="with --auto-brightness: measure each text line's brightness "
             "separately so subtitles follow partial dimming (a darkened top "
             "bar leaves bright lines bright; default: off)",
    )
    parser.add_argument(
        "--occlusion-clip", action="store_true",
        help="with motion trajectory modes: detect partial hand occlusion "
             "(unwarped frame vs anchor-keyframe difference) and clip "
             "affected events with \\iclip so subtitles never render over "
             "the occluder (default: off)",
    )
    parser.add_argument(
        "--motion-auto", action="store_true",
        help="auto-detect moving text (FR-1): sample-OCR each ROI rect every "
             "--motion-auto-stride seconds; a text line whose centre moves "
             "more than --motion-auto-threshold px runs the trajectory "
             "pipeline on its detected quad+time range (replacing that "
             "ROI's static fragments). Scope detection with --roi/--roi-file "
             "(default: off)",
    )
    parser.add_argument(
        "--motion-auto-stride", type=float, default=0.5, metavar="SEC",
        help="sampling interval for --motion-auto detection (default: 0.5)",
    )
    parser.add_argument(
        "--motion-auto-threshold", type=float, default=24.0, metavar="PX",
        help="line-centre displacement that counts as moving for "
             "--motion-auto (default: 24)",
    )
    parser.add_argument("--keep-temp", action="store_true", help="keep temp work dir")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.preset:
        # Validate up front: the pipeline only checks the preset again in
        # stage 4, after a full OCR pass has already run.
        from core.scene_presets import get_preset_by_id

        if get_preset_by_id(args.preset) is None:
            parser.error(f"unknown preset id: {args.preset}")
    return args


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
