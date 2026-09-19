# tests/test_scene_text_policy.py
"""场景文字显示策略(core/scene_text_policy.py)单元测试。

覆盖《场景文字显示策略设计》§3/§4/§6:

- sample_background_color:白底黑字 → 近白 + 低 std;花色噪声 → 高 std;
  均匀暗区 → 精确取色且 std≈0;
- merge_line_blocks:邮件式布局(标题栏/发件人/10 行正文)→ 3 块;
  水平不重叠的相邻行不并块;输入乱序时按 y 内部排序;
- find_whitespace_band:上半有字下半空 → 命中下半带;全满 → None;
  occupied_boxes 占用行同样参与剖面;
- wrap_cjk:行首禁则(」。，、不下头);ASCII 单词尽量不断;超长词硬切;
- fit_font_size:按带高/带宽夹取到 [min_fs, base];
- apply_policy:overlap 原样返回;mask 生成 `\\p1` 遮罩(layer 0)+ 文本
  layer 1、`\\1c` 为采样色;背景杂色 → 回退 external;external 多块时间重叠
  合并单事件(底带位置、过高缩字号、时间不相交不合并);whitespace 文本
  落入空白带且坐标随轨迹;无带 → 回退 mask(杂色时继续 → external,
  完整回退链);
- 上下文与诊断契约:显式 `ref_frame`(≠ tracks[0])锚定 mask/whitespace
  轨迹;空轨迹按回退链降级不抛 IndexError;PolicyResult 携带 requested/
  applied mode、notes、diagnostics(参考帧、行框有效性统计);行框校验
  (NaN/inf 剔除、非正宽高剔除、越界裁剪、全无效行降级留痕);
- brightness_tag_chain 的 base_color 扩展:给定 BGR 基色时按通道缩放、
  缺省保持灰色行为(回归由 test_motion_ass 既有用例保证);
- 背景取色加固:sample_background_stats 的 robust 统计(MAD/IQR)、采样
  像素数与 confidence;暗底反白字/压缩振铃/彩色纯色/低对比渐变/极少背景
  像素;默认 std 模式判定与旧规则一致;
- 空白带加固:find_whitespace_band_scored 的 confidence 评分(面积/宽高/
  背景均匀性/可排版长度)与局部百分位/Otsu 阈值选项;低置信度按既定回退链
  降级;diagnostics 携带逐块背景统计与候选带信息。

轨迹用合成单应(平移)构造,与 test_motion_ass 同一模式;不读视频、不加载模型。
"""

from __future__ import annotations

