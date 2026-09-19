# tests/test_line_restoration.py
"""逐行还原估计(core/line_restoration.py)与轨迹行去重单元测试。

- line_frz_deg:方向约定(右下沉为负、右上升为正,与 GUI _compute_roi_pose
  一致)、对边对选取、退化输入;
- sample_line_style:合成"气泡+文字"图上的取色与描边估计、不可信守卫
  (纯色无墨迹/墨迹占比过高);
- format_style_tags:BGR 十六进制标签串;
- scripts.motion_ass.dedupe_rows:同文本近重复行合并(IoU 阈值)。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.line_restoration import (  # noqa: E402
    format_style_tags,
    line_frz_deg,
    sample_line_style,
)


# ---------------------------------------------------------------------------
# line_frz_deg
# ---------------------------------------------------------------------------

def _tilted_poly(cx, cy, length, height, tilt_deg, falling_right):
    th = math.radians(tilt_deg)
    dy = math.sin(th) if falling_right else -math.sin(th)
    d = np.array([math.cos(th), dy])
    p = np.array([-d[1], d[0]])
    c = np.array([cx, cy], dtype=float)
    L, H = length / 2.0, height / 2.0
    corners = [c + L * d + H * p, c - L * d + H * p,
               c - L * d - H * p, c + L * d - H * p]
    return [[float(x), float(y)] for x, y in corners]


def test_frz_falling_right_negative():
    poly = _tilted_poly(500, 400, 300, 40, 8.0, falling_right=True)
    assert line_frz_deg(poly) == pytest.approx(-8.0, abs=0.1)


def test_frz_rising_right_positive():
    poly = _tilted_poly(500, 400, 300, 40, 8.0, falling_right=False)
    assert line_frz_deg(poly) == pytest.approx(8.0, abs=0.1)


def test_frz_axis_aligned_zero_and_normalized_sign():
    poly = [[100, 100], [300, 100], [300, 140], [100, 140]]
    frz = line_frz_deg(poly)
    assert frz == 0.0
    assert math.copysign(1.0, frz) == 1.0  # 不输出 -0.0


def test_frz_vertical_short_pair_ignored():
    # 竖长四边形(竖排文字/退化):\frz±90 对横排替换文本无意义 → 0.0
    poly = [[200, 100], [230, 100], [230, 300], [200, 300]]
    assert line_frz_deg(poly) == 0.0


def test_frz_degenerate_inputs():
    assert line_frz_deg([[1, 2], [3, 4]]) == 0.0
    assert line_frz_deg([[0, 0], [0, 0], [0, 0], [0, 0]]) == 0.0


# ---------------------------------------------------------------------------
# sample_line_style
# ---------------------------------------------------------------------------

def _bubble_frame(text_color=(90, 110, 120), bg_color=(245, 240, 210),
                  stroke=2, box=(60, 40, 260, 70)):
    """浅黄气泡 + 深灰"文字"笔画(与 12.mp4 聊天气泡同构)。"""
    frame = np.full((120, 320, 3), 30, np.uint8)
    x1, y1, x2, y2 = box
    frame[y1 - 6:y2 + 6, x1 - 6:x2 + 6] = bg_color
    xx = np.arange(x1 + 4, x2 - 4, 9)
    for x in xx:
        cv2_frame = frame
        cv2_frame[y1 + 4:y2 - 4, x:x + stroke] = text_color
    return frame


import cv2  # noqa: E402  (放此处避免模块头与 numpy 混排)


def test_sample_style_dark_text_on_light_bubble():
    frame = _bubble_frame()
    poly = [[60, 40], [260, 40], [260, 70], [60, 70]]
    style = sample_line_style(frame, poly, fs_px=28.0)
    assert style is not None
    r, g, b = style["primary"][2], style["primary"][1], style["primary"][0]
    # 深灰蓝文字:三通道都远低于气泡背景(245,240,210)
    assert r < 160 and g < 160 and b < 180
    assert style["outline"] == style["primary"]
    assert style["bord"] is not None and 0.2 <= style["bord"] <= 3.0


def test_sample_style_guards_uniform_region():
    frame = np.full((80, 200, 3), 128, np.uint8)
    poly = [[20, 20], [180, 20], [180, 60], [20, 60]]
    assert sample_line_style(frame, poly, fs_px=28.0) is None


def test_sample_style_guards_dense_ink():
    # 墨迹占比过高(>0.6):整块深色,无法区分文字/背景
    frame = np.full((80, 200, 3), 40, np.uint8)
    frame[25:55, 25:175] = 20  # 几乎全深色
    poly = [[20, 20], [180, 20], [180, 60], [20, 60]]
    assert sample_line_style(frame, poly, fs_px=28.0) is None


def test_format_style_tags_bgr_hex_and_bord():
    tags = format_style_tags({
        "primary": (0x69, 0xA2, 0xB3), "outline": (0x69, 0xA2, 0xB3),
        "bord": 0.2})
    assert tags == "\\1c&H69A2B3&\\3c&H69A2B3&\\bord0.2"


def test_format_style_tags_without_bord():
    # (b, g, r) = (1, 2, 3) → &H010203&(ASS 颜色为 BGR 十六进制)
    tags = format_style_tags({"primary": (1, 2, 3), "outline": (1, 2, 3),
                              "bord": None})
    assert tags == "\\1c&H010203&\\3c&H010203&"


# ---------------------------------------------------------------------------
# scripts.motion_ass.dedupe_rows
# ---------------------------------------------------------------------------

def test_dedupe_rows_merges_same_text_overlap_boxes():
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("あんなに早く", (469.0, 608.0, 601.0, 632.0)),
        ("あんなに早く", (470.0, 609.0, 600.0, 631.0)),  # 近重复
        ("帰ってくると思ってなくて", (469.0, 660.0, 700.0, 684.0)),
    ]
    kept, removed = dedupe_rows(rows)
    assert removed == 1
    assert len(kept) == 2
    assert kept[0][0] == "あんなに早く"


def test_dedupe_rows_keeps_different_text_same_box():
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("大丈夫", (469.0, 608.0, 601.0, 632.0)),
        ("大丈夫?", (469.0, 608.0, 601.0, 632.0)),  # 同位不同文本:保留
    ]
    kept, removed = dedupe_rows(rows)
    assert removed == 0 and len(kept) == 2


def test_dedupe_rows_keeps_disjoint_boxes():
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("まだ帰ってない?", (400.0, 100.0, 600.0, 124.0)),
        ("まだ帰ってない?", (400.0, 400.0, 600.0, 424.0)),  # 滚动前后的两个位置
    ]
    kept, removed = dedupe_rows(rows)
    assert removed == 0 and len(kept) == 2
