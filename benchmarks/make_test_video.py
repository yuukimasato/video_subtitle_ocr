#!/usr/bin/env python3
"""Generate the benchmark test video (with known ground-truth subtitles).

The shell script at the repository root (generate_test_video.sh) currently has
a bash quoting bug on bash 5.2 (unmatched quote around line 34/39), so this
Python re-implementation builds the same content without shell quoting issues.

Usage:
  python make_test_video.py --video test_video_subtitle.mp4 --gt gt.json
"""

import argparse
import json
import subprocess
from pathlib import Path

# Keep in sync with the ground truth used by benchmark_regression.py.
# The last entry uses a 0.5s linear fade-in/fade-out (rendered via the
# drawtext alpha expression below) as the boundary-refinement test case.
SUBTITLES = [
    {"start": 0.5, "end": 3.5, "text": "这是一段测试字幕"},
    {"start": 4.0, "end": 7.0, "text": "欢迎使用视频字幕OCR系统"},
    {"start": 7.5, "end": 10.5, "text": "今天天气真不错"},
    {"start": 11.0, "end": 13.0, "text": "我们一起去公园散步吧"},
    {"start": 13.5, "end": 15.5, "text": "淡入淡出的字幕", "fade": True},
]

FONT = "/home/hope/.local/share/fonts/opentype/SourceHanSansSC-Regular.otf"
FONT_BOLD = "/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/benchmarks/SourceHanSansSC-Regular.otf"


def esc(text: str) -> str:
    """Escape drawtext text (colons and quotes inside filter graph)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="test_video_subtitle.mp4")
    ap.add_argument("--gt", default="gt.json")
    ap.add_argument("--duration", type=int, default=16)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    filters = [
        # Animated noise so consecutive frames are not bit-identical.
        "geq=r='r(X,Y)+30*sin(X/200+T*2)':g='g(X,Y)+20*cos(Y/150+T*1.5)'"
        ":b='b(X,Y)+25*sin((X+Y)/300+T*1.8)'",
        "drawbox=x=100:y=150:w=300:h=200:c=0xe74c3c@0.3:t=fill",
        "drawbox=x=500:y=100:w=400:h=250:c=0x27ae60@0.3:t=fill",
        "drawbox=x=800:y=300:w=350:h=280:c=0x8e44ad@0.3:t=fill",
    ]
    for sub in SUBTITLES:
        alpha_expr = ""
        if sub.get("fade"):
            start, end = float(sub["start"]), float(sub["end"])
            fade_in_end, fade_out_start = start + 0.5, end - 0.5
            alpha_expr = (
                f":alpha='if(lt(t,{start}),0,"
                f"if(lt(t,{fade_in_end}),(t-{start})/0.5,"
                f"if(lt(t,{fade_out_start}),1,"
                f"if(lt(t,{end}),({end}-t)/0.5,0))))'"
            )
        filters.append(
            f"drawtext=fontfile={FONT}:text='{esc(sub['text'])}':fontcolor=white"
            f":fontsize=48:x=(w-text_w)/2:y=h-th-80:borderw=2:bordercolor=black@0.6"
            f"{alpha_expr}"
            f":enable='between(t,{sub['start']},{sub['end']})'"
        )
    filters.append(
        f"drawtext=fontfile={FONT}:text='视频字幕OCR测试':fontcolor=white@0.9"
        ":fontsize=36:x=20:y=20:borderw=1:bordercolor=black@0.4"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        f"color=c=0x2c3e50:s={args.width}x{args.height}:d={args.duration}:r={args.fps}",
        "-filter_complex", ",".join(filters),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        args.video,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Path(args.gt).write_text(
        json.dumps(SUBTITLES, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"video: {args.video}\nground truth: {args.gt}")


if __name__ == "__main__":
    main()
