# tests/test_occlusion_render.py
"""B2:离散遮挡切片的 libass 渲染像素回归(FFmpeg + ass 滤镜)。

- 构造静态 "TEST" 文本事件,经 :func:`attach_occlusion_clips` 离散切片
  (运动 cfg),遮挡多边形盖住画面右半;
- 分别渲染无遮挡帧、命中帧、确认清晰帧,以灰底差分识别字形墨迹;
- 断言:命中帧目标区(右半)字形像素减少 ≥80%;未遮挡区(左半)与
  无裁剪参考逐像素差异 ≤1%;清晰帧右半有墨迹(防空白假通过)。

无 FFmpeg/libass 的开发机 skip;G0/G1 交付环境必须执行。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import LinePose, LineTrack, MotionAssConfig  # noqa: E402
from core.occlusion_mask import (  # noqa: E402
    OcclusionConfig,
    attach_occlusion_clips,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FPS = 25
W, H = 320, 120
BASE = np.array([48, 48, 48])
FONT = "DejaVu Sans"


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not available")


def render_ass_frame(ass_path: Path, frame: int, fps: int = FPS) -> np.ndarray:
    """FFmpeg 渲染 ASS 到灰底,返回指定帧的 RGB uint8(subprocess 参数数组,
    timeout=30 秒)。"""
    png = ass_path.with_suffix(f".f{frame}.png")
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x303030:s={W}x{H}:r={fps}:d=8",
        "-vf", f"ass={ass_path},select=eq(n\\,{frame})",
        "-vsync", "0", "-frames:v", "1",
        "-update", "1", str(png),
    ]
    subprocess.run(cmd, check=True, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    import cv2

    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
    assert img is not None, f"render failed: {png}"
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _write_sliced_ass(path: Path, events) -> None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        (f"Style: Scene,{FONT},36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
         "0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    for ev in events:
        lines.append(
            "Dialogue: {layer},{start},{end},{style},{name},0,0,0,,{tags}{body}".format(
                layer=ev.get("layer", 0), start=ev["start_time"],
                end=ev["end_time"], style=ev.get("style", "Scene"),
                name=ev.get("name", ""), tags=ev.get("tags", ""),
                body=ev.get("body", "")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _static_setup():
    """静态行 + 匀速静止轨迹;遮挡多边形 = 画面右半(平面=画面,恒等单应)。"""
    n = 75  # 3s
    tracks = []
    for i in range(n):
        hom = np.eye(3)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0, 0], [1, 0], [1, 1], [0, 1]],
            homography=[[float(v) for v in row] for row in hom],
            homography_inv=[[float(v) for v in row]
                            for row in np.eye(3)]))
    poses = {i: LinePose(center=(W / 2.0, H / 2.0), angle_deg=0.0, scale=1.0)
             for i in range(n)}
    lt = LineTrack(text="TEST", ref_box=(W / 2 - 40, H / 2 - 12,
                                         W / 2 + 40, H / 2 + 12),
                   height=24.0, poses=poses)
    # 事件 tags 与 LineTrack.height 同源(生产中由 synthesize_events 生成)。
    event = {
        "start_time": "0:00:00.00", "end_time": "0:00:03.00",
        "style": "Scene", "name": "motion",
        "tags": "{\\an5\\fs24\\pos(160.0,60.0)}",
        "body": "TEST", "line_idx": 0,
    }
    # 遮挡多边形:x 170..320(留 10px 给 clip 边缘抗锯齿),命中窗口 [1s, 2s)。
    poly = np.array([[170.0, 0.0], [320.0, 0.0], [320.0, 120.0], [170.0, 120.0]])
    stride = 3
    sample_frames = list(range(25, 50, stride)) + [49]
    occlusions = {f: [poly.copy()] for f in sample_frames}
    samples = {f: [] for f in range(0, 75, stride) if f not in occlusions}
    samples[74] = []
    for f in sample_frames:
        samples[f] = [poly.copy()]
    return event, tracks, lt, occlusions, samples


def test_discrete_clip_renders_time_local_mask(tmp_path):
    """命中帧右半字形被裁、左半不变;清晰帧完整;无 \\t clip 动画。"""
    event, tracks, lt, occlusions, samples = _static_setup()
    sliced = attach_occlusion_clips(
        [event], tracks=tracks, line_tracks=[lt], occlusions=occlusions,
        cfg=OcclusionConfig(min_line_overlap=0.01),
        samples=samples, motion_cfg=MotionAssConfig(), video_height=H)
    assert any("\\iclip(" in e["tags"] for e in sliced)
    assert not any("\\t(" in e["tags"] and "\\iclip(" in e["tags"]
                   for e in sliced)
    # 切片覆盖完整时间轴:清晰段无 clip,命中段有 clip。
    clear_events = [e for e in sliced if "\\iclip(" not in e["tags"]]
    hit_events = [e for e in sliced if "\\iclip(" in e["tags"]]
    assert clear_events and hit_events

    ass = tmp_path / "sliced.ass"
    _write_sliced_ass(ass, sliced)
    ref = tmp_path / "ref.ass"
    _write_sliced_ass(ref, [event])

    frame_clear_before = 10   # 0.4s:命中前
    frame_hit = 35            # 1.4s:命中中
    frame_clear_after = 60    # 2.4s:确认清晰后

    def ink(rgb):
        return np.any(np.abs(rgb.astype(int) - BASE) > 20, axis=2)

    # 参考帧:同一时刻的无裁剪版本(不能拿不同运动时刻互比——此处静止,
    # 直接用原事件参考)。
    ref_frames = {f: render_ass_frame(ref, f)
                  for f in (frame_clear_before, frame_hit, frame_clear_after)}
    out_frames = {f: render_ass_frame(ass, f)
                  for f in (frame_clear_before, frame_hit, frame_clear_after)}

    # 目标区 = 右半画面中参考帧有字形的部分;未遮挡区 = 左半。
    target = np.zeros((H, W), bool)
    target[:, 180:] = True   # clip 边界(170)右侧,避开抗锯齿带
    outside = np.zeros((H, W), bool)
    outside[:, :160] = True  # clip 边界左侧 10px 之外

    clear_ref = ink(ref_frames[frame_clear_before])
    assert clear_ref[target].sum() > 0, "reference must have ink in target"

    # 命中帧:目标区字形减少 ≥80%;未遮挡区与参考一致(≤1% 像素不同)。
    hit_ink = ink(out_frames[frame_hit])
    clear_ref_ink = ink(ref_frames[frame_hit])
    assert clear_ref_ink[target].sum() > 0
    assert hit_ink[target].sum() <= clear_ref_ink[target].sum() * 0.2
    diff_out = np.mean(clear_ref_ink[outside] != hit_ink[outside])
    assert diff_out <= 0.01

    # 命中前/后:与对应参考完全一致(无裁剪)。
    for f in (frame_clear_before, frame_clear_after):
        a = ink(out_frames[f])
        b = ink(ref_frames[f])
        assert np.mean(a != b) <= 0.01, f"frame {f} must render unclipped"
        assert a[target].sum() > 0, f"frame {f} target must keep ink"
