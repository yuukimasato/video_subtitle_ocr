# tests/test_line_geometry_render.py
"""C2:行框约束字号的 libass 渲染回归(固定字体、人工行框)。

两个人工行框(小字标签 12px + 大字正文 30px)→ 生成 ASS → FFmpeg 渲染
纯 ASS 灰底帧 → 字形连通区域断言:
- 字号层级保持(大字行墨迹高度显著大于小字行);
- 原本不相交的两个框内字形不得相交(小字不被放大侵入正文区);
- 实测墨迹宽高记录在测试输出(字体度量差异允许,不作为断言)。

无 FFmpeg 的开发机 skip;G0/G1 交付环境必须执行。
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

W, H = 640, 360
BASE = np.array([48, 48, 48])
FONT = "DejaVu Sans"

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available")


def _write_ass(path: Path, lines):
    content = [
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
        (f"Style: Scene,{FONT},30,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
         "0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    for start, tags, text in lines:
        content.append(
            f"Dialogue: 0,{start},0:00:05.00,Scene,,0,0,0,,{tags}{text}")
    path.write_text("\n".join(content) + "\n", encoding="utf-8")


def _render_frame(ass_path: Path) -> np.ndarray:
    import cv2

    png = ass_path.with_suffix(".png")
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x303030:s={W}x{H}:r=25:d=2",
        "-vf", f"ass={ass_path},select=eq(n\\,10)",
        "-vsync", "0", "-frames:v", "1", "-update", "1", str(png),
    ]
    subprocess.run(cmd, check=True, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
    assert img is not None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _ink(rgb: np.ndarray) -> np.ndarray:
    return np.any(np.abs(rgb.astype(int) - BASE) > 20, axis=2)


def test_line_size_hierarchy_renders(tmp_path):
    """人工行框 → per-line fs:大字/小字层级在渲染中保持,框间字形不相交。"""
    from core.line_geometry import fit_line_size

    small_box = (80.0, 80.0, 260.0, 92.0)     # 12px 高
    large_box = (80.0, 200.0, 520.0, 230.0)   # 30px 高
    small_fs = fit_line_size(small_box, None)
    large_fs = fit_line_size(large_box, None)
    assert (small_fs, large_fs) == (12.0, 30.0)

    ass = tmp_path / "sizes.ass"
    _write_ass(ass, [
        ("0:00:00.00", f"{{\\an7\\pos({small_box[0]},{small_box[1]})"
                       f"\\fs{int(small_fs)}}}", "ラベル 123"),
        ("0:00:00.00", f"{{\\an7\\pos({large_box[0]},{large_box[1]})"
                       f"\\fs{int(large_fs)}}}", "大字正文"),
    ])
    ink = _ink(_render_frame(ass))

    def _ink_height(y0, y1):
        rows = [y for y in range(y0, y1)
                if ink[y, max(0, int(y0 and 0)):].any()]
        return len(rows)

    # 字形垂直跨度:大字行的墨迹行数应明显大于小字行(层级保持)。
    small_rows = sum(1 for y in range(70, 130)
                     if ink[y, 70:int(small_box[2] + 20)].any())
    large_rows = sum(1 for y in range(190, 270)
                     if ink[y, 70:int(large_box[2] + 20)].any())
    assert small_rows > 0 and large_rows > 0
    assert large_rows >= small_rows * 1.8, (small_rows, large_rows)

    # 两框(含字高的渲染余量)之间不得有字形:框间空带 130..190。
    gap_rows = [y for y in range(130, 190) if ink[y, :].any()]
    assert not gap_rows, f"ink leaked into the gap between boxes: {gap_rows}"

    # 实测墨迹宽高记录(字体度量差异允许)。
    measured = {
        "small_rows": small_rows,
        "large_rows": large_rows,
    }
    print(f"rendered ink metrics: {measured}")