import math
import os
import re
import sys

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import (  # noqa: E402
    brightness_tag_chain,
    build_line_tracks,
    format_ass_time,
    smooth_line_track,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402
from core.scene_text_policy import (  # noqa: E402
    BackgroundStats,
    SceneTextPolicyConfig,
    apply_policy,
    apply_policy_static,
    find_whitespace_band,
    find_whitespace_band_scored,
    fit_font_size,
    merge_line_blocks,
    sample_background_color,
    sample_background_stats,
    wrap_cjk,
)

FPS = 25.0


# ---------------------------------------------------------------------------
# 合成轨迹构造(纯平移;与 test_motion_ass 同一模式)
# ---------------------------------------------------------------------------

def _translation_homogs(n: int, dx: float = 2.0) -> list:
    return [np.array([
        [1.0, 0.0, dx * i],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64) for i in range(n)]


def make_translation_tracks(n: int = 10, dx: float = 2.0) -> list:
    """n 帧纯平移轨迹:H(init→t) = 平移 dx·t,plane 点 (x,y) → (x+dx·t, y)。"""
    tracks = []
    for i, h_mat in enumerate(_translation_homogs(n, dx)):
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


def simple_event(text: str, start: float, end: float) -> dict:
    """最小 motion 事件(正文 = 行文本,供 apply_policy 按正文归属块)。"""
    return {
        "start_time": format_ass_time(start),
        "end_time": format_ass_time(end),
        "style": "Scene",
        "name": "motion",
        "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}",
        "body": text,
    }


def move_points(tags: str):
    m = re.search(r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", tags)
    assert m, tags
    return tuple(float(g) for g in m.groups())


# 全流程共用:白底平面 + 两个块(单行标题 + 双行正文),左上角起步
PLANE_W, PLANE_H = 300, 400
ROWS = [
    ("标题", (20.0, 20.0, 200.0, 36.0)),    # 块 0:单行
    ("正文一", (20.0, 60.0, 200.0, 76.0)),  # 块 1:两行,间距 4 < 0.35×16
    ("正文二", (20.0, 80.0, 200.0, 96.0)),
]


def make_white_plane() -> np.ndarray:
    # 细墨条(行框内 4px 高,≈真实文本笔画占比):中位灰停在背景上
    plane = np.full((PLANE_H, PLANE_W, 3), 250, np.uint8)
    for _text, (x1, y1, x2, _y2) in ROWS:
        plane[int(y1) + 4:int(y1) + 8, int(x1) + 4:int(x2) - 4] = 10
    return plane


def policy_cfg(mode: str, **kw) -> SceneTextPolicyConfig:
    return SceneTextPolicyConfig(mode=mode, **kw)


# ---------------------------------------------------------------------------
# sample_background_color
# ---------------------------------------------------------------------------

class TestSampleBackgroundColor:
    def test_white_bg_black_text_near_white_low_std(self):
        img = np.full((80, 120, 3), 245, np.uint8)
        cv2.rectangle(img, (10, 10), (60, 30), (10, 10, 10), -1)  # ~13% 墨水
        color, std = sample_background_color(img, (0, 0, 120, 80))
        assert all(c >= 240 for c in color), color
        assert std < 2.0

    def test_colorful_background_high_std(self):
        rng = np.random.default_rng(1)
        img = rng.integers(0, 256, (80, 120, 3), dtype=np.uint8)
        color, std = sample_background_color(img, (0, 0, 120, 80))
        assert std > 40.0

    def test_uniform_dark_region_exact_color(self):
        # 中位灰即背景(均匀暗区):不判墨、逐通道中位 = 该色,std≈0
        img = np.full((60, 60, 3), 100, np.uint8)
        color, std = sample_background_color(img, (5, 5, 55, 55))
        assert color == (100, 100, 100)
        assert std == pytest.approx(0.0, abs=1e-6)

    def test_ink_removed_from_std(self):
        # 白底 + 大面积黑字:剔除墨水后 std 仍≈0(不剔除会显著放大)
        img = np.full((100, 100, 3), 200, np.uint8)
        cv2.rectangle(img, (0, 40), (99, 60), (5, 5, 5), -1)  # 21% 墨水
        color, std = sample_background_color(img, (0, 0, 100, 100))
        assert color == (200, 200, 200)
        assert std < 2.0

    def test_box_clipped_to_image(self):
        img = np.full((50, 50, 3), 245, np.uint8)
        color, _std = sample_background_color(img, (-20, -20, 500, 500))
        assert color == (245, 245, 245)

    def test_empty_region_guard(self):
        img = np.full((50, 50, 3), 245, np.uint8)
        color, std = sample_background_color(img, (60, 60, 90, 90))
        assert color == (0, 0, 0) and std == 0.0


# ---------------------------------------------------------------------------
# merge_line_blocks
# ---------------------------------------------------------------------------

class TestMergeLineBlocks:
    def test_mail_layout_three_blocks(self):
        # 邮件式布局:标题栏 / 发件人 / 10 行正文(行距 4 < 0.35×14)
        boxes = [(20.0, 10.0, 200.0, 30.0)]                # 标题栏 h=20
        boxes.append((20.0, 40.0, 150.0, 54.0))            # 发件人 h=14(间距 10)
        for i in range(10):                                # 正文 10 行(间距 4)
            y1 = 64.0 + 18.0 * i
            boxes.append((20.0, y1, 180.0, y1 + 14.0))
        blocks = merge_line_blocks(boxes, vgap_ratio=0.35)
        assert blocks == [(0, 0), (1, 1), (2, 11)]

    def test_unsorted_input_sorted_internally(self):
        boxes = [
            (20.0, 64.0, 180.0, 78.0),
            (20.0, 10.0, 200.0, 30.0),
            (20.0, 82.0, 180.0, 96.0),
        ]
        blocks = merge_line_blocks(boxes, vgap_ratio=0.35)
        assert blocks == [(1, 1), (0, 2)]  # 指向 y 升序后的序列

    def test_horizontal_disjoint_never_merged(self):
        boxes = [(10.0, 10.0, 50.0, 30.0), (60.0, 12.0, 100.0, 32.0)]
        assert merge_line_blocks(boxes) == [(0, 0), (1, 1)]

    def test_overlapping_y_merged(self):
        boxes = [(10.0, 10.0, 50.0, 30.0), (12.0, 26.0, 52.0, 46.0)]
        assert merge_line_blocks(boxes) == [(0, 1)]


# ---------------------------------------------------------------------------
# find_whitespace_band
# ---------------------------------------------------------------------------

class TestFindWhitespaceBand:
    def test_bottom_half_hit(self):
        img = np.full((300, 200), 240, np.uint8)
        img[20:120, 10:190] = 20  # 上半有字
        band = find_whitespace_band(img, [], line_h=20.0)
        assert band is not None
        x1, y1, x2, y2 = band
        assert y1 >= 120 - 10  # 膨胀余量内
        assert y2 >= 290
        assert (x2 - x1) >= 0.6 * 200

    def test_full_plane_returns_none(self):
        img = np.full((300, 200), 240, np.uint8)
        for y in range(0, 300, 6):
            img[y:y + 3, :] = 20  # 全满
        assert find_whitespace_band(img, [], line_h=20.0) is None

    def test_occupied_boxes_block_rows(self):
        img = np.full((300, 200), 240, np.uint8)
        # 无墨迹,但 occupied_boxes 盖满全部行 → None
        band = find_whitespace_band(
            img, [(0.0, 0.0, 200.0, 300.0)], line_h=20.0)
        assert band is None

    def test_band_between_blocks(self):
        img = np.full((400, 200), 240, np.uint8)
        img[20:80, :] = 20
        img[200:260, :] = 20
        band = find_whitespace_band(img, [], line_h=16.0)
        assert band is not None
        _x1, y1, _x2, y2 = band
        # 两个候选带(84~196 与 264~400),取面积最大者 = 下带
        assert y1 >= 260 - 10 and y2 - y1 >= 2 * 16.0

    def test_area_max_band_chosen(self):
        img = np.full((500, 200), 240, np.uint8)
        img[0:40, :] = 20      # 首块上方带高 0(顶格)
        img[240:280, :] = 20   # 上带 40px / 下带 220px → 取下带
        band = find_whitespace_band(img, [], line_h=16.0)
        assert band is not None
        _x1, y1, _x2, _y2 = band
        assert y1 >= 280 - 10


# ---------------------------------------------------------------------------
# wrap_cjk / fit_font_size
# ---------------------------------------------------------------------------

class TestWrapCjk:
    def test_plain_cjk_wrap(self):
        assert wrap_cjk("一二三四五六七", 3) == ["一二三", "四五六", "七"]

    def test_kinsoku_forbidden_line_start(self):
        # 」放不下且不得开头:连同上一字符下挪
        assert wrap_cjk("一二三四五」六", 5) == ["一二三四", "五」六"]

    def test_kinsoku_variants(self):
        for ch in "。，、!?:;…)】":
            out = wrap_cjk("甲乙丙丁" + ch, 4)
            assert out[0] == "甲乙丙丁" or not out[1].startswith(ch), (ch, out)
            assert all(not line.startswith(ch) for line in out if line), (ch, out)

    def test_ascii_words_not_broken(self):
        assert wrap_cjk("hello world foo", 8) == ["hello", "world", "foo"]

    def test_overlong_word_hard_split(self):
        assert wrap_cjk("abcdefgh", 3) == ["abc", "def", "gh"]

    def test_empty_text(self):
        assert wrap_cjk("", 10) == [""]


class TestFitFontSize:
    def test_base_kept_when_fits(self):
        assert fit_font_size(2, 200, 10, 800) == 40

    def test_shrunk_by_height(self):
        assert fit_font_size(30, 240, 4, 400) == 24  # 240//30=8 → 夹到下限

    def test_shrunk_by_width(self):
        assert fit_font_size(2, 100, 50, 100) == 24  # 100//50=2 → 夹到下限

    def test_mid_value(self):
        assert fit_font_size(10, 300, 5, 200) == 30  # min(40,30,40)

    def test_custom_base_and_min(self):
        assert fit_font_size(1, 1000, 2, 1000, base=50, min_fs=10) == 50
        assert fit_font_size(100, 100, 2, 1000, base=50, min_fs=10) == 10


# ---------------------------------------------------------------------------
# apply_policy:overlap
# ---------------------------------------------------------------------------

class TestApplyOverlap:
    def test_events_returned_as_is(self):
        events = [simple_event(t, 0.0, 0.4) for t, _b in ROWS]
        out, applied, notes = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("overlap"), PLANE_W, PLANE_H)
        assert applied == "overlap"
        assert notes == []
        assert out == events


# ---------------------------------------------------------------------------
# apply_policy:mask
# ---------------------------------------------------------------------------

class TestApplyMask:
    def _run(self, plane=None, **kw):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        plane_img = make_white_plane() if plane is None else plane
        return apply_policy(
            events, ROWS, plane_img, make_translation_tracks(),
            policy_cfg("mask", **kw), PLANE_W, PLANE_H)

    def test_mask_events_and_text_layer(self):
        out, applied, notes = self._run()
        assert applied == "mask" and notes == []
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        texts = [ev for ev in out if "\\p1" not in ev["tags"]]
        assert len(masks) == 2 and len(texts) == 3  # 两块遮罩 + 三行文本
        for ev in masks:
            assert ev["layer"] == 0
            assert "\\an7" in ev["tags"] and "\\move(" in ev["tags"]
            assert ev["tags"].startswith("{\\an7\\p1")
            assert ev["tags"].endswith("{\\p0}")
            assert "m 0 0 l " in ev["tags"]
            assert ev["base_color"] == (250, 250, 250)
            # 单段 \move 带 0,ms 时长端点
            assert re.search(r"\\move\([^)]+,0,400\)", ev["tags"]), ev["tags"]
            assert ev["start_time"] == "0:00:00.00"
            # 链尾 +1 帧距(与文本事件 motion_ass 同款修复)
            assert ev["end_time"] == format_ass_time(10 / FPS)
        for ev in texts:
            assert ev["layer"] == 1
            assert ev["tags"] == "{\\an5\\move(0.0,0.0,0.0,0.0)}"  # 原样保留
            assert ev["body"] in {"标题", "正文一", "正文二"}

    def test_mask_color_tag_is_sampled(self):
        out, _applied, _notes = self._run()
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert all("\\1c&HFAFAFA&" in ev["tags"] for ev in masks)  # 250=0xFA

    def test_mask_suppresses_style_outline(self):
        # \p 矩形不得继承 Scene 样式的 1px 描边(否则补丁带灰边)
        out, _applied, _notes = self._run()
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert masks and all("\\bord0" in ev["tags"] for ev in masks)

    def test_mask_expands_to_rendered_text_width(self):
        # 替换字体字宽 > 原 OCR 框宽时,遮罩按渲染宽度居中外扩
        # (平面放宽,排除边界裁剪干扰)
        plane_w = 900
        long_text = "一" * 30  # 30 全角 × 行高 26 = 780 > 框宽 90
        rows = [(long_text, (10.0, 10.0, 100.0, 36.0))]
        events = [simple_event(rows[0][0], 0.0, 9 / FPS)]
        out, _applied, _notes = apply_policy(
            events, rows, np.full((PLANE_H, plane_w, 3), 250, np.uint8),
            make_translation_tracks(), policy_cfg("mask"), plane_w, PLANE_H)
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        m = re.search(r"m 0 0 l (\d+) 0", mask0["tags"])
        assert m
        # 字幕 \an5 中心锚定:渲染文本(30×36=780)以行框中心 x=55 对称扩展,
        # 可见部分 0..445;遮罩以其为中心等宽覆盖(0..449,左侧触平面边界)
        assert int(m.group(1)) == pytest.approx(449, abs=2)

    def test_mask_covers_expanded_block_box(self):
        # 块 0 行高 16、pad=0.12×16≈1.92 → 遮罩框高 ≈ 19.84 → 绘制高 20
        out, _applied, _notes = self._run()
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        m = re.search(r"m 0 0 l (\d+) 0 (\d+) (\d+) 0 (\d+)", mask0["tags"])
        assert m
        w, h = int(m.group(1)), int(m.group(3))  # l w 0 w h 0 h
        assert w == pytest.approx(200 - 20 + 2 * 1.92, abs=1.5)
        assert h == pytest.approx(16 + 2 * 1.92, abs=1.5)

    def test_mask_move_tracks_top_left_corner(self):
        # 平移 dx=2/帧:遮罩左上角随帧平移;窗口 5 平滑后首末端各内收 1 帧
        # (帧 0 窗口 {0,1,2}、帧 9 窗口 {7,8,9}),跨度 = 2×(9-2) = 14
        out, _applied, _notes = self._run()
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        x1, y1, x2, y2 = move_points(mask0["tags"])
        assert x2 - x1 == pytest.approx(14.0, abs=0.6)
        assert y1 == y2
        # 块 0 外扩框左上角 (20-1.92, 20-1.92),帧 0(平滑后)≈ 18.08+2
        assert x1 == pytest.approx(18.08 + 2.0, abs=1.5)
        assert y1 == pytest.approx(18.08, abs=1.0)

    def test_noisy_background_falls_back_to_external(self):
        rng = np.random.default_rng(3)
        noise = rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)
        out, applied, notes = self._run(plane=noise)
        assert applied == "external"
        assert len(notes) == 1 and "mask" in notes[0] and "external" in notes[0]
        assert all(ev["style"] == "NoteBox" for ev in out)
        assert all(not ev["tags"].startswith("{\\an7\\p1") for ev in out)

    def test_mask_follows_punct_compensation(self):
        # 行尾「。」触发 core.motion_ass 的 x 字形补偿(默认开,上限随
        # PlayRes 高度缩放:7×400/1080≈2.59 < 0.25×16),遮罩块同步右移。
        plane = make_white_plane()
        tracks = make_translation_tracks()
        expected_dx = min(7.0 * PLANE_H / 1080.0, 0.25 * 16.0)
        xs = []
        for text in ("あいう", "あいう。"):
            rows = [(text, (20.0, 20.0, 200.0, 36.0))]
            events = [simple_event(text, 0.0, 9 / FPS)]
            out, _a, _n = apply_policy(
                events, rows, plane, tracks, policy_cfg("mask"),
                PLANE_W, PLANE_H)
            mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
            x1, _y1, _x2, _y2 = move_points(mask0["tags"])
            xs.append(x1)
        assert xs[1] - xs[0] == pytest.approx(expected_dx, abs=0.05)


# ---------------------------------------------------------------------------
# apply_policy:external
# ---------------------------------------------------------------------------

class TestApplyExternal:
    def _run(self, events, rows, **kw):
        return apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("external", **kw), PLANE_W, PLANE_H)

    def test_overlapping_blocks_merge_single_event(self):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        out, applied, notes = self._run(events, ROWS)
        assert applied == "external" and notes == []
        assert len(out) == 1
        ev = out[0]
        assert ev["style"] == "NoteBox" and ev["name"] == "motion"
        # 底带居中:\an2\pos(W/2, H-margin)
        assert ev["tags"].startswith("{\\an2\\pos(150.0,360.0)\\fs")
        # 行序自上而下,\N 连接
        assert ev["body"] == "标题\\N正文一\\N正文二"
        assert ev["start_time"] == "0:00:00.00"
        assert ev["end_time"] == format_ass_time(9 / FPS)

    def test_disjoint_time_spans_not_merged(self):
        events = [simple_event("标题", 0.0, 1.0),
                  simple_event("正文一", 4.0, 5.0),
                  simple_event("正文二", 4.0, 5.0)]
        out, applied, _notes = self._run(events, ROWS)
        assert applied == "external"
        assert len(out) == 2
        assert out[0]["body"] == "标题"
        assert out[1]["body"] == "正文一\\N正文二"
        assert out[0]["end_time"] == "0:00:01.00"
        assert out[1]["start_time"] == "0:00:04.00"

    def test_too_many_rows_shrink_font(self):
        # 12 行短文本 → 带高 360//12=30 < base 40 → 缩到 30
        rows = [(f"行{i}", (20.0, 10.0 + 18.0 * i, 120.0, 24.0 + 18.0 * i))
                for i in range(12)]
        events = [simple_event(t, 0.0, 1.0) for t, _b in rows]
        out, _applied, _notes = self._run(events, rows)
        assert len(out) == 1
        m = re.search(r"\\fs(\d+)", out[0]["tags"])
        assert m and int(m.group(1)) == 30

    def test_top_position(self):
        events = [simple_event("标题", 0.0, 1.0)]
        out, _applied, _notes = self._run(
            events, ROWS[:1], external_pos="top")
        assert out[0]["tags"].startswith("{\\an8\\pos(150.0,40.0)\\fs")

    def test_wrap_long_line_by_screen_width(self):
        long_text = "一二三四五六七八九十" * 3  # 30 字 > 每行 (300-80)//40=5 字
        events = [simple_event(long_text, 0.0, 1.0)]
        out, _applied, _notes = self._run(events, [(long_text, ROWS[0][1])])
        lines = out[0]["body"].split("\\N")
        assert len(lines) == 6 and all(len(l) == 5 for l in lines)


