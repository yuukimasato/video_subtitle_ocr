#!/usr/bin/env python3
# scripts/verify_motion_ass.py
"""
运动轨迹 ASS 渲染偏差校验(headless,可独立运行)。

把合成的轨迹字幕烧录到纯黑底(或提供的实底)视频上,抽样导出指定帧,
提取每帧字幕亮色像素质心作为「渲染出的字幕中心」,与期望行中心
(`--expect-json`,由 core/motion_ass 侧生成)的加权均值比较,输出逐帧
欧氏偏差与统计(median / p95 / max),校验渲染误差预算
(spec §8 DoD:中位 ≤ 4px、95 分位 ≤ 8px)。

用法:
    python scripts/verify_motion_ass.py \
        --video base.mp4 --ass motion.ass --expect-json expect.json \
        [--frames 20] [--thresh 160] \
        [--tol-median 4.0 --tol-p95 8.0] \
        [--retry-cmd "python scripts/motion_ass.py ... --ass {ass} ..."] \
        [--report report.json]

- `--video` 缺省(或取值 "black")时用 ffmpeg lavfi `color=black` 源,
  分辨率/帧率取期望 JSON 的 width/height/fps;
- 期望 JSON 格式:
    {"width":1920,"height":1080,"fps":23.976,
     "frames": {"12": {"points": [[822.5, 309.0, 1.0], [957.0, 580.0, 1.5]]}}}
  每帧 points 为 [x, y, 权重] 列表(权重可取文本长度,缺省 1),
  期望中心 = 各点加权均值;
- 超预算(median > --tol-median 或 p95 > --tol-p95)且给出 `--retry-cmd`
  时,执行该 shell 命令模板一次(调用方负责以更小容差重新合成 .ass,
  模板中 `{ass}` 展开为 .ass 路径),随后重新烧录复验一次;
- 抽样帧上字幕缺失(无亮色像素)视为校验失败,不参与偏差统计但计入报告;
- 退出码:0 = 通过;2 = 超预算(含复验后仍超 / 缺字幕帧 / 无有效偏差);
  3 = 环境或输入错误(ffmpeg 缺失、期望 JSON 非法、烧录失败等)。

烧录方式:一次 ffmpeg 段导出 —— `select` 滤波器按解码帧号(从 0 计)精确
选帧,避免 `-ss` 输入寻址重置时间戳导致 ASS 字幕时间错位。
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np


# ---------------------------------------------------------------------------
# 纯函数(便于单测,不依赖 ffmpeg / cv2)
# ---------------------------------------------------------------------------

def bright_centroid(gray: np.ndarray, thresh: int = 160) -> Optional[Tuple[float, float]]:
    """灰度图中亮度 > thresh 的像素质心 (cx, cy);无像素返回 None。

    坐标约定:cx = 列均值(x 向右),cy = 行均值(y 向下),与画面坐标一致。
    二值掩码质心(不做亮度加权),阈值比较为严格大于。
    """
    arr = np.asarray(gray)
    if arr.ndim != 2:
        raise ValueError(f"bright_centroid expects a 2-D grayscale image, got shape {arr.shape}")
    mask = arr > thresh
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return (float(xs.mean()), float(ys.mean()))


def deviation_stats(deviations: List[float]) -> Dict[str, float]:
    """偏差序列的统计 {"median", "p95", "max"};p95 用线性插值分位。

    空序列抛 ValueError(统计无定义,不应静默给 0)。
    """
    arr = np.asarray([float(d) for d in deviations], dtype=float)
    if arr.size == 0:
        raise ValueError("deviation_stats: empty deviation list")
    return {
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95.0, method="linear")),
        "max": float(arr.max()),
    }


def load_expect_json(path) -> Dict:
    """加载期望 JSON 并归一化。

    归一化:frames 键转 int;points 每项补齐为 [x, y, weight](权重缺省 1.0)。
    缺少 frames / width / height / fps、points 为空或结构非法时抛 ValueError。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("expectation JSON top level must be an object")
    if "frames" not in data or not isinstance(data["frames"], dict):
        raise ValueError("expectation JSON missing 'frames' object")
    header_fields = [k for k in ("width", "height", "fps") if k not in data]
    if header_fields:
        raise ValueError(f"expectation JSON missing field(s): {', '.join(header_fields)}")

    frames: Dict[int, Dict] = {}
    for key, entry in data["frames"].items():
        try:
            frame_num = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"expectation JSON frame key is not an integer: {key!r}")
        if not isinstance(entry, dict) or "points" not in entry:
            raise ValueError(f"frame {key}: missing 'points' list")
        raw_points = entry["points"]
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError(f"frame {key}: 'points' must be a non-empty list")
        points: List[List[float]] = []
        for p in raw_points:
            if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
                raise ValueError(
                    f"frame {key}: each point must be [x, y] or [x, y, weight], got {p!r}")
            x, y = float(p[0]), float(p[1])
            w = float(p[2]) if len(p) == 3 else 1.0
            points.append([x, y, w])
        frames[frame_num] = {"points": points}

    return {
        "width": int(data["width"]),
        "height": int(data["height"]),
        "fps": float(data["fps"]),
        "frames": frames,
    }


