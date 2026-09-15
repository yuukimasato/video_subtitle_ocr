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
- brightness_tag_chain 的 base_color 扩展:给定 BGR 基色时按通道缩放、
  缺省保持灰色行为(回归由 test_motion_ass 既有用例保证)。

轨迹用合成单应(平移)构造,与 test_motion_ass 同一模式;不读视频、不加载模型。
"""

from __future__ import annotations

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
    SceneTextPolicyConfig,
    apply_policy,
    find_whitespace_band,
    fit_font_size,
    merge_line_blocks,
    sample_background_color,
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
            assert re.search(r"\\move\([^)]+,0,360\)", ev["tags"]), ev["tags"]
            assert ev["start_time"] == "0:00:00.00"
            assert ev["end_time"] == format_ass_time(9 / FPS)
        for ev in texts:
            assert ev["layer"] == 1
            assert ev["tags"] == "{\\an5\\move(0.0,0.0,0.0,0.0)}"  # 原样保留
            assert ev["body"] in {"标题", "正文一", "正文二"}

    def test_mask_color_tag_is_sampled(self):
        out, _applied, _notes = self._run()
        masks = [ev for ev in out if "\\p1" in ev["tags"]]
        assert all("\\1c&HFAFAFA&" in ev["tags"] for ev in masks)  # 250=0xFA

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
            assert (x1, y1) == pytest.approx(lt.poses[f0].center, abs=0.05), ev
            assert (x2, y2) == pytest.approx(lt.poses[f1].center, abs=0.05), ev


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