# ---------------------------------------------------------------------------
# apply_policy:whitespace
# ---------------------------------------------------------------------------

class TestApplyWhitespace:
    def _rows_events(self):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        return events

    def test_text_lands_in_band_and_follows_track(self):
        out, applied, notes = apply_policy(
            self._rows_events(), ROWS, make_white_plane(),
            make_translation_tracks(), policy_cfg("whitespace"),
            PLANE_W, PLANE_H)
        assert applied == "whitespace" and notes == []
        # 全部块文本合并、按带宽折行(LINE 式 3 字行 → 原文一行一条)
        bodies = sorted(ev["body"] for ev in out)
        assert bodies == sorted(["标题", "正文一", "正文二"])
        for ev in out:
            assert "\\move(" in ev["tags"]
            x1, y1, x2, y2 = move_points(ev["tags"])
            # 纯水平平移:y 不变;x 随轨迹前进(平滑后首末各内收 1 帧 → 14)
            assert y1 == y2
            assert x2 - x1 == pytest.approx(14.0, abs=0.6)
            # 落入下半空白带(原文字最低 y=96,带从 ~100 起)
            assert y1 > 100.0, ev["tags"]
            assert y1 < float(PLANE_H)

    def test_wrapped_rows_increase_line_count(self):
        # 长文本 → 带内折行成多行,行数 > 原行数
        long_rows = [("很长的一行文本需要折行" * 3, (20.0, 20.0, 250.0, 36.0))]
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in long_rows]
        out, applied, _notes = apply_policy(
            events, long_rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert applied == "whitespace"
        assert len(out) > 1
        ys = [move_points(ev["tags"])[1] for ev in out]
        assert ys == sorted(ys)  # 折行行自上而下
        assert ys[-1] - ys[0] > 0  # 多行排布

    def test_no_band_falls_back_to_mask(self):
        # 白底 + 全宽横向条纹:任何行都有墨迹 → 无空白带;白底干净 → mask 可用
        plane = np.full((PLANE_H, PLANE_W, 3), 245, np.uint8)
        plane[::4, :, :] = 10
        events = self._rows_events()
        out, applied, notes = apply_policy(
            events, ROWS, plane, make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert applied == "mask"
        assert len(notes) == 1
        assert "whitespace" in notes[0] and "mask" in notes[0]
        assert sum(1 for ev in out if "\\p1" in ev["tags"]) == 2
        assert all(ev["layer"] == 1 for ev in out if "\\p1" not in ev["tags"])

    def test_full_chain_whitespace_mask_external(self):
        # 无空白带 + 背景杂色 → 完整回退链 whitespace → mask → external
        rng = np.random.default_rng(5)
        noise = rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)
        plane = noise.copy()
        plane[::4, :, :] = 10  # 保证无空白带
        events = self._rows_events()
        out, applied, notes = apply_policy(
            events, ROWS, plane, make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert applied == "external"
        assert len(notes) == 2
        assert "whitespace" in notes[0] and "mask" in notes[0]
        assert "mask" in notes[1] and "external" in notes[1]
        assert all(ev["style"] == "NoteBox" for ev in out)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            apply_policy(
                [], ROWS, make_white_plane(), make_translation_tracks(),
                policy_cfg("bogus"), PLANE_W, PLANE_H)


# ---------------------------------------------------------------------------
# apply_policy:轨迹复用一致性(合成行框经 build_line_tracks 同款映射)
# ---------------------------------------------------------------------------

class TestWhitespaceTrackReuse:
    def test_matches_direct_build_line_tracks(self):
        # 与「手动构造同款合成行框 → build_line_tracks → smooth」逐帧一致,
        # 证明 whitespace 复用同一坐标链(轨迹零成本重用)。
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        out, applied, _notes = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert applied == "whitespace"
        # 按 apply_policy 的折行/字号/排布公式重建行框
        band = find_whitespace_band(
            cv2.cvtColor(make_white_plane(), cv2.COLOR_BGR2GRAY),
            [b for _t, b in ROWS], line_h=16.0)
        assert band is not None
        band_w, band_h = band[2] - band[0], band[3] - band[1]
        parts = []
        for text, _b in ROWS:
            parts.extend(wrap_cjk(text, max(1, band_w // 40)))
        fs = fit_font_size(len(parts), band_h, max(len(p) for p in parts),
                           band_w)
        y0 = band[1] + (band_h - fs * len(parts)) / 2.0
        boxes = [(band[0], y0 + i * fs, band[0] + max(1, len(p)) * fs,
                  y0 + (i + 1) * fs) for i, p in enumerate(parts)]
        tracks = make_translation_tracks()
        ref_tracks = build_line_tracks(boxes, parts, tracks, ref_frame=0)
        for lt in ref_tracks:
            smooth_line_track(lt, window=5)
        for ev in out:
            lt = next(t for t in ref_tracks if t.text == ev["body"])
            x1, y1, x2, y2 = move_points(ev["tags"])
            f0, f1 = min(lt.poses), max(lt.poses)
            # 合成行框全部左对齐于带左缘(宽度随行长变化)→ 检测为 left 块,
            # 事件锚 \an4 于行框左缘中点 = 中心 − 半框宽(平移轨迹 scale=1)
            w0 = lt.ref_box[2] - lt.ref_box[0]
            assert (x1, y1) == pytest.approx(
                (lt.poses[f0].center[0] - w0 / 2.0, lt.poses[f0].center[1]),
                abs=0.05), ev
            assert (x2, y2) == pytest.approx(
                (lt.poses[f1].center[0] - w0 / 2.0, lt.poses[f1].center[1]),
                abs=0.05), ev


# ---------------------------------------------------------------------------
# brightness_tag_chain 的 base_color 扩展(遮罩调暗联动)
# ---------------------------------------------------------------------------

class TestBrightnessBaseColor:
    CURVE = [(0.0, 1.0), (2.0, 0.5)]

    def test_base_color_scales_channels_no_alpha(self):
        # BGR (250, 100, 20) × 0.5 = (125, 50, 10) = &H7D320A&
        assert brightness_tag_chain(
            self.CURVE, 0.0, 4.0, use_alpha=False,
            base_color=(250, 100, 20)) == "\\1c&HFA6414&\\t(0,2000,\\1c&H7D320A&)"

    def test_base_color_dark_start(self):
        # r0=0.12:B=30=0x1E、G=12=0x0C、R=2(2.4→2)
        out = brightness_tag_chain(
            [(0.0, 0.12)], 0.0, 1.0, use_alpha=False, base_color=(250, 100, 20))
        assert out == "\\1c&H1E0C02&"

    def test_default_gray_behavior_unchanged(self):
        assert brightness_tag_chain(self.CURVE, 0.0, 4.0) == (
            "\\1c&HFFFFFF&\\alpha&H00&"
            "\\t(0,2000,\\1c&H808080&\\alpha&H80&)")

    def test_base_color_respects_use_color_off(self):
        # 遮罩链 use_alpha=False:再关掉 \1c 后整个链为空
        assert brightness_tag_chain(
            self.CURVE, 0.0, 4.0, use_color=False, use_alpha=False,
            base_color=(250, 100, 20)) == ""


# ---------------------------------------------------------------------------
# apply_policy_static:静态 \pos 路径(主流水线用,不带时间/轨迹)
# ---------------------------------------------------------------------------

class TestApplyPolicyStatic:
    def test_overlap_returns_empty_specs(self):
        specs, applied, notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("overlap"), PLANE_W, PLANE_H)
        assert specs == [] and applied == "overlap" and notes == []

    def test_mask_static_rect_and_text_layers(self):
        specs, applied, notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        assert applied == "mask" and notes == []
        masks = [s for s in specs if s["kind"] == "mask"]
        texts = [s for s in specs if s["kind"] == "text"]
        assert len(masks) == 2 and len(texts) == 3  # 两块遮罩 + 三行文本
        for s in masks:
            assert s["layer"] == 0 and s["style"] == "Scene"
            assert "\\move" not in s["tags"]  # 静态路径不含 \move
            assert s["tags"].startswith("{\\an7\\pos(")
            assert "\\p1\\bord0" in s["tags"]
            assert "\\1c&HFAFAFA&" in s["tags"]  # 采样色 = 白底
            assert s["tags"].endswith("{\\p0}")
            assert "m 0 0 l " in s["tags"]
        covered = sorted(i for s in masks for i in s["rows"])
        assert covered == [0, 1, 2]  # 全部行归属某块(输入序索引)
        for s in texts:
            assert s["layer"] == 1 and s["style"] == "Scene"
            assert s["tags"].startswith("{\\an5\\pos(")
            assert s["body"] in {"标题", "正文一", "正文二"}
            assert isinstance(s["row"], int)

    def test_mask_box_top_left_matches_expanded_block(self):
        # 块 0:行框 (20,20)-(200,36),pad=0.12×16≈1.92 → 外扩框左上角 ≈(18.1,18.1)
        specs, _applied, _notes = apply_policy_static(
            ROWS[:1], make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        assert specs[0]["kind"] == "mask"
        assert "\\pos(18.1,18.1)" in specs[0]["tags"]

    def test_noisy_background_falls_back_to_external(self):
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)
        specs, applied, notes = apply_policy_static(
            ROWS, noise, policy_cfg("mask"), PLANE_W, PLANE_H)
        assert applied == "external"
        assert len(notes) == 1 and "mask" in notes[0] and "external" in notes[0]
        assert len(specs) == 1
        spec = specs[0]
        assert spec["kind"] == "note" and spec["style"] == "NoteBox"
        # 底带居中:\an2\pos(W/2, H-margin)
        assert spec["tags"].startswith("{\\an2\\pos(150.0,360.0)\\fs")
        assert spec["body"] == "标题\\N正文一\\N正文二"

    def test_external_dedups_repeated_row_texts(self):
        # 静态路径 rows 跨整个 ROI:常驻文字(状态栏)随每个 OCR 组重复进入,
        # NoteBox 按整行文本去重(保持首次出现顺序),否则同一行重复几十次。
        rows = ([("べにっぽ", (20.0, 8.0, 120.0, 20.0))]
                + [(t, box) for t, box in ROWS] * 3)
        specs, _applied, _notes = apply_policy_static(
            rows, make_white_plane(), policy_cfg("external"), PLANE_W, PLANE_H)
        assert len(specs) == 1
        body = specs[0]["body"]
        assert body.split("\\N") == ["べにっぽ", "标题", "正文一", "正文二"]

    def test_whitespace_dedups_repeated_row_texts(self):
        rows = ([("べにっぽ", (20.0, 8.0, 120.0, 20.0))]
                + [(t, box) for t, box in ROWS] * 3)
        specs, applied, _notes = apply_policy_static(
            rows, make_white_plane(), policy_cfg("whitespace"),
            PLANE_W, PLANE_H)
        assert applied == "whitespace"
        assert specs[0]["body"].split("\\N") == [
            "べにっぽ", "标题", "正文一", "正文二"]
        assert specs[0]["kind"] == "scene_ws" and specs[0]["style"] == "Scene"

    def test_external_single_note_spec(self):
        specs, applied, notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("external"), PLANE_W, PLANE_H)
        assert applied == "external" and notes == []
        assert len(specs) == 1
        spec = specs[0]
        assert spec["kind"] == "note" and spec["style"] == "NoteBox"
        assert spec["tags"] == "{\\an2\\pos(150.0,360.0)\\fs40}"

    def test_whitespace_spec_lands_in_band(self):
        specs, applied, notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("whitespace"),
            PLANE_W, PLANE_H)
        assert applied == "whitespace" and notes == []
        assert len(specs) == 1
        spec = specs[0]
        assert spec["kind"] == "scene_ws" and spec["style"] == "Scene"
        # 左对齐于带左缘(\an4):与 motion 版合成行框同一布局约定
        assert spec["tags"].startswith("{\\an4\\pos(")
        m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", spec["tags"])
        assert m
        x, y = float(m.group(1)), float(m.group(2))
        assert 0.0 <= x < float(PLANE_W)
        assert y > 100.0  # 原文字最低 y=96,锚点落在下方空白带
        assert "\\fs" in spec["tags"]
        assert spec["body"] == "标题\\N正文一\\N正文二"

    def test_whitespace_no_band_falls_back_to_mask(self):
        # 白底 + 全宽横向条纹:无空白带;背景干净 → mask 可用
        plane = np.full((PLANE_H, PLANE_W, 3), 245, np.uint8)
        plane[::4, :, :] = 10
        specs, applied, notes = apply_policy_static(
            ROWS, plane, policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert applied == "mask"
        assert any(s["kind"] == "mask" for s in specs)
        assert len(notes) == 1
        assert "whitespace" in notes[0] and "mask" in notes[0]

    def test_row_indices_follow_input_order(self):
        rows = [ROWS[2], ROWS[0], ROWS[1]]  # 乱序输入
        specs, _applied, _notes = apply_policy_static(
            rows, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        texts = {s["body"]: s["row"] for s in specs if s["kind"] == "text"}
        assert texts == {"标题": 1, "正文一": 2, "正文二": 0}
        covered = sorted(i for s in specs if s["kind"] == "mask" for i in s["rows"])
        assert covered == [0, 1, 2]

    def test_base_font_size_controls_fit(self):
        specs, _applied, _notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("external"), PLANE_W, PLANE_H,
            base_font_size=10)
        assert "\\fs10" in specs[0]["tags"]

    def test_empty_rows_no_crash(self):
        for mode in ("mask", "external", "whitespace"):
            specs, applied, _notes = apply_policy_static(
                [], make_white_plane(), policy_cfg(mode), PLANE_W, PLANE_H)
            assert specs == [] and applied == mode


# ---------------------------------------------------------------------------
# 上下文与诊断契约:显式 ref_frame、空轨迹降级、结果诊断字段、行框校验
# ---------------------------------------------------------------------------

def make_offset_tracks(first_frame: int = 100, n: int = 10, dx: float = 2.0) -> list:
    """n 帧纯平移轨迹,帧号 first_frame..first_frame+n-1(时间 = 帧号/FPS)。

    H(init→f) = 平移 dx·f(init = 0 号帧平面);于是 H(a→b) = 平移 dx·(b-a),
    行框在参考帧 a 的平面坐标映射到帧 b 时平移 dx·(b-a)。tracks[0].frame_num
    = first_frame,与显式 ref_frame(如 OCR 锚定帧 105)不同,用于验证策略
    不再隐式使用 tracks[0] 作参考帧。
    """
    tracks = []
    for k in range(n):
        f = first_frame + k
        h_mat = np.array([
            [1.0, 0.0, dx * f],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=f, time_sec=f / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


class TestExplicitRefFrame:
    """ref_frame ≠ tracks[0].frame_num 时,轨迹以显式参考帧构建。"""

    ANCHOR = 105  # OCR 锚定帧;tracks[0].frame_num = 100

    def _rows_events(self):
        return [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]

    def test_mask_track_anchored_at_explicit_ref_frame(self):
        tracks = make_offset_tracks(first_frame=100)
        res = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H, ref_frame=self.ANCHOR)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        assert res.diagnostics["ref_frame"] == self.ANCHOR
        assert res.diagnostics["ref_frame_source"] == "explicit"
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        x1, y1, x2, y2 = move_points(mask0["tags"])
        # 块 0 外扩框左上角 (18.08, 18.08) 在锚定帧 105 的平面坐标;窗口 5
        # 平滑后链首帧 100 的位姿 = 参考帧 105 坐标 + dx·(101-105):
        # 显式参考帧 → x ≈ 18.08 - 8 = 10.08(隐式 tracks[0]=100 会是 20.08)
        assert x1 == pytest.approx(18.08 + 2.0 * (101 - 105), abs=1.5)
        assert y1 == pytest.approx(18.08, abs=1.0)
        assert x2 - x1 == pytest.approx(14.0, abs=0.6)
        assert y1 == y2

    def test_whitespace_track_anchored_at_explicit_ref_frame(self):
        tracks = make_offset_tracks(first_frame=100)
        base = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), tracks,
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        expl = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), tracks,
            policy_cfg("whitespace"), PLANE_W, PLANE_H, ref_frame=self.ANCHOR)
        assert base[1] == "whitespace" and expl[1] == "whitespace"
        assert expl.diagnostics["ref_frame"] == self.ANCHOR
        assert expl.diagnostics["ref_frame_source"] == "explicit"
        assert len(base[0]) == len(expl[0]) > 0
        for ev_b, ev_e in zip(base[0], expl[0]):
            bx1, by1, _bx2, _by2 = move_points(ev_b["tags"])
            ex1, ey1, _ex2, _ey2 = move_points(ev_e["tags"])
            # 纯平移:显式 ref(105) 相对隐式 ref(100) 整体左移 dx×5 = 10
            assert ex1 - bx1 == pytest.approx(-2.0 * 5, abs=0.6)
            assert ey1 == by1

    def test_default_ref_frame_inferred_backward_compatible(self):
        # 缺省 ref_frame:保持旧行为(隐式 tracks[0].frame_num)并在
        # diagnostics 注明推断参考帧
        tracks = make_offset_tracks(first_frame=100)
        res = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        assert res.diagnostics["ref_frame"] == 100
        assert res.diagnostics["ref_frame_source"] == "inferred"
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        x1, _y1, _x2, _y2 = move_points(mask0["tags"])
        assert x1 == pytest.approx(18.08 + 2.0, abs=1.5)  # 旧隐式行为不变


class TestEmptyTracksDegradation:
    """tracks=[] 而 rows 非空:明确降级,不抛 IndexError。"""

    def _rows_events(self):
        return [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]

    def test_mask_empty_tracks_falls_back_to_external(self):
        res = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), [],
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "external"
        assert notes and "tracks" in notes[0]
        assert res.diagnostics["applied_mode"] == "external"
        assert res.requested_mode == "mask"
        assert all(ev["style"] == "NoteBox" for ev in out)

    def test_whitespace_empty_tracks_falls_back_to_external(self):
        res = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), [],
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "external"
        assert notes and "tracks" in notes[0]
        assert res.diagnostics["applied_mode"] == "external"
        assert all(ev["style"] == "NoteBox" for ev in out)

    def test_external_empty_tracks_still_layouts(self):
        res = apply_policy(
            self._rows_events(), ROWS, make_white_plane(), [],
            policy_cfg("external"), PLANE_W, PLANE_H)
        out, applied, _notes = res
        assert applied == "external"
        assert len(out) == 1 and out[0]["style"] == "NoteBox"

    def test_overlap_empty_tracks_returned_as_is(self):
        events = self._rows_events()
        out, applied, notes = apply_policy(
            events, ROWS, make_white_plane(), [],
            policy_cfg("overlap"), PLANE_W, PLANE_H)
        assert out == events and applied == "overlap" and notes == []