def weighted_mean_point(points: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """期望点加权均值 (x, y);权重 = 第三列。总权重 <= 0 时退化为普通均值。"""
    if not points:
        raise ValueError("weighted_mean_point: points list is empty")
    total_w = float(sum(float(p[2]) for p in points))
    n = len(points)
    if total_w <= 0.0:
        return (
            sum(float(p[0]) for p in points) / n,
            sum(float(p[1]) for p in points) / n,
        )
    return (
        sum(float(p[0]) * float(p[2]) for p in points) / total_w,
        sum(float(p[1]) * float(p[2]) for p in points) / total_w,
    )


def point_distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """两点欧氏距离(px)。"""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def sample_frame_numbers(frame_map: Dict[int, Dict], count: int) -> List[int]:
    """从期望帧号集合中均匀抽取至多 count 个帧号(升序、确定性的)。"""
    keys = sorted(int(k) for k in frame_map.keys())
    if not keys:
        raise ValueError("sample_frame_numbers: no frames in expectation")
    count = int(count)
    if count <= 0:
        raise ValueError("sample_frame_numbers: count must be >= 1")
    if count >= len(keys):
        return keys
    if count == 1:
        return [keys[len(keys) // 2]]
    chosen = {int(round(i * (len(keys) - 1) / (count - 1))) for i in range(count)}
    filler = 0  # 极端舍入碰撞时从头补齐,保证数量
    while len(chosen) < count and filler < len(keys):
        chosen.add(filler)
        filler += 1
    return [keys[j] for j in sorted(chosen)][:count]


def expand_retry_cmd(template: str, ass_path: str) -> str:
    """展开重试命令模板中的 `{ass}` 占位符为 .ass 路径。"""
    return template.replace("{ass}", ass_path)


def budget_exceeded(stats: Dict[str, float], tol_median: float, tol_p95: float) -> bool:
    """统计是否超出误差预算(严格大于容差)。"""
    return stats["median"] > tol_median or stats["p95"] > tol_p95


def assess(stats: Dict[str, float], missing_frames: List[int],
           tol_median: float, tol_p95: float) -> Dict:
    """综合判定单次校验:超预算或存在缺字幕帧即失败。"""
    reasons: List[str] = []
    if missing_frames:
        reasons.append(
            f"subtitle missing on {len(missing_frames)} sampled frame(s): "
            f"{missing_frames}")
    if stats["median"] > tol_median:
        reasons.append(f"median {stats['median']:.2f}px > tol {tol_median:.2f}px")
    if stats["p95"] > tol_p95:
        reasons.append(f"p95 {stats['p95']:.2f}px > tol {tol_p95:.2f}px")
    return {"passed": not reasons, "reasons": reasons,
            "stats": stats, "missing_frames": list(missing_frames)}


# ---------------------------------------------------------------------------
# ffmpeg 烧录与导出
# ---------------------------------------------------------------------------

def _quote_filter_value(value: str) -> str:
    """ffmpeg filtergraph 选项值的单引号包裹(引号内一切字面,含空格/:/,)。"""
    return "'" + value.replace("'", "'\\''") + "'"


def export_burned_frames(video: Optional[str], ass_path: str,
                         frame_nums: Sequence[int],
                         width: int, height: int, fps: float,
                         out_dir: str) -> Dict[int, str]:
    """一次 ffmpeg 段导出:烧录 .ass 并按帧号精确抽样导出 PNG。

    返回 {帧号: PNG 路径}。`video` 为 None / "black" 时用 lavfi
    color=black 源(分辨率/帧率由参数给定,时长覆盖最大抽样帧)。

    说明:不用 `-ss` 输入寻址逐帧导出,因为它会把输入时间戳重置为 0,
    导致 ass 滤波器按错位后的时间取字幕;`select` 按 `n`(解码帧号,
    从 0 计)选帧则与帧号精确对应。
    """
    frames_sorted = sorted(int(f) for f in frame_nums)
    if not frames_sorted:
        raise ValueError("export_burned_frames: no frames requested")
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%06d.png")

    if video is None or video in ("black", "color=black"):
        duration = (frames_sorted[-1] + 2) / float(fps)
        src = ["-f", "lavfi", "-i",
               f"color=black:s={int(width)}x{int(height)}:r={float(fps):g}:d={duration:.6f}"]
    else:
        src = ["-i", video]

    select_expr = "+".join(f"eq(n,{f})" for f in frames_sorted)
    vf = f"ass={_quote_filter_value(os.path.abspath(ass_path))},select={_quote_filter_value(select_expr)}"
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        *src, "-vf", vf, "-vsync", "0",
        "-frames:v", str(len(frames_sorted)),
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg burn failed (rc={proc.returncode}): {proc.stderr.strip()[-800:]}")

    pngs = sorted(glob.glob(os.path.join(out_dir, "frame_*.png")))
    if len(pngs) != len(frames_sorted):
        raise RuntimeError(
            f"ffmpeg exported {len(pngs)} PNG(s), expected {len(frames_sorted)} "
            f"(sampled frames beyond video end?)")
    return {f: png for f, png in zip(frames_sorted, pngs)}


# ---------------------------------------------------------------------------
# 校验编排(烧录 → 质心 → 偏差 → 统计,含一次重试)
# ---------------------------------------------------------------------------

def _run_attempt(video: Optional[str], ass_path: str, expect: Dict,
                 sampled: Sequence[int], thresh: int, base_dir: str,
                 tag: str, tol_median: float, tol_p95: float) -> Dict:
    """单次完整校验:导出烧录帧、提质心、算偏差与统计、判定。"""
    import cv2  # 延迟导入:纯函数单测不依赖 cv2

    out_dir = os.path.join(base_dir, tag)
    png_map = export_burned_frames(
        video, ass_path, sampled,
        width=expect["width"], height=expect["height"], fps=expect["fps"],
        out_dir=out_dir,
    )
    fps = float(expect["fps"])
    records: List[Dict] = []
    deviations: List[float] = []
    missing: List[int] = []
    for f in sampled:
        gray = cv2.imread(png_map[f], cv2.IMREAD_GRAYSCALE)
        centroid = bright_centroid(gray, thresh=thresh) if gray is not None else None
        expected = weighted_mean_point(expect["frames"][f]["points"])
        rec: Dict = {
            "frame": int(f),
            "time_sec": int(f) / fps,
            "expected": [float(expected[0]), float(expected[1])],
        }
        if centroid is None:
            rec["centroid"] = None
            rec["deviation"] = None
            missing.append(int(f))
        else:
            rec["centroid"] = [float(centroid[0]), float(centroid[1])]
            rec["deviation"] = point_distance(centroid, expected)
            deviations.append(rec["deviation"])
        records.append(rec)

    if deviations:
        stats = deviation_stats(deviations)
    else:
        # 所有抽样帧都无字幕:统计无定义,用 inf 表示必然超预算。
        stats = {"median": float("inf"), "p95": float("inf"), "max": float("inf")}
    verdict = assess(stats, missing, tol_median, tol_p95)
    return {
        "tag": tag,
        "frames": records,
        "stats": stats,
        "missing_frames": missing,
        "passed": verdict["passed"],
        "reasons": verdict["reasons"],
    }


def verify_ass(video: Optional[str], ass_path: str, expect: Dict, *,
               frames_n: int = 20, thresh: int = 160,
               tol_median: float = 4.0, tol_p95: float = 8.0,
               retry_cmd: Optional[str] = None,
               work_dir: Optional[str] = None) -> Dict:
    """完整校验:首次烧录评估 →(超预算且有 retry_cmd 时)重试复验一次。

    返回报告 dict;`exit_code` 为 0(通过)或 2(超预算)。
    `work_dir` 缺省时使用临时目录并在结束时清理。
    """
    sampled = sample_frame_numbers(expect["frames"], frames_n)
    owned = work_dir is None
    base_dir = work_dir or tempfile.mkdtemp(prefix="verify_motion_ass_")
    attempts: List[Dict] = []
    retry: Dict = {"attempted": False, "command": None, "returncode": None}
    passed = False
    try:
        first = _run_attempt(video, ass_path, expect, sampled, thresh, base_dir,
                             "pass1", tol_median, tol_p95)
        attempts.append(first)
        passed = first["passed"]

        if not passed and retry_cmd:
            cmd = expand_retry_cmd(retry_cmd, os.path.abspath(ass_path))
            retry = {"attempted": True, "command": cmd, "returncode": None}
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            retry["returncode"] = proc.returncode
            if proc.returncode == 0:
                second = _run_attempt(video, ass_path, expect, sampled, thresh,
                                      base_dir, "pass2", tol_median, tol_p95)
                attempts.append(second)
                passed = second["passed"]
    finally:
        if owned:
            shutil.rmtree(base_dir, ignore_errors=True)

    return {
        "ass": os.path.abspath(ass_path),
        "video": video if video else "color=black",
        "width": expect["width"],
        "height": expect["height"],
        "fps": expect["fps"],
        "tolerance": {"median": tol_median, "p95": tol_p95},
        "sampled_frames": list(sampled),
        "attempts": attempts,
        "retry": retry,
        "passed": bool(passed),
        "exit_code": 0 if passed else 2,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_motion_ass",
        description="Burn a motion-trajectory .ass onto a black (or supplied) "
                    "base video, sample frames, and check the subtitle bright-"
                    "centroid deviation against expected line centers "
                    "(budget: median <= 4px, p95 <= 8px by default).",
    )
    parser.add_argument("--video", default=None,
                        help="base video file; omit or use 'black' to burn onto a "
                             "generated color=black source sized from the expectation JSON")
    parser.add_argument("--ass", required=True, help="subtitle .ass file to verify")
    parser.add_argument("--expect-json", required=True,
                        help="expectation JSON: {width,height,fps,frames:{N:{points:[[x,y,w],...]}}}")
    parser.add_argument("--frames", type=int, default=20,
                        help="number of frames to sample uniformly (default: 20)")
    parser.add_argument("--thresh", type=int, default=160,
                        help="brightness threshold for centroid pixels, strict > (default: 160)")
    parser.add_argument("--tol-median", type=float, default=4.0,
                        help="median deviation budget in px (default: 4.0)")
    parser.add_argument("--tol-p95", type=float, default=8.0,
                        help="95th-percentile deviation budget in px (default: 8.0)")
    parser.add_argument("--retry-cmd", default=None,
                        help="shell command template executed once when over budget; "
                             "the caller must re-synthesize the .ass (e.g. with halved "
                             "tolerance); '{ass}' expands to the .ass path")
    parser.add_argument("--report", default=None,
                        help="write per-frame deviation report JSON to this path")
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print("error: ffmpeg not found on PATH; install ffmpeg (with libass) "
              "to run render verification", file=sys.stderr)
        return 3

    try:
        expect = load_expect_json(args.expect_json)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load expectation JSON: {exc}", file=sys.stderr)
        return 3

    if args.frames < 1:
        print("error: --frames must be >= 1", file=sys.stderr)
        return 3

    try:
        report = verify_ass(
            args.video, args.ass, expect,
            frames_n=args.frames, thresh=args.thresh,
            tol_median=args.tol_median, tol_p95=args.tol_p95,
            retry_cmd=args.retry_cmd,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    last = report["attempts"][-1]
    status = "PASS" if report["passed"] else "FAIL"
    print(f"[{status}] {report['ass']}")
    print(f"  sampled {len(report['sampled_frames'])} frame(s), "
          f"attempts: {len(report['attempts'])}"
          + (f" (retry attempted, rc={report['retry']['returncode']})"
             if report["retry"]["attempted"] else ""))
    print(f"  deviation: median={last['stats']['median']:.2f}px "
          f"p95={last['stats']['p95']:.2f}px max={last['stats']['max']:.2f}px "
          f"(budget median<={args.tol_median} p95<={args.tol_p95})")
    for reason in last["reasons"]:
        print(f"  - {reason}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
