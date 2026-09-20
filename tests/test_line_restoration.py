# tests/test_line_restoration.py
"""逐行还原估计(core/line_restoration.py)与轨迹行去重单元测试。

- line_frz_deg:方向约定(右下沉为负、右上升为正,与 GUI _compute_roi_pose
  一致)、对边对选取、退化输入;
- sample_line_style:合成"气泡+文字"图上的取色与描边估计、不可信守卫
  (纯色无墨迹/墨迹占比过高);
- format_style_tags:BGR 十六进制标签串;
- scripts.motion_ass.dedupe_rows:同文本近重复行合并(IoU 阈值)+ 时间/
  来源维度(frames_seen 帧距超关键帧池网格步长的合法重复保留)。
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


def test_frz_non_finite_inputs_return_zero():
    """NaN/inf 坐标:不得输出 NaN(会写成 ``\\frznan`` 被 libass 丢弃)。"""
    nan = float("nan")
    inf = float("inf")
    assert line_frz_deg([[0, 0], [nan, 0], [10, 10], [0, 10]]) == 0.0
    assert line_frz_deg([[0, 0], [inf, 0], [10, 10], [0, 10]]) == 0.0
    assert line_frz_deg([[0, 0], [10, -inf], [10, 10], [0, 10]]) == 0.0
    # 有限行不受影响(NaN 守卫不得把正常输入一并归零)
    assert line_frz_deg([[100, 100], [300, 100], [300, 140], [100, 140]]) == 0.0
    poly = _tilted_poly(500, 400, 300, 40, 8.0, falling_right=True)
    assert line_frz_deg(poly) == pytest.approx(-8.0, abs=0.1)


def test_frz_five_point_polygon_uses_longest_edge():
    """点数 ≠ 4:对边配对无定义 → 最长边方向(不再借用前 4 条边的错误配对)。

    旧实现按 (e0,e2)/(e1,e3) 配对 5 点多边形 → 方向算错并退化成 0.0。
    """
    # 底边 (100,100)→(300,100) 长 200 为最长边、方向水平 → frz 0.0
    poly5 = [[100, 100], [300, 100], [320, 140], [200, 150], [100, 140]]
    assert line_frz_deg(poly5) == 0.0
    assert math.copysign(1.0, line_frz_deg(poly5)) == 1.0
    # 倾斜 5° 的 5 点凸多边形(长边中点插入第 5 点):底边长边方向为准
    base = _tilted_poly(500, 400, 300, 40, 5.0, falling_right=True)
    mid_top = [(base[0][0] + base[1][0]) / 2.0,
               (base[0][1] + base[1][1]) / 2.0]
    poly5_tilted = [base[0], mid_top, base[1], base[2], base[3]]
    assert len(poly5_tilted) == 5
    assert line_frz_deg(poly5_tilted) == pytest.approx(-5.0, abs=0.1)
    # 4 点路径不受影响:同一几何的四边形取对边平均,结果一致
    assert line_frz_deg(base) == pytest.approx(-5.0, abs=0.1)


def test_private_hypot_not_exported():
    """math_hypot 是模块内部工具:私有名化,不进导出面。"""
    import core.line_restoration as lr

    assert not hasattr(lr, "math_hypot")
    assert lr.__all__ == ["line_frz_deg", "sample_line_style",
                          "format_style_tags", "merge_restoration_tags"]


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



def test_sample_style_degenerate_polygons_return_none():
    """空/退化多边形:返回 None(旧实现对空列表 IndexError 崩溃)。"""
    frame = _bubble_frame()
    assert sample_line_style(frame, [], fs_px=28.0) is None
    assert sample_line_style(frame, [[60, 40]], fs_px=28.0) is None
    assert sample_line_style(frame, [[60, 40], [260, 40]], fs_px=28.0) is None
    # 零面积:4 点共线(200px 长的细线不是可采样区域)
    collinear = [[20, 50], [100, 50], [300, 50], [300, 50]]
    assert sample_line_style(frame, collinear, fs_px=28.0) is None
    # 全部点重合(零面积且零长度)
    assert sample_line_style(
        frame, [[60, 40], [60, 40], [60, 40], [60, 40]], fs_px=28.0) is None
    # 非退化多边形仍可采样(守卫不得误伤)
    assert sample_line_style(
        frame, [[60, 40], [260, 40], [260, 70], [60, 70]], fs_px=28.0) is not None


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
# merge_restoration_tags
# ---------------------------------------------------------------------------

def test_merge_restoration_tags_joins_inside_existing_block():
    """旋转/颜色必须并入既有 override 块**内部**(块外会被当字面文本)。"""
    from core.line_restoration import merge_restoration_tags

    out = merge_restoration_tags("{\\an4\\pos(20,28)\\fs16}",
                                 [[0, 0], [10, 0], [10, 10], [0, 10]])
    assert out == "{\\an4\\pos(20,28)\\fs16\\frz0.0\\frx0.0\\fry0.0}"
    assert "}\\frz" not in out


def test_merge_restoration_tags_defensive_wrap_without_block():
    """无 ``}`` 可并入时的防御分支:包成独立 override 块。"""
    from core.line_restoration import merge_restoration_tags

    out = merge_restoration_tags("plain", [[0, 0], [10, 0], [10, 10], [0, 10]])
    assert out == "plain{\\frz0.0\\frx0.0\\fry0.0}"


def test_merge_restoration_tags_passthrough_frx_fry_and_polygon_angle():
    from core.line_restoration import merge_restoration_tags

    poly = _tilted_poly(500, 400, 300, 40, 8.0, falling_right=True)
    out = merge_restoration_tags("{\\an5\\pos(500,400)}", poly,
                                 frx=12.5, fry=-3.0)
    assert out == "{\\an5\\pos(500,400)\\frz-8.0\\frx12.5\\fry-3.0}"


def test_merge_restoration_tags_samples_color_from_frame():
    from core.line_restoration import merge_restoration_tags

    frame = _bubble_frame()
    poly = [[60, 40], [260, 40], [260, 70], [60, 70]]
    out = merge_restoration_tags("{\\an5\\pos(160,55)}", poly, frame, 28.0)
    assert "\\frz0.0\\frx0.0\\fry0.0" in out
    assert "\\1c&H" in out and "\\3c&H" in out
    assert out.endswith("}")


def test_merge_restoration_tags_keeps_frz_when_sampling_fails():
    """取帧缺失/采样异常:只跳过取色,几何标签照常输出。"""
    from core.line_restoration import merge_restoration_tags

    poly = [[60, 40], [260, 40], [260, 70], [60, 70]]
    no_frame = merge_restoration_tags("{\\an5\\pos(160,55)}", poly, None, 28.0)
    assert no_frame == "{\\an5\\pos(160,55)\\frz0.0\\frx0.0\\fry0.0}"
    # 形状非法(shape 解包失败)的帧图:采样异常被吞掉,几何标签不受影响
    broken = merge_restoration_tags(
        "{\\an5\\pos(160,55)}", poly, np.zeros((5,), np.uint8), 28.0)
    assert broken == no_frame
    # 空多边形(退化)→ 无几何也无取色,但不抛异常
    empty = merge_restoration_tags("{\\an5\\pos(1,2)}", [], None, 28.0)
    assert empty == "{\\an5\\pos(1,2)\\frz0.0\\frx0.0\\fry0.0}"


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


def test_dedupe_rows_merges_temporally_close_provenance():
    """frames_seen 帧集相邻/交错(同一持续实例)→ 仍合并。

    原跳变修复不回退:近重复槽通常共享帧来源或来自相邻关键帧,
    最小交叉帧距为 0 ~ 1 个网格步。
    """
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("あんなに早く", (469.0, 608.0, 601.0, 632.0)),
        ("あんなに早く", (470.0, 609.0, 600.0, 631.0)),  # 同一实例的漂移读法
    ]
    kept, removed = dedupe_rows(
        rows, frames_seen=[{100, 104}, {102}], merge_frame_gap=100)
    assert removed == 1 and len(kept) == 1
    assert kept[0][0] == "あんなに早く"


def test_dedupe_rows_keeps_time_separated_duplicates():
    """frames_seen 帧距超过网格步长 → 不同时刻的合法重复,双份保留并留痕。

    副歌歌词重现/状态栏文字周期性出现:此前会被静默删掉一份(P0)。
    """
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("まだ帰ってない?", (469.0, 608.0, 601.0, 632.0)),
        ("まだ帰ってない?", (470.0, 609.0, 600.0, 631.0)),
    ]
    logs: list = []
    kept, removed = dedupe_rows(
        rows, frames_seen=[{100}, {5000}], merge_frame_gap=480,
        log=logs.append)
    assert removed == 0 and len(kept) == 2
    assert any("kept time-separated duplicate" in m for m in logs)


def test_dedupe_rows_merges_among_time_separated_group():
    """同文本三行、两两框位重叠:新行并入帧距最近的组,不并入超距组。"""
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("まだ帰ってない?", (469.0, 608.0, 601.0, 632.0)),   # 实例一
        ("まだ帰ってない?", (470.0, 609.0, 600.0, 631.0)),   # 实例二(超距,保留)
        ("まだ帰ってない?", (469.5, 608.5, 601.5, 632.5)),   # 与实例一同帧附近
    ]
    logs: list = []
    kept, removed = dedupe_rows(
        rows, frames_seen=[{100}, {5000}, {102}], merge_frame_gap=480,
        log=logs.append)
    # 第二行与第一行超距保留;第三行并入第一行(帧距 2),第二行仍是独立实例
    assert removed == 1 and len(kept) == 2
    assert any("kept time-separated duplicate" in m for m in logs)


def test_dedupe_rows_missing_provenance_falls_back_to_merge():
    """旧签名(无 frames_seen/merge_frame_gap)行为与历史版本一致。"""
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("あんなに早く", (469.0, 608.0, 601.0, 632.0)),
        ("あんなに早く", (470.0, 609.0, 600.0, 631.0)),
    ]
    kept, removed = dedupe_rows(rows)
    assert removed == 1 and len(kept) == 1


def test_dedupe_rows_empty_provenance_merges_conservatively():
    """来源缺失(空集,如回配失败的行)按旧规则合并:不扩大误删面之外的
    行为变化,保守保住跳变修复。"""
    from scripts.motion_ass import dedupe_rows

    rows = [
        ("あんなに早く", (469.0, 608.0, 601.0, 632.0)),
        ("あんなに早く", (470.0, 609.0, 600.0, 631.0)),
    ]
    kept, removed = dedupe_rows(
        rows, frames_seen=[set(), {100}], merge_frame_gap=10)
    assert removed == 1 and len(kept) == 1