class TestPolicyResultContract:
    """策略结果携带 requested/applied mode、notes、diagnostics。"""

    def test_full_fallback_chain_result_fields(self):
        # whitespace → mask → external 全链:requested/applied/notes/diagnostics
        rng = np.random.default_rng(9)
        plane = rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)
        plane[::4, :, :] = 10  # 保证无空白带
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        res = apply_policy(
            events, ROWS, plane, make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        out, applied, notes = res  # 三元组解包兼容(既有消费方不受影响)
        assert res.requested_mode == "whitespace"
        assert res.applied_mode == applied == "external"
        assert res.notes == notes and len(notes) == 2
        diag = res.diagnostics
        assert isinstance(diag, dict)
        assert diag["requested_mode"] == "whitespace"
        assert diag["applied_mode"] == "external"
        assert diag["ref_frame"] == 0  # make_translation_tracks 帧号从 0 起
        assert diag["ref_frame_source"] == "inferred"
        assert diag["total_rows"] == 3
        assert diag["valid_rows"] == 3
        assert diag["invalid_rows"] == 0

    def test_overlap_result_fields(self):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        res = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("overlap"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert res.requested_mode == "overlap"
        assert res.applied_mode == applied == "overlap"
        assert res.notes == notes == []
        assert res.diagnostics["applied_mode"] == "overlap"
        assert out == events

    def test_static_result_fields(self):
        res = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        specs, applied, notes = res
        assert res.requested_mode == "mask"
        assert res.applied_mode == applied == "mask"
        assert res.notes == notes == []
        diag = res.diagnostics
        assert diag["requested_mode"] == "mask"
        assert diag["applied_mode"] == "mask"
        assert diag["ref_frame"] is None       # 静态路径无轨迹
        assert diag["ref_frame_source"] == "none"
        assert diag["invalid_rows"] == 0


class TestRowBoxValidation:
    """输入行框校验:非有限/非正宽高/越界的判定与裁剪。"""

    def test_nan_inf_and_non_positive_boxes_dropped(self):
        rows = [
            ("nan行", (float("nan"), 20.0, 100.0, 36.0)),
            ("inf行", (20.0, float("inf"), 100.0, 36.0)),
            ("负宽", (100.0, 20.0, 20.0, 36.0)),
            ("零高", (20.0, 36.0, 100.0, 36.0)),
            ("标题", (20.0, 20.0, 200.0, 36.0)),  # 唯一有效行
        ]
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in rows]
        res = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        assert res.diagnostics["total_rows"] == 5
        assert res.diagnostics["invalid_rows"] == 4
        assert res.diagnostics["valid_rows"] == 1
        assert len(res.diagnostics["invalid_reasons"]) == 4
        assert sum(1 for ev in out if "\\p1" in ev["tags"]) == 1

    def test_partial_out_of_bounds_box_clipped_to_plane(self):
        rows = [("越界", (-30.0, -10.0, 500.0, 36.0))]
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in rows]
        res = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, _notes = res
        assert applied == "mask"
        assert res.diagnostics["clipped_rows"] == 1
        assert res.diagnostics["invalid_rows"] == 0
        mask0 = next(ev for ev in out if "\\p1" in ev["tags"])
        x1, y1, _x2, _y2 = move_points(mask0["tags"])
        assert x1 >= 0.0 and y1 >= 0.0  # 遮罩不越出平面边界

    def test_fully_out_of_bounds_box_invalid(self):
        rows = [("出界", (400.0, 500.0, 600.0, 600.0))]  # 平面 300×400 之外
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in rows]
        res = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert res.diagnostics["invalid_rows"] == 1
        assert applied == "overlap"          # 无有效行 → 策略未应用
        assert out == events                  # 原事件返回
        assert notes and "invalid" in notes[0]

    def test_all_rows_invalid_overlap_returns_original_events(self):
        rows = [("a", (float("nan"), 0.0, 10.0, 10.0)),
                ("b", (10.0, 10.0, 5.0, 20.0))]
        events = [simple_event(t, 0.0, 1.0) for t, _b in rows]
        for mode in ("overlap", "mask", "external", "whitespace"):
            res = apply_policy(
                events, rows, make_white_plane(), make_translation_tracks(),
                policy_cfg(mode), PLANE_W, PLANE_H)
            out, applied, notes = res
            assert res.diagnostics["invalid_rows"] == 2
            if mode == "overlap":
                assert out == events and applied == "overlap" and notes == []
            else:
                # 按回退链降级:无可布局对象 → 策略未应用(原事件返回)+ 原因
                assert out == events and applied == "overlap"
                assert notes and "invalid" in notes[0]
                assert res.diagnostics["applied_mode"] == "overlap"

    def test_static_invalid_rows_dropped_with_diagnostics(self):
        rows = [("坏", (float("nan"), 20.0, 100.0, 36.0)),
                ("标题", (20.0, 20.0, 200.0, 36.0))]
        res = apply_policy_static(
            rows, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        specs, applied, _notes = res
        assert applied == "mask"
        assert res.diagnostics["total_rows"] == 2
        assert res.diagnostics["invalid_rows"] == 1
        texts = [s["body"] for s in specs if s["kind"] == "text"]
        assert texts == ["标题"]

    def test_static_all_rows_invalid_degrades_with_reason(self):
        rows = [("坏", (float("nan"), 20.0, 100.0, 36.0))]
        res = apply_policy_static(
            rows, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        specs, applied, notes = res
        assert specs == [] and applied == "mask"  # 静态路径既有约定
        assert notes and "invalid" in notes[0]
        assert res.diagnostics["invalid_rows"] == 1


# ---------------------------------------------------------------------------
# 事件归属:重复 body 不得按正文合并(line_idx 优先,正文仅兼容回退)
# ---------------------------------------------------------------------------

class TestDuplicateBodyEventIdentity:
    """两行文案完全相同但时间跨度不同:external 块跨度按行归属,不按 body。"""

    def test_external_block_spans_follow_line_idx(self):
        # 旧实现按 body 匹配:两个块都把两条事件并入自己的跨度 → 全部并成
        # [0,5] 一条 NoteBox;正确行为是每块只归属自己 line_idx 的事件。
        rows = [("重复台词", (20.0, 20.0, 200.0, 36.0)),
                ("重复台词", (20.0, 60.0, 200.0, 76.0))]
        events = [dict(simple_event("重复台词", 0.0, 1.0), line_idx=0),
                  dict(simple_event("重复台词", 4.0, 5.0), line_idx=1)]
        out, applied, _notes = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("external"), PLANE_W, PLANE_H)
        assert applied == "external"
        assert len(out) == 2, out
        spans = sorted((ev["start_time"], ev["end_time"]) for ev in out)
        assert spans == [("0:00:00.00", "0:00:01.00"),
                         ("0:00:04.00", "0:00:05.00")]

    def test_body_match_still_works_without_line_idx(self):
        # 兼容回退:事件不带 line_idx 时保持按正文归属(既有行为)
        rows = [("标题", (20.0, 20.0, 200.0, 36.0))]
        events = [simple_event("标题", 0.0, 1.0)]
        out, applied, _notes = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("external"), PLANE_W, PLANE_H)
        assert applied == "external"
        assert len(out) == 1
        assert out[0]["start_time"] == "0:00:00.00"
        assert out[0]["end_time"] == "0:00:01.00"


