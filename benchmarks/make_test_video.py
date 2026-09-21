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

# Seam-hostile schedule for chunk-parallel equivalence testing (150 s,
# 50 s windows -> core seams at 50 s and 100 s with 15 s overlap):
# entries deliberately straddle seams, fade in exactly inside an overlap
# zone, flash quickly near a seam, and repeat identical text around a seam
# to guard against false merge. No two entries overlap in time.
SEAM_HOSTILE_SUBTITLES = [
    {"start": 0.5, "end": 3.5, "text": "开头常规字幕"},
    {"start": 4.0, "end": 7.0, "text": "第一个窗口的内容"},
    {"start": 40.0, "end": 52.0, "text": "横跨接缝的长句子在这里"},
    {"start": 46.0, "end": 46.8, "text": "快对白一"},
    {"start": 48.0, "end": 48.8, "text": "快对白二"},
    {"start": 52.5, "end": 56.0, "text": "接缝处淡入的台词", "fade": True},
    {"start": 60.0, "end": 63.0, "text": "重叠区右侧的常规字幕"},
    {"start": 70.0, "end": 73.0, "text": "第二个窗口的内容"},
    {"start": 99.0, "end": 101.5, "text": "跨过百秒接缝的短句"},
    {"start": 103.0, "end": 105.0, "text": "重复出现的相同文本"},
    {"start": 106.0, "end": 108.0, "text": "重复出现的相同文本"},
    {"start": 115.0, "end": 118.0, "text": "第三个窗口的内容"},
    {"start": 140.0, "end": 143.0, "text": "结尾常规字幕"},
    {"start": 146.5, "end": 149.0, "text": "片尾淡入淡出", "fade": True},
]

# Japanese schedule (--lang ja): same timing structure as the zh schedule
# (5 entries, fade in/out on the last) so boundary-refinement and voting
# benchmarks exercise identical timelines; text carries kana + kanji to
# exercise the JP recognition path.
SUBTITLES_JA = [
    {"start": 0.5, "end": 3.5, "text": "これはテスト字幕です"},
    {"start": 4.0, "end": 7.0, "text": "動画字幕OCRシステムへようこそ"},
    {"start": 7.5, "end": 10.5, "text": "今日はいい天気ですね"},
    {"start": 11.0, "end": 13.0, "text": "一緒に公園を散歩しましょう"},
    {"start": 13.5, "end": 15.5, "text": "フェードイン・フェードアウト", "fade": True},
]

# Corner watermark drawtext per language (top-left overlay).
WATERMARKS = {"zh": "视频字幕OCR测试", "ja": "動画字幕OCRテスト"}


def select_schedule(lang: str) -> tuple:
    """Return (subtitles, watermark_text) for a language schedule.

    Raises ValueError for languages without a schedule (argparse limits
    --lang choices to zh/ja; this guard is for direct callers).
    """
    if lang == "zh":
        return list(SUBTITLES), WATERMARKS["zh"]
    if lang == "ja":
        return list(SUBTITLES_JA), WATERMARKS["ja"]
    raise ValueError(f"unsupported language: {lang!r} (expected 'zh' or 'ja')")


def _find_cjk_font(lang: str = "zh") -> str:
    """Locate an installed CJK-capable font for ffmpeg drawtext.

    ``lang`` only steers the fc-match fallback preference (zh/ja both render
    from the same Noto/Source Han CJK families on this project's targets).
    """
    candidates = [
        Path.home() / ".local/share/fonts/opentype/SourceHanSansSC-Regular.otf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", f"sans:lang={lang}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            found = Path(out.stdout.strip())
            if found.is_file():
                return str(found)
    except Exception:
        pass
    raise SystemExit(
        "no CJK font found for drawtext; install Noto Sans CJK / WenQuanYi "
        "or place SourceHanSansSC-Regular.otf under ~/.local/share/fonts"
    )


# Kept as a module alias: the historical entry point name.
resolve_font = _find_cjk_font


def esc(text: str) -> str:
    """Escape drawtext text (colons and quotes inside filter graph)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_filters(subs, font: str, watermark: str) -> list:
    """Build the ffmpeg -filter_complex list: noise base + boxes + one
    drawtext per subtitle (fade entries carry an alpha expression) + the
    corner watermark drawtext."""
    filters = [
        # Animated noise so consecutive frames are not bit-identical.
        "geq=r='r(X,Y)+30*sin(X/200+T*2)':g='g(X,Y)+20*cos(Y/150+T*1.5)'"
        ":b='b(X,Y)+25*sin((X+Y)/300+T*1.8)'",
        "drawbox=x=100:y=150:w=300:h=200:c=0xe74c3c@0.3:t=fill",
        "drawbox=x=500:y=100:w=400:h=250:c=0x27ae60@0.3:t=fill",
        "drawbox=x=800:y=300:w=350:h=280:c=0x8e44ad@0.3:t=fill",
    ]
    for sub in subs:
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
            f"drawtext=fontfile={font}:text='{esc(sub['text'])}':fontcolor=white"
            f":fontsize=48:x=(w-text_w)/2:y=h-th-80:borderw=2:bordercolor=black@0.6"
            f"{alpha_expr}"
            f":enable='between(t,{sub['start']},{sub['end']})'"
        )
    filters.append(
        f"drawtext=fontfile={font}:text='{esc(watermark)}':fontcolor=white@0.9"
        ":fontsize=36:x=20:y=20:borderw=1:bordercolor=black@0.4"
    )
    return filters


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="test_video_subtitle.mp4")
    ap.add_argument("--gt", default="gt.json")
    ap.add_argument("--duration", type=int, default=16)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument(
        "--lang", choices=["zh", "ja"], default="zh",
        help="subtitle language schedule (default: zh; ja emits the Japanese "
             "5-entry schedule with a matching watermark)",
    )
    ap.add_argument(
        "--seam-hostile", action="store_true",
        help="emit the 150s seam-hostile schedule (subtitles straddling the "
             "50s/100s core seams, fades inside overlap zones, rapid dialog, "
             "identical-text repeats) for chunk-parallel equivalence tests",
    )
    args = ap.parse_args()

    if args.seam_hostile:
        if args.lang != "zh":
            ap.error("--seam-hostile schedule is zh-only; drop --lang or use --lang zh")
        args.duration = 150
        subs, watermark = list(SEAM_HOSTILE_SUBTITLES), WATERMARKS["zh"]
    else:
        subs, watermark = select_schedule(args.lang)
    font = resolve_font(args.lang)

    filters = build_filters(subs, font, watermark)

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
        json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"video: {args.video}\nground truth: {args.gt}")


if __name__ == "__main__":
    main()
