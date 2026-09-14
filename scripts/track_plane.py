#!/usr/bin/env python3
# scripts/track_plane.py
"""
场景文字平面跟踪（headless，可独立运行）。

在视频上跟踪一个用户框选的文字平面（手机屏幕/信件/招牌）四边形，输出逐帧
轨迹 JSON：四边形（原画面坐标）、累计单应（关键帧 → 当前帧，含逆）、内点率、
重投影误差与状态。丢失帧不外推；文字重新可见后自动重捕获。

典型用法（四角顺序：左上 → 右上 → 右下 → 左下）:
    python scripts/track_plane.py --video clip.mp4 \
        --quad 640,495 1290,495 1290,855 640,855 \
        --start-frame 100 --end-frame 400 \
        --output trajectory.json

也可以从关键帧图像文件读取四角（配合人工标注）:
    python scripts/track_plane.py --video clip.mp4 --quad-file quad.json -o trajectory.json

可选导出透视展开预览（检查跟踪贴合度，也可直接送 OCR）:
    ... --dump-unwarped unwarped/ --unwarped-size 650 360 --dump-step 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def parse_quad(spec: str):
    """解析 "x1,y1 x2,y2 x3,y3 x4,y4"（或逗号分隔的 8 个数）。"""
    nums = [v for chunk in spec.replace(",", " ").split() for v in [chunk]]
    try:
        values = [float(v) for v in nums]
    except ValueError:
        raise SystemExit(f"Invalid quad spec: {spec!r}")
    if len(values) != 8:
        raise SystemExit(f"Quad needs 8 numbers (4 points), got {len(values)}: {spec!r}")
    return [[values[i], values[i + 1]] for i in range(0, 8, 2)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="track_plane",
        description="Track a hand-picked text plane (quad) across a video and "
                    "export a per-frame trajectory JSON.",
    )
    parser.add_argument("--video", required=True, help="input video file")
    parser.add_argument("--quad", help="four corners: x1,y1 x2,y2 x3,y3 x4,y4 (keyframe coords)")
    parser.add_argument("--quad-file", help="JSON file with a 4x2 quad (list of [x,y])")
    parser.add_argument("--start-frame", type=int, default=0, help="first frame to track (default: 0)")
    parser.add_argument("--end-frame", type=int, default=None, help="last frame (inclusive; default: video end)")
    parser.add_argument("--output", "-o", default="trajectory.json", help="trajectory JSON output path")
    parser.add_argument("--min-matches", type=int, default=12)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.35)
    parser.add_argument("--max-reproj-error", type=float, default=4.0)
    parser.add_argument("--dump-unwarped", metavar="DIR",
                        help="dump perspective-unwarped plane crops for review")
    parser.add_argument("--unwarped-size", nargs=2, type=int, metavar=("W", "H"),
                        help="unwarped crop size (default: quad bbox size)")
    parser.add_argument("--dump-step", type=int, default=1, metavar="N",
                        help="dump every Nth ok frame (default: 1)")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    if bool(args.quad) == bool(args.quad_file):
        parser.error("provide exactly one of --quad / --quad-file")

    if args.quad_file:
        with open(args.quad_file, "r", encoding="utf-8") as f:
            init_quad = json.load(f)
    else:
        init_quad = parse_quad(args.quad)

    import cv2

    from core.scene_plane_tracker import track_plane, unwarp_canonical, unwarp_plane, save_trajectory

    def _progress(done: int, total: int) -> None:
        if not args.quiet:
            print(f"\r  tracking: {done}/{total} frames", end="", file=sys.stderr, flush=True)

    tracks = track_plane(
        args.video, init_quad,
        start_frame=args.start_frame, end_frame=args.end_frame,
        progress_cb=_progress,
        min_matches=args.min_matches,
        min_inlier_ratio=args.min_inlier_ratio,
        max_reproj_error=args.max_reproj_error,
    )
    if not args.quiet:
        print(file=sys.stderr)

    ok_count = sum(1 for t in tracks if t.status == "ok")
    save_trajectory(args.output, tracks, video_path=os.path.abspath(args.video), init_quad=init_quad)
    if not args.quiet:
        print(
            f"tracked {ok_count}/{len(tracks)} frames ok -> {args.output}",
            file=sys.stderr,
        )

    if args.dump_unwarped:
        cap = cv2.VideoCapture(args.video)
        os.makedirs(args.dump_unwarped, exist_ok=True)
        dumped = 0
        for t in tracks:
            if t.status != "ok" or t.frame_num % max(1, args.dump_step) != 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, t.frame_num)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            if args.unwarped_size:
                size = tuple(args.unwarped_size)
                crop = unwarp_canonical(frame, t.homography_inv, size) \
                    if t.homography_inv is not None else unwarp_plane(frame, t.quad, size)
            else:
                xs = [p[0] for p in t.quad]
                ys = [p[1] for p in t.quad]
                size = (max(8, int(round(max(xs) - min(xs)))), max(8, int(round(max(ys) - min(ys)))))
                crop = unwarp_plane(frame, t.quad, size)
            cv2.imwrite(
                os.path.join(args.dump_unwarped, f"frame_{t.frame_num:06d}.png"), crop,
            )
            dumped += 1
        cap.release()
        if not args.quiet:
            print(f"dumped {dumped} unwarped crops -> {args.dump_unwarped}", file=sys.stderr)

    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