# ---------------------------------------------------------------------------
# style 传递:自定义 style 必须出现在 mask/whitespace 事件
# ---------------------------------------------------------------------------

class TestCustomStyleThreading:
    """调用者传入自定义 style 名时,mask/whitespace 事件携带该 style。"""

    def test_dynamic_mask_events_use_custom_style(self):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        out, applied, _notes = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H, style="MyScene")
        assert applied == "mask"
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert masks
        assert all(ev["style"] == "MyScene" for ev in masks)

    def test_dynamic_whitespace_events_use_custom_style(self):
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        out, applied, _notes = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H, style="MyScene")
        assert applied == "whitespace"
        assert out
        assert all(ev["style"] == "MyScene" for ev in out)

    def test_static_mask_and_text_specs_use_custom_style(self):
        specs, applied, _notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("mask"), PLANE_W, PLANE_H,
            style="MyScene")
        assert applied == "mask"
        assert specs
        assert all(s["style"] == "MyScene" for s in specs
                   if s["kind"] in ("mask", "text"))

    def test_static_whitespace_spec_uses_custom_style(self):
        specs, applied, _notes = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("whitespace"), PLANE_W,
            PLANE_H, style="MyScene")
        assert applied == "whitespace"
        assert specs
        assert specs[0]["style"] == "MyScene"

    def test_default_style_unchanged(self):
        # 缺省 style 保持 "Scene"(动态 mask 硬编码消除后不得改变默认值)
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        out, applied, _notes = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H)
        assert applied == "mask"
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert masks
        assert all(ev["style"] == "Scene" for ev in masks)


# ---------------------------------------------------------------------------
# 多行块遮罩:按行补偿后求 union
# ---------------------------------------------------------------------------

class TestMaskPerLineUnion:
    """块内各行渲染宽度/标点补偿不同:按行外扩+补偿后求 union,逐行覆盖。"""

    def test_per_line_union_covers_each_row(self):
        # 两行并成一个块:x 框相同 (20..90)。
        # 行 0「ああああああ」渲染宽 6×16=96(块内最宽,无标点补偿);
        # 行 1「あああ。」渲染宽 64、行尾。补偿 dx=min(7, 0.25×16)=4@1080p。
        # 旧实现取块级最大补偿把整块遮罩右移 4 → 行 0 渲染左缘 7 悬出
        # 遮罩左缘 9.08;按行 union 后遮罩左缘 5.08 ≤ 7,行 0 被覆盖。
        plane = np.full((PLANE_H, PLANE_W, 3), 250, np.uint8)
        rows = [("ああああああ", (20.0, 20.0, 90.0, 36.0)),
                ("あああ。", (20.0, 40.0, 90.0, 56.0))]
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in rows]
        # video_h=1080:标点补偿上限 = 7(0.25×16=4 更小,取 4)
        out, applied, _notes = apply_policy(
            events, rows, plane, make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, 1080)
        assert applied == "mask"
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert len(masks) == 1  # 两行并一块,单条遮罩
        mask = masks[0]
        x1, y1, x2, y2 = move_points(mask["tags"])
        m = re.search(r"m 0 0 l (\d+) 0 (\d+) (\d+) 0 (\d+)", mask["tags"])
        assert m
        w, h = int(m.group(1)), int(m.group(3))
        # window=5 平滑把帧 0 位姿移到帧 1 位置(线性轨迹 +2px):比较时减掉
        left = x1 - 2.0
        right = x1 + w - 2.0
        # 行 0 渲染跨度 = 55±48 → [7, 103];必须被遮罩覆盖
        assert left <= 7.0 + 0.05, mask["tags"]
        assert right >= 103.0 - 0.5, mask["tags"]
        # 行 1 文本事件右移 dx=4 → 渲染跨度 [27, 91]
        assert left <= 27.0 + 0.05, mask["tags"]
        assert right >= 91.0 - 0.5, mask["tags"]
        # 垂直方向覆盖两行(含 pad)
        assert y1 <= 20.0 - 1.92 + 0.5
        assert y1 + h >= 56.0 + 1.92 - 0.5


# ---------------------------------------------------------------------------
# Task 3:背景取色 robust 统计(先红后绿)
# ---------------------------------------------------------------------------

def make_dark_plane() -> np.ndarray:
    """暗底反白字:背景灰 30,行框内 4px 高浅色笔画(230)。"""
    plane = np.full((PLANE_H, PLANE_W, 3), 30, np.uint8)
    for _text, (x1, y1, x2, _y2) in ROWS:
        plane[int(y1) + 4:int(y1) + 8, int(x1) + 4:int(x2) - 4] = 230
    return plane


def make_ringing_plane() -> np.ndarray:
    """近纯色背景(灰 128)+ 稀疏亮色压缩振铃(每 13 像素 1 个亮斑 ≈7.7%)。"""
    plane = np.full((PLANE_H, PLANE_W, 3), 128, np.uint8)
    yy, xx = np.mgrid[0:PLANE_H, 0:PLANE_W]
    plane[(yy * PLANE_W + xx) % 13 == 0] = 250
    return plane


def make_solid_color_plane() -> np.ndarray:
    """彩色纯色背景(青 BGR 80,160,160)+ 深色笔画。"""
    plane = np.zeros((PLANE_H, PLANE_W, 3), np.uint8)
    plane[:] = (80, 160, 160)
    for _text, (x1, y1, x2, _y2) in ROWS:
        plane[int(y1) + 4:int(y1) + 8, int(x1) + 4:int(x2) - 4] = (10, 10, 10)
    return plane


def make_gradient_gray(h: int, w: int, top: float, bottom: float) -> np.ndarray:
    """垂直线性渐变灰度图(逐行常值,float64)。"""
    rows = top + (bottom - top) * (np.arange(h, dtype=np.float64) / max(1, h - 1))
    return np.repeat(rows[:, None], w, axis=1)


class TestBackgroundRobustStats:
    """sample_background_stats:MAD/IQR、样本数、confidence;默认模式等价旧规则。"""

    def test_dark_bg_light_text_default_mode_degrades(self):
        # 旧墨剔除只认「比中位暗」:反白字全部留在背景样本里 → std 爆炸
        img = np.full((80, 120, 3), 30, np.uint8)
        img[10:30, 10:60] = 230  # 1000/9600 像素笔画
        stats = sample_background_stats(img, (0, 0, 120, 80))
        assert stats.mode == "std"
        assert stats.std_max_channel > 18.0
        assert stats.uniform is False
        assert stats.confidence == 0.0

    def test_dark_bg_light_text_robust_mode_recovers(self):
        img = np.full((80, 120, 3), 30, np.uint8)
        img[10:30, 10:60] = 230
        stats = sample_background_stats(
            img, (0, 0, 120, 80), background_mode="robust")
        assert isinstance(stats, BackgroundStats)
        assert stats.mode == "robust"
        assert stats.color_bgr == (30, 30, 30)
        assert stats.std_max_channel < 2.0
        assert stats.robust_spread_mad < 2.0
        assert stats.robust_spread_iqr < 2.0
        assert stats.n_samples == 8600  # 亮字被双向墨剔除
        assert stats.confidence > 0.9
        assert stats.uniform is True

    def test_compression_ringing_default_vs_robust(self):
        img = np.full((80, 120, 3), 128, np.uint8)
        yy, xx = np.mgrid[0:80, 0:120]
        img[(yy * 120 + xx) % 13 == 0] = 250
        legacy = sample_background_stats(img, (0, 0, 120, 80))
        assert legacy.std_max_channel > 18.0  # 旧规则误判为杂色
        assert legacy.uniform is False
        robust = sample_background_stats(
            img, (0, 0, 120, 80), background_mode="robust")
        assert robust.std_max_channel < 2.0
        assert robust.robust_spread_mad < 2.0
        assert robust.robust_spread_iqr < 2.0
        assert robust.color_bgr == (128, 128, 128)
        assert robust.n_samples > 0
        assert robust.confidence > 0.9
        assert robust.uniform is True

    def test_colored_solid_background_both_modes(self):
        img = np.full((80, 120, 3), 160, np.uint8)
        img[:, :, 0] = 80  # BGR 青色 (80,160,160)
        img[10:30, 10:60] = 10
        for mode in ("std", "robust"):
            stats = sample_background_stats(
                img, (0, 0, 120, 80), background_mode=mode)
            assert stats.color_bgr == (80, 160, 160), mode
            assert stats.uniform is True, mode
            assert stats.confidence > 0.9, mode
        robust = sample_background_stats(
            img, (0, 0, 120, 80), background_mode="robust")
        assert robust.robust_spread_mad == pytest.approx(0.0, abs=1e-6)
        assert robust.robust_spread_iqr == pytest.approx(0.0, abs=1e-6)
        assert robust.n_samples == 8600

    def test_low_contrast_gradient_stats(self):
        # 均匀分布渐变:std = R/√12,σ 等价 MAD = 1.4826·R/4,IQR σ = (R/2)/1.349
        img = np.zeros((80, 120, 3), np.uint8)
        img[:] = make_gradient_gray(80, 120, 140.0, 184.0)[..., None]
        stats = sample_background_stats(img, (0, 0, 120, 80))
        assert stats.std_max_channel == pytest.approx(
            44.0 / np.sqrt(12.0), abs=0.6)
        assert stats.robust_spread_mad == pytest.approx(
            44.0 * 1.4826 / 4.0, abs=1.2)
        assert stats.robust_spread_iqr == pytest.approx(
            22.0 / 1.349, abs=1.2)
        assert stats.uniform is True  # 两种 spread 均低于旧阈值 18
        assert 0.0 < stats.confidence < 0.5
        robust = sample_background_stats(
            img, (0, 0, 120, 80), background_mode="robust")
        assert robust.robust_spread_mad == pytest.approx(
            44.0 * 1.4826 / 4.0, abs=1.2)
        assert robust.robust_spread_iqr == pytest.approx(
            22.0 / 1.349, abs=1.2)
        # robust spread 16.3 虽低于 18,但均匀度 0.09 × 置信度后低于默认
        # 下限 0.2 → robust 模式对渐变更保守(判为不可用)
        assert robust.uniform is False
        assert robust.confidence < 0.2

    def test_few_background_pixels_report_sample_starvation(self):
        # 采样窗几乎被文字占满:窗 40×15,墨行 y%5∈{0,1} → 背景 360/600 像素
        img = np.full((30, 60, 3), 245, np.uint8)
        for y in range(30):
            if y % 5 in (0, 1):
                img[y, :] = 10
        stats = sample_background_stats(
            img, (0, 0, 40, 15), background_mode="robust")
        assert stats.n_samples == 360
        assert stats.confidence == pytest.approx(360 / 512, rel=1e-3)
        assert stats.uniform is True  # 默认置信度下限 0.2 之内
        strict = sample_background_stats(
            img, (0, 0, 40, 15), background_mode="robust",
            bg_min_confidence=0.8)
        assert strict.confidence == pytest.approx(360 / 512, rel=1e-3)
        assert strict.uniform is False  # 置信度不足 → 判定不均匀

    def test_empty_region_stats_guard(self):
        img = np.full((50, 50, 3), 245, np.uint8)
        stats = sample_background_stats(img, (60, 60, 90, 90))
        assert stats.n_samples == 0
        assert stats.confidence == 0.0
        assert stats.uniform is False
        # 旧接口行为不变
        assert sample_background_color(img, (60, 60, 90, 90)) == ((0, 0, 0), 0.0)

    def test_legacy_wrapper_matches_std_mode(self):
        img = make_white_plane()
        color, std = sample_background_color(img, (0, 0, PLANE_W, 200))
        stats = sample_background_stats(img, (0, 0, PLANE_W, 200))
        assert (color, std) == (stats.color_bgr, stats.std_max_channel)

    def test_unknown_mode_raises(self):
        img = np.full((10, 10, 3), 128, np.uint8)
        with pytest.raises(ValueError):
            sample_background_stats(
                img, (0, 0, 10, 10), background_mode="bogus")

    def test_config_validates_new_modes(self):
        with pytest.raises(ValueError):
            policy_cfg("overlap", background_mode="bogus")
        with pytest.raises(ValueError):
            policy_cfg("overlap", ws_threshold_mode="bogus")


# ---------------------------------------------------------------------------
# Task 3:空白带 confidence 评分与局部百分位/Otsu 选项
# ---------------------------------------------------------------------------

class TestWhitespaceBandScoring:
    """find_whitespace_band_scored:面积/宽高/均匀性/可排版长度评分。"""

    def test_flat_plane_band_confidence_value(self):
        img = np.full((300, 200), 240, np.uint8)
        band, conf, info = find_whitespace_band_scored(
            img, [(0.0, 0.0, 200.0, 100.0)], line_h=20.0)
        assert band == (0, 100, 200, 300)
        # c_area = 40000/60000 = 2/3,c_size = c_uniformity = c_layout = 1
        assert conf == pytest.approx(0.25 * (2 / 3 + 1 + 1 + 1), abs=1e-3)
        assert info["threshold_mode"] == "global"
        assert info["n_candidates"] == 1
        assert info["c_area"] == pytest.approx(2 / 3, abs=1e-3)
        assert 0.0 <= info["c_uniformity"] <= 1.0

    def test_gradient_global_truncates_percentile_recovers(self):
        # 渐变下部灰度低于「全图中位 − 40」→ 全局阈值把带截短;
        # 行内百分位逐行自适应 → 带延伸到底
        gray = make_gradient_gray(300, 200, 180.0, 60.0)
        boxes = [(10.0, 20.0, 190.0, 100.0)]
        g_band, g_conf, g_info = find_whitespace_band_scored(
            gray, boxes, line_h=20.0)
        assert g_band is not None
        # median ≈ 120,thr = 80 → y ≥ 250 判墨;膨胀核 5×5 再上扩 2 行
        assert g_band[3] == 248
        assert g_info["threshold_mode"] == "global"
        p_band, p_conf, p_info = find_whitespace_band_scored(
            gray, boxes, line_h=20.0, threshold_mode="percentile")
        assert p_band == (0, 100, 200, 300)
        assert p_info["threshold_mode"] == "percentile"
        assert 0.0 < g_conf <= 1.0 and 0.0 < p_conf <= 1.0

    def test_otsu_mode_finds_band(self):
        img = np.full((300, 200), 240, np.uint8)
        img[20:120, 10:190] = 20
        band, conf, info = find_whitespace_band_scored(
            img, [], line_h=20.0, threshold_mode="otsu")
        assert band is not None
        _x1, y1, _x2, y2 = band
        assert y1 >= 120 - 10
        assert y2 >= 290
        assert info["threshold_mode"] == "otsu"
        assert 0.0 < conf <= 1.0

    def test_no_band_zero_confidence(self):
        img = np.full((300, 200), 240, np.uint8)
        for y in range(0, 300, 6):
            img[y:y + 3, :] = 20
        band, conf, info = find_whitespace_band_scored(img, [], line_h=20.0)
        assert band is None and conf == 0.0
        assert info["n_candidates"] == 0

    def test_unknown_threshold_mode_raises(self):
        img = np.full((50, 50), 240, np.uint8)
        with pytest.raises(ValueError):
            find_whitespace_band(
                img, [], line_h=10.0, threshold_mode="bogus")


# ---------------------------------------------------------------------------
# Task 3:背景/空白带加固的策略端到端(动态 + 静态)
# ---------------------------------------------------------------------------

class TestBackgroundPolicyIntegration:
    """background_mode / ws 阈值与置信度在 apply_policy(_static) 的表现。"""

    def _events(self):
        return [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]

    def test_dark_bg_default_mask_falls_back_external(self):
        res = apply_policy(self._events(), ROWS, make_dark_plane(),
                           make_translation_tracks(), policy_cfg("mask"),
                           PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "external"
        assert "mask" in notes[0] and "external" in notes[0]
        assert res.diagnostics["background_blocks"][0]["mode"] == "std"
        assert res.diagnostics["background_blocks"][0]["uniform"] is False

    def test_dark_bg_robust_mask_applied_with_diagnostics(self):
        res = apply_policy(
            self._events(), ROWS, make_dark_plane(),
            make_translation_tracks(),
            policy_cfg("mask", background_mode="robust"),
            PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert len(masks) == 2
        assert all("\\1c&H1E1E1E&" in ev["tags"] for ev in masks)  # 30=0x1E
        bg = res.diagnostics["background_blocks"]
        assert len(bg) == 2
        for entry in bg:
            assert entry["mode"] == "robust"
            assert entry["uniform"] is True
            assert entry["n_samples"] > 0
            assert entry["confidence"] > 0.9
            assert entry["robust_spread_mad"] < 2.0

    def test_ringing_noise_default_external_robust_mask(self):
        events = self._events()
        legacy = apply_policy(events, ROWS, make_ringing_plane(),
                              make_translation_tracks(), policy_cfg("mask"),
                              PLANE_W, PLANE_H)
        assert legacy[1] == "external"  # 默认模式行为与旧规则一致
        robust = apply_policy(events, ROWS, make_ringing_plane(),
                              make_translation_tracks(),
                              policy_cfg("mask", background_mode="robust"),
                              PLANE_W, PLANE_H)
        out, applied, notes = robust
        assert applied == "mask" and notes == []
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert masks
        assert all("\\1c&H808080&" in ev["tags"] for ev in masks)

    def test_colored_solid_background_mask_color(self):
        res = apply_policy(
            self._events(), ROWS, make_solid_color_plane(),
            make_translation_tracks(),
            policy_cfg("mask", background_mode="robust"),
            PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert masks
        assert all("\\1c&H50A0A0&" in ev["tags"] for ev in masks)

    def test_gradient_whitespace_percentile_extends_band(self):
        plane = np.zeros((PLANE_H, PLANE_W, 3), np.uint8)
        plane[:] = make_gradient_gray(PLANE_H, PLANE_W, 180.0, 20.0)[..., None]
        global_res = apply_policy(
            self._events(), ROWS, plane, make_translation_tracks(),
            policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert global_res[1] == "whitespace"
        pct_res = apply_policy(
            self._events(), ROWS, plane, make_translation_tracks(),
            policy_cfg("whitespace", ws_threshold_mode="percentile"),
            PLANE_W, PLANE_H)
        out, applied, notes = pct_res
        assert applied == "whitespace" and notes == []
        g_band = global_res.diagnostics["whitespace_band"]
        p_band = pct_res.diagnostics["whitespace_band"]
        assert g_band is not None and p_band is not None
        assert g_band["threshold_mode"] == "global"
        assert p_band["threshold_mode"] == "percentile"
        assert g_band["box"][3] < p_band["box"][3] == PLANE_H

    def test_whitespace_band_diagnostics_and_low_confidence_fallback(self):
        events = self._events()
        res = apply_policy(events, ROWS, make_white_plane(),
                           make_translation_tracks(),
                           policy_cfg("whitespace"), PLANE_W, PLANE_H)
        assert res[1] == "whitespace"
        band_info = res.diagnostics["whitespace_band"]
        assert band_info is not None
        assert len(band_info["box"]) == 4
        assert 0.0 < band_info["confidence"] <= 1.0
        assert band_info["threshold_mode"] == "global"
        # 置信度下限抬高 → 候选带判为不足 → 沿回退链降到 mask,带留痕
        strict = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("whitespace", ws_min_confidence=0.999),
            PLANE_W, PLANE_H)
        out, applied, notes = strict
        assert applied == "mask"
        assert "confidence" in notes[0] and "mask" in notes[0]
        assert strict.diagnostics["whitespace_band"] is not None
        assert sum(1 for ev in out if "\\p1" in ev["tags"]) == 2

    def test_starved_window_diagnostics_and_configurable_fallback(self):
        # 行框 40×6,外扩采样窗 ≈42×8:窗内墨行(y%3==0)占 2/8 → 背景样本稀少
        rows = [("字", (20.0, 20.0, 60.0, 26.0))]
        events = [simple_event("字", 0.0, 9 / FPS)]
        plane = np.full((PLANE_H, PLANE_W, 3), 245, np.uint8)
        plane[::3, :, :] = 10
        default = apply_policy(events, rows, plane, make_translation_tracks(),
                               policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = default
        assert applied == "mask" and notes == []  # 旧规则 std=0 → 可用(不变)
        assert default.diagnostics["background_blocks"][0]["n_samples"] < 512
        strict = apply_policy(
            events, rows, plane, make_translation_tracks(),
            policy_cfg("mask", background_mode="robust",
                       bg_min_confidence=0.5),
            PLANE_W, PLANE_H)
        out, applied, notes = strict
        assert applied == "external"
        assert "confidence" in notes[0]

    def test_static_mask_robust_mode_and_diagnostics(self):
        res = apply_policy_static(
            ROWS, make_dark_plane(),
            policy_cfg("mask", background_mode="robust"), PLANE_W, PLANE_H)
        specs, applied, notes = res
        assert applied == "mask" and notes == []
        assert any("\\1c&H1E1E1E&" in s["tags"]
                   for s in specs if s["kind"] == "mask")
        bg = res.diagnostics["background_blocks"]
        assert bg and bg[0]["mode"] == "robust" and bg[0]["uniform"] is True

    def test_static_default_dark_bg_still_degrades(self):
        # 默认模式在暗底反白字上保持旧行为:回退 external
        _specs, applied, _notes = apply_policy_static(
            ROWS, make_dark_plane(), policy_cfg("mask"), PLANE_W, PLANE_H)
        assert applied == "external"

    def test_static_whitespace_band_diagnostics(self):
        res = apply_policy_static(
            ROWS, make_white_plane(), policy_cfg("whitespace"),
            PLANE_W, PLANE_H)
        specs, applied, _notes = res
        assert applied == "whitespace"
        info = res.diagnostics["whitespace_band"]
        assert info is not None and len(info["box"]) == 4
        assert 0.0 < info["confidence"] <= 1.0

    def test_static_low_confidence_band_falls_back_to_mask(self):
        res = apply_policy_static(
            ROWS, make_white_plane(),
            policy_cfg("whitespace", ws_min_confidence=0.999),
            PLANE_W, PLANE_H)
        specs, applied, notes = res
        assert applied == "mask"
        assert "confidence" in notes[0]
        assert res.diagnostics["whitespace_band"] is not None


# ---------------------------------------------------------------------------
# Task 5:透视遮罩的安全边界(先红后绿)
# ---------------------------------------------------------------------------

def make_tilt_tracks(n: int = 10, dx: float = 2.0, px: float = 0.002) -> list:
    """n 帧透视(梯形)轨迹:H(init→t) = 平移(dx·t) ∘ 透视除法。

    透视参数随帧线性增长(平面相对相机随时间倾斜的现实情形):帧 0 恒等,
    t > 0 时相对单应 H(ref→t) 为真透视变换,行框四角映射为梯形——旋转+
    等比缩放的矩形遮罩无法完全覆盖(原字从遮罩边缘透出,重影)。
    W = 1 + px·t·x'(x' = x + dx·t),px 越大梯形越夸张。
    """
    tracks = []
    for i in range(n):
        d = dx * i
        p = px * i
        h_mat = np.array([
            [1.0, 0.0, d],
            [0.0, 1.0, 0.0],
            [p, 0.0, 1.0 + p * d],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


def make_squash_tracks(n: int = 4, eps: float = 1e-3) -> list:
    """退化轨迹:帧 0 恒等(参考帧),t > 0 起 y 向压缩 ×eps。

    相对单应 H(ref→t) 把行框四角压到一条水平线附近(shoelace 面积 ≈ 0,
    三点近似共线),eps ≠ 0 保证单应可逆(homography_inv 存在)。注意退化
    必须是「相对参考帧」的:所有帧共享的固定形变会在 H(ref→t) 中抵消
    (平面坐标系由参考帧矫正定义)。
    """
    tracks = []
    for i in range(n):
        e = 1.0 if i == 0 else eps
        h_mat = np.array([
            [1.0, 0.0, 2.0 * i],
            [0.0, e, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


def make_similarity_tracks(n: int = 10, dx: float = 2.0, ds: float = 0.02,
                           dth: float = 2.0) -> list:
    """n 帧相似变换轨迹(平移 + 等比缩放 + 旋转):矩形遮罩可精确覆盖。"""
    tracks = []
    for i in range(n):
        th = math.radians(dth * i)
        s = 1.0 + ds * i
        cos, sin = math.cos(th), math.sin(th)
        h_mat = np.array([
            [s * cos, -s * sin, dx * i],
            [s * sin, s * cos, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


def polygon_points(tags: str):
    """解析 ``\\p1`` 多边形遮罩事件:``\\pos`` 原点 + drawing 四点 → 绝对坐标。"""
    m = re.search(r"\\pos\((-?[\d.]+),(-?[\d.]+)\)", tags)
    assert m, tags
    ox, oy = float(m.group(1)), float(m.group(2))
    body = tags.split("}", 1)[1]
    body = body[:body.index("{\\p0}")]
    nums = re.findall(r"-?[\d.]+", body)
    assert len(nums) == 8, body  # m x0 y0 l x1 y1 x2 y2 x3 y3(四角)
    vals = [float(v) for v in nums]
    return [(ox + vals[i], oy + vals[i + 1]) for i in range(0, 8, 2)]


class TestMaskPerspectiveGuard:
    """矩形近似误差超限 / 退化裁剪框 → mask 降级 external 并留痕。"""

    def _events(self):
        return [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]

    def test_trapezoid_error_exceeds_threshold_falls_back_to_external(self):
        # 梯形平面(px=0.002 → 矩形近似误差 ≈0.30 > 默认阈值):不得生成
        # 虚假「完美」矩形遮罩,必须降级 external 并记录原因与误差值
        tracks = make_tilt_tracks(px=0.002)
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "external"
        assert len(notes) == 1
        assert "mask->external" in notes[0]
        assert "perspective" in notes[0], notes
        m = re.search(r"error (\d+\.\d+)", notes[0])
        assert m, notes  # notes 含误差数值
        err = float(m.group(1))
        assert err > 0.1
        # diagnostics 记录误差度量
        mp = res.diagnostics["mask_perspective"]
        assert mp and mp[0]["max_error"] == pytest.approx(err, abs=0.01)
        assert all(ev["style"] == "NoteBox" for ev in out)  # external 事件
        assert all("\\p1" not in ev["tags"] for ev in out)  # 无矩形遮罩

    def test_trapezoid_within_threshold_still_mask(self):
        # px=0.0002 → 最坏块误差 ≈0.061 < 默认 0.1:不降级(证明阈值生效)
        tracks = make_tilt_tracks(px=0.0002)
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H)
        assert res[1] == "mask" and res[2] == []
        assert any("\\p1" in ev["tags"] for ev in res[0])

    def test_similarity_scenarios_never_hit_threshold(self):
        # 默认阈值必须宽松到不影响既有场景:纯平移/相似变换(平移+等比
        # 缩放+旋转)误差 ≈ 0 → 不触发降级
        for tracks in (make_translation_tracks(), make_similarity_tracks()):
            res = apply_policy(
                self._events(), ROWS, make_white_plane(), tracks,
                policy_cfg("mask"), PLANE_W, PLANE_H)
            assert res[1] == "mask" and res[2] == [], type(tracks)

    def test_degenerate_clipped_box_falls_back_to_external(self):
        # 退化裁剪框:遮罩框完全在 analysis_box 右侧,裁剪后宽为负/零
        rows = [("外", (150.0, 20.0, 200.0, 36.0))]
        events = [simple_event("外", 0.0, 9 / FPS)]
        res = apply_policy(
            events, rows, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H,
            analysis_box=(0, 0, 100, 400))
        out, applied, notes = res
        assert applied == "external"
        assert notes and "mask->external" in notes[0]
        assert "degenerate mask box" in notes[0], notes
        assert all(ev["style"] == "NoteBox" for ev in out)

    def test_degenerate_quad_zero_area_falls_back_to_external(self):
        # 退化四边形:y 向压缩 1e-3 → 四角近似共线(shoelace 面积 ≈ 0)
        tracks = make_squash_tracks(eps=1e-3)
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "external"
        assert notes and "mask->external" in notes[0]
        assert "degenerate" in notes[0], notes
        assert all(ev["style"] == "NoteBox" for ev in out)

    def test_axis_aligned_mask_output_unchanged(self):
        # 回归红线:规则矩形 quad 误差为 0,输出与加固前实现逐字段一致,
        # 唯一例外是遮罩链尾结束时间按「尾帧 + 1 帧距」延伸(0.36→0.40,
        # 与文本事件 motion_ass 同款修复对齐,遮罩不再先于文本消失)
        events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
        res = apply_policy(
            events, ROWS, make_white_plane(), make_translation_tracks(),
            policy_cfg("mask"), PLANE_W, PLANE_H)
        assert res.applied_mode == "mask" and res.notes == []
        assert [dict(ev) for ev in res.events] == [
            {
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.40",
                "style": "Scene",
                "name": "motion",
                "tags": ("{\\an7\\p1\\bord0\\1c&HFAFAFA&"
                         "\\move(20.1,18.1,34.1,18.1,0,400)}"
                         "m 0 0 l 184 0 184 20 0 20{\\p0}"),
                "body": "",
                "layer": 0,
                "base_color": (250, 250, 250),
            },
            {
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.40",
                "style": "Scene",
                "name": "motion",
                "tags": ("{\\an7\\p1\\bord0\\1c&HFAFAFA&"
                         "\\move(20.1,58.1,34.1,58.1,0,400)}"
                         "m 0 0 l 184 0 184 40 0 40{\\p0}"),
                "body": "",
                "layer": 0,
                "base_color": (250, 250, 250),
            },
            {
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.36",
                "style": "Scene",
                "name": "motion",
                "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}",
                "body": "标题",
                "layer": 1,
            },
            {
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.36",
                "style": "Scene",
                "name": "motion",
                "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}",
                "body": "正文一",
                "layer": 1,
            },
            {
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.36",
                "style": "Scene",
                "name": "motion",
                "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}",
                "body": "正文二",
                "layer": 1,
            },
        ]


class TestMaskPolygonClip:
    """实验开关 mask_polygon_clip:逐帧四角 ``\\p1`` 多边形遮罩事件。"""

    def _events(self):
        return [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]

    def test_polygon_events_per_frame_with_valid_tags(self):
        tracks = make_translation_tracks()
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask", mask_polygon_clip=True), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        polys = [ev for ev in out if "\\p1" in ev["tags"]]
        texts = [ev for ev in out if "\\p1" not in ev["tags"]]
        # 两块 × 10 个 ok 帧 = 20 条逐帧多边形;3 行文本升 layer 1
        assert len(polys) == 2 * 10
        assert len(texts) == 3
        assert all(ev["layer"] == 1 for ev in texts)
        for ev in polys:
            assert ev["style"] == "Scene"
            assert ev["layer"] == 0
            assert ev["name"] == "motion" and ev["body"] == ""
            assert ev["base_color"] == (250, 250, 250)
            # 标签合法:{\an7\pos(..)\p1\bord0\1c..} ... {\p0}
            assert ev["tags"].startswith("{\\an7\\pos(")
            assert "\\p1" in ev["tags"] and "\\bord0" in ev["tags"]
            assert "\\1c&HFAFAFA&" in ev["tags"]
            assert ev["tags"].endswith("{\\p0}")
        # 逐帧四角 = 遮罩框四角平移 dx·t(块 0 框 (18.08,18.08)-(201.92,37.92))
        for t, ev in enumerate(polys[:10]):
            pts = polygon_points(ev["tags"])
            assert len(pts) == 4
            xs = sorted(p[0] for p in pts)
            ys = sorted(p[1] for p in pts)
            assert xs[0] == pytest.approx(18.08 + 2.0 * t, abs=0.06)
            assert xs[1] == pytest.approx(18.08 + 2.0 * t, abs=0.06)
            assert xs[2] == pytest.approx(201.92 + 2.0 * t, abs=0.06)
            assert xs[3] == pytest.approx(201.92 + 2.0 * t, abs=0.06)
            assert ys[0] == pytest.approx(18.08, abs=0.06)
            assert ys[3] == pytest.approx(37.92, abs=0.06)
            assert ev["start_time"] == format_ass_time(t / FPS)
        # 块 1 框 y ∈ {58.08, 97.92}
        for t, ev in enumerate(polys[10:]):
            ys = sorted(p[1] for p in polygon_points(ev["tags"]))
            assert ys[0] == pytest.approx(58.08, abs=0.06)
            assert ys[3] == pytest.approx(97.92, abs=0.06)
            assert ev["start_time"] == format_ass_time(t / FPS)
        # 末帧事件延续一个帧间隔(hold)
        assert polys[9]["end_time"] == format_ass_time(10 / FPS)

    def test_polygon_covers_trapezoid_without_fallback(self):
        # 实验开关打开:透视梯形不再触发矩形误差降级(多边形精确覆盖)
        tracks = make_tilt_tracks(px=0.002)
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask", mask_polygon_clip=True), PLANE_W, PLANE_H)
        out, applied, notes = res
        assert applied == "mask" and notes == []
        assert sum(1 for ev in out if "\\p1" in ev["tags"]) == 2 * 10

    def test_polygon_off_by_default(self):
        # 默认关闭:输出与既有矩形遮罩一致
        tracks = make_translation_tracks()
        res = apply_policy(
            self._events(), ROWS, make_white_plane(), tracks,
            policy_cfg("mask"), PLANE_W, PLANE_H)
        assert res[1] == "mask"
        assert all("\\move(" in ev["tags"]
                   for ev in res[0] if "\\p1" in ev["tags"])


class TestMaskPerspectiveConfig:
    """新配置项默认值与 pipeline 配置键(policy_ 前缀动态路由)。"""

    def test_defaults_and_construction(self):
        cfg = SceneTextPolicyConfig()
        assert cfg.mask_max_perspective_error == pytest.approx(0.1)
        assert cfg.mask_polygon_clip is False
        cfg = policy_cfg("mask", mask_max_perspective_error=0.02,
                         mask_polygon_clip=True)
        assert cfg.mask_max_perspective_error == pytest.approx(0.02)
        assert cfg.mask_polygon_clip is True

    def test_policy_config_keys_route_from_pipeline_config(self):
        # CLI/pipeline --config-json 的 policy_ 前缀键动态路由 dataclass
        # fields(dataclasses.fields 驱动):新配置键无需改动路由代码即可用
        from scripts.motion_ass import build_config
        _motion_cfg, routed = build_config({
            "scene_text_policy": "mask",
            "policy_mask_max_perspective_error": 0.02,
            "policy_mask_polygon_clip": True,
        })
        assert routed.mode == "mask"
        assert routed.mask_max_perspective_error == pytest.approx(0.02)
        assert routed.mask_polygon_clip is True
